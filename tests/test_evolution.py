# SPDX-License-Identifier: LGPL-3.0-or-later
from fastapi.testclient import TestClient
from heng_api.main import create_app
from test_api import login

NEW_SAY = "帮王总订50个B200，单价1200"


def _say(client, h, text):
    return client.post("/v1/copilot/messages", headers=h, json={"utterance": text}).json()


def test_self_evolution_full_loop(env):
    engine, sf, inv, tenant = env
    app = create_app(engine, sf, inv)
    with TestClient(app) as client:
        h = login(client)
        # 1 系统不支持的说法 → 升级转人工，并自动留下优化点案例
        j = _say(client, h, NEW_SAY)
        assert j["escalated"] and not j["cards"]
        cases = client.get("/v1/evolution/cases", headers=h).json()
        case = next(c for c in cases if c["utterance"] == NEW_SAY)
        # 2 生成候选：沙箱全量评测门禁
        cand = client.post("/v1/evolution/candidates", headers=h, json={"case_id": case["id"]}).json()
        assert "pr_id" in cand, cand
        g = cand["eval_report"]["golden"]
        assert g["passed"] == g["total"] and g["total"] >= 16
        # 3 人审批准 → 本租户灰度 → 新说法立即可用
        rv = client.post(f"/v1/evolution/prs/{cand['pr_id']}/review", headers=h, json={"decision": "approve"}).json()
        assert rv["status"] == "canary"
        j2 = _say(client, h, NEW_SAY)
        assert not j2["escalated"] and len(j2["cards"]) == 1
        assert j2["cards"][0]["data_preview"]["total_amount"] == "60000.00"
        # 4 全量 → 回滚 → 恢复原行为
        assert client.post(f"/v1/evolution/prs/{cand['pr_id']}/activate", headers=h).json()["status"] == "active"
        assert client.post(f"/v1/evolution/prs/{cand['pr_id']}/rollback", headers=h).json()["status"] == "rolled_back"
        j3 = _say(client, h, NEW_SAY)
        assert j3["escalated"]


def test_gate_blocks_bad_candidate(env):
    engine, sf, inv, tenant = env
    app = create_app(engine, sf, inv)
    with TestClient(app) as client:
        h = login(client)
        _say(client, h, "替李总下10台A100，单价565")  # 歧义客户：进化不能以瞎猜为代价
        cases = client.get("/v1/evolution/cases", headers=h).json()
        case = next(c for c in cases if "替李总" in c["utterance"])
        cand = client.post("/v1/evolution/candidates", headers=h, json={"case_id": case["id"]}).json()
        assert "error" in cand and "门禁" in cand["error"]


def test_cooldown_guard(env):
    engine, sf, inv, tenant = env
    app = create_app(engine, sf, inv)
    with TestClient(app) as client:
        h = login(client)
        _say(client, h, NEW_SAY)
        cases = client.get("/v1/evolution/cases", headers=h).json()
        case = next(c for c in cases if c["utterance"] == NEW_SAY)
        cand = client.post("/v1/evolution/candidates", headers=h, json={"case_id": case["id"]}).json()
        client.post(f"/v1/evolution/prs/{cand['pr_id']}/review", headers=h, json={"decision": "approve"})
        client.post(f"/v1/evolution/prs/{cand['pr_id']}/activate", headers=h)
        _say(client, h, "为王总订30个B200，单价1200")
        cases = client.get("/v1/evolution/cases", headers=h).json()
        case2 = next(c for c in cases if "为王总" in c["utterance"])
        cand2 = client.post("/v1/evolution/candidates", headers=h, json={"case_id": case2["id"]}).json()
        assert "error" in cand2 and "冷却" in cand2["error"]
