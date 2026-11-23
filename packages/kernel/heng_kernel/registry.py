# SPDX-License-Identifier: LGPL-3.0-or-later
"""元数据注册中心（§3.1/3.2/8）：实体/动作皆声明式 YAML，版本化。
M0：从磁盘加载官方包；租户覆盖层与热加载在 M1（双缓冲切换）。"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class EntitySpec:
    name: str
    version: int
    spec: dict


@dataclass
class ActionSpec:
    name: str
    version: int
    label: str
    description: str
    input_schema: dict
    preconditions: list[str]
    postconditions: list[str]
    effects: list
    risk_level: str            # low / medium / high / critical
    scopes: list[str]
    automation: dict
    evals: list[str]
    compensation: str | None
    handler: str
    raw: dict = field(default_factory=dict)

    @property
    def required_approvals(self) -> int:
        return {"low": 0, "medium": 1, "high": 1, "critical": 2}[self.risk_level]


class MetadataRegistry:
    def __init__(self):
        self.entities: dict[str, EntitySpec] = {}
        self.actions: dict[str, ActionSpec] = {}

    def load_dir(self, path: str | Path):
        path = Path(path)
        for f in sorted((path / "entities").glob("*.yaml")):
            spec = yaml.safe_load(f.read_text(encoding="utf-8"))
            self.entities[spec["entity"]] = EntitySpec(spec["entity"], spec.get("version", 1), spec)
        for f in sorted((path / "actions").glob("*.yaml")):
            spec = yaml.safe_load(f.read_text(encoding="utf-8"))
            a = ActionSpec(
                name=spec["action"], version=spec.get("version", 1),
                label=spec.get("label", spec["action"]),
                description=spec.get("description", ""),
                input_schema=spec.get("input_schema", {"type": "object"}),
                preconditions=spec.get("preconditions", []),
                postconditions=spec.get("postconditions", []),
                effects=spec.get("effects", []),
                risk_level=spec.get("risk_level", "medium"),
                scopes=spec.get("scopes", []),
                automation=spec.get("automation", {"max_level": "L1"}),
                evals=spec.get("evals", []),
                compensation=spec.get("compensation"),
                handler=spec.get("handler", spec["action"].replace(".", "_")),
                raw=spec,
            )
            if not a.evals and a.risk_level != "low":
                raise ValueError(f"action {a.name}: 无评测集不得注册（CI 强制，§3.2）")
            self.actions[a.name] = a
        return self

    def load_overlay_dir(self, path: str | Path):
        """扩展包：实体以 mode=extend 叠加字段（禁改官方字段）；动作按正常规则注册。"""
        path = Path(path)
        ent_dir = path / "entities"
        if ent_dir.exists():
            for f in sorted(ent_dir.glob("*.yaml")):
                spec = yaml.safe_load(f.read_text(encoding="utf-8"))
                base = self.entities.get(spec["entity"])
                if base is None:
                    raise ValueError(f"overlay 目标实体不存在: {spec['entity']}")
                if spec.get("mode") != "extend":
                    raise ValueError("扩展包实体必须声明 mode: extend（禁改官方定义）")
                for fname, fspec in (spec.get("fields") or {}).items():
                    if fname in base.spec.get("fields", {}):
                        raise ValueError(f"禁止覆盖官方字段: {spec['entity']}.{fname}")
                    fspec = dict(fspec)
                    fspec["x_extension"] = True
                    base.spec.setdefault("fields", {})[fname] = fspec
        act_dir = path / "actions"
        if act_dir.exists():
            for f in sorted(act_dir.glob("*.yaml")):
                spec = yaml.safe_load(f.read_text(encoding="utf-8"))
                a = ActionSpec(
                    name=spec["action"], version=spec.get("version", 1),
                    label=spec.get("label", spec["action"]),
                    description=spec.get("description", ""),
                    input_schema=spec.get("input_schema", {"type": "object"}),
                    preconditions=spec.get("preconditions", []),
                    postconditions=spec.get("postconditions", []),
                    effects=spec.get("effects", []),
                    risk_level=spec.get("risk_level", "medium"),
                    scopes=spec.get("scopes", []),
                    automation=spec.get("automation", {"max_level": "L1"}),
                    evals=spec.get("evals", []),
                    compensation=spec.get("compensation"),
                    handler=spec.get("handler", spec["action"].replace(".", "_")),
                    raw=spec)
                if not a.evals and a.risk_level != "low":
                    raise ValueError(f"action {a.name}: 无评测集不得注册")
                self.actions[a.name] = a
        return self

    def get_action(self, name: str) -> ActionSpec:
        if name not in self.actions:
            raise KeyError(name)
        return self.actions[name]

    def tool_catalog(self, names: list[str] | None = None) -> list[dict]:
        """给 LLM 的精简工具描述（完整 schema 在网关强制校验，§5.1）。"""
        out = []
        for name, a in self.actions.items():
            if names is not None and name not in names:
                continue
            props = a.input_schema.get("properties", {})
            out.append({
                "name": name, "label": a.label, "description": a.description,
                "risk": a.risk_level,
                "params": {k: v.get("type", "any") for k, v in props.items()},
                "required": a.input_schema.get("required", []),
            })
        return out
