"""合规规则引擎（§4.2）：规则包版本化 + 生效日期；历史单据按业务日期命中当年口径。
纯函数、可单测；LLM 不参与裁决。"""
from __future__ import annotations
import datetime as dt
from decimal import Decimal
from pathlib import Path
import yaml
from heng_contracts.models import D, q2


class RuleSet:
    def __init__(self, spec: dict):
        self.name = spec["name"]
        self.version = str(spec["version"])
        self.effective_from = dt.date.fromisoformat(str(spec["effective_from"]))
        self.effective_to = dt.date.fromisoformat(str(spec["effective_to"]))
        self.rates = {k: D(v) for k, v in spec.get("rates", {}).items()}
        self.spec = spec

    def covers(self, date: dt.date) -> bool:
        return self.effective_from <= date < self.effective_to


class RuleEngine:
    def __init__(self):
        self.sets: list[RuleSet] = []

    def load_dir(self, path: str | Path):
        for f in sorted(Path(path).glob("*.yaml")):
            self.sets.append(RuleSet(yaml.safe_load(f.read_text(encoding="utf-8"))))
        return self

    def resolve(self, name: str, date: dt.date) -> RuleSet:
        candidates = [s for s in self.sets if s.name == name and s.covers(date)]
        if not candidates:
            raise LookupError(f"no ruleset {name} effective at {date}")
        return sorted(candidates, key=lambda s: (s.effective_from, s.version))[-1]

    def rate_for(self, category: str, date: dt.date, ruleset: str = "tax_cn") -> Decimal:
        rs = self.resolve(ruleset, date)
        if category not in rs.rates:
            raise LookupError(f"unknown tax category: {category}")
        return rs.rates[category]

    def ruleset_version(self, date: dt.date, ruleset: str = "tax_cn") -> str:
        rs = self.resolve(ruleset, date)
        return f"{rs.name}@{rs.version}"

    @staticmethod
    def split_tax(total_included: Decimal, rate: Decimal) -> tuple[Decimal, Decimal]:
        """价税分离：不含税 = round(含税/(1+税率),2)；税 = 含税 - 不含税（保证勾稽）。"""
        total_included = q2(total_included)
        if rate == 0:
            return total_included, D("0")
        amount = q2(total_included / (D("1") + rate))
        tax = q2(total_included - amount)
        return amount, tax

    @staticmethod
    def tax_from_untaxed(amount: Decimal, rate: Decimal) -> Decimal:
        return q2(q2(amount) * rate)
