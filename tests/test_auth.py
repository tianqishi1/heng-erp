# SPDX-License-Identifier: LGPL-3.0-or-later
from fastapi.testclient import TestClient
from heng_api.main import create_app
from test_api import login


def test_auth_required_and_roles(env):
    engine, sf, inv, tenant = env
    app = create_app(engine, sf, inv)
    with TestClient(app) as client:
        assert client.get("/v1/entities/sales.order").status_code == 401
        assert client.post("/v1/auth/login", json={"username": "chen", "password": "wrong"}).status_code == 401
        h = login(client)
        assert client.get("/v1/auth/me", headers=h).json()["role"] == "boss"
        hw = login(client, "wang", "fin123")
        # 财务不能审批进化单 / 安装扩展包（仅老板）
        assert client.post("/v1/evolution/prs/xxx/review", headers=hw, json={"decision": "approve"}).status_code == 403
        assert client.post("/v1/packages/trade_priority/install", headers=hw).status_code == 403
        # 退出后会话失效
        client.post("/v1/auth/logout", headers=hw)
        assert client.get("/v1/auth/me", headers=hw).status_code == 401
