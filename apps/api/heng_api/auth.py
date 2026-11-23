"""M1 认证与角色（alpha：加盐 SHA256 + 会话令牌；生产换 Argon2 + OAuth/SSO）。
审批人身份 = 登录用户，杜绝自报身份。"""
from __future__ import annotations
import datetime as dt
import hashlib
import secrets
from sqlalchemy import select
from heng_kernel import db

ROLE_SCOPES = {
    "boss": ["*"],
    "finance": ["sales.order.write", "finance.journal.write", "finance.payment.execute",
                "tax.invoice.write", "metrics.read", "catalog.read"],
    "approver": ["sales.order.write", "finance.payment.execute", "tax.invoice.write",
                 "metrics.read", "catalog.read"],
    "sales": ["sales.order.write", "metrics.read", "catalog.read"],
}
ROLE_LABEL = {"boss": "老板", "finance": "财务", "approver": "审批人", "sales": "销售"}
SALT = "heng-m1-alpha"  # 生产：每用户随机盐 + Argon2


def hash_pw(pw: str) -> str:
    return hashlib.sha256((SALT + pw).encode("utf-8")).hexdigest()


def seed_users(sf, tenant_id: str):
    with sf() as s:
        if s.execute(select(db.AppUser).where(db.AppUser.tenant_id == tenant_id)).scalars().first():
            return
        for username, pw, name, role in [
            ("chen", "boss123", "陈总（老板）", "boss"),
            ("wang", "fin123", "王姐（财务）", "finance"),
            ("au_a", "approve123", "审批人 A", "approver"),
            ("au_b", "approve123", "审批人 B", "approver"),
        ]:
            s.add(db.AppUser(tenant_id=tenant_id, username=username, password_hash=hash_pw(pw),
                             display_name=name, role=role, scopes=ROLE_SCOPES[role]))
        s.commit()


def login(sf, tenant_id: str, username: str, password: str):
    with sf() as s:
        u = s.execute(select(db.AppUser).where(
            db.AppUser.tenant_id == tenant_id, db.AppUser.username == username)).scalars().first()
        if u is None or u.password_hash != hash_pw(password):
            return None
        token = secrets.token_hex(24)
        s.add(db.AuthSession(token=token, user_id=u.id, tenant_id=tenant_id,
                             expires_at=db.utcnow() + dt.timedelta(hours=12)))
        s.commit()
        return {"token": token, "user": {"id": u.id, "name": u.display_name,
                                         "role": u.role, "role_label": ROLE_LABEL.get(u.role, u.role)}}


def resolve(sf, token: str | None):
    if not token:
        return None
    with sf() as s:
        sess = s.get(db.AuthSession, token)
        if sess is None or sess.expires_at < db.utcnow():
            return None
        return s.get(db.AppUser, (sess.user_id, sess.tenant_id))


def logout(sf, token: str):
    with sf() as s:
        sess = s.get(db.AuthSession, token)
        if sess:
            s.delete(sess)
            s.commit()
