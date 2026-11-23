"""L1 数据层。M0: SQLite 开发库 + 精确十进制 TEXT 存储；生产切 PostgreSQL(RLS)。
所有业务表带 tenant_id；访问一律经 tenant 过滤（PG RLS 见 deploy/pg_rls.sql）。"""
from __future__ import annotations
import datetime as dt
from decimal import Decimal
from sqlalchemy import (String, Integer, Boolean, Date, DateTime, Text, JSON,
                        ForeignKey, create_engine, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session
from sqlalchemy.types import TypeDecorator
from sqlalchemy.pool import StaticPool


class Dec(TypeDecorator):
    """精确十进制：以 TEXT 存储，出入库均 Decimal（M0 sqlite 友好；PG 迁移映射 NUMERIC）。"""
    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(value if isinstance(value, Decimal) else Decimal(str(value)))

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(value)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


def _pk(prefix: str):
    import uuid
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Tenant(Base):
    __tablename__ = "tenant"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    edition: Mapped[str] = mapped_column(String(20), default="community")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Partner(Base):
    __tablename__ = "res_partner"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _pk("pt"))
    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    region: Mapped[str] = mapped_column(String(50), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    credit_limit: Mapped[Decimal] = mapped_column(Dec(), default=Decimal("0"))
    is_customer: Mapped[bool] = mapped_column(Boolean, default=True)


class Sku(Base):
    __tablename__ = "product_sku"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _pk("sku"))
    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    tax_category: Mapped[str] = mapped_column(String(40), default="standard")
    ref_price: Mapped[Decimal] = mapped_column(Dec(), default=Decimal("0"))  # 含税参考价


class PriceHistory(Base):
    __tablename__ = "price_history"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _pk("ph"))
    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True, index=True)
    partner_id: Mapped[str] = mapped_column(String(40))
    sku_id: Mapped[str] = mapped_column(String(40))
    price: Mapped[Decimal] = mapped_column(Dec())
    tax_included: Mapped[bool] = mapped_column(Boolean, default=True)
    date: Mapped[dt.date] = mapped_column(Date)


class SalesOrder(Base):
    __tablename__ = "sales_order"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _pk("so"))
    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True, index=True)
    no: Mapped[str] = mapped_column(String(40))
    partner_id: Mapped[str] = mapped_column(String(40))
    warehouse_id: Mapped[str] = mapped_column(String(40), default="WH1")
    state: Mapped[str] = mapped_column(String(20), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    business_date: Mapped[dt.date] = mapped_column(Date)
    total_untaxed: Mapped[Decimal] = mapped_column(Dec(), default=Decimal("0"))
    total_tax: Mapped[Decimal] = mapped_column(Dec(), default=Decimal("0"))
    total_amount: Mapped[Decimal] = mapped_column(Dec(), default=Decimal("0"))
    created_by: Mapped[str] = mapped_column(String(80), default="")
    actor_type: Mapped[str] = mapped_column(String(20), default="human")
    trace_id: Mapped[str] = mapped_column(String(60), default="")
    ext: Mapped[dict] = mapped_column(JSON, default=dict)  # 元数据扩展包字段容器（M1）


class SalesOrderLine(Base):
    __tablename__ = "sales_order_line"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _pk("sol"))
    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True, index=True)
    order_id: Mapped[str] = mapped_column(String(40), index=True)
    sku_id: Mapped[str] = mapped_column(String(40))
    qty: Mapped[Decimal] = mapped_column(Dec())
    price: Mapped[Decimal] = mapped_column(Dec())
    tax_included: Mapped[bool] = mapped_column(Boolean, default=True)
    tax_rate: Mapped[Decimal] = mapped_column(Dec())
    amount: Mapped[Decimal] = mapped_column(Dec())       # 不含税
    tax_amount: Mapped[Decimal] = mapped_column(Dec())
    total: Mapped[Decimal] = mapped_column(Dec())        # 价税合计


class StockQuant(Base):
    __tablename__ = "stock_quant"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _pk("q"))
    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True, index=True)
    sku_id: Mapped[str] = mapped_column(String(40))
    warehouse_id: Mapped[str] = mapped_column(String(40))
    opening_qty: Mapped[Decimal] = mapped_column(Dec(), default=Decimal("0"))
    qty: Mapped[Decimal] = mapped_column(Dec(), default=Decimal("0"))


class StockMove(Base):
    __tablename__ = "stock_move"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _pk("mv"))
    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True, index=True)
    sku_id: Mapped[str] = mapped_column(String(40))
    warehouse_id: Mapped[str] = mapped_column(String(40))
    qty: Mapped[Decimal] = mapped_column(Dec())  #  signed
    doc_ref: Mapped[str] = mapped_column(String(60), default="")
    trace_id: Mapped[str] = mapped_column(String(60), default="")


class JournalEntry(Base):
    __tablename__ = "journal_entry"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _pk("je"))
    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True, index=True)
    no: Mapped[str] = mapped_column(String(40))
    period: Mapped[str] = mapped_column(String(7))
    state: Mapped[str] = mapped_column(String(20), default="posted")
    source_action: Mapped[str] = mapped_column(String(80), default="")
    doc_ref: Mapped[str] = mapped_column(String(60), default="")
    business_date: Mapped[dt.date] = mapped_column(Date)
    trace_id: Mapped[str] = mapped_column(String(60), default="")


class JournalLine(Base):
    __tablename__ = "journal_line"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _pk("jl"))
    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True, index=True)
    entry_id: Mapped[str] = mapped_column(String(40), index=True)
    account_id: Mapped[str] = mapped_column(String(20))
    debit: Mapped[Decimal] = mapped_column(Dec(), default=Decimal("0"))
    credit: Mapped[Decimal] = mapped_column(Dec(), default=Decimal("0"))
    partner_id: Mapped[str] = mapped_column(String(40), default="")
    doc_ref: Mapped[str] = mapped_column(String(60), default="")


class Invoice(Base):
    __tablename__ = "tax_invoice"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _pk("inv"))
    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True, index=True)
    no: Mapped[str] = mapped_column(String(60))
    order_id: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(20), default="issued")
    channel: Mapped[str] = mapped_column(String(30), default="sandbox")  # M0 沙箱通道
    total_amount: Mapped[Decimal] = mapped_column(Dec())
    total_tax: Mapped[Decimal] = mapped_column(Dec())
    issued_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Payment(Base):
    __tablename__ = "finance_payment"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _pk("pay"))
    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True, index=True)
    order_id: Mapped[str] = mapped_column(String(40))
    amount: Mapped[Decimal] = mapped_column(Dec())
    state: Mapped[str] = mapped_column(String(20), default="executed")
    journal_id: Mapped[str] = mapped_column(String(40), default="")


class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    event_type: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict] = mapped_column(JSON)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class Idempotency(Base):
    __tablename__ = "idempotency"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40))
    response: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)


class Approval(Base):
    __tablename__ = "approval"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _pk("ap"))
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    action_req: Mapped[dict] = mapped_column(JSON)
    risk: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    approver_a: Mapped[str] = mapped_column(String(80), default="")
    approver_b: Mapped[str] = mapped_column(String(80), default="")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _pk("au"))
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    action: Mapped[str] = mapped_column(String(120))
    actor_type: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[str] = mapped_column(String(80))
    on_behalf_of: Mapped[str] = mapped_column(String(80), default="")
    args: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30))
    trace_id: Mapped[str] = mapped_column(String(60), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class AgentActionLog(Base):
    """决策 Lineage（§9.2）：可离线回放。"""
    __tablename__ = "agent_action_log"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _pk("al"))
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    agent_id: Mapped[str] = mapped_column(String(60))
    utterance: Mapped[str] = mapped_column(Text)
    plan: Mapped[dict] = mapped_column(JSON)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    prompt_version: Mapped[str] = mapped_column(String(60), default="")
    model_id: Mapped[str] = mapped_column(String(80), default="")
    steps: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(default=1.0)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_tokens: Mapped[int] = mapped_column(Integer, default=0)
    trace_id: Mapped[str] = mapped_column(String(60), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class InvariantCheck(Base):
    __tablename__ = "invariant_check"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _pk("ic"))
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(10))  # ok / violation
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    checked_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


def make_engine(url: str = "sqlite:///heng_dev.db"):
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool  # 内存库跨线程共享单连接（TestClient/评测）
        return create_engine(url, future=True, **kwargs)
    return create_engine(url, future=True)


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db(engine):
    Base.metadata.create_all(engine)


def tq(session: Session, model, tenant_id: str):
    """tenant 过滤查询：M0 的应用层隔离（生产叠加 PG RLS 双保险）。"""
    return select(model).where(model.tenant_id == tenant_id)


class AppUser(Base):
    __tablename__ = "app_user"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _pk("u"))
    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(60))
    password_hash: Mapped[str] = mapped_column(String(120))
    display_name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(30))
    scopes: Mapped[list] = mapped_column(JSON, default=list)


class AuthSession(Base):
    __tablename__ = "auth_session"
    token: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40))
    tenant_id: Mapped[str] = mapped_column(String(40))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)


class EvolutionCase(Base):
    __tablename__ = "evolution_case"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _pk("ec"))
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    source: Mapped[str] = mapped_column(String(30))
    utterance: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="new")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class EvolutionPR(Base):
    __tablename__ = "evolution_pr"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _pk("ep"))
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    asset_type: Mapped[str] = mapped_column(String(30), default="pattern")
    title: Mapped[str] = mapped_column(String(200))
    diff: Mapped[dict] = mapped_column(JSON, default=dict)
    case_ids: Mapped[list] = mapped_column(JSON, default=list)
    eval_report: Mapped[dict] = mapped_column(JSON, default=dict)
    risk: Mapped[str] = mapped_column(String(20), default="low")
    status: Mapped[str] = mapped_column(String(20), default="pending_review")
    reviewed_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    activated_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class PatternAsset(Base):
    __tablename__ = "pattern_asset"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _pk("pa"))
    name: Mapped[str] = mapped_column(String(60), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="active")
    canary_tenants: Mapped[list] = mapped_column(JSON, default=list)
    pr_id: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class PackageInstall(Base):
    __tablename__ = "package_install"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _pk("pkg"))
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(80))
    version: Mapped[str] = mapped_column(String(30), default="1.0")
    path: Mapped[str] = mapped_column(String(300))
    installed_by: Mapped[str] = mapped_column(String(80), default="")
    installed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


def ensure_columns(engine):
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if insp.has_table("sales_order"):
        cols = {c["name"] for c in insp.get_columns("sales_order")}
        if "ext" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE sales_order ADD COLUMN ext TEXT DEFAULT '{}'"))
