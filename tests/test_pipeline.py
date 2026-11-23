from decimal import Decimal
from sqlalchemy import select, func
from heng_contracts.models import ActionRequest
from heng_kernel import db
from conftest import actor

ORDER_ARGS = {"customer_id": "CUST-0021",
              "lines": [{"sku_id": "SKU-A100", "qty": 10, "price": 565, "tax_included": True}]}


def _create(env, **kw):
    engine, sf, inv, tenant = env
    return engine.execute(ActionRequest(action="sales.order.create", args=ORDER_ARGS,
                                        actor=actor(tenant), **kw))


def test_create_ok_and_tax(env):
    r = _create(env)
    assert r.status == "ok", r.violations
    assert r.data["total_amount"] == "5650.00"
    assert r.data["total_tax"] == "650.00"
    assert r.rollback_token == "sales.order.cancel"


def test_schema_rejection(env):
    engine, sf, inv, tenant = env
    args = {**ORDER_ARGS, "lines": [{"sku_id": "SKU-A100", "qty": 0, "price": 565}]}
    r = engine.execute(ActionRequest(action="sales.order.create", args=args, actor=actor(tenant)))
    assert r.status == "rejected" and r.violations[0].code == "schema"


def test_credit_precondition(env):
    engine, sf, inv, tenant = env
    args = {"customer_id": "CUST-0050",
            "lines": [{"sku_id": "SKU-A100", "qty": 200, "price": 565}]}
    r = engine.execute(ActionRequest(action="sales.order.create", args=args, actor=actor(tenant)))
    assert r.status == "rejected" and r.violations[0].code == "precondition"


def test_idempotent_replay(env):
    r1 = _create(env, client_request_id="idem-x")
    r2 = _create(env, client_request_id="idem-x")
    assert r1.data["order_id"] == r2.data["order_id"]
    engine, sf, inv, tenant = env
    with sf() as s:
        n = s.execute(select(func.count()).select_from(db.SalesOrder).where(
            db.SalesOrder.tenant_id == tenant, db.SalesOrder.no.like("SO-2026-%"))).scalar_one()
    assert n == 3  # fixture 2 + 本次 1（重放不新增）


def test_dry_run_no_persist(env):
    engine, sf, inv, tenant = env
    r = engine.execute(ActionRequest(action="sales.order.create", args=ORDER_ARGS,
                                     actor=actor(tenant), dry_run=True))
    assert r.status == "ok" and r.effects_preview
    with sf() as s:
        n = s.execute(select(func.count()).select_from(db.SalesOrder).where(
            db.SalesOrder.tenant_id == tenant)).scalar_one()
    assert n == 2  # 仅 fixture


def test_agent_l1_parks(env):
    engine, sf, inv, tenant = env
    r = engine.execute(ActionRequest(action="sales.order.create", args=ORDER_ARGS,
                                     actor=actor(tenant, "agent", "order_clerk",
                                                 ["sales.order.write"])))
    assert r.status == "pending_approval" and r.approval_id
    res = engine.approve(r.approval_id,
                         __import__("heng_contracts.models", fromlist=["ApprovalDecision"]).ApprovalDecision(
                             approver_id="u_boss", decision="approve"), tenant)
    assert res.status == "ok"


def test_l2_automation(env):
    from heng_actions.compose import build_engine
    from heng_eval.fixture import build_fixture
    engine, sf, inv = build_engine("sqlite://", automation={
        "t_l2": {"sales.order.create": {"level": "L2"}}})
    build_fixture(engine, sf, "t_l2")
    r = engine.execute(ActionRequest(action="sales.order.create", args=ORDER_ARGS,
                                     actor=actor("t_l2", "agent", "order_clerk", ["sales.order.write"])))
    assert r.status == "ok"  # L2 条件满足：信用检查通过


def test_scope_forbidden(env):
    engine, sf, inv, tenant = env
    r = engine.execute(ActionRequest(action="payment.execute",
                                     args={"order_id": "X", "amount": 1},
                                     actor=actor(tenant, "agent", "rogue", ["sales.order.write"])))
    assert r.status == "rejected" and r.violations[0].code == "forbidden"


def test_audit_and_outbox(env):
    engine, sf, inv, tenant = env
    r = _create(env)
    assert r.audit_id
    with sf() as s:
        audits = s.execute(select(db.AuditLog).where(db.AuditLog.tenant_id == tenant)).scalars().all()
        outbox = s.execute(select(db.Outbox).where(db.Outbox.tenant_id == tenant)).scalars().all()
    assert any(a.action == "sales.order.create" and a.status == "ok" for a in audits)
    assert all(o.published_at is not None for o in outbox)
    assert any("sales.order.created" in e["type"] for e in engine.bus.published)


def test_unknown_action(env):
    engine, sf, inv, tenant = env
    r = engine.execute(ActionRequest(action="no.such.action", args={}, actor=actor(tenant)))
    assert r.status == "rejected" and r.violations[0].code == "unknown_action"
