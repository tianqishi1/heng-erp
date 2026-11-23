"""API 冒烟验证（需先启动服务）：python scripts/smoke_api.py [base_url]"""
import sys
import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
c = httpx.Client(base_url=BASE, timeout=30)

# 0 清理遗留待批单
for ap in c.get("/v1/approvals", params={"status": "pending"}).json():
    c.post(f"/v1/approvals/{ap['id']}/approve", json={"approver_id": "u_boss", "decision": "approve"})

# 1 一句话开单 → 行动卡
j = c.post("/v1/copilot/messages", json={"utterance": "给华东李总发200台A100，按上次的价格，含税"}).json()
assert len(j["cards"]) == 1, j
ap_id = j["cards"][0]["approval_id"]
print("1 开单卡:", j["reply"])

# 2 审批中心业务化摘要
aps = c.get("/v1/approvals", params={"status": "pending"}).json()
assert len(aps) == 1 and "李振华" in aps[0]["summary"] and "113,000.00" in aps[0]["summary"], aps
print("2 审批摘要:", aps[0]["title"], "|", aps[0]["summary"], "| 风险:", aps[0]["risk"])

# 3 第一次批准 → 执行；第二次 → 防重复拒绝
r1 = c.post(f"/v1/approvals/{ap_id}/approve", json={"approver_id": "u_boss", "decision": "approve"}).json()
assert r1["status"] == "ok", r1
r2 = c.post(f"/v1/approvals/{ap_id}/approve", json={"approver_id": "u_boss", "decision": "approve"}).json()
assert r2["status"] == "rejected" and r2["violations"][0]["code"] == "bad_state", r2
print("3 第一次:", r1["status"], r1["data"]["no"], "| 第二次:", r2["status"], r2["violations"][0]["code"])

# 4 四眼收款：同一人批两次被拒，换人通过
oid = r1["data"]["order_id"]
c.post("/v1/actions/sales.order.confirm/execute", json={"args": {"order_id": oid}, "actor_id": "u_boss"})
p = c.post("/v1/actions/payment.execute/execute", json={"args": {"order_id": oid, "amount": 113000}, "actor_id": "u_boss"}).json()
assert p["status"] == "pending_approval", p
pid = p["approval_id"]
a1 = c.post(f"/v1/approvals/{pid}/approve", json={"approver_id": "u_a", "decision": "approve"}).json()
a2 = c.post(f"/v1/approvals/{pid}/approve", json={"approver_id": "u_a", "decision": "approve"}).json()
a3 = c.post(f"/v1/approvals/{pid}/approve", json={"approver_id": "u_b", "decision": "approve"}).json()
assert a1["status"] == "pending_approval" and a2["violations"][0]["code"] == "four_eyes" and a3["status"] == "ok", (a1, a2, a3)
print("4 四眼收款: A批->", a1["status"], "| A再批->", a2["violations"][0]["code"], "| B批->", a3["status"])

# 5 账务体检
h = c.get("/v1/invariants").json()
assert h["status"] == "ok" and len(h["checks"]) == 4, h
print("5 账务体检:", h["status"], [x["label"] for x in h["checks"]])

# 6 界面业务化
page = c.get("/").text
for kw in ["审批中心", "账务体检", "销售订单", "当前身份"]:
    assert kw in page, kw
assert "alert(" not in page  # 内部 fetch 序列化允许，用户可见输出不得有 JSON/弹窗
print("6 界面: 业务页面齐全，无 alert 弹窗与用户可见 JSON")
print("\n冒烟验证全部通过 ✔")
