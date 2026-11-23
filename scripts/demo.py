# SPDX-License-Identifier: LGPL-3.0-or-later
"""端到端演示：python scripts/demo.py
一句话开单 → 行动卡审批 → 确认出库记账 → 开票 → 四眼收款 → 指标问数 → 不变量断言。"""
from heng_contracts.models import ApprovalDecision
from heng_actions.compose import build_engine
from heng_eval.fixture import build_fixture
from heng_agent.orchestrator import Orchestrator
from heng_agent.providers import RuleBasedProvider
from heng_llm.gateway import LLMGateway
from heng_contracts.models import ActionRequest, ActorRef


def main():
    engine, sf, inv = build_engine("sqlite://")
    tenant = "t_demo"
    build_fixture(engine, sf, tenant)
    orch = Orchestrator(engine, LLMGateway([RuleBasedProvider()], offline=True))
    human = ActorRef(type="human", id="u_boss", tenant_id=tenant, scopes=["*"])

    print("== 1. Copilot 一句话开单（离线降级链：规则模板兜底） ==")
    res = orch.run(tenant, "u_boss", "给华东李总发200台A100，按上次的价格，含税")
    print("AI:", res["reply"])
    card = res["cards"][0]
    print("行动卡证据:", card["evidence"])

    print("\n== 2. 人工确认行动卡（L1） ==")
    r = engine.approve(card["approval_id"], ApprovalDecision(approver_id="u_boss", decision="approve"), tenant)
    oid = r.data["order_id"]
    print("落库:", r.data["no"], "价税合计", r.data["total_amount"], "税额", r.data["total_tax"])

    print("\n== 3. 确认订单：出库 + 自动记账 ==")
    r = engine.execute(ActionRequest(action="sales.order.confirm", args={"order_id": oid}, actor=human))
    print("凭证:", r.data["journal_no"], "出库:", r.data["moves"])

    print("\n== 4. 开数电票（沙箱通道，high 风险单人审批） ==")
    r = engine.execute(ActionRequest(action="tax.invoice.issue", args={"order_id": oid}, actor=human))
    r = engine.approve(r.approval_id, ApprovalDecision(approver_id="u_fin", decision="approve"), tenant)
    print("发票:", r.data["no"], "税额", r.data["total_tax"])

    print("\n== 5. 收款（critical：四眼审批） ==")
    r = engine.execute(ActionRequest(action="payment.execute", args={"order_id": oid, "amount": 113000}, actor=human))
    r1 = engine.approve(r.approval_id, ApprovalDecision(approver_id="u_a", decision="approve"), tenant)
    print("第一审批后:", r1.status)
    r2 = engine.approve(r.approval_id, ApprovalDecision(approver_id="u_b", decision="approve"), tenant)
    print("第二审批后:", r2.status, "收款", r2.data["amount"])

    print("\n== 6. 指标问数（语义编译，口径唯一） ==")
    res = orch.run(tenant, "u_boss", "上月华东区收入多少")
    print("AI:", res["reply"])

    print("\n== 7. 不变量断言（账没坏是被证明的） ==")
    with sf() as s:
        violations = inv.check_all(s, tenant)
    print("结果:", "全部通过" if not violations else violations)

    print("\n== 8. 决策 Lineage 回放入口 ==")
    print("trace_id:", res["trace_id"], "→ GET /v1/agents/lineage/{trace_id}")


if __name__ == "__main__":
    main()
