# SPDX-License-Identifier: LGPL-3.0-or-later
"""进化流水线（§13）：候选 → 沙箱全量评测门禁 → 进化PR(人审) → 本租户灰度 → 全量/回滚。
护栏：每周限额、同资产冷却期、黄金集零退化、新案例必须通过。"""
from __future__ import annotations
import datetime as dt
from sqlalchemy import select, func
from heng_kernel import db
from heng_actions.compose import REPO_ROOT
from heng_agent.providers import RuleBasedProvider
from heng_eval.runner import run_suite, run_scenario
from .assets import ASSET_NAME, DEFAULT_ORDER_REGEX, active_asset, latest_version
from .candidates import generate_candidates

WEEKLY_LIMIT = 10
COOLDOWN_HOURS = 72


class EvolutionPipeline:
    def __init__(self, sf):
        self.sf = sf

    def _guard(self, s, tenant_id):
        week_ago = dt.datetime.utcnow() - dt.timedelta(days=7)
        n = s.execute(select(func.count()).select_from(db.EvolutionPR).where(
            db.EvolutionPR.created_at >= week_ago)).scalar_one()
        if n >= WEEKLY_LIMIT:
            return f"本周进化 PR 已达上限（{WEEKLY_LIMIT}），请下周再试"
        last = s.execute(select(db.EvolutionPR).where(
            db.EvolutionPR.asset_type == "pattern",
            db.EvolutionPR.status.in_(["canary", "active"]))
            .order_by(db.EvolutionPR.created_at.desc())).scalars().first()
        if last and last.activated_at and \
                dt.datetime.utcnow() - last.activated_at < dt.timedelta(hours=COOLDOWN_HOURS):
            return f"同资产冷却期（{COOLDOWN_HOURS}h）未过，上次变更 {last.activated_at:%m-%d %H:%M}"
        return None

    def propose(self, tenant_id: str, case_id: str, actor_id: str) -> dict:
        with self.sf() as s:
            case = s.get(db.EvolutionCase, case_id)
            if case is None or case.tenant_id != tenant_id:
                return {"error": "案例不存在"}
            if case.source != "agent_escalation":
                return {"error": "M1 仅支持话术模式类案例的自动候选；驳回类案例请人工分析"}
            guard = self._guard(s, tenant_id)
            if guard:
                return {"error": guard}
            current = active_asset(s, tenant_id)
            current_regex = (current.spec or {}).get("regex", DEFAULT_ORDER_REGEX) if current else DEFAULT_ORDER_REGEX
            utterance = case.utterance
        new_case = {"id": f"evolution-{case_id}", "given": {"fixture": "demo_trading_cn"},
                    "when": {"channel": "copilot", "utterance": utterance},
                    "then": {"deterministic": ["action_called: sales.order.create",
                                               "escalated == false"]}}
        attempts = []
        for cand in generate_candidates(utterance, current_regex):
            provider = RuleBasedProvider(order_regex=cand)
            golden = run_suite(REPO_ROOT / "evals", provider=provider)
            fresh = run_scenario(new_case, provider=provider)
            attempts.append({"regex": cand, "golden_pass": golden["failed"] == 0,
                             "new_case_pass": fresh["passed"]})
            if golden["failed"] == 0 and fresh["passed"]:
                with self.sf() as s:
                    pr = db.EvolutionPR(
                        tenant_id=tenant_id, asset_type="pattern",
                        title=f"开单话术支持新说法：{utterance[:30]}",
                        diff={"before": current_regex, "after": cand,
                              "before_asset_id": current.id if current else None},
                        case_ids=[case_id],
                        eval_report={"golden": {"total": golden["total"], "passed": golden["passed"]},
                                     "new_case": {"id": new_case["id"], "passed": True},
                                     "attempts": attempts},
                        risk="low", status="pending_review")
                    s.add(pr)
                    case = s.get(db.EvolutionCase, case_id)
                    case.status = "candidate"
                    s.commit()
                    return {"pr_id": pr.id, "title": pr.title, "eval_report": pr.eval_report}
        return {"error": "没有候选通过评测门禁（黄金集零退化+新案例通过）", "attempts": attempts}

    def review(self, pr_id: str, decision: str, actor_id: str, tenant_id: str) -> dict:
        with self.sf() as s:
            pr = s.get(db.EvolutionPR, pr_id)
            if pr is None or pr.tenant_id != tenant_id or pr.status != "pending_review":
                return {"error": "PR 不存在或状态不允许审批"}
            if decision == "reject":
                pr.status = "rejected"
                pr.reviewed_by = actor_id
                for cid in pr.case_ids:
                    c = s.get(db.EvolutionCase, cid)
                    if c:
                        c.status = "new"
                s.commit()
                return {"status": "rejected"}
            asset = db.PatternAsset(name=ASSET_NAME, version=latest_version(self.sf) + 1,
                                    spec={"regex": pr.diff["after"]}, status="canary",
                                    canary_tenants=[tenant_id], pr_id=pr.id)
            s.add(asset)
            s.flush()  # 先落库取真实 id，再写入 PR.diff
            pr.status = "canary"
            pr.reviewed_by = actor_id
            pr.diff = {**pr.diff, "after_asset_id": asset.id}
            s.commit()
            return {"status": "canary", "asset_version": asset.version}

    def activate(self, pr_id: str, actor_id: str, tenant_id: str) -> dict:
        with self.sf() as s:
            pr = s.get(db.EvolutionPR, pr_id)
            if pr is None or pr.status != "canary":
                return {"error": "仅灰度中的 PR 可全量"}
            new = s.get(db.PatternAsset, pr.diff.get("after_asset_id"))
            old = s.get(db.PatternAsset, pr.diff.get("before_asset_id")) if pr.diff.get("before_asset_id") else None
            if new is None:
                return {"error": "资产缺失"}
            new.status = "active"
            new.canary_tenants = []
            if old:
                old.status = "retired"
            pr.status = "active"
            pr.activated_at = dt.datetime.utcnow()
            for cid in pr.case_ids:
                c = s.get(db.EvolutionCase, cid)
                if c:
                    c.status = "adopted"
            s.commit()
            return {"status": "active"}

    def rollback(self, pr_id: str, actor_id: str, tenant_id: str) -> dict:
        with self.sf() as s:
            pr = s.get(db.EvolutionPR, pr_id)
            if pr is None or pr.status not in ("canary", "active"):
                return {"error": "仅灰度/已全量的 PR 可回滚"}
            new = s.get(db.PatternAsset, pr.diff.get("after_asset_id"))
            if new:
                new.status = "retired"
            old_id = pr.diff.get("before_asset_id")
            if old_id:
                old = s.get(db.PatternAsset, old_id)
                if old:
                    old.status = "active"
            pr.status = "rolled_back"
            pr.activated_at = dt.datetime.utcnow()
            s.commit()
            return {"status": "rolled_back"}
