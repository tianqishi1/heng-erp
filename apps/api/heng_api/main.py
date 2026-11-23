# SPDX-License-Identifier: LGPL-3.0-or-later
"""FastAPI 组合根（M1）：登录鉴权 + 角色权限 + 进化中心 + 扩展包管理。
审批人身份=登录用户；进化审批/扩展包安装仅老板角色。"""
from __future__ import annotations
import os
from pathlib import Path
import yaml
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from heng_contracts.models import ActionRequest, ActorRef, ApprovalDecision, new_id
from heng_kernel import db
from heng_kernel.db import ensure_columns
from heng_actions.compose import build_engine, ensure_tenant, REPO_ROOT
from heng_eval.fixture import build_fixture
from heng_agent.orchestrator import Orchestrator
from heng_agent.providers import RuleBasedProvider
from heng_llm.gateway import LLMGateway, OpenAICompatProvider
from heng_evolution import EvolutionPipeline, Miner
from heng_evolution.assets import active_regex, active_asset
from . import auth

ENTITY_MODELS = {
    "sales.order": db.SalesOrder, "sales.order.line": db.SalesOrderLine,
    "journal.entry": db.JournalEntry, "journal.line": db.JournalLine,
    "stock.quant": db.StockQuant, "stock.move": db.StockMove,
    "tax.invoice": db.Invoice, "res.partner": db.Partner, "product.sku": db.Sku,
    "finance.payment": db.Payment,
}
RISK_LABEL = {"low": "低", "medium": "中", "high": "高", "critical": "极高（需双人审批）"}
CHECK_LABEL = {"order_totals": "订单金额勾稽（价税合计=不含税+税额）",
               "trial_balance": "借贷平衡（每凭证/每期间 借=贷）",
               "stock_consistency": "库存账实一致（现存=期初+出入库）",
               "invoice_tax_match": "发票与订单税额勾稽"}


class LoginBody(BaseModel):
    username: str
    password: str
    tenant_id: str = "t_demo"


class ActionBody(BaseModel):
    args: dict = Field(default_factory=dict)
    tenant_id: str = "t_demo"
    client_request_id: str = Field(default_factory=lambda: new_id("req"))


class CopilotBody(BaseModel):
    utterance: str
    tenant_id: str = "t_demo"
    sensitive: bool = False


class ApproveBody(BaseModel):
    decision: str = "approve"
    reason: str = ""
    tenant_id: str = "t_demo"


class CandidateBody(BaseModel):
    case_id: str


class ReviewBody(BaseModel):
    decision: str = "approve"


def _money(v):
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


def create_app(engine=None, sf=None, inv=None) -> FastAPI:
    if engine is None:
        engine, sf, inv = build_engine(os.environ.get("HENG_DB_URL", "sqlite:///heng_dev.db"))
    ensure_columns(sf.kw["bind"])  # SQLAlchemy 引擎（非业务管线 Engine）
    app = FastAPI(title="HengERP API", version="0.2.0")

    providers = [RuleBasedProvider()]
    base, key = os.environ.get("HENG_LLM_BASE_URL"), os.environ.get("HENG_LLM_API_KEY")
    if base and key:
        providers.insert(0, OpenAICompatProvider("cloud-llm", base, key,
                                                 os.environ.get("HENG_LLM_MODEL", "qwen-max"),
                                                 is_private=False))
    gateway = LLMGateway(providers)
    orch = Orchestrator(engine, gateway)
    pipeline = EvolutionPipeline(sf)
    miner = Miner(sf)

    @app.on_event("startup")
    def _seed():
        ensure_tenant(sf, "t_demo", "演示租户")
        auth.seed_users(sf, "t_demo")
        with sf() as s:
            n = s.execute(select(db.Partner).where(db.Partner.tenant_id == "t_demo")).scalars().all()
            if not n:
                build_fixture(engine, sf, "t_demo")
            installed = s.execute(select(db.PackageInstall)).scalars().all()
            for p in installed:
                try:
                    engine.registry.load_overlay_dir(p.path)
                except Exception:  # noqa: BLE001 包损坏不阻塞启动
                    pass

    def user(x_auth_token: str | None = Header(None)) -> db.AppUser:
        u = auth.resolve(sf, x_auth_token)
        if u is None:
            raise HTTPException(401, "未登录或会话已过期")
        return u

    def boss(u: db.AppUser = Depends(user)) -> db.AppUser:
        if u.role != "boss":
            raise HTTPException(403, "该操作仅限老板角色")
        return u

    def _actor(u) -> ActorRef:
        return ActorRef(type="human", id=u.id, tenant_id=u.tenant_id, scopes=list(u.scopes))

    def _partner_name(s, tenant_id, pid):
        p = s.execute(select(db.Partner).where(db.Partner.tenant_id == tenant_id,
                                               db.Partner.id == pid)).scalars().first()
        return p.name if p else pid

    def _approval_summary(s, ap):
        req = ap.action_req or {}
        action, args = req.get("action", ""), req.get("args", {})
        spec = engine.registry.actions.get(action)
        label = spec.label if spec else action
        tenant_id = ap.tenant_id
        parts = []
        if args.get("customer_id"):
            parts.append("客户：" + _partner_name(s, tenant_id, args["customer_id"]))
        for ln in args.get("lines", []):
            sku = s.execute(select(db.Sku).where(db.Sku.tenant_id == tenant_id,
                                                 db.Sku.id == ln.get("sku_id"))).scalars().first()
            parts.append(f"{ln.get('qty')} × {sku.name if sku else ln.get('sku_id')}（单价 {_money(ln.get('price'))}）")
        if args.get("lines"):
            try:
                total = sum(float(l.get("qty", 0)) * float(l.get("price", 0)) for l in args["lines"])
                parts.append(f"价税合计 {_money(total)} 元")
            except (TypeError, ValueError):
                pass
        if args.get("order_id"):
            o = s.execute(select(db.SalesOrder).where(db.SalesOrder.tenant_id == tenant_id,
                                                      db.SalesOrder.id == args["order_id"])).scalars().first()
            if o:
                parts.append(f"订单 {o.no}（{_money(o.total_amount)} 元）")
        if args.get("amount") is not None:
            parts.append(f"金额 {_money(args['amount'])} 元")
        have = (1 if ap.approver_a else 0) + (1 if ap.approver_b else 0)
        return {"id": ap.id, "title": label, "summary": "；".join(parts) or "—",
                "risk": RISK_LABEL.get(ap.risk, ap.risk),
                "required": 2 if ap.risk == "critical" else 1, "have": have,
                "status": ap.status, "created_at": ap.created_at.isoformat()}

    # ---------- 认证 ----------
    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "clock": engine.clock.isoformat(),
                "llm_offline": gateway.offline, "edition": "community-M1"}

    @app.post("/v1/auth/login")
    def login(body: LoginBody):
        r = auth.login(sf, body.tenant_id, body.username, body.password)
        if r is None:
            raise HTTPException(401, "用户名或密码错误")
        return r

    @app.post("/v1/auth/logout")
    def logout(x_auth_token: str | None = Header(None)):
        auth.logout(sf, x_auth_token or "")
        return {"status": "ok"}

    @app.get("/v1/auth/me")
    def me(u: db.AppUser = Depends(user)):
        return {"id": u.id, "name": u.display_name, "role": u.role,
                "role_label": auth.ROLE_LABEL.get(u.role, u.role)}

    # ---------- 业务 ----------
    @app.post("/v1/actions/{action}/execute")
    def execute(action: str, body: ActionBody, u: db.AppUser = Depends(user)):
        req = ActionRequest(action=action, args=body.args, actor=_actor(u),
                            client_request_id=body.client_request_id)
        return engine.execute(req).model_dump(mode="json")

    @app.post("/v1/actions/{action}/dry-run")
    def dry_run(action: str, body: ActionBody, u: db.AppUser = Depends(user)):
        req = ActionRequest(action=action, args=body.args, dry_run=True, actor=_actor(u),
                            client_request_id=body.client_request_id)
        return engine.execute(req).model_dump(mode="json")

    @app.post("/v1/copilot/messages")
    def copilot(body: CopilotBody, u: db.AppUser = Depends(user)):
        res = orch.run(u.tenant_id, u.id, body.utterance, sensitive=body.sensitive)
        if res.get("escalated"):
            miner.record_escalation(u.tenant_id, body.utterance, res.get("reply", ""))
        return res

    @app.post("/v1/approvals/{approval_id}/approve")
    def approve(approval_id: str, body: ApproveBody, u: db.AppUser = Depends(user)):
        res = engine.approve(approval_id,
                             ApprovalDecision(approver_id=u.id, decision=body.decision,
                                              reason=body.reason),
                             u.tenant_id, scopes=list(u.scopes))
        return res.model_dump(mode="json")

    @app.get("/v1/approvals")
    def approvals(tenant_id: str = "t_demo", status: str = "pending",
                  u: db.AppUser = Depends(user)):
        with sf() as s:
            rows = s.execute(select(db.Approval).where(
                db.Approval.tenant_id == u.tenant_id, db.Approval.status == status)
                .order_by(db.Approval.created_at.desc())).scalars().all()
            return [_approval_summary(s, ap) for ap in rows]

    @app.get("/v1/entities/{entity}")
    def entities(entity: str, field: str | None = None, value: str | None = None,
                 u: db.AppUser = Depends(user)):
        model = ENTITY_MODELS.get(entity)
        if model is None:
            raise HTTPException(404, f"unknown entity: {entity}")
        with sf() as s:
            stmt = select(model).where(model.tenant_id == u.tenant_id)
            if field and value is not None and hasattr(model, field):
                stmt = stmt.where(getattr(model, field) == value)
            rows = s.execute(stmt).scalars().all()
            out = []
            for r in rows:
                d = {c.name: getattr(r, c.name) for c in r.__table__.columns}
                out.append({k: (str(v) if hasattr(v, "isoformat") or v.__class__.__name__ == "Decimal" else v)
                            for k, v in d.items()})
            return out

    @app.get("/v1/audit/actions")
    def audit(trace_id: str | None = None, limit: int = 50, u: db.AppUser = Depends(user)):
        with sf() as s:
            stmt = select(db.AuditLog).where(db.AuditLog.tenant_id == u.tenant_id)
            if trace_id:
                stmt = stmt.where(db.AuditLog.trace_id == trace_id)
            rows = s.execute(stmt.order_by(db.AuditLog.created_at.desc()).limit(limit)).scalars().all()
            return [{"id": r.id, "action": r.action, "actor": f"{r.actor_type}:{r.actor_id}",
                     "status": r.status, "trace_id": r.trace_id,
                     "created_at": r.created_at.isoformat()} for r in rows]

    @app.get("/v1/agents/lineage/{trace_id}")
    def lineage(trace_id: str, u: db.AppUser = Depends(user)):
        with sf() as s:
            row = s.execute(select(db.AgentActionLog).where(
                db.AgentActionLog.tenant_id == u.tenant_id,
                db.AgentActionLog.trace_id == trace_id)).scalars().first()
            if row is None:
                raise HTTPException(404, "lineage not found")
            return {"utterance": row.utterance, "plan": row.plan, "steps": row.steps,
                    "evidence": row.evidence, "model_id": row.model_id,
                    "prompt_version": row.prompt_version, "confidence": row.confidence,
                    "escalated": row.escalated, "cost_tokens": row.cost_tokens}

    @app.get("/v1/invariants")
    def invariants(u: db.AppUser = Depends(user)):
        with sf() as s:
            violations = inv.check_all(s, u.tenant_id, persist=True)
            rows = s.execute(select(db.InvariantCheck).where(
                db.InvariantCheck.tenant_id == u.tenant_id)
                .order_by(db.InvariantCheck.checked_at.desc())).scalars().all()
            latest = {}
            for r in rows:
                latest.setdefault(r.name, r)
            checks = [{"name": n, "label": CHECK_LABEL.get(n, n),
                       "status": latest[n].status if n in latest else "ok",
                       "checked": (latest[n].detail or {}).get("checked", 0) if n in latest else 0}
                      for n in CHECK_LABEL]
        return {"status": "violation" if violations else "ok",
                "checks": checks, "violations": violations}

    # ---------- 进化中心 ----------
    @app.post("/v1/evolution/mine")
    def ev_mine(u: db.AppUser = Depends(user)):
        return {"new_cases": miner.mine(u.tenant_id)}

    @app.get("/v1/evolution/cases")
    def ev_cases(u: db.AppUser = Depends(user)):
        with sf() as s:
            rows = s.execute(select(db.EvolutionCase).where(
                db.EvolutionCase.tenant_id == u.tenant_id)
                .order_by(db.EvolutionCase.created_at.desc())).scalars().all()
            return [{"id": c.id, "source": c.source, "utterance": c.utterance,
                     "status": c.status, "created_at": c.created_at.isoformat()} for c in rows]

    @app.post("/v1/evolution/candidates")
    def ev_candidate(body: CandidateBody, u: db.AppUser = Depends(user)):
        if u.role not in ("boss", "finance"):
            raise HTTPException(403, "仅老板/财务可发起候选")
        return pipeline.propose(u.tenant_id, body.case_id, u.id)

    @app.get("/v1/evolution/prs")
    def ev_prs(u: db.AppUser = Depends(user)):
        with sf() as s:
            rows = s.execute(select(db.EvolutionPR).where(
                db.EvolutionPR.tenant_id == u.tenant_id)
                .order_by(db.EvolutionPR.created_at.desc())).scalars().all()
            return [{"id": p.id, "title": p.title, "diff": p.diff,
                     "eval_report": p.eval_report, "risk": p.risk, "status": p.status,
                     "reviewed_by": p.reviewed_by,
                     "created_at": p.created_at.isoformat()} for p in rows]

    @app.post("/v1/evolution/prs/{pr_id}/review")
    def ev_review(pr_id: str, body: ReviewBody, u: db.AppUser = Depends(boss)):
        return pipeline.review(pr_id, body.decision, u.id, u.tenant_id)

    @app.post("/v1/evolution/prs/{pr_id}/activate")
    def ev_activate(pr_id: str, u: db.AppUser = Depends(boss)):
        return pipeline.activate(pr_id, u.id, u.tenant_id)

    @app.post("/v1/evolution/prs/{pr_id}/rollback")
    def ev_rollback(pr_id: str, u: db.AppUser = Depends(boss)):
        return pipeline.rollback(pr_id, u.id, u.tenant_id)

    @app.get("/v1/evolution/state")
    def ev_state(u: db.AppUser = Depends(user)):
        a = active_asset(sf, u.tenant_id)
        return {"regex": active_regex(sf, u.tenant_id),
                "version": a.version if a else 0,
                "canary": bool(a and a.status == "canary")}

    # ---------- 扩展包 ----------
    @app.get("/v1/packages")
    def packages(u: db.AppUser = Depends(user)):
        avail = []
        ext_root = REPO_ROOT / "metadata" / "extensions"
        if ext_root.exists():
            for d in sorted(ext_root.iterdir()):
                mf = d / "manifest.yaml"
                if mf.exists():
                    m = yaml.safe_load(mf.read_text(encoding="utf-8"))
                    avail.append({"name": m["name"], "version": str(m.get("version", "1.0")),
                                  "description": m.get("description", ""),
                                  "publisher": m.get("publisher", "")})
        with sf() as s:
            installed = s.execute(select(db.PackageInstall).where(
                db.PackageInstall.tenant_id == u.tenant_id)).scalars().all()
            inst = [{"name": p.name, "version": p.version,
                     "installed_at": p.installed_at.isoformat()} for p in installed]
        return {"available": avail, "installed": inst}

    @app.post("/v1/packages/{name}/install")
    def package_install(name: str, u: db.AppUser = Depends(boss)):
        path = REPO_ROOT / "metadata" / "extensions" / name
        if not (path / "manifest.yaml").exists():
            raise HTTPException(404, "扩展包不存在")
        with sf() as s:
            dup = s.execute(select(db.PackageInstall).where(
                db.PackageInstall.tenant_id == u.tenant_id,
                db.PackageInstall.name == name)).scalars().first()
            if dup:
                return {"status": "already_installed"}
        engine.registry.load_overlay_dir(path)
        mf = yaml.safe_load((path / "manifest.yaml").read_text(encoding="utf-8"))
        with sf() as s:
            s.add(db.PackageInstall(tenant_id=u.tenant_id, name=name,
                                    version=str(mf.get("version", "1.0")),
                                    path=str(path), installed_by=u.id))
            s.commit()
        return {"status": "installed", "name": name,
                "new_actions": [a for a in engine.registry.actions if "priority" in a]}

    @app.get("/", response_class=HTMLResponse)
    def workbench():
        html = (Path(__file__).parent / "workbench.html").read_text(encoding="utf-8")
        return HTMLResponse(html)

    return app


app = create_app()
