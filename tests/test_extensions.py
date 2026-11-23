import pytest
import yaml
from fastapi.testclient import TestClient
from heng_contracts.models import ActionRequest
from heng_kernel import db
from heng_actions.compose import REPO_ROOT
from heng_api.main import create_app
from conftest import actor
from test_api import login

PKG = REPO_ROOT / "metadata" / "extensions" / "trade_priority"
ARGS = {"customer_id": "CUST-0021",
        "lines": [{"sku_id": "SKU-A100", "qty": 10, "price": 565, "tax_included": True}]}


def test_overlay_and_generic_handler(env):
    engine, sf, inv, tenant = env
    engine.registry.load_overlay_dir(PKG)
    assert "sales.order.set_priority" in engine.registry.actions
    r = engine.execute(ActionRequest(action="sales.order.create", args=ARGS, actor=actor(tenant)))
    oid = r.data["order_id"]
    r2 = engine.execute(ActionRequest(action="sales.order.set_priority",
                                      args={"id": oid, "value": "urgent"}, actor=actor(tenant)))
    assert r2.status == "ok", r2.violations
    with sf() as s:
        from sqlalchemy import select
        o = s.execute(select(db.SalesOrder).where(db.SalesOrder.tenant_id == tenant,
                                                  db.SalesOrder.id == oid)).scalars().one()
        assert o.ext["priority"] == "urgent"
    r3 = engine.execute(ActionRequest(action="sales.order.set_priority",
                                      args={"id": oid, "value": "asap"}, actor=actor(tenant)))
    assert r3.status == "rejected"  # 枚举白名单


def test_overlay_cannot_override_official(env, tmp_path):
    engine, sf, inv, tenant = env
    d = tmp_path / "entities"
    d.mkdir()
    (d / "bad.yaml").write_text(yaml.safe_dump({
        "entity": "sales.order", "mode": "extend",
        "fields": {"total_amount": {"type": "string"}}}), encoding="utf-8")
    with pytest.raises(ValueError):
        engine.registry.load_overlay_dir(tmp_path)


def test_package_install_api(env):
    engine, sf, inv, tenant = env
    app = create_app(engine, sf, inv)
    with TestClient(app) as client:
        h = login(client)
        pk = client.get("/v1/packages", headers=h).json()
        assert any(p["name"] == "trade_priority" for p in pk["available"])
        r = client.post("/v1/packages/trade_priority/install", headers=h).json()
        assert r["status"] == "installed"
        j = client.post("/v1/copilot/messages", headers=h, json={
            "utterance": "给华东李总发200台A100，按上次的价格，含税"}).json()
        ap = client.post(f"/v1/approvals/{j['cards'][0]['approval_id']}/approve",
                         headers=h, json={"decision": "approve"}).json()
        oid = ap["data"]["order_id"]
        sp = client.post("/v1/actions/sales.order.set_priority/execute", headers=h,
                         json={"args": {"id": oid, "value": "urgent"}}).json()
        assert sp["status"] == "ok"
        orders = client.get("/v1/entities/sales.order", headers=h).json()
        assert any(o["id"] == oid and o["ext"]["priority"] == "urgent" for o in orders)
