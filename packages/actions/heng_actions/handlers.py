"""确定性业务 Action 处理器（L1）。LLM 永不进入此层。
金额一律 Decimal + q2；写入与审计/Outbox 由管线同事务管理。"""
from __future__ import annotations
import datetime as dt
from decimal import Decimal
from sqlalchemy import select, func
from heng_contracts.models import D, q2
from heng_kernel import db
from heng_kernel.context import ActionContext
from heng_semantic import catalog, metrics


def _seq(session, model, tenant_id: str) -> int:
    return session.execute(select(func.count()).select_from(model).where(
        model.tenant_id == tenant_id)).scalar_one() + 1


def _lines_of(session, tenant_id, order_id):
    return session.execute(select(db.SalesOrderLine).where(
        db.SalesOrderLine.tenant_id == tenant_id,
        db.SalesOrderLine.order_id == order_id)).scalars().all()


def sales_order_create(ctx: ActionContext, args: dict) -> dict:
    s, t = ctx.session, ctx.tenant_id
    date = dt.date.fromisoformat(args["business_date"]) if args.get("business_date") else ctx.clock
    wid = args.get("warehouse_id", "WH1")
    no = f"SO-{date.year}-{_seq(s, db.SalesOrder, t):05d}"
    order = db.SalesOrder(tenant_id=t, no=no, partner_id=args["customer_id"], warehouse_id=wid,
                          state="draft", business_date=date,
                          created_by=ctx.actor.id, actor_type=ctx.actor.type)
    s.add(order)
    s.flush()
    tot_u = tot_t = tot_a = Decimal("0")
    line_data = []
    for ln in args["lines"]:
        sku = s.execute(select(db.Sku).where(db.Sku.tenant_id == t,
                                             db.Sku.id == ln["sku_id"])).scalars().one()
        rate = ctx.rules.rate_for(sku.tax_category, date)
        qty, price = D(ln["qty"]), D(ln["price"])
        included = ln.get("tax_included", True)
        if included:
            total = q2(qty * price)
            amount, tax = ctx.rules.split_tax(total, rate)
        else:
            amount = q2(qty * price)
            tax = ctx.rules.tax_from_untaxed(amount, rate)
            total = amount + tax
        s.add(db.SalesOrderLine(tenant_id=t, order_id=order.id, sku_id=sku.id, qty=qty,
                                price=price, tax_included=included, tax_rate=rate,
                                amount=amount, tax_amount=tax, total=total))
        tot_u += amount; tot_t += tax; tot_a += total
        line_data.append({"sku_id": sku.id, "qty": str(qty), "price": str(price),
                          "tax_rate": str(rate), "amount": str(amount),
                          "tax_amount": str(tax), "total": str(total)})
    order.total_untaxed, order.total_tax, order.total_amount = tot_u, tot_t, tot_a
    s.flush()
    data = {"id": order.id, "order_id": order.id, "no": no, "state": "draft",
            "customer_id": args["customer_id"], "business_date": date.isoformat(),
            "total_untaxed": str(tot_u), "total_tax": str(tot_t), "total_amount": str(tot_a),
            "lines": line_data, "ruleset": ctx.rules.ruleset_version(date)}
    ctx.emit("cn.heng.sales.order.created", no, {"order_id": order.id, "total_amount": str(tot_a)})
    return {"data": data, "effects_preview": {"creates": "sales.order(draft)",
                                              "total_amount": str(tot_a), "total_tax": str(tot_t)}}


def _post_journal(ctx, order, source_action: str, doc_ref: str | None = None):
    s, t = ctx.session, ctx.tenant_id
    no = f"JE-{order.business_date.year}-{_seq(s, db.JournalEntry, t):05d}"
    je = db.JournalEntry(tenant_id=t, no=no, period=order.business_date.strftime("%Y-%m"),
                         source_action=source_action, doc_ref=doc_ref or order.no,
                         business_date=order.business_date)
    s.add(je)
    s.flush()
    s.add_all([
        db.JournalLine(tenant_id=t, entry_id=je.id, account_id="1122",
                       debit=order.total_amount, credit=D("0"), partner_id=order.partner_id,
                       doc_ref=doc_ref or order.no),
        db.JournalLine(tenant_id=t, entry_id=je.id, account_id="6001",
                       debit=D("0"), credit=order.total_untaxed, doc_ref=doc_ref or order.no),
        db.JournalLine(tenant_id=t, entry_id=je.id, account_id="2221",
                       debit=D("0"), credit=order.total_tax, doc_ref=doc_ref or order.no),
    ])
    return je


def sales_order_confirm(ctx: ActionContext, args: dict) -> dict:
    s, t = ctx.session, ctx.tenant_id
    order = s.execute(select(db.SalesOrder).where(db.SalesOrder.tenant_id == t,
                                                  db.SalesOrder.id == args["order_id"])).scalars().one()
    moves = []
    for ln in _lines_of(s, t, order.id):
        q = s.execute(select(db.StockQuant).where(
            db.StockQuant.tenant_id == t, db.StockQuant.sku_id == ln.sku_id,
            db.StockQuant.warehouse_id == order.warehouse_id)).scalars().one()
        q.qty = q.qty - ln.qty
        mv = db.StockMove(tenant_id=t, sku_id=ln.sku_id, warehouse_id=order.warehouse_id,
                          qty=-ln.qty, doc_ref=order.no)
        s.add(mv)
        moves.append({"sku_id": ln.sku_id, "qty": str(-ln.qty)})
    je = _post_journal(ctx, order, "sales.order.confirm")
    order.state = "confirmed"
    order.version += 1
    s.flush()
    ctx.emit("cn.heng.sales.order.confirmed", order.no,
             {"order_id": order.id, "total_amount": str(order.total_amount)})
    return {"data": {"id": order.id, "order_id": order.id, "state": "confirmed",
                     "journal_id": je.id, "journal_no": je.no, "moves": moves},
            "effects_preview": {"stock": moves, "journal": {"debit": str(order.total_amount),
                                                           "credit_untaxed": str(order.total_untaxed),
                                                           "credit_tax": str(order.total_tax)}}}


def sales_order_cancel(ctx: ActionContext, args: dict) -> dict:
    s, t = ctx.session, ctx.tenant_id
    order = s.execute(select(db.SalesOrder).where(db.SalesOrder.tenant_id == t,
                                                  db.SalesOrder.id == args["order_id"])).scalars().one()
    reversed_je = None
    if order.state == "confirmed":
        for ln in _lines_of(s, t, order.id):
            q = s.execute(select(db.StockQuant).where(
                db.StockQuant.tenant_id == t, db.StockQuant.sku_id == ln.sku_id,
                db.StockQuant.warehouse_id == order.warehouse_id)).scalars().one()
            q.qty = q.qty + ln.qty
            s.add(db.StockMove(tenant_id=t, sku_id=ln.sku_id, warehouse_id=order.warehouse_id,
                               qty=ln.qty, doc_ref=f"cancel:{order.no}"))
        orig = s.execute(select(db.JournalEntry).where(
            db.JournalEntry.tenant_id == t, db.JournalEntry.doc_ref == order.no,
            db.JournalEntry.source_action == "sales.order.confirm")).scalars().first()
        if orig:
            no = f"JE-{order.business_date.year}-{_seq(s, db.JournalEntry, t):05d}"
            rev = db.JournalEntry(tenant_id=t, no=no, period=orig.period,
                                  source_action="sales.order.cancel",
                                  doc_ref=f"cancel:{order.no}", business_date=ctx.clock)
            s.add(rev)
            s.flush()
            for jl in s.execute(select(db.JournalLine).where(
                    db.JournalLine.tenant_id == t, db.JournalLine.entry_id == orig.id)).scalars():
                s.add(db.JournalLine(tenant_id=t, entry_id=rev.id, account_id=jl.account_id,
                                     debit=jl.credit, credit=jl.debit, partner_id=jl.partner_id,
                                     doc_ref=f"cancel:{order.no}"))
            reversed_je = rev.id
    order.state = "cancelled"
    order.version += 1
    s.flush()
    ctx.emit("cn.heng.sales.order.cancelled", order.no, {"order_id": order.id})
    return {"data": {"id": order.id, "order_id": order.id, "state": "cancelled",
                     "reversed_journal_id": reversed_je}}


def payment_execute(ctx: ActionContext, args: dict) -> dict:
    s, t = ctx.session, ctx.tenant_id
    order = s.execute(select(db.SalesOrder).where(db.SalesOrder.tenant_id == t,
                                                  db.SalesOrder.id == args["order_id"])).scalars().one()
    amount = q2(args["amount"])
    no = f"JE-{ctx.clock.year}-{_seq(s, db.JournalEntry, t):05d}"
    je = db.JournalEntry(tenant_id=t, no=no, period=ctx.clock.strftime("%Y-%m"),
                         source_action="payment.execute", doc_ref=order.no, business_date=ctx.clock)
    s.add(je)
    s.flush()
    s.add_all([
        db.JournalLine(tenant_id=t, entry_id=je.id, account_id="1002", debit=amount,
                       credit=D("0"), doc_ref=order.no),
        db.JournalLine(tenant_id=t, entry_id=je.id, account_id="1122", debit=D("0"),
                       credit=amount, partner_id=order.partner_id, doc_ref=order.no),
    ])
    pay = db.Payment(tenant_id=t, order_id=order.id, amount=amount, journal_id=je.id)
    s.add(pay)
    s.flush()
    ctx.emit("cn.heng.finance.payment.executed", order.no,
             {"payment_id": pay.id, "amount": str(amount)})
    return {"data": {"id": pay.id, "payment_id": pay.id, "order_id": order.id,
                     "amount": str(amount), "journal_id": je.id}}


def tax_invoice_issue(ctx: ActionContext, args: dict) -> dict:
    s, t = ctx.session, ctx.tenant_id
    order = s.execute(select(db.SalesOrder).where(db.SalesOrder.tenant_id == t,
                                                  db.SalesOrder.id == args["order_id"])).scalars().one()
    no = f"INV-{ctx.clock.year}-{_seq(s, db.Invoice, t):06d}"
    inv = db.Invoice(tenant_id=t, no=no, order_id=order.id, channel="sandbox",
                     total_amount=order.total_amount, total_tax=order.total_tax)
    s.add(inv)
    s.flush()
    ctx.emit("cn.heng.tax.invoice.issued", no, {"invoice_id": inv.id, "order_id": order.id})
    return {"data": {"id": inv.id, "invoice_id": inv.id, "no": no, "channel": "sandbox",
                     "total_amount": str(order.total_amount), "total_tax": str(order.total_tax)}}


def finance_journal_post(ctx: ActionContext, args: dict) -> dict:
    s, t = ctx.session, ctx.tenant_id
    no = f"JE-{ctx.clock.year}-{_seq(s, db.JournalEntry, t):05d}"
    je = db.JournalEntry(tenant_id=t, no=no, period=args["period"],
                         source_action="finance.journal.post",
                         doc_ref=args.get("doc_ref", ""), business_date=ctx.clock)
    s.add(je)
    s.flush()
    dr = cr = Decimal("0")
    for ln in args["lines"]:
        debit, credit = q2(ln.get("debit", 0)), q2(ln.get("credit", 0))
        dr += debit; cr += credit
        s.add(db.JournalLine(tenant_id=t, entry_id=je.id, account_id=ln["account_id"],
                             debit=debit, credit=credit, partner_id=ln.get("partner_id", ""),
                             doc_ref=args.get("doc_ref", "")))
    s.flush()
    return {"data": {"id": je.id, "journal_id": je.id, "no": no, "period": args["period"],
                     "debit_total": str(dr), "credit_total": str(cr)}}


def metrics_query(ctx: ActionContext, args: dict) -> dict:
    data = metrics.query_metric(ctx.session, ctx.tenant_id, args["metric"],
                                args.get("filters") or {}, ctx.clock)
    return {"data": data}


def catalog_search(ctx: ActionContext, args: dict) -> dict:
    if args["kind"] == "partner":
        cands = catalog.search_partners(ctx.session, ctx.tenant_id, args["query"],
                                        args.get("region_hint"))
    else:
        cands = catalog.search_skus(ctx.session, ctx.tenant_id, args["query"])
    return {"data": {"candidates": cands}}


def generic_set_field(ctx: ActionContext, args: dict) -> dict:
    """元数据扩展点：只允许写扩展包声明的 x_extension 字段（白名单+枚举校验）。"""
    ha = (ctx.meta.raw if ctx.meta else {}).get("handler_args", {})
    entity, field = ha.get("entity"), ha.get("field")
    models = {"sales.order": db.SalesOrder}
    model = models.get(entity)
    if model is None:
        raise ValueError(f"unsupported entity: {entity}")
    s, t = ctx.session, ctx.tenant_id
    row = s.execute(select(model).where(model.tenant_id == t,
                                        model.id == args["id"])).scalars().one()
    allowed = ha.get("allowed")
    if allowed and args["value"] not in allowed:
        raise ValueError(f"value not in allowed: {allowed}")
    ext = dict(row.ext or {})
    ext[field] = args["value"]
    row.ext = ext
    s.flush()
    ctx.emit(f"cn.heng.{entity}.ext_updated", str(row.id), {"field": field, "value": args["value"]})
    return {"data": {"id": row.id, "field": field, "value": args["value"]}}


HANDLERS = {
    "generic.set_field": generic_set_field,
    "sales_order_create": sales_order_create,
    "sales_order_confirm": sales_order_confirm,
    "sales_order_cancel": sales_order_cancel,
    "payment_execute": payment_execute,
    "tax_invoice_issue": tax_invoice_issue,
    "finance_journal_post": finance_journal_post,
    "metrics_query": metrics_query,
    "catalog_search": catalog_search,
}
