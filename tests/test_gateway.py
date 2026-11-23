# SPDX-License-Identifier: LGPL-3.0-or-later
from heng_llm.gateway import LLMGateway
from heng_agent.providers import RuleBasedProvider


class Boom:
    def __init__(self, name, is_private):
        self.name = name
        self.is_private = is_private

    def available(self):
        return True

    def plan(self, utterance, context):
        raise RuntimeError("provider down")


CTX = {"clock": "2026-11-23", "partners": [], "skus": [], "price_lookup": {}, "tools": []}


def test_degrade_to_rule_template():
    gw = LLMGateway([Boom("cloud", False), RuleBasedProvider()], offline=False)
    out, meta = gw.plan("给华东李总发200台A100", CTX)
    assert meta["model_id"] == "rule-template-v1" and meta["degraded"]


def test_sensitive_never_cloud():
    gw = LLMGateway([Boom("cloud", False), RuleBasedProvider()], offline=False)
    out, meta = gw.plan("查工资", CTX, sensitive=True)
    assert meta["model_id"] == "rule-template-v1" and not meta["degraded"]


def test_all_down_escalates_to_human():
    gw = LLMGateway([Boom("private", True)], offline=True)
    out, meta = gw.plan("任意指令", CTX)
    assert out["plan"]["confidence"] == 0.0
    assert "人工" in out["plan"]["clarify"]
