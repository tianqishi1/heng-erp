# SPDX-License-Identifier: LGPL-3.0-or-later
"""Agent 策略（§3.2/6）：工具白名单 + scope + 置信阈值。
资金支付/红字发票永远不在任何 Agent 工具白名单内（四眼人类专属）。"""
AGENTS = {
    "order_clerk": {
        "label": "开单员",
        "tools": ["sales.order.create", "catalog.search", "metrics.query"],
        "scopes": ["sales.order.write", "catalog.read", "metrics.read"],
        "prompt_version": "rulebased-v1",
        "confidence_threshold": 0.7,
    },
}
NEVER_AGENT_TOOLS = {"payment.execute", "tax.invoice.issue", "finance.journal.post"}
