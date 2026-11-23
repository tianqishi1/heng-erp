"""M1 API 冒烟验证（需先启动服务）：python scripts/smoke_api.py [base_url]
覆盖：登录鉴权 → 开单审批 → 防重复 → 四眼收款 → 自进化闭环 → 扩展包 → 账务体检。"""
import sys
import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
c = httpx.Client(base_url=BASE, timeout=120)

def login(u, p):
    r = c.post("/v1/auth/login", json={"username": u, "password": p})
    r.raise_for_status()
    return {"X-Auth-Token": r.json()["token"]}

H = login("chen", "boss123")
assert c.get("/v1/entities/sales.order").status_code == 401  # 未登录拒绝
print("0 鉴权: 未登录 401 ✔  老板登录 ✔")

for ap in c.get("/v1/approvals", headers=H).json():
    c.post(f"/v1/approvals/{ap['id']}/approve", headers=H, json={"decision": "approve"})

j = c.post("/v1/copilot/messages", headers=H, json={"utterance": "给华东李总发200台A100，按上次的价格，含税"}).json()
ap_id = j["cards"][0]["approval_id"]
r1 = c.post(f"/v1/approvals/{ap_id}/approve", headers=H, json={"decision": "approve"}).json()
r2 = c.post(f"/v1/approvals/{ap_id}/approve", headers=H, json={"decision": "approve"}).json()
assert r1["status"] == "ok" and r2["violations"][0]["code"] == "bad_state"
print("1 开单+防重复:", r1["data"]["no"], "| 二次点击:", r2["violations"][0]["code"], "✔")

oid = r1["data"]["order_id"]
c.post("/v1/actions/sales.order.confirm/execute", headers=H, json={"args": {"order_id": oid}})
p = c.post("/v1/actions/payment.execute/execute", headers=H, json={"args": {"order_id": oid, "amount": 113000}}).json()
pid = p["approval_id"]
HA, HB = login("au_a", "approve123"), login("au_b", "approve123")
a1 = c.post(f"/v1/approvals/{pid}/approve", headers=HA, json={"decision": "approve"}).json()
a2 = c.post(f"/v1/approvals/{pid}/approve", headers=HA, json={"decision": "approve"}).json()
a3 = c.post(f"/v1/approvals/{pid}/approve", headers=HB, json={"decision": "approve"}).json()
assert a1["status"] == "pending_approval" and a2["violations"][0]["code"] == "four_eyes" and a3["status"] == "ok"
print("2 四眼收款(真实账号): A批→待批 | A再批→拒绝 | B批→执行 ✔")

SAY = "帮王总订50个B200，单价1200"
j1 = c.post("/v1/copilot/messages", headers=H, json={"utterance": SAY}).json()
assert j1["escalated"]
c.post("/v1/evolution/mine", headers=H)
cases = c.get("/v1/evolution/cases", headers=H).json()
case = next(x for x in cases if x["utterance"] == SAY)
cand = c.post("/v1/evolution/candidates", headers=H, json={"case_id": case["id"]}).json()
assert "pr_id" in cand, cand
g = cand["eval_report"]["golden"]
c.post(f"/v1/evolution/prs/{cand['pr_id']}/review", headers=H, json={"decision": "approve"})
j2 = c.post("/v1/copilot/messages", headers=H, json={"utterance": SAY}).json()
assert not j2["escalated"] and j2["cards"]
c.post(f"/v1/evolution/prs/{cand['pr_id']}/activate", headers=H)
c.post(f"/v1/evolution/prs/{cand['pr_id']}/rollback", headers=H)
j3 = c.post("/v1/copilot/messages", headers=H, json={"utterance": SAY}).json()
assert j3["escalated"]
print(f"3 自进化闭环: 不支持→挖掘→候选(黄金集{g['passed']}/{g['total']})→人审→灰度可用→全量→回滚复原 ✔")

pk = c.post("/v1/packages/trade_priority/install", headers=H).json()
sp = c.post("/v1/actions/sales.order.set_priority/execute", headers=H, json={"args": {"id": oid, "value": "urgent"}}).json()
assert sp["status"] == "ok", sp
print("4 扩展包:", pk["status"], "| 订单优先级=urgent ✔")

h = c.get("/v1/invariants", headers=H).json()
assert h["status"] == "ok" and len(h["checks"]) == 4
page = c.get("/").text
for kw in ["进化中心", "扩展与设置", "审批中心", "登 录"]:
    assert kw in page, kw
print("5 账务体检四项通过 ✔  界面含登录/进化中心/扩展页 ✔")
print("\nM1 冒烟验证全部通过")
