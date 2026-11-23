# SPDX-License-Identifier: LGPL-3.0-or-later
"""指标注册中心（§5.2）：口径唯一；M0  Python 编译执行，生产生成参数化 SQL/物化视图。
所有数字附口径说明与单据下钻——杜绝 AI 编数字。"""
from __future__ import annotations
import datetime as dt
from decimal import Decimal
from sqlalchemy import select
from heng_kernel import db

METRICS = {
    "revenue": {
        "label": "营业收入",
        "caliber": "已确认销售订单的不含税金额合计（收入=已确认订单不含税；口径 owner: finance）",
        "dimensions": ["region", "period"],
    },
}


def query_metric(session, tenant_id: str, metric: str, filters: dict, clock: dt.date) -> dict:
    if metric not in METRICS:
        raise KeyError(f"未注册指标: {metric}")
    m = METRICS[metric]
    period = (filters or {}).get("period")
    region = (filters or {}).get("region")
    orders = session.execute(select(db.SalesOrder).where(
        db.SalesOrder.tenant_id == tenant_id,
        db.SalesOrder.state == "confirmed")).scalars().all()
    partners = {p.id: p for p in session.execute(select(db.Partner).where(
        db.Partner.tenant_id == tenant_id)).scalars()}
    value = Decimal("0")
    drill = []
    for o in orders:
        if period and o.business_date.strftime("%Y-%m") != period:
            continue
        p = partners.get(o.partner_id)
        if region and (p is None or p.region != region):
            continue
        value += o.total_untaxed
        drill.append(o.no)
    return {"metric": metric, "label": m["label"], "value": str(value),
            "filters": {"period": period, "region": region},
            "caliber": m["caliber"], "drill": drill, "as_of": clock.isoformat()}
