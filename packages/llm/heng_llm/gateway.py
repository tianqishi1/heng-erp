"""LLM 网关（§7）：多供应商路由 + 降级链 + 成本计量。
降级链：云大模型 → 私有小模型 → 规则/模板应答 → 纯人工 UI。
敏感任务强制私有；M0 默认离线（规则模板兜底），配置 HENG_LLM_BASE_URL/KEY 后启用云模型。"""
from __future__ import annotations
import json
import os
from typing import Protocol


class PlanProvider(Protocol):
    name: str
    is_private: bool

    def available(self) -> bool: ...
    def plan(self, utterance: str, context: dict) -> dict: ...


class OpenAICompatProvider:
    """云/私有 vLLM 的 OpenAI 兼容接入（M0 代码就位，默认不启用）。"""
    def __init__(self, name: str, base_url: str, api_key: str, model: str, is_private: bool):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.is_private = is_private

    def available(self) -> bool:
        return bool(self.base_url and self.api_key)

    def plan(self, utterance: str, context: dict) -> dict:
        import httpx
        tools = context.get("tools", [])
        sys = ("你是 ERP 业务编排器。只输出 JSON：{goal, steps:[{tool,args,on_fail}], confidence, clarify}。"
               "tool 必须来自工具目录；资金/发票类动作不得出现在计划中；不确定时 confidence<0.7 并给 clarify。"
               f"工具目录: {json.dumps(tools, ensure_ascii=False)}")
        r = httpx.post(f"{self.base_url}/chat/completions",
                       headers={"Authorization": f"Bearer {self.api_key}"},
                       json={"model": self.model, "temperature": 0,
                             "response_format": {"type": "json_object"},
                             "messages": [{"role": "system", "content": sys},
                                          {"role": "user", "content": utterance}]},
                       timeout=20)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return {"plan": json.loads(content), "evidence": []}


class LLMGateway:
    def __init__(self, providers: list[PlanProvider], offline: bool | None = None):
        self.providers = providers
        self.offline = (os.environ.get("HENG_LLM_OFFLINE", "1") == "1") if offline is None else offline
        self.degradations: list[dict] = []

    def plan(self, utterance: str, context: dict, sensitive: bool = False) -> tuple[dict, dict]:
        chain = [p for p in self.providers if p.available()]
        if self.offline:
            chain = [p for p in chain if p.is_private]
        if sensitive:
            chain = [p for p in chain if p.is_private]  # 硬规则：敏感不出域，绝不降级到云
        last_err = None
        for level, p in enumerate(chain):
            try:
                out = p.plan(utterance, context)
                tokens = (len(utterance) + len(json.dumps(context, ensure_ascii=False, default=str))) // 2
                meta = {"model_id": p.name, "tokens": tokens, "degraded": level > 0,
                        "level": level, "sensitive": sensitive}
                if level > 0:
                    self.degradations.append({"to": p.name, "reason": str(last_err)})
                return out, meta
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
        # 全链路失效 → 纯人工（调用方收到空计划+升级标记）
        return {"plan": {"goal": utterance, "steps": [], "confidence": 0.0,
                         "clarify": "AI 服务不可用，已转人工处理，请使用工作台表单。"},
                "evidence": []}, {"model_id": "none", "tokens": 0, "degraded": True,
                                  "level": len(chain), "sensitive": sensitive}
