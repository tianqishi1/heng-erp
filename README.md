# 衡 ERP（HengERP）— AI 原生开源 ERP · M0 骨架

> 设计文档：《AI原生ERP产品设计方案》《AI原生ERP技术实现方案》（本仓库按技术方案 §19 第一批任务落地）。
> 铁律：**LLM 永不直接写账**；人/AI/ISV/MCP 的一切写操作收敛到同一条确定性 Action 管线。

## 快速开始

```powershell
cd D:\codex\heng-erp
uv venv .venv --python 3.12
uv pip install -e ".[dev]" --python .venv\Scripts\python.exe
.venv\Scripts\python.exe -m pytest                      # 单元+集成测试
.venv\Scripts\python.exe -m heng_eval run evals         # 黄金评测集（CI 门禁）
.venv\Scripts\python.exe scripts\demo.py                # 端到端演示
.venv\Scripts\python.exe -m uvicorn heng_api.main:app --port 8000   # 工作台 http://localhost:8000
```

默认 **AI 离线**（降级链第 3 档：规则模板兜底）。配置 `HENG_LLM_BASE_URL` / `HENG_LLM_API_KEY` / `HENG_LLM_MODEL` 启用云模型；`HENG_LLM_OFFLINE=0` 关闭强制离线。敏感任务永远只走私有 Provider。

## M0 已实现（垂直切片）

| 设计目标 | 落地 |
|---|---|
| 确定性内核 | Action 管线 11 步（schema→scope→幂等→前置→风险审批→dry-run→事务→后置断言→Outbox→审计）；安全表达式引擎（白名单 AST）；税率规则包带生效日期（2019/2026 双版本可复现历史口径） |
| 业务闭环 | 销售订单创建/确认/取消（红冲补偿）→ 出库 → 收入凭证 → 数电票（沙箱）→ 四眼收款 → 指标问数 |
| Agent | 确定性状态机编排器；工具白名单+scope 交集；L1 行动卡/L2 条件自动；置信度升级；决策 Lineage 落库可回放 |
| 可观测 | 审计日志、Outbox 事件、决策 Lineage、成本计量（tokens）、不变量断言服务（试算平衡/账实/发票勾稽/订单勾稽） |
| 可评测 | 沙箱租户工厂 + 场景 DSL + 16 个黄金用例（消歧陷阱/价税分离精确断言/四眼/注入对抗/幂等/降级）；`python -m heng_eval` 即 CI 门禁 |
| 高可用 | LLM 降级链（云→私有→规则模板→纯人工）；工作台零 LLM 依赖；幂等回放；dry-run savepoint |
| 健壮性 | 金额 Decimal 定点；注入纵深（表达式白名单+工具白名单+四眼）；tenant 过滤；fail-closed 前置条件 |
| 双通道 | 通道 A 表单/列表工作台 + 通道 B Copilot 行动卡，共用 Action API |

## M0 替代项（生产路径见技术方案）

| M0 | 生产（M1+） |
|---|---|
| SQLite + 应用层 tenant 过滤 | PostgreSQL 16 + RLS（deploy/pg_rls.sql）+ Patroni |
| 进程内 EventBus + Outbox relay | NATS JetStream + 独立 relay worker |
| 同步编排器 | Temporal durable execution（断点续跑） |
| 规则模板 Provider | vLLM 私有小模型 + 云大模型路由（接口已就位） |
| 确定性 judge 代理 | LLM-as-judge + 人工校准 |
| REST execute/dry-run | + MCP Server（Action 即 tool） |
| 数电票沙箱通道 | 持牌服务商 API（百望/航信选型中） |

## 目录

```
packages/contracts   契约 v1（ActionRequest/Result、AgentPlan、事件）
packages/kernel      元数据注册中心/规则引擎/表达式/管线/不变量/Outbox
packages/semantic    主数据消歧 + 指标注册中心（口径唯一）
packages/actions     确定性业务处理器 + 组合根
packages/agent       策略/Provider/确定性编排器
packages/llm         网关（降级链/敏感路由/成本计量）
packages/eval        沙箱工厂 + 场景 DSL + 门禁 CLI
apps/api             FastAPI + 最小双通道工作台
metadata/            实体/动作 DSL（无评测集不得注册）
rulesets/            合规规则包（生效日期版本化）
evals/               黄金评测集
```

## 下一步（M1）

PG+RLS 迁移与方言 CI；Temporal 长任务（月结 checklist）；MCP Server；LLM judge 校准；
影子模式与 L2 晋级流程；进化流水线 alpha（仅 prompt 资产，评测门禁+灰度）；前端元数据渲染器。
