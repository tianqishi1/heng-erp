from fastapi.testclient import TestClient
from heng_api.main import create_app


def test_approval_center_and_double_click_protection(env):
    engine, sf, inv, tenant = env
    app = create_app(engine, sf, inv)
    with TestClient(app) as client:
        j = client.post("/v1/copilot/messages", json={
            "utterance": "给华东李总发200台A100，按上次的价格，含税", "tenant_id": tenant}).json()
        ap_id = j["cards"][0]["approval_id"]
        # 审批中心：业务化摘要（客户姓名，而非技术 ID）
        aps = client.get("/v1/approvals", params={"tenant_id": tenant}).json()
        assert len(aps) == 1
        assert aps[0]["title"] == "创建销售订单(草稿)"
        assert "李振华" in aps[0]["summary"] and "113,000.00" in aps[0]["summary"]
        # 第一次批准 → 执行
        r1 = client.post(f"/v1/approvals/{ap_id}/approve",
                         json={"approver_id": "u_boss", "tenant_id": tenant}).json()
        assert r1["status"] == "ok"
        # 第二次点击 → 后端拒绝重复执行（防重复落库），审批中心不再列出
        r2 = client.post(f"/v1/approvals/{ap_id}/approve",
                         json={"approver_id": "u_boss", "tenant_id": tenant}).json()
        assert r2["status"] == "rejected" and r2["violations"][0]["code"] == "bad_state"
        assert client.get("/v1/approvals", params={"tenant_id": tenant}).json() == []
        # 单据筛选（订单明细抽屉用）
        lines = client.get("/v1/entities/sales.order.line",
                           params={"tenant_id": tenant, "field": "order_id",
                                   "value": r1["data"]["order_id"]}).json()
        assert len(lines) == 1 and lines[0]["tax_amount"] == "13000.00"
        # 账务体检：业务化检查项
        h = client.get("/v1/invariants", params={"tenant_id": tenant}).json()
        assert h["status"] == "ok" and len(h["checks"]) == 4
        assert any("借贷平衡" in c["label"] for c in h["checks"])
        # 工作台页面为业务界面（不含面向用户的 JSON 调试输出）
        page = client.get("/").text
        assert "审批中心" in page and "账务体检" in page
