"""Action 执行管线（设计文档 §4.1）：人/AI/ISV/MCP 的唯一写路径。
校验/规则/断言/审计与业务写入同事务；Outbox 同事务落库，提交后 relay。"""
from __future__ import annotations
import datetime as dt
import json
from decimal import Decimal
from typing import Callable
import jsonschema
from sqlalchemy.orm import Session
from heng_contracts.models import (ActionRequest, ActionResult, ActorRef, ApprovalDecision,
                                   Violation, new_id)
from . import db
from .registry import MetadataRegistry
from .rules import RuleEngine
from .context import ActionContext, build_eval_ctx
from .expr import check
from .bus import EventBus, drain_outbox

RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _jsonable(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (dt.date, dt.datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


class Engine:
    def __init__(self, session_factory, registry: MetadataRegistry, rules: RuleEngine,
                 handlers: dict[str, Callable], bus: EventBus | None = None,
                 clock: dt.date | None = None, automation: dict | None = None):
        self.sf = session_factory
        self.registry = registry
        self.rules = rules
        self.handlers = handlers
        self.bus = bus or EventBus()
        self.clock = clock or dt.date.today()
        self.automation = automation or {}   # tenant -> action -> {level, conditions}
        self.calls: list[dict] = []          # 评测记录器（生产由 OTel 替代）

    # ---------- 主入口 ----------
    def execute(self, req: ActionRequest, session: Session | None = None) -> ActionResult:
        own = session is None
        session = session or self.sf()
        try:
            return self._execute(req, session)
        finally:
            if own:
                session.close()

    def _execute(self, req: ActionRequest, session: Session) -> ActionResult:
        rec = {"action": req.action, "args": _jsonable(req.args), "dry_run": req.dry_run,
               "actor_type": req.actor.type, "status": None, "approval_id": None}
        self.calls.append(rec)

        def finish(result: ActionResult, cache: bool = True) -> ActionResult:
            rec["status"] = result.status
            rec["approval_id"] = result.approval_id
            rec["data"] = result.data
            self._audit(session, req, result.status)
            session.commit()
            if cache and not req.dry_run and result.status in ("ok", "rejected"):
                self._idem_put(session, req, result)
            return result

        # 1 元数据
        try:
            meta = self.registry.get_action(req.action)
        except KeyError:
            return finish(ActionResult(status="rejected", violations=[
                Violation(code="unknown_action", message=f"未注册动作: {req.action}")]), cache=False)

        # 2 结构校验（LLM 输出在此被视为不可信输入）
        validator = jsonschema.Draft202012Validator(meta.input_schema)
        errors = sorted(validator.iter_errors(req.args), key=lambda e: e.path)
        if errors:
            return finish(ActionResult(status="rejected", violations=[
                Violation(code="schema", message=e.message,
                          field=".".join(str(p) for p in e.path) or None) for e in errors[:5]]))

        # 3 权限（scope 交集；on_behalf_of 权限单调不增）
        if not self._authorized(req, meta):
            return finish(ActionResult(status="rejected", violations=[
                Violation(code="forbidden", message=f"缺少 scope: {meta.scopes}")]))

        # 4 幂等回放
        if not req.dry_run:
            cached = self._idem_get(session, req)
            if cached is not None:
                rec["status"] = cached.status
                return cached

        # 5 确定性前置条件
        ectx = build_eval_ctx(session, req.actor.tenant_id, req.args, self.rules, self.clock)
        for expr in meta.preconditions:
            ok, err = check(expr, ectx)
            if not ok:
                return finish(ActionResult(status="rejected", violations=[
                    Violation(code="precondition", message=f"{expr} -> {err or 'false'}")]))

        # 6 风险与自动化策略（L1 建议 / L2 条件自动 / 四眼）
        need = self._approvals_needed(req, meta, ectx)
        if need > 0 and not req.dry_run and not req.approval_id:
            ap = db.Approval(tenant_id=req.actor.tenant_id,
                             action_req=_jsonable(req.model_dump(mode="json")),
                             risk=meta.risk_level, status="pending")
            session.add(ap)
            session.flush()
            result = ActionResult(status="pending_approval", approval_id=ap.id,
                                  data={"required_approvals": need, "risk": meta.risk_level})
            return finish(result, cache=False)

        handler = self.handlers.get(meta.handler)
        if handler is None:
            return finish(ActionResult(status="failed", violations=[
                Violation(code="no_handler", message=meta.handler)]), cache=False)

        ctx = ActionContext(session=session, tenant_id=req.actor.tenant_id,
                            actor=req.actor, rules=self.rules, clock=self.clock, meta=meta)

        # 7 dry-run：savepoint 模拟后回滚，产出影响预览（不落库）
        if req.dry_run:
            try:
                sp = session.begin_nested()
                out = handler(ctx, req.args)
                sp.rollback()
                return finish(ActionResult(status="ok", data=_jsonable(out.get("data", {})),
                                           effects_preview=_jsonable(out.get("effects_preview", {}))),
                              cache=False)
            except Exception as e:  # noqa: BLE001
                session.rollback()
                return finish(ActionResult(status="failed", violations=[
                    Violation(code="dry_run_error", message=str(e))]), cache=False)

        # 8-11 事务：写入 + 后置断言 + Outbox + 审计（同事务）
        session.rollback()  # 结束前置检查的只读事务，开启写事务
        try:
            with session.begin():
                out = handler(ctx, req.args)
                data = out.get("data", {})
                pctx = dict(ectx)
                pctx.update({"result": data, "data": data})
                for expr in meta.postconditions:
                    ok, err = check(expr, pctx)
                    if not ok:
                        raise AssertionError(f"postcondition {expr}: {err or 'false'}")
                events = ctx.events + [{
                    "type": f"cn.heng.{req.action}.done", "subject": str(data.get("id", req.action)),
                    "data": _jsonable(data)}]
                for ev in events:
                    session.add(db.Outbox(
                        tenant_id=req.actor.tenant_id, event_type=ev["type"],
                        payload={"type": ev["type"], "tenant_id": req.actor.tenant_id,
                                 "subject": ev["subject"], "trace_id": req.trace_id,
                                 "data": ev["data"],
                                 "heng_meta": {"actor": f"{req.actor.type}:{req.actor.id}",
                                               "on_behalf_of": req.on_behalf_of or "",
                                               "ruleset": self.rules.ruleset_version(self.clock)}}))
                audit_id = new_id("au")
                session.add(db.AuditLog(id=audit_id, tenant_id=req.actor.tenant_id,
                                        action=req.action, actor_type=req.actor.type,
                                        actor_id=req.actor.id,
                                        on_behalf_of=req.on_behalf_of or "",
                                        args=_jsonable(req.args), status="ok", trace_id=req.trace_id))
            result = ActionResult(status="ok", data=_jsonable(data),
                                  rollback_token=meta.compensation, audit_id=audit_id)
            rec["status"] = "ok"
            rec["data"] = result.data
            self._idem_put(session, req, result)
            drain_outbox(session, self.bus)
            return result
        except Exception as e:  # noqa: BLE001 事务已回滚
            session.rollback()
            return finish(ActionResult(status="failed", violations=[
                Violation(code="handler_error", message=f"{type(e).__name__}: {e}")]), cache=False)

    # ---------- 审批（行动卡 / 四眼） ----------
    def approve(self, approval_id: str, decision: ApprovalDecision, tenant_id: str,
                session: Session | None = None, scopes: list[str] | None = None) -> ActionResult:
        own = session is None
        session = session or self.sf()
        try:
            ap = session.get(db.Approval, approval_id)
            if ap is None or ap.tenant_id != tenant_id:
                return ActionResult(status="rejected", violations=[
                    Violation(code="not_found", message="审批单不存在或跨租户")])
            if ap.status != "pending":
                return ActionResult(status="rejected", violations=[
                    Violation(code="bad_state", message=f"审批单状态: {ap.status}")])
            if decision.decision == "reject":
                ap.status = "rejected"
                session.add(db.AuditLog(tenant_id=tenant_id, action="approval.decide",
                                        actor_type="human", actor_id=decision.approver_id,
                                        args={"approval_id": ap.id, "decision": "reject"},
                                        status="rejected"))
                session.commit()
                return ActionResult(status="rejected", violations=[
                    Violation(code="approval_rejected", message=decision.reason or "人工驳回")])
            required = 2 if ap.risk == "critical" else 1
            if not ap.approver_a:
                ap.approver_a = decision.approver_id
            elif required == 2 and not ap.approver_b:
                if decision.approver_id == ap.approver_a:
                    session.add(db.AuditLog(tenant_id=tenant_id, action="approval.decide",
                                            actor_type="human", actor_id=decision.approver_id,
                                            args={"approval_id": ap.id, "decision": "four_eyes_violation"},
                                            status="rejected"))
                    session.commit()
                    return ActionResult(status="rejected", violations=[
                        Violation(code="four_eyes", message="四眼原则：第二审批人必须不同于第一审批人")])
                ap.approver_b = decision.approver_id
            have = 2 if ap.approver_b else 1
            if have < required:
                session.commit()
                return ActionResult(status="pending_approval", approval_id=ap.id,
                                    data={"required_approvals": required, "have": have})
            ap.status = "approved"
            session.commit()
            req = ActionRequest(**ap.action_req)
            req.approval_id = ap.id
            req.actor = ActorRef(type="human", id=decision.approver_id,
                                 tenant_id=tenant_id, scopes=scopes or ["*"])
            result = self.execute(req, session)
            ap.status = "executed" if result.status == "ok" else "failed"
            ap.result = _jsonable(result.model_dump(mode="json"))
            session.commit()
            return result
        finally:
            if own:
                session.close()

    # ---------- 内部 ----------
    def _authorized(self, req: ActionRequest, meta) -> bool:
        scopes = set(req.actor.scopes)
        if "*" in scopes:
            return True
        return all(s in scopes for s in meta.scopes)

    def _approvals_needed(self, req: ActionRequest, meta, ectx: dict) -> int:
        if req.approval_id:
            return 0
        if req.actor.type == "human":
            return meta.required_approvals if meta.risk_level in ("high", "critical") else 0
        # agent / isv / mcp_external
        if meta.risk_level == "low":
            return 0
        tenant_policy = self.automation.get(req.actor.tenant_id) or self.automation.get("*") or {}
        policy = tenant_policy.get(req.action, {})
        if policy.get("level") == "L2" and meta.automation.get("max_level") in ("L2", "L3"):
            cond = policy.get("conditions") or meta.automation.get("l2_conditions")
            if cond:
                ok, _ = check(cond, ectx)
                if ok:
                    return 0
            else:
                return 0
        return max(1, meta.required_approvals)

    def _idem_key(self, req: ActionRequest) -> str:
        return f"{req.actor.tenant_id}|{req.action}|{req.client_request_id}"

    def _idem_get(self, session: Session, req: ActionRequest) -> ActionResult | None:
        row = session.get(db.Idempotency, self._idem_key(req))
        if row and row.expires_at > db.utcnow():
            return ActionResult(**row.response)
        return None

    def _idem_put(self, session: Session, req: ActionRequest, result: ActionResult):
        key = self._idem_key(req)
        row = session.get(db.Idempotency, key)
        payload = _jsonable(result.model_dump(mode="json"))
        if row:
            row.response = payload
        else:
            session.add(db.Idempotency(key=key, tenant_id=req.actor.tenant_id, response=payload,
                                       expires_at=db.utcnow() + dt.timedelta(hours=24)))
        session.commit()

    def _audit(self, session: Session, req: ActionRequest, status: str):
        session.add(db.AuditLog(tenant_id=req.actor.tenant_id, action=req.action,
                                actor_type=req.actor.type, actor_id=req.actor.id,
                                on_behalf_of=req.on_behalf_of or "",
                                args=_jsonable(req.args), status=status, trace_id=req.trace_id))
