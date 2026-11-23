"""沙箱租户工厂（§10.2）：fixture 模板 → 一致假账套（含消歧陷阱与价格史）。"""
from __future__ import annotations
import datetime as dt
from decimal import Decimal
from heng_contracts.models import ActorRef, ActionRequest
from heng_kernel import db
from heng_actions.compose import ensure_tenant

FIXTURES = ("demo_trading_cn",)


def build_fixture(engine, sf, tenant_id: str, name: str = "demo_trading_cn"):
    if name not in FIXTURES:
        raise KeyError(f"unknown fixture: {name}")
    ensure_tenant(sf, tenant_id, name)
    with sf() as s:
        s.add_all([
            db.Partner(id="CUST-0021", tenant_id=tenant_id, name="李振华", region="华东",
                       credit_limit=Decimal("500000")),
            db.Partner(id="CUST-0034", tenant_id=tenant_id, name="李国强", region="华南",
                       credit_limit=Decimal("500000")),
            db.Partner(id="CUST-0050", tenant_id=tenant_id, name="王建国", region="华东",
                       credit_limit=Decimal("100000")),
            db.Sku(id="SKU-A100", tenant_id=tenant_id, name="工业传感器 A100",
                   tax_category="standard", ref_price=Decimal("565")),
            db.Sku(id="SKU-B200", tenant_id=tenant_id, name="控制器 B200",
                   tax_category="standard", ref_price=Decimal("1200")),
            db.Sku(id="SKU-SVC", tenant_id=tenant_id, name="安装服务",
                   tax_category="services", ref_price=Decimal("1000")),
            db.PriceHistory(tenant_id=tenant_id, partner_id="CUST-0021", sku_id="SKU-A100",
                            price=Decimal("565"), tax_included=True, date=dt.date(2026, 9, 15)),
            db.PriceHistory(tenant_id=tenant_id, partner_id="CUST-0034", sku_id="SKU-A100",
                            price=Decimal("540"), tax_included=True, date=dt.date(2026, 8, 1)),
            db.StockQuant(tenant_id=tenant_id, sku_id="SKU-A100", warehouse_id="WH1",
                          opening_qty=Decimal("1000"), qty=Decimal("1000")),
            db.StockQuant(tenant_id=tenant_id, sku_id="SKU-B200", warehouse_id="WH1",
                          opening_qty=Decimal("50"), qty=Decimal("50")),
        ])
        s.commit()
    # 历史已确认订单（供指标问数与信用占用）：走真实 Action 管线，保证账实一致
    actor = ActorRef(type="human", id="u_fixture", tenant_id=tenant_id, scopes=["*"])
    for pid, qty, price, date in [
        ("CUST-0021", 100, 565, "2026-10-10"),
        ("CUST-0034", 50, 540, "2026-10-20"),
    ]:
        r1 = engine.execute(ActionRequest(
            action="sales.order.create", actor=actor,
            client_request_id=f"fixture-{pid}-{date}",
            args={"customer_id": pid, "business_date": date,
                  "lines": [{"sku_id": "SKU-A100", "qty": qty, "price": price, "tax_included": True}]}))
        assert r1.status == "ok", r1.violations
        r2 = engine.execute(ActionRequest(
            action="sales.order.confirm", actor=actor,
            client_request_id=f"fixture-confirm-{pid}-{date}",
            args={"order_id": r1.data["order_id"]}))
        assert r2.status == "ok", r2.violations
