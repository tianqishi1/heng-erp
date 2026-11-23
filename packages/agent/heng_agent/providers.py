"""规则/模板 Provider：降级链的确定性兜底（也是 M0 离线默认）。
它不是"假 AI"，而是降级链第 3 档的真实实现：可预测、可评测、零成本。"""
from __future__ import annotations
import datetime as dt
import re
from heng_semantic.catalog import split_query

REGIONS = ["华东", "华南", "华北", "华中", "西南", "西北", "东北"]


class RuleBasedProvider:
    name = "rule-template-v1"
    is_private = True

    def available(self) -> bool:
        return True

    def plan(self, utterance: str, context: dict) -> dict:
        clock = dt.date.fromisoformat(context["clock"])
        partners = context.get("partners", [])
        skus = context.get("skus", [])
        prices = context.get("price_lookup", {})

        # --- 场景 1：一句话开单 ---
        m = re.search(r"给\s*([^\s，。,]+?)\s*(?:发|下|订|要)\s*(\d+)\s*(?:台|个|件|套)\s*([A-Za-z0-9\-]+)", utterance)
        if m:
            who, qty, sku_q = m.group(1), int(m.group(2)), m.group(3).upper()
            sku = next((s for s in skus if sku_q == s["id"].upper() or sku_q in s["id"].upper()), None)
            if sku is None:
                return self._clarify(f"未找到商品 {sku_q}，请确认商品编码。")
            name_part, region = split_query(who)
            scored = []
            for p in partners:
                score = 0
                if name_part and name_part[0] in p["name"]:
                    score += 2
                if name_part and name_part in p["name"]:
                    score += 2
                if region and p["region"] == region:
                    score += 2
                if score > 0:
                    scored.append((score, p))
            scored.sort(key=lambda x: -x[0])
            if not scored:
                return self._clarify(f"未找到客户「{who}」，请提供客户全称或编号。")
            if len(scored) > 1 and scored[0][0] == scored[1][0]:
                names = "、".join(f"{p['name']}({p['region']})" for _, p in scored[:3])
                return self._clarify(f"找到多位匹配客户：{names}。请补充区域或客户编号。", confidence=0.4)
            partner = scored[0][1]
            key = f"{partner['id']}|{sku['id']}"
            evidence = []
            if "上次" in utterance or "上次价" in utterance:
                if key not in prices:
                    return self._clarify(f"客户 {partner['name']} 没有 {sku['id']} 的历史成交价，请指定单价。")
                price = float(prices[key])
                evidence.append({"kind": "history", "ref": f"price_history:{key}",
                                 "summary": f"上次成交价 {prices[key]}（含税）"})
            else:
                pm = re.search(r"单价\s*(\d+(?:\.\d+)?)", utterance)
                price = float(pm.group(1)) if pm else float(sku.get("ref_price", 0))
                evidence.append({"kind": "doc", "ref": f"sku:{sku['id']}",
                                 "summary": f"商品参考价 {price}"})
            included = "不含税" not in utterance
            return {"plan": {
                "goal": f"为 {partner['name']} 创建 {qty} 台 {sku['id']} 的销售订单草稿",
                "steps": [{"tool": "sales.order.create",
                           "args": {"customer_id": partner["id"],
                                    "lines": [{"sku_id": sku["id"], "qty": qty,
                                               "price": price, "tax_included": included}]},
                           "on_fail": "escalate",
                           "expect": ["data.total_amount == data.total_untaxed + data.total_tax"]}],
                "confidence": 0.95, "clarify": None}, "evidence": evidence}

        # --- 场景 2：指标问数 ---
        if re.search(r"(收入|营收)", utterance):
            region = next((r for r in REGIONS if r in utterance), None)
            period = None
            pm = re.search(r"(\d{4})-(\d{2})", utterance)
            if pm:
                period = f"{pm.group(1)}-{pm.group(2)}"
            elif "上月" in utterance:
                first = clock.replace(day=1)
                last_month = first - dt.timedelta(days=1)
                period = last_month.strftime("%Y-%m")
            elif "本月" in utterance:
                period = clock.strftime("%Y-%m")
            filters = {}
            if region:
                filters["region"] = region
            if period:
                filters["period"] = period
            return {"plan": {"goal": "查询营业收入",
                             "steps": [{"tool": "metrics.query",
                                        "args": {"metric": "revenue", "filters": filters},
                                        "on_fail": "escalate", "expect": []}],
                             "confidence": 0.9, "clarify": None},
                    "evidence": [{"kind": "metric", "ref": "metric:revenue",
                                  "summary": "口径：已确认订单不含税金额"}]}

        # --- 其余：不猜，转人工 ---
        return self._clarify("暂不支持该指令。您可以说：给华东李总发200台A100，按上次的价格，含税；或：上月华东区收入多少。",
                             confidence=0.3)

    @staticmethod
    def _clarify(text: str, confidence: float = 0.5) -> dict:
        return {"plan": {"goal": "", "steps": [], "confidence": confidence, "clarify": text},
                "evidence": []}
