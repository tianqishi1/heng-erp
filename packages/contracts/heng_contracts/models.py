# SPDX-License-Identifier: LGPL-3.0-or-later
"""HengERP contracts v1 (设计文档 §3). 一切扩展与评测的锚点。"""
from __future__ import annotations
from decimal import Decimal
from typing import Any, Literal
from pydantic import BaseModel, Field
import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class ActorRef(BaseModel):
    type: Literal["human", "agent", "isv", "mcp_external"]
    id: str
    tenant_id: str
    scopes: list[str] = Field(default_factory=list)


class EvidenceRef(BaseModel):
    kind: Literal["doc", "rule", "retrieval", "history", "metric"]
    ref: str
    summary: str = ""


class Budget(BaseModel):
    max_tokens: int = 20000
    max_cost_cny: float = 2.0
    max_seconds: int = 60
    max_steps: int = 12


class Violation(BaseModel):
    code: str
    message: str
    field: str | None = None


class ActionRequest(BaseModel):
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    client_request_id: str = Field(default_factory=lambda: new_id("req"))
    actor: ActorRef
    on_behalf_of: str | None = None
    trace_id: str = Field(default_factory=lambda: new_id("trace"))
    evidence: list[EvidenceRef] = Field(default_factory=list)
    approval_id: str | None = None  # 审批通过后回填执行


class ActionResult(BaseModel):
    status: Literal["ok", "rejected", "pending_approval", "failed"]
    data: dict[str, Any] | None = None
    effects_preview: dict[str, Any] | None = None
    violations: list[Violation] = Field(default_factory=list)
    approval_id: str | None = None
    rollback_token: str | None = None
    audit_id: str | None = None


class PlanStep(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    expect: list[str] = Field(default_factory=list)
    on_fail: Literal["abort", "escalate", "compensate"] = "escalate"
    max_retries: int = 1


class AgentPlan(BaseModel):
    goal: str
    steps: list[PlanStep] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    confidence: float = 1.0
    clarify: str | None = None  # 低置信时向用户澄清的问题


class DomainEvent(BaseModel):
    type: str
    tenant_id: str
    subject: str
    trace_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    heng_meta: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    approver_id: str
    decision: Literal["approve", "reject"]
    reason: str = ""


def D(x: Any) -> Decimal:
    """金额/数量一律定点小数，禁 float 运算（§12.2）。"""
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def q2(x: Any) -> Decimal:
    return D(x).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP")


def q6(x: Any) -> Decimal:
    return D(x).quantize(Decimal("0.000001"), rounding="ROUND_HALF_UP")
