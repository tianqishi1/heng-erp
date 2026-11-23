"""失败案例挖掘（§13.2 第一步）：从决策留痕/驳回记录自动发现优化点。只读生产数据，脱敏在案例层。"""
from __future__ import annotations
from sqlalchemy import select
from heng_kernel import db


class Miner:
    def __init__(self, sf):
        self.sf = sf

    def _exists(self, s, tenant_id, utterance):
        row = s.execute(select(db.EvolutionCase).where(
            db.EvolutionCase.tenant_id == tenant_id,
            db.EvolutionCase.utterance == utterance,
            db.EvolutionCase.status.in_(["new", "candidate"]))).scalars().first()
        return row is not None

    def record_escalation(self, tenant_id: str, utterance: str, reply: str) -> bool:
        if "暂不支持" not in reply:
            return False
        with self.sf() as s:
            if self._exists(s, tenant_id, utterance):
                return False
            s.add(db.EvolutionCase(tenant_id=tenant_id, source="agent_escalation",
                                   utterance=utterance, detail={"reply": reply}))
            s.commit()
            return True

    def mine(self, tenant_id: str) -> int:
        n = 0
        with self.sf() as s:
            logs = s.execute(select(db.AgentActionLog).where(
                db.AgentActionLog.tenant_id == tenant_id,
                db.AgentActionLog.escalated == True)).scalars().all()  # noqa: E712
            for log in logs:
                clarify = (log.plan or {}).get("clarify") or ""
                if "暂不支持" not in clarify:
                    continue  # 消歧反问等升级是正确行为，不属于优化点
                if not self._exists(s, tenant_id, log.utterance):
                    s.add(db.EvolutionCase(tenant_id=tenant_id, source="agent_escalation",
                                           utterance=log.utterance,
                                           detail={"trace_id": log.trace_id}))
                    n += 1
            rejects = s.execute(select(db.AuditLog).where(
                db.AuditLog.tenant_id == tenant_id, db.AuditLog.action == "approval.decide",
                db.AuditLog.status == "rejected")).scalars().all()
            for r in rejects:
                if (r.args or {}).get("decision") == "reject":
                    key = f"人工驳回:{r.args.get('approval_id')}"
                    if not self._exists(s, tenant_id, key):
                        s.add(db.EvolutionCase(tenant_id=tenant_id, source="card_reject",
                                               utterance=key, detail=r.args or {}))
                        n += 1
            s.commit()
        return n
