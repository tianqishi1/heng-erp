from fastapi.testclient import TestClient
from heng_api.main import create_app


def test_api_end_to_end(env):
    engine, sf, inv, tenant = env
    app = create_app(engine, sf, inv)
    with TestClient(app) as client:
        assert client.get("/healthz").json()["status"] == "ok"
        r = client.post("/v1/copilot/messages", json={
            "utterance": "给华东李总发200台A100，按上次的价格，含税", "tenant_id": tenant})
        j = r.json()
        assert len(j["cards"]) == 1
        ap = client.post(f"/v1/approvals/{j['cards'][0]['approval_id']}/approve",
                         json={"approver_id": "u_boss", "tenant_id": tenant}).json()
        assert ap["status"] == "ok"
        orders = client.get("/v1/entities/sales.order", params={"tenant_id": tenant}).json()
        assert any(o["total_tax"] == "13000.00" for o in orders)
        assert client.get("/v1/invariants", params={"tenant_id": tenant}).json()["status"] == "ok"
        lin = client.get(f"/v1/agents/lineage/{j['trace_id']}", params={"tenant_id": tenant}).json()
        assert lin["model_id"] == "rule-template-v1"
        dry = client.post("/v1/actions/sales.order.create/dry-run", json={
            "tenant_id": tenant, "args": {"customer_id": "CUST-0021",
                                          "lines": [{"sku_id": "SKU-A100", "qty": 1, "price": 565}]}}).json()
        assert dry["status"] == "ok" and dry["effects_preview"]
        assert client.get("/").status_code == 200
