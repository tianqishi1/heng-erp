"""组合根：装配 Engine（eval 与 API 共用）。生产在此替换 PG/NATS/Temporal 适配器。"""
from __future__ import annotations
import datetime as dt
from pathlib import Path
from heng_kernel.db import make_engine, make_session_factory, init_db, Tenant
from heng_kernel.registry import MetadataRegistry
from heng_kernel.rules import RuleEngine
from heng_kernel.pipeline import Engine
from heng_kernel.invariants import InvariantService
from heng_kernel.bus import EventBus
from .handlers import HANDLERS

REPO_ROOT = Path(__file__).resolve().parents[3]


def build_engine(database_url: str = "sqlite://", metadata_dir: str | Path | None = None,
                 ruleset_dir: str | Path | None = None, clock: dt.date | None = None,
                 automation: dict | None = None, bus: EventBus | None = None):
    engine = make_engine(database_url)
    init_db(engine)
    sf = make_session_factory(engine)
    registry = MetadataRegistry().load_dir(metadata_dir or REPO_ROOT / "metadata" / "core")
    rules = RuleEngine().load_dir(ruleset_dir or REPO_ROOT / "rulesets" / "tax_cn")
    eng = Engine(sf, registry, rules, HANDLERS, bus=bus,
                 clock=clock or dt.date(2026, 11, 23), automation=automation)
    return eng, sf, InvariantService()


def ensure_tenant(sf, tenant_id: str, name: str = "demo") -> None:
    with sf() as s:
        if s.get(Tenant, tenant_id) is None:
            s.add(Tenant(id=tenant_id, name=name))
            s.commit()
