# SPDX-License-Identifier: LGPL-3.0-or-later
"""主数据消歧（§5.1）：名称+区域提示打分，置信度由调用方裁决。"""
from __future__ import annotations
import re
from sqlalchemy import select
from heng_kernel import db

REGIONS = ["华东", "华南", "华北", "华中", "西南", "西北", "东北"]


def split_query(query: str) -> tuple[str, str | None]:
    region = next((r for r in REGIONS if r in query), None)
    name_part = query
    for r in REGIONS:
        name_part = name_part.replace(r, "")
    name_part = re.sub(r"(总|经理|先生|女士|公司)$", "", name_part.strip())
    return name_part.strip(), region


def search_partners(session, tenant_id: str, query: str, region_hint: str | None = None):
    name_part, region_in_query = split_query(query)
    region = region_hint or region_in_query
    rows = session.execute(select(db.Partner).where(
        db.Partner.tenant_id == tenant_id, db.Partner.active == True)).scalars().all()  # noqa: E712
    out = []
    for p in rows:
        score = 0
        if name_part and name_part[0] in p.name:
            score += 2
        if name_part and name_part in p.name:
            score += 2
        if region and p.region == region:
            score += 2
        if score > 0:
            out.append({"id": p.id, "name": p.name, "region": p.region, "score": score})
    return sorted(out, key=lambda x: -x["score"])


def search_skus(session, tenant_id: str, query: str):
    rows = session.execute(select(db.Sku).where(
        db.Sku.tenant_id == tenant_id, db.Sku.active == True)).scalars().all()  # noqa: E712
    q = query.upper()
    out = []
    for s in rows:
        if q == s.id.upper() or q in s.name.upper() or q in s.id.upper():
            out.append({"id": s.id, "name": s.name, "ref_price": str(s.ref_price)})
    return out
