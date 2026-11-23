"""场景 DSL 执行器（§10.2/10.3）：确定性断言一票否决；judge M0 为确定性代理（M1 接 LLM judge+校准）。
支持指令：action_called / no_action / no_action_executed / invariants_ok；其余为安全表达式断言。"""
from __future__ import annotations
import datetime as dt
import uuid
from pathlib import Path
import yaml
from heng_contracts.models import ActorRef, ActionRequest, ApprovalDecision, new_id
from heng_actions.compose import build_engine
from heng_agent.orchestrator import Orchestrator
from heng_agent.providers import RuleBasedProvider
from heng_llm.gateway import LLMGateway
from heng_kernel.expr import check
from .fixture import build_fixture

CLOCK = dt.date(2026, 11, 23)


def _resolve_refs(obj, run_res):
    if isinstance(obj, str) and obj.startswith("$setup["):
        import re as _re
        m = _re.match(r"\$setup\[(\d+)\]\.(.+)", obj)
        cur = run_res.get("setup_results", [])[int(m.group(1))]
        for part in m.group(2).split("."):
            cur = cur[part]
        return cur
    if isinstance(obj, dict):
        return {k: _resolve_refs(v, run_res) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_refs(v, run_res) for v in obj]
    return obj


def run_scenario(spec: dict) -> dict:
    tenant = f"ev_{uuid.uuid4().hex[:10]}"
    engine, sf, inv = build_engine("sqlite://", clock=CLOCK)
    build_fixture(engine, sf, tenant, spec.get("given", {}).get("fixture", "demo_trading_cn"))
    engine.calls.clear()  # fixture 属于环境搭建，不计入场景行为断言
    when = spec["when"]
    run_res: dict = {}

    if when["channel"] == "copilot":
        gw = LLMGateway([RuleBasedProvider()], offline=True)
        orch = Orchestrator(engine, gw, spec.get("agent", "order_clerk"))
        run_res = orch.run(tenant, "u_eval", when["utterance"],
                           sensitive=when.get("sensitive", False))
    else:
        actor = ActorRef(type=when.get("actor", "human"),
                         id=when.get("actor_id", "u_eval"), tenant_id=tenant,
                         scopes=when.get("scopes", ["*"]))
        for st in when.get("setup", []):
            r = engine.execute(ActionRequest(action=st["action"], args=_resolve_refs(st.get("args", {}), run_res),
                                             actor=actor, client_request_id=new_id("setup")))
            if r.status != "ok":
                return {"id": spec["id"], "passed": False,
                        "failures": [f"setup {st['action']}: {[v.message for v in r.violations]}"]}
            run_res.setdefault("setup_results", []).append(r.model_dump(mode="json"))
        req = ActionRequest(action=when["action"], args=_resolve_refs(when.get("args", {}), run_res), actor=actor,
                            client_request_id=when.get("client_request_id", new_id("req")))
        results = []
        for _ in range(max(1, int(when.get("repeat", 1)))):
            results.append(engine.execute(req))
        for dec in when.get("approvals", []):
            ap_id = next((r.approval_id for r in reversed(results) if r.approval_id), None)
            if ap_id is None:
                break
            results.append(engine.approve(ap_id, ApprovalDecision(**dec), tenant))
        run_res["results"] = [r.model_dump(mode="json") for r in results]
        run_res["reply"] = ""
        run_res["cards"] = []
        run_res["escalated"] = False

    # ---- 断言上下文 ----
    calls = engine.calls
    nondry = [c for c in calls if not c["dry_run"]]
    dry_ok = [c for c in calls if c["dry_run"] and c["status"] == "ok" and c.get("data")]
    exec_ok = [c for c in nondry if c["status"] == "ok" and c.get("data")]
    last = nondry[-1] if nondry else {}
    ctx = {
        "calls": calls, "proposed": nondry, "executed": exec_ok,
        "args": last.get("args", {}), "status": last.get("status"),
        "data": (dry_ok[-1]["data"] if dry_ok else (exec_ok[-1]["data"] if exec_ok else {})),
        "result": exec_ok[-1]["data"] if exec_ok else {},
        "run": run_res, "cards": run_res.get("cards", []),
        "escalated": run_res.get("escalated", False), "reply": run_res.get("reply", ""),
        "called": lambda a: any(c["action"] == a for c in nondry),
        "executed_action": lambda a: any(c["action"] == a and c["status"] == "ok" for c in nondry),
        "count_called": lambda a: sum(1 for c in nondry if c["action"] == a),
    }
    failures: list[str] = []
    then = spec.get("then", {})
    for item in then.get("deterministic", []):
        if item.startswith("action_called:"):
            name = item.split(":", 1)[1].strip()
            if not ctx["called"](name):
                failures.append(f"expected action_called: {name}")
        elif item.startswith("no_action_executed:"):
            names = yaml.safe_load(item.split(":", 1)[1].strip())
            for n in names:
                if ctx["executed_action"](n):
                    failures.append(f"forbidden executed action: {n}")
        elif item.startswith("no_action:"):
            names = yaml.safe_load(item.split(":", 1)[1].strip())
            for n in names:
                if ctx["called"](n):
                    failures.append(f"forbidden action called: {n}")
        elif item.startswith("invariants_ok:"):
            with sf() as s:
                violations = inv.check_all(s, tenant, persist=False)
            if violations and item.split(":", 1)[1].strip().lower() == "true":
                failures.append(f"invariant violations: {violations}")
        else:
            ok, err = check(item, ctx)
            if not ok:
                failures.append(f"assert failed: {item} ({err or 'false'})")

    # judge（M0 确定性代理：证据完整性）
    judge_score = None
    j = then.get("judge")
    if j:
        cards = run_res.get("cards", [])
        if j["rubric"] == "evidence_completeness":
            judge_score = 5 if cards and all(c.get("evidence") for c in cards) else 0
        if judge_score < j.get("min_score", 4):
            failures.append(f"judge {j['rubric']} score {judge_score} < {j.get('min_score', 4)}")

    # 预算
    b = then.get("budget", {})
    if "max_steps" in b and len(run_res.get("plan", {}).get("steps", [])) > b["max_steps"]:
        failures.append("budget max_steps exceeded")
    if "max_tokens" in b and run_res.get("model", {}).get("tokens", 0) > b["max_tokens"]:
        failures.append("budget max_tokens exceeded")

    return {"id": spec["id"], "passed": not failures, "failures": failures,
            "judge_score": judge_score, "reply": run_res.get("reply", ""),
            "escalated": run_res.get("escalated", False)}


def run_suite(path: str | Path) -> dict:
    files = sorted(Path(path).rglob("*.yaml"))
    results = []
    for f in files:
        spec = yaml.safe_load(f.read_text(encoding="utf-8"))
        try:
            results.append(run_scenario(spec))
        except Exception as e:  # noqa: BLE001
            results.append({"id": spec.get("id", f.name), "passed": False,
                            "failures": [f"runner error: {type(e).__name__}: {e}"]})
    passed = sum(1 for r in results if r["passed"])
    return {"suite": str(path), "total": len(results), "passed": passed,
            "failed": len(results) - passed,
            "pass_rate": round(passed / len(results), 4) if results else 0.0,
            "results": results}
