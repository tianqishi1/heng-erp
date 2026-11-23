"""Action 执行上下文 + 前置条件函数库（确定性，绑定 session/tenant/clock）。"""
from __future__ import annotations
import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from heng_contracts.models import D
from . import db
from .expr import ExprError


@dataclass
class ActionContext:
    session: Session
    tenant_id: str
    actor: object
    rules: object
    clock: dt.date
    events: list = field(default_factory=list)
    meta: object = None

    def emit(self, event_type: str, subject: str, data: dict):
        self.events.append({"type": event_type, "subject": subject, "data": data})


def _one(session, model, tenant_id, **kw):
    stmt = select(model).where(model.tenant_id == tenant_id)
    for k, v in kw.items():
        stmt = stmt.where(getattr(model, k) == v)
    return session.execute(stmt).scalars().first()


def build_eval_ctx(session: Session, tenant_id: str, args: dict, rules, clock: dt.date) -> dict:
    def partner(pid):
        p = _one(session, db.Partner, tenant_id, id=pid)
        if p is None:
            raise ExprError(f"partner not found: {pid}")
        return {"id": p.id, "name": p.name, "region": p.region, "active": p.active,
                "credit_limit": p.credit_limit}

    def sku(sid):
        s = _one(session, db.Sku, tenant_id, id=sid)
        if s is None:
            raise ExprError(f"sku not found: {sid}")
        return {"id": s.id, "name": s.name, "active": s.active,
                "tax_category": s.tax_category, "ref_price": s.ref_price}

    def order(oid):
        o = _one(session, db.SalesOrder, tenant_id, id=oid)
        if o is None:
            raise ExprError(f"order not found: {oid}")
        return {"id": o.id, "no": o.no, "state": o.state, "partner_id": o.partner_id,
                "total_amount": o.total_amount, "total_tax": o.total_tax,
                "total_untaxed": o.total_untaxed, "business_date": o.business_date,
                "warehouse_id": o.warehouse_id}

    def lines_of(oid):
        rows = session.execute(select(db.SalesOrderLine).where(
            db.SalesOrderLine.tenant_id == tenant_id,
            db.SalesOrderLine.order_id == oid)).scalars().all()
        return [{"sku_id": r.sku_id, "qty": r.qty, "price": r.price, "amount": r.amount,
                 "tax_amount": r.tax_amount, "total": r.total, "tax_rate": r.tax_rate} for r in rows]

    def last_price(pid, sid):
        row = session.execute(select(db.PriceHistory).where(
            db.PriceHistory.tenant_id == tenant_id,
            db.PriceHistory.partner_id == pid,
            db.PriceHistory.sku_id == sid).order_by(db.PriceHistory.date.desc())).scalars().first()
        return row.price if row else None

    def credit_check(pid, amount):
        p = _one(session, db.Partner, tenant_id, id=pid)
        if p is None:
            return False
        if p.credit_limit <= 0:
            return True  # 0 = 不限信用
        exposure = session.execute(select(func.coalesce(func.sum(db.SalesOrder.total_amount), 0)).where(
            db.SalesOrder.tenant_id == tenant_id,
            db.SalesOrder.partner_id == pid,
            db.SalesOrder.state == "confirmed")).scalar_one()
        return D(exposure) + D(amount) <= p.credit_limit

    def stock_available(sid, wid, qty):
        q = _one(session, db.StockQuant, tenant_id, sku_id=sid, warehouse_id=wid)
        return q is not None and q.qty >= D(qty)

    def balanced(lines):
        dr = sum((D(l.get("debit", 0)) for l in lines), Decimal("0"))
        cr = sum((D(l.get("credit", 0)) for l in lines), Decimal("0"))
        return dr == cr and dr > 0

    def has_invoice(oid):
        return _one(session, db.Invoice, tenant_id, order_id=oid) is not None

    def order_total(oid):
        return order(oid)["total_amount"]

    ctx = {
        "partner": partner, "sku": sku, "order": order, "lines_of": lines_of,
        "last_price": last_price, "credit_check": credit_check,
        "stock_available": stock_available, "balanced": balanced,
        "has_invoice": has_invoice, "order_total": order_total,
        "now": clock, "D": D,
        "args": args,
    }
    def skus_active(lines):
        return all(sku(l["sku_id"])["active"] for l in lines)

    def qtys_positive(lines):
        return all(D(l["qty"]) > 0 for l in lines)

    def order_stock_ok(oid):
        o = _one(session, db.SalesOrder, tenant_id, id=oid)
        if o is None:
            return False
        return all(stock_available(l["sku_id"], o.warehouse_id, l["qty"]) for l in lines_of(oid))

    ctx["skus_active"] = skus_active
    ctx["qtys_positive"] = qtys_positive
    ctx["order_stock_ok"] = order_stock_ok
    ctx.update(args)
    return ctx
