"""进化资产库：话术模式（pattern）版本化。内核代码/账逻辑/权限永不在此列（ADR-11 白名单）。"""
from __future__ import annotations
from sqlalchemy import select
from heng_kernel import db

DEFAULT_ORDER_REGEX = r"给\s*([^\s，。,]+?)\s*(?:发|下|订|要)\s*(\d+)\s*(?:台|个|件|套)\s*([A-Za-z0-9\-]+)"
ASSET_NAME = "order_intent"


def active_asset(s, tenant_id: str):
    """灰度优先：本租户在灰度名单 → canary 版本；否则最新 active 版本；都没有 → 内置默认。
    参数为已打开的 Session。"""
    canaries = s.execute(select(db.PatternAsset).where(
        db.PatternAsset.name == ASSET_NAME, db.PatternAsset.status == "canary")
        .order_by(db.PatternAsset.version.desc())).scalars().all()
    for a in canaries:
        if tenant_id in (a.canary_tenants or []):
            return a
    act = s.execute(select(db.PatternAsset).where(
        db.PatternAsset.name == ASSET_NAME, db.PatternAsset.status == "active")
        .order_by(db.PatternAsset.version.desc())).scalars().first()
    return act


def active_regex(sf, tenant_id: str) -> str:
    with sf() as s:
        a = active_asset(s, tenant_id)
    return (a.spec or {}).get("regex", DEFAULT_ORDER_REGEX) if a else DEFAULT_ORDER_REGEX


def latest_version(sf) -> int:
    with sf() as s:
        a = s.execute(select(db.PatternAsset).where(db.PatternAsset.name == ASSET_NAME)
                      .order_by(db.PatternAsset.version.desc())).scalars().first()
        return a.version if a else 0
