# SPDX-License-Identifier: LGPL-3.0-or-later
"""不变量断言服务（§4.4/9.4）：提交前 + 周期性双重校验。违规 → 告警 + 自动化降级。"""
from __future__ import annotations
import datetime as dt
from decimal import Decimal
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from heng_contracts.models import D
from . import db


class InvariantService:
    CHECKS = ("order_totals", "trial_balance", "stock_consistency", "invoice_tax_match")

    def check_all(self, session: Session, tenant_id: str, persist: bool = True) -> list[dict]:
        violations: list[dict] = []
        results: dict[str, dict] = {}

        # 1 订单勾稽：价税合计 = 不含税 + 税 = sum(行)
        bad = []
        orders = session.execute(select(db.SalesOrder).where(db.SalesOrder.tenant_id == tenant_id)).scalars().all()
        for o in orders:
            lines = session.execute(select(db.SalesOrderLine).where(
                db.SalesOrderLine.tenant_id == tenant_id, db.SalesOrderLine.order_id == o.id)).scalars().all()
            sum_total = sum((l.total for l in lines), Decimal("0"))
            sum_tax = sum((l.tax_amount for l in lines), Decimal("0"))
            line_ok = all(l.amount + l.tax_amount == l.total for l in lines)
            if not (o.total_untaxed + o.total_tax == o.total_amount == sum_total
                    and o.total_tax == sum_tax and line_ok):
                bad.append({"order": o.no})
        results["order_totals"] = {"checked": len(orders), "violations": bad}

        # 2 试算平衡：每凭证 借=贷；每期间 借=贷
        bad = []
        entries = session.execute(select(db.JournalEntry).where(db.JournalEntry.tenant_id == tenant_id)).scalars().all()
        periods: dict[str, list[Decimal]] = {}
        for e in entries:
            lines = session.execute(select(db.JournalLine).where(
                db.JournalLine.tenant_id == tenant_id, db.JournalLine.entry_id == e.id)).scalars().all()
            dr = sum((l.debit for l in lines), Decimal("0"))
            cr = sum((l.credit for l in lines), Decimal("0"))
            if dr != cr:
                bad.append({"entry": e.no, "debit": str(dr), "credit": str(cr)})
            p = periods.setdefault(e.period, [Decimal("0"), Decimal("0")])
            p[0] += dr; p[1] += cr
        for period, (dr, cr) in periods.items():
            if dr != cr:
                bad.append({"period": period, "debit": str(dr), "credit": str(cr)})
        results["trial_balance"] = {"checked": len(entries), "violations": bad}

        # 3 库存一致性：现存 = 期初 + sum(移动)
        bad = []
        quants = session.execute(select(db.StockQuant).where(db.StockQuant.tenant_id == tenant_id)).scalars().all()
        for q in quants:
            moves = session.execute(select(func.coalesce(func.sum(db.StockMove.qty), 0)).where(
                db.StockMove.tenant_id == tenant_id, db.StockMove.sku_id == q.sku_id,
                db.StockMove.warehouse_id == q.warehouse_id)).scalar_one()
            if q.qty != q.opening_qty + D(moves):
                bad.append({"sku": q.sku_id, "warehouse": q.warehouse_id,
                            "qty": str(q.qty), "expected": str(q.opening_qty + D(moves))})
        results["stock_consistency"] = {"checked": len(quants), "violations": bad}

        # 4 发票-订单税额勾稽
        bad = []
        invoices = session.execute(select(db.Invoice).where(db.Invoice.tenant_id == tenant_id)).scalars().all()
        for inv in invoices:
            o = session.execute(select(db.SalesOrder).where(
                db.SalesOrder.tenant_id == tenant_id, db.SalesOrder.id == inv.order_id)).scalars().first()
            if o is None or inv.total_tax != o.total_tax or inv.total_amount != o.total_amount:
                bad.append({"invoice": inv.no})
        results["invoice_tax_match"] = {"checked": len(invoices), "violations": bad}

        for name, detail in results.items():
            status = "violation" if detail["violations"] else "ok"
            if detail["violations"]:
                violations.append({"check": name, **detail})
            if persist:
                session.add(db.InvariantCheck(tenant_id=tenant_id, name=name, status=status, detail=detail))
        if persist:
            session.commit()
        return violations
