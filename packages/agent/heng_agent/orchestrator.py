"""确定性编排器（§6.1）：状态机驱动 plan-execute-verify。
LLM 只在 PARSE/PLAN 被调用；其余全部确定性代码。任何一步可回放（lineage 落库）。"""
from __future__ import annotations
from sqlalchemy import select
from heng_contracts.models import (ActionRequest, ActorRef, AgentPlan, EvidenceRef, new_id)
from heng_kernel import db
from heng_kernel.expr import check
from heng_llm.gateway import LLMGateway
from .policy import AGENTS, NEVER_AGENT_TOOLS


class Orchestrator:
    def __init__(self, engine, gateway: LLMGateway, agent_id: str = "order_clerk"):
        self.engine = engine
        self.gateway = gateway
        self.agent_id = agent_id
        self.policy = AGENTS[agent_id]

    def run(self, tenant_id: str, user_id: str, utterance: str, sensitive: bool = False) -> dict:
        eng = self.engine
        trace_id = new_id("trace")
        session = eng.sf()
        try:
            # INTAKE：上下文快照（主数据 + 价格史 + 工具目录）
            partners = [{"id": p.id, "name": p.name, "region": p.region} for p in
                        session.execute(select(db.Partner).where(db.Partner.tenant_id == tenant_id)).scalars()]
            skus = [{"id": s.id, "name": s.name, "ref_price": str(s.ref_price)} for s in
                    session.execute(select(db.Sku).where(db.Sku.tenant_id == tenant_id)).scalars()]
            price_lookup = {f"{h.partner_id}|{h.sku_id}": str(h.price) for h in
                            session.execute(select(db.PriceHistory).where(db.PriceHistory.tenant_id == tenant_id)).scalars()}
            context = {"clock": eng.clock.isoformat(), "partners": partners, "skus": skus,
                       "price_lookup": price_lookup,
                       "tools": eng.registry.tool_catalog(self.policy["tools"])}

            # PARSE/PLAN（唯一 LLM 调用点）
            out, meta = self.gateway.plan(utterance, context, sensitive=sensitive)
            plan = AgentPlan(**out["plan"])
            evidence = [EvidenceRef(**e) for e in out.get("evidence", [])]

            cards: list[dict] = []
            steps_log: list[dict] = []
            reply_parts: list[str] = []
            escalated = False

            # VALIDATE
            threshold = self.policy["confidence_threshold"]
            if plan.clarify or plan.confidence < threshold:
                escalated = True
                reply_parts.append(plan.clarify or "置信度不足，已转人工。")
            elif len(plan.steps) > plan.budget.max_steps:
                escalated = True
                reply_parts.append("计划超出步数预算，已转人工。")
            elif meta["tokens"] > plan.budget.max_tokens:
                escalated = True
                reply_parts.append("超出 token 预算，已转人工。")
            else:
                actor = ActorRef(type="agent", id=self.agent_id, tenant_id=tenant_id,
                                 scopes=list(self.policy["scopes"]))
                # STEP_LOOP
                for i, step in enumerate(plan.steps):
                    if step.tool in NEVER_AGENT_TOOLS or step.tool not in self.policy["tools"]:
                        escalated = True
                        reply_parts.append(f"已拦截越权工具调用 {step.tool}（安全策略）。")
                        steps_log.append({"tool": step.tool, "status": "blocked_by_policy"})
                        break
                    base = dict(action=step.tool, args=step.args, actor=actor,
                                on_behalf_of=user_id, trace_id=trace_id, evidence=evidence,
                                client_request_id=f"{trace_id}-s{i}")
                    dry = eng.execute(ActionRequest(**base, dry_run=True), session)
                    if dry.status != "ok":
                        escalated = True
                        reply_parts.append("校验未通过：" + "；".join(v.message for v in dry.violations))
                        steps_log.append({"tool": step.tool, "status": "dry_run_failed",
                                          "violations": [v.message for v in dry.violations]})
                        break
                    expect_ok = True
                    for expr in step.expect:
                        ok, err = check(expr, {"data": dry.data, "result": dry.data})
                        if not ok:
                            expect_ok = False
                            escalated = True
                            reply_parts.append(f"后置断言失败：{expr}（{err}）")
                            break
                    if not expect_ok:
                        steps_log.append({"tool": step.tool, "status": "expect_failed"})
                        break
                    spec = eng.registry.get_action(step.tool)
                    res = eng.execute(ActionRequest(**base), session)
                    steps_log.append({"tool": step.tool, "status": res.status,
                                      "approval_id": res.approval_id,
                                      "preview": dry.effects_preview})
                    if res.status == "pending_approval":
                        cards.append({"action": step.tool, "args": step.args,
                                      "risk": spec.risk_level, "approval_id": res.approval_id,
                                      "required_approvals": (res.data or {}).get("required_approvals", 1),
                                      "effects_preview": dry.effects_preview,
                                      "data_preview": dry.data,
                                      "evidence": [e.model_dump() for e in evidence]})
                        reply_parts.append(self._render_card(step.tool, dry.data or {}))
                    elif res.status == "ok":
                        reply_parts.append(self._render(step.tool, res.data or {}))
                    else:
                        escalated = True
                        reply_parts.append("执行失败：" + "；".join(v.message for v in res.violations))
                        break

            reply = " ".join(reply_parts) if reply_parts else "已转人工处理。"
            # CLOSE：决策 Lineage 落库（可回放）
            session.add(db.AgentActionLog(
                tenant_id=tenant_id, agent_id=self.agent_id, utterance=utterance,
                plan=plan.model_dump(mode="json"), evidence=[e.model_dump() for e in evidence],
                prompt_version=self.policy["prompt_version"], model_id=meta["model_id"],
                steps=steps_log, confidence=plan.confidence, escalated=escalated,
                cost_tokens=meta["tokens"], trace_id=trace_id))
            session.commit()
            return {"reply": reply, "cards": cards, "escalated": escalated,
                    "trace_id": trace_id, "plan": plan.model_dump(mode="json"),
                    "steps": steps_log, "model": meta, "confidence": plan.confidence}
        finally:
            session.close()

    @staticmethod
    def _render_card(tool: str, data: dict) -> str:
        if tool == "sales.order.create":
            return (f"已生成订单草稿卡 {data.get('no', '')}：价税合计 {data.get('total_amount', '?')} 元"
                    f"（税额 {data.get('total_tax', '?')} 元，{data.get('ruleset', '')}）。请在行动卡确认后落库。")
        return f"已生成 {tool} 行动卡，待确认。"

    @staticmethod
    def _render(tool: str, data: dict) -> str:
        if tool == "metrics.query":
            f = data.get("filters", {})
            scope = " ".join(x for x in [f.get("period"), f.get("region")] if x)
            drill = "、".join(data.get("drill", [])[:5])
            return (f"{data.get('label', '')}（{scope or '全部'}）：{data.get('value', '?')} 元。"
                    f"口径：{data.get('caliber', '')}。下钻单据：{drill or '无'}。")
        if tool == "catalog.search":
            cands = data.get("candidates", [])
            return "检索结果：" + "、".join(c["id"] for c in cands[:5]) if cands else "未找到匹配记录。"
        return f"{tool} 执行完成。"
