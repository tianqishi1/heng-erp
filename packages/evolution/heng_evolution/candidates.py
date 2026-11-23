# SPDX-License-Identifier: LGPL-3.0-or-later
"""候选生成（M1 alpha：确定性变体搜索；接真实大模型后由 LLM 起草+人审，门禁不变）。
只改话术模式资产，不碰任何确定性内核。"""
from __future__ import annotations
import re
from .assets import DEFAULT_ORDER_REGEX

PREFIX_VARIANTS = ["(?:给|帮)", "(?:给|帮|为)", "(?:给|帮|为|替)"]


def generate_candidates(utterance: str, current_regex: str):
    base = current_regex or DEFAULT_ORDER_REGEX
    for variant in PREFIX_VARIANTS:
        cand = re.sub(r"^给", variant, base)
        if cand == base:
            continue
        try:
            if re.search(cand, utterance):
                yield cand
        except re.error:
            continue
