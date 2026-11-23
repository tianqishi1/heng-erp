from fastapi.testclient import TestClient
from heng_api.main import create_app
from test_api import login


def test_approval_center_and_double_click_protection(env):
    engine, sf, inv, tenant = env
    app = create_app(engine, sf, inv)
    with TestClient(app) as client:
        h = login(client)
        j = client.post("/v1/copilot/messages", headers=h, json={
            "utterance": "给华东李总发200台A100，按上次的价格，含税"}).json()
        ap_id = j["cards"][0]["approval_id"]
        aps = client.get("/v1/approvals", headers=h).json()
        assert len(aps) == 1
        assert aps[0]["title"] == "创建销售订单(草稿)"
        assert "李振华" in aps[0]["summary"] and "113,000.00" in aps[0]["summary"]
        r1 = client.post(f"/v1/approvals/{ap_id}/approve", headers=h, json={"decision": "approve"}).json()
        assert r1["status"] == "ok"
        r2 = client.post(f"/v1/approvals/{ap_id}/approve", headers=h, json={"decision": "approve"}).json()
        assert r2["status"] == "rejected" and r2["violations"][0]["code"] == "bad_state"
        assert client.get("/v1/approvals", headers=h).json() == []
        lines = client.get("/v1/entities/sales.order.line", headers=h,
                           params={"field": "order_id", "value": r1["data"]["order_id"]}).json()
        assert len(lines) == 1 and lines[0]["tax_amount"] == "13000.00"
        hh = client.get("/v1/invariants", headers=h).json()
        assert hh["status"] == "ok" and len(hh["checks"]) == 4
        page = client.get("/").text
        assert "审批中心" in page and "进化中心" in page


def test_four_eyes_with_real_identities(env):
    engine, sf, inv, tenant = env
    app = create_app(engine, sf, inv)
    with TestClient(app) as client:
        h = login(client)
        j = client.post("/v1/copilot/messages", headers=h, json={
            "utterance": "给华东李总发200台A100，按上次的价格，含税"}).json()
        r = client.post(f"/v1/approvals/{j['cards'][0]['approval_id']}/approve",
                        headers=h, json={"decision": "approve"}).json()
        oid = r["data"]["order_id"]
        client.post("/v1/actions/sales.order.confirm/execute", headers=h, json={"args": {"order_id": oid}})
        p = client.post("/v1/actions/payment.execute/execute", headers=h,
                        json={"args": {"order_id": oid, "amount": 113000}}).json()
        pid = p["approval_id"]
        ha = login(client, "au_a", "approve123")
        hb = login(client, "au_b", "approve123")
        a1 = client.post(f"/v1/approvals/{pid}/approve", headers=ha, json={"decision": "approve"}).json()
        a2 = client.post(f"/v1/approvals/{pid}/approve", headers=ha, json={"decision": "approve"}).json()
        a3 = client.post(f"/v1/approvals/{pid}/approve", headers=hb, json={"decision": "approve"}).json()
        assert a1["status"] == "pending_approval"
        assert a2["violations"][0]["code"] == "four_eyes"
        assert a3["status"] == "ok"
