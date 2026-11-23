# SPDX-License-Identifier: LGPL-3.0-or-later
from decimal import Decimal
from sqlalchemy import select
from heng_contracts.models import ActionRequest
from heng_kernel import db
from conftest import actor

ARGS = {"customer_id": "CUST-0021",
        "lines": [{"sku_id": "SKU-A100", "qty": 10, "price": 565, "tax_included": True}]}


def _confirmed_order(env):
    engine, sf, inv, tenant = env
    r1 = engine.execute(ActionRequest(action="sales.order.create", args=ARGS, actor=actor(tenant)))
    r2 = engine.execute(ActionRequest(action="sales.order.confirm",
                                      args={"order_id": r1.data["order_id"]}, actor=actor(tenant)))
    assert r2.status == "ok", r2.violations
    return r1.data["order_id"]


def test_all_invariants_ok(env):
    engine, sf, inv, tenant = env
    _confirmed_order(env)
    with sf() as s:
        assert inv.check_all(s, tenant, persist=False) == []


def test_stock_tamper_detected(env):
    engine, sf, inv, tenant = env
    _confirmed_order(env)
    with sf() as s:
        q = s.execute(select(db.StockQuant).where(db.StockQuant.tenant_id == tenant,
                                                  db.StockQuant.sku_id == "SKU-A100")).scalars().first()
        q.qty = q.qty + Decimal("1")  # 人为篡改
        s.commit()
    with sf() as s:
        violations = inv.check_all(s, tenant, persist=False)
    assert any(v["check"] == "stock_consistency" for v in violations)


def test_trial_balance_tamper_detected(env):
    engine, sf, inv, tenant = env
    _confirmed_order(env)
    with sf() as s:
        jl = s.execute(select(db.JournalLine).where(db.JournalLine.tenant_id == tenant)).scalars().first()
        jl.debit = jl.debit + Decimal("0.01")
        s.commit()
    with sf() as s:
        violations = inv.check_all(s, tenant, persist=False)
    assert any(v["check"] == "trial_balance" for v in violations)


def test_cancel_restores_books(env):
    engine, sf, inv, tenant = env
    oid = _confirmed_order(env)
    r = engine.execute(ActionRequest(action="sales.order.cancel", args={"order_id": oid},
                                     actor=actor(tenant)))
    assert r.status == "ok" and r.data["reversed_journal_id"]
    with sf() as s:
        assert inv.check_all(s, tenant, persist=False) == []
        q = s.execute(select(db.StockQuant).where(db.StockQuant.tenant_id == tenant,
                                                  db.StockQuant.sku_id == "SKU-A100")).scalars().first()
    assert q.qty == Decimal("1000") - Decimal("100") - Decimal("50")  # fixture 出库后回到原值
