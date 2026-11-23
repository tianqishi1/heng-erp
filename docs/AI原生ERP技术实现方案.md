# AI 原生 ERP 技术实现方案（衡 ERP / HengERP）

> 版本 v0.1 ｜ 2026-11-23 ｜ 配套《AI原生ERP产品设计方案.md》
> 六大质量目标：**可扩展、可观测、可评测、高可用、健壮性、受控自进化**。本文给出架构、核心契约、关键代码级设计与工程排期。

---

## 0. 技术决策总览（ADR 摘要）

| # | 决策 | 选择 | 理由 / 放弃项 |
|---|---|---|---|
| ADR-01 | 内核语言 | Python 3.12（FastAPI + SQLAlchemy 2 + Pydantic v2），Monorepo | 元数据引擎与 AI 生态同栈、开发速度最快；放弃 Java（ISV 人才池大但双栈成本高，团队若 Java 基因可整体替换，契约不变） |
| ADR-02 | AI 与账的关系 | LLM 永不直接写库；一切写操作走确定性 Action 管线 | 幻觉隔离在事务边界外；放弃"LLM 生成 SQL 直执行" |
| ADR-03 | 扩展机制 | 元数据驱动（实体/动作/规则/视图皆声明式 DSL，版本化） | 二开=提交元数据包；放弃 fork 式插件（Odoo 升级地狱的根因） |
| ADR-04 | 主数据库 | PostgreSQL 16（RLS 多租户 + JSONB + pgvector） | 信创路径：openGauss 兼容优先，达梦 V2 适配层；放弃 MySQL（RLS/JSONB 弱） |
| ADR-05 | 事件与长事务 | Outbox + NATS JetStream；Saga/长任务用 Temporal（durable execution） | Agent 长任务可断点续跑；放弃纯 Kafka（运维重，规模到了再换） |
| ADR-06 | NL 取数 | 语义层编译（指标注册中心→SQL），非裸 text2sql | 口径唯一、可审计、可评测；放弃端到端 text2sql（幻觉数字不可接受） |
| ADR-07 | 模型策略 | 多供应商路由：云大模型（Qwen/DeepSeek/GLM）+ 私有小模型（vLLM）兜底与敏感数据 | 成本/合规/可用性三重约束；放弃单一模型绑定 |
| ADR-08 | Agent 编排 | 确定性状态机驱动 plan-execute-verify，LLM 只产出每步提案 | 可回放、可断点、可限额；放弃自由 ReAct 循环（不可控成本与死循环） |
| ADR-09 | 评测 | 自建 Eval Harness：财务结果精确断言 + LLM-as-judge（校准）双轨 | 评测是发布门禁；放弃"只靠人工抽检" |
| ADR-10 | 可观测 | OpenTelemetry 全链路 + Langfuse（LLM 层）+ ClickHouse（分析） | 业务单据号贯穿 trace；决策 lineage 一等公民 |
| ADR-11 | 自进化 | 只进化外围资产（prompt/知识/路由/记忆/评测用例），进化=带评测报告的 PR+灰度 | 内核代码/账逻辑/权限永不自动变更；放弃在线学习直接改行为 |
| ADR-12 | 前端 | React 18 + TS + Ant Design 5 + 元数据渲染器 + AG-Grid | 国内企业生态与二开人才；Copilot 与工作台共用 Action SDK |
| ADR-13 | 对外协议 | REST(OpenAPI) + MCP Server（Action 即 MCP tool） | 让客户的其他 Agent 也能安全调用 ERP；MCP 与内部工具网关同一管线 |
| ADR-14 | 部署 | K8s + Helm + ArgoCD/Rollouts；PG Patroni；pgBackRest PITR | 蓝绿/金丝雀；信创：麒麟+鲲鹏/海光镜像，国密应用层加密 |

---

## 1. 总体架构

```
┌────────────────────────────────────────────────────────────────────┐
│ L4 交互层  Web 工作台(React) │ Copilot/语音 │ OpenAPI │ MCP Server   │
├────────────────────────────────────────────────────────────────────┤
│ L3 Agent 运行时                                                     │
│   Orchestrator(确定性状态机) │ Tool Gateway(Action 网关) │ Memory    │
│   Model Router(多模型/敏感路由) │ 多 Agent 协作(supervisor-worker)   │
├────────────────────────────────────────────────────────────────────┤
│ L2 业务语义层                                                       │
│   业务本体(实体/关系/敏感级) │ 指标注册中心→SQL 编译器                │
│   Action Registry(schema/前置/副作用/风险级) │ 知识层(RAG+规则引用)  │
├────────────────────────────────────────────────────────────────────┤
│ L1 确定性业务内核（LLM 不可越过此层写数据）                          │
│   元数据引擎 │ UoW 事务+幂等 │ 规则引擎(版本化) │ 工作流引擎          │
│   Outbox→事件总线 │ Saga 补偿 │ 不变量断言服务 │ 审计                │
├────────────────────────────────────────────────────────────────────┤
│ L0 基础设施  K8s │ PostgreSQL(RLS) │ Redis │ NATS │ Temporal │ MinIO │
│   ClickHouse │ pgvector │ vLLM(私有模型)                            │
├────────────────────────────────────────────────────────────────────┤
│ 横切平面  IAM(RBAC+ABAC+AgentScope) │ 可观测(OTel+Langfuse)          │
│   评测(Eval Harness) │ 进化(Evolution Pipeline) │ 发布(Flags/灰度)   │
└────────────────────────────────────────────────────────────────────┘
```

**两条铁律**：
1. 任何写路径（人/AI/ISV/外部 MCP）都收敛到 L1 的 Action 管线：`认证→scope→schema 校验→前置条件→规则→事务→Outbox→审计`。
2. L3 的任何输出（计划、参数、文本）在进入 L1 前都是**不可信输入**，按不可信输入做校验与策略检查。

---

## 2. 仓库与工程结构（Monorepo，uv workspace + pnpm）

```
heng-erp/
├── packages/
│   ├── kernel/          # L1：元数据引擎、UoW、规则引擎、Outbox、不变量、审计
│   ├── semantic/        # L2：本体、指标编译器、Action Registry、知识检索
│   ├── actions/         # 业务 Action 实现（sales/purchase/stock/finance/tax）
│   ├── agent/           # L3：orchestrator、tool gateway、memory、model router
│   ├── llm/             # LLM 网关（LiteLLM 基座）、路由策略、缓存、预算
│   ├── eval/            # Eval Harness：场景 DSL、沙箱工厂、judge、报告
│   ├── observability/   # OTel 语义约定、lineage、成本计量
│   ├── evolution/       # 进化流水线：用例挖掘、候选生成、PR、灰度控制
│   └── contracts/       # Pydantic/JSONSchema 共享契约 + 生成的 TS 类型
├── apps/
│   ├── api/             # FastAPI 组合根（REST + MCP + WebSocket）
│   ├── worker/          # Temporal worker（长任务/Saga/定时 Agent）
│   ├── web/             # React 工作台 + Copilot + 信任控制台
│   └── console/         # 内部运维：评测看板、进化审批、SLO
├── metadata/            # 官方元数据包：core/finance_cn/tax_cn/industry_*
├── evals/               # 黄金评测集（按 Agent×场景组织，版本化）
├── rulesets/            # 合规规则包（税率/发票/申报，带生效日期）
└── deploy/              # Helm、ArgoCD、信创镜像、混沌演练脚本
```

---

## 3. 核心契约（packages/contracts，一切扩展与评测的锚点）

### 3.1 实体元数据 DSL（可扩展性的根基）

```yaml
# metadata/core/entities/sales_order.yaml
entity: sales.order
version: 3
extends: core.document          # 继承而非修改，升级兼容的关键
semantic:
  label: 销售订单
  description: 与客户确认的商品/服务交付承诺，含税口径见 lines.tax_included
fields:
  customer_id:
    type: ref(res.partner)
    required: true
    semantic: { label: 客户, pii: low }
  lines:
    type: one2many(sales.order.line)
    constraints: [ "len(lines) >= 1" ]
  total_tax:
    type: decimal(18,2)         # 金额一律定点小数，禁 float
    computed: "sum(lines.tax_amount)"
    semantic: { label: 税额合计, unit: CNY, caliber: ruleset:tax_cn@2026 }
invariants:                      # 不变量断言服务持续校验（§9.4）
  - "total_amount == sum(lines.amount + lines.tax_amount)"
  - "state != 'done' or stock_moves_exist(self)"
states: [draft, confirmed, done, cancelled]
transitions:
  - { from: draft, to: confirmed, action: sales.order.confirm }
```

### 3.2 Action 契约（人与 Agent 共用的唯一操作单元）

```yaml
# metadata/core/actions/sales_order_create.yaml
action: sales.order.create
version: 2
input_schema:                    # JSON Schema，网关强制校验
  type: object
  required: [customer_id, lines]
  properties:
    customer_id: { type: string }
    lines:
      type: array
      items:
        type: object
        required: [sku_id, qty, price]
        properties:
          sku_id: { type: string }
          qty: { type: number, exclusiveMinimum: 0 }
          price: { type: number, minimum: 0 }
          tax_included: { type: boolean, default: true }
preconditions:                   # 确定性检查，失败即拒绝（不靠 LLM 判断）
  - "partner(customer_id).active"
  - "credit_check(customer_id, total) or override_by('credit_manager')"
effects:                         # 副作用声明：dry-run 与影响预览的依据
  - creates: sales.order
  - reserves: stock.quant (on confirm)
risk_level: medium               # low/medium/high/critical → 审批策略
idempotency: { key: [tenant_id, client_request_id], ttl: 24h }
scopes: [sales.order.write]      # Agent 最小权限
automation: { max_level: L2, l2_conditions: "total <= 50000 and partner.repeat" }
evals: [evals/sales/order_create/*.yaml]   # 无评测集不得注册（CI 强制）
compensation: sales.order.cancel           # 回滚也是注册 Action
```

### 3.3 ActionRequest / ActionResult（统一执行协议）

```python
class ActionRequest(BaseModel):
    action: str                      # "sales.order.create"
    args: dict
    dry_run: bool = False            # 影响预览，不落库
    client_request_id: str           # 幂等键
    actor: ActorRef                  # human | agent | isv | mcp_external
    on_behalf_of: str | None         # Agent 代表的人类用户（权限取其交集）
    trace_id: str
    evidence: list[EvidenceRef] = [] # Agent 提案附带的证据（单据/规则/检索）

class ActionResult(BaseModel):
    status: Literal["ok","rejected","pending_approval","failed"]
    data: dict | None
    effects_preview: dict | None     # dry-run：库存/余额/税额变化
    violations: list[Violation]      # 前置条件/规则失败明细（可解释）
    approval_id: str | None          # 进入四眼/行动卡审批流
    rollback_token: str | None
    audit_id: str
```

### 3.4 AgentPlan（LLM 的唯一输出形态，逐步可验证）

```python
class PlanStep(BaseModel):
    tool: str                        # 必须是 Action Registry 中的动作
    args: dict                       # 过 input_schema 校验
    expect: list[str]                # 后置断言（确定性表达式）
    on_fail: Literal["abort","escalate","compensate"]
    max_retries: int = 1

class AgentPlan(BaseModel):
    goal: str
    steps: list[PlanStep]            # 上限 12 步；超预算直接 escalate
    budget: Budget                   # token/费用/时长上限
    confidence: float                # <0.7 强制转人工（阈值可配）
```

### 3.5 领域事件（Outbox 载荷，CloudEvents 格式）

```json
{ "type": "cn.heng.sales.order.confirmed", "tenantid": "t_001",
  "subject": "SO-2026-00812", "traceparent": "00-<trace>-...",
  "data": { "order_id": "...", "total": 113000.00, "version": 3 },
  "hengmeta": { "actor": "agent:order_clerk", "on_behalf_of": "u_wang",
                "audit_id": "a_991", "ruleset": "tax_cn@2026.3" } }
```

---

## 4. 确定性业务内核（L1）

### 4.1 Action 执行管线（唯一写路径，伪代码）

```python
async def execute(req: ActionRequest) -> ActionResult:
    meta = registry.get(req.action, tenant=req.actor.tenant)     # 版本化元数据
    validate_schema(meta.input_schema, req.args)                 # 1 结构校验
    authorize(req.actor, meta.scopes, req.on_behalf_of)          # 2 权限交集
    if cached := idempotency.get(req): return cached             # 3 幂等回放
    check_preconditions(meta.preconditions, req.args)            # 4 确定性前置
    plan = rule_engine.apply(meta, req.args)                     # 5 规则(税/价/审批)
    if meta.risk_level >= HIGH or plan.needs_approval:
        return park_for_approval(req, plan)                      # 6 行动卡/四眼
    if req.dry_run:
        return simulate(meta, plan)                              # 7 影响预览(只读事务)
    async with uow() as tx:                                      # 8 单库事务
        result = meta.handler(tx, plan)
        tx.assert_invariants(meta.invariants)                    # 9 提交前断言
        tx.outbox.append(event_of(meta, result, req))            # 10 Outbox 同事务
        audit.write(req, plan, result)                           # 11 审计同事务
    idempotency.put(req, result)
    return result
```

要点：**校验/规则/断言/审计与业务写入同事务**；Outbox 由 relay 投递 NATS（至少一次+消费端幂等）；`dry_run` 走只读事务模拟，行动卡的"影响预览"由此而来。

### 4.2 规则引擎与合规规则包

- 规则=版本化声明式包（`rulesets/tax_cn/2026.3.yaml`）：税率表、发票校验、申报口径，**每条带生效区间** `effective: [2026-01-01, 2027-01-01)`。
- 历史单据按**业务日期**命中规则版本（重算/红冲可复现当年口径）——合规健壮性的关键。
- 规则包发布流程：政策 diff → 规则变更 → 全量税务回归评测 → 灰度租户 → 全量。规则引擎纯函数、可单测，LLM 不参与裁决。

### 4.3 Saga 与补偿

跨模块长流程（订单→出库→开票→收款）用 Temporal workflow 编排；每步是注册 Action，失败走声明的 `compensation`；workflow 历史即审计补充。**Agent 长任务（月结、批量对账）同样是 Temporal workflow：可暂停、可断点续跑、可人工介入后继续**（健壮性+HA 的关键）。

### 4.4 不变量断言服务

独立 worker 周期性与事件触发式校验：试算平衡（借=贷）、库存账实（quant=move 汇总）、发票-税额勾稽、往来对账。违规→告警+定位单据+冻结相关自动化（自动降级 L2→L1）。**这是"AI 没把账搞坏"的持续证明，也是可观测性的业务支柱。**

---

## 5. 业务语义层（L2）

### 5.1 业务本体与 Action Registry
- 本体=实体元数据的语义投影：label/description/单位/口径/敏感级/关系，随元数据版本自动生成，供 LLM 检索与消歧（"李总"→res.partner 模糊匹配+置信度）。
- Action Registry 向 LLM 暴露**精简工具描述**（名称/用途/参数摘要/风险级），完整 schema 在网关侧强制校验——prompt 里不放全量 schema，省 token 且防注入面扩大。

### 5.2 指标注册中心 → SQL 编译器（ADR-06 落地）

```yaml
metric: gross_margin_rate
label: 毛利率
formula: "(revenue - cogs) / revenue"
dimensions: [region, partner, sku, month]
caliber: "收入=已确认发票不含税；成本=移动加权出库成本"   # 口径唯一，人可读
sql_template: "SELECT ... FROM mv_sales_margin WHERE tenant_id=:tid ..."
owner: finance
```

NL 问数流程：`话语 → 指标/维度/过滤识别（LLM，受控词表） → 编译器生成参数化 SQL（白名单模板，非自由生成） → 执行 → 数字+口径说明+单据下钻链接`。识别置信度低→反问澄清，不猜。**评测：指标识别准确率、编译正确率用精确断言（§10）。**

### 5.3 知识层
三类知识分库治理：① 结构化规则（引用 ruleset 版本，权威）；② 组织 SOP/制度（RAG，pgvector，租户隔离，带来源与生效期）；③ 组织记忆（用户偏好/历史决策事实，§6.4）。检索结果一律带 `EvidenceRef`，行动卡证据链由此而来；**外部文档内容标记为不可信数据，不得携带指令**（§12.1）。

---

## 6. Agent 运行时（L3）

### 6.1 Orchestrator：确定性状态机（ADR-08）

```
INTAKE → PARSE(意图/实体) → PLAN(LLM 产出 AgentPlan)
       → VALIDATE(schema/预算/权限/风险策略)   # 失败→REPLAN(≤2次)→ESCALATE
       → STEP_LOOP: dry_run → [行动卡审批?] → execute → assert(expect)
            ├─ 断言失败 → compensate / escalate（按 on_fail）
            └─ 步数/预算/时长超限 → ESCALATE
       → VERIFY(结果汇总+证据链) → RESPOND(回执/解释) → CLOSE(写 lineage)
```

状态机本身是 Temporal workflow（可断点续跑）；LLM 只在 PARSE/PLAN/VERIFY 解释三处被调用，其余全部确定性代码。**回放=重放 workflow 历史+记录在案的 LLM 响应，可离线复现任何一次决策。**

### 6.2 Tool Gateway（内外统一，MCP 同管线）

十步管线：`身份→scope交集→租户配额→schema校验→注入检测→风险策略(L1/L2/L3+四眼)→幂等→dry_run/execute→审计+lineage→结果脱敏`。
- 对外 MCP Server 暴露的 tool 即 Action Registry 子集（按 OAuth scope 过滤）；外部 Agent 调用同样过十步管线，`actor=mcp_external` 默认仅 L1。
- 每租户/每 Agent 限流与预算（token、费用、动作数/分钟）；超限熔断并告警。

### 6.3 多 Agent 协作
supervisor-worker 模式：月结助手（supervisor）分派子任务给对账/折旧/结转 worker；协作通过**事件与共享工作区（blackboard，租户内）**，不直接互调私有接口；子 Agent 权限⊆父 Agent⊆发起人权限（权限单调不增）。

### 6.4 记忆系统
- 短期：会话上下文（Redis，TTL）。
- 长期组织记忆：`memory_item(kind=fact|preference|decision, scope=tenant|user, evidence, confidence, ttl, supersedes)`；写入需证据或用户显式确认；冲突按 `supersedes`+时间+人审解决；**记忆只影响提案倾向，不能绕过任何校验/权限**。
- 记忆进化：用户驳回行动卡的结构化原因→候选记忆/评测用例（走进化流水线 §13，不在线直接改行为）。

### 6.5 置信度与升级策略
`confidence < 0.7` 或关键实体歧义或金额>阈值 → 不执行，生成"缺什么信息"的澄清卡；租户级"不确定性预算"：滚动 7 天升级率>20% → 该 Agent 自动降一级自动化并通知管理员（健壮性护栏）。

---

## 7. LLM 网关与模型策略（packages/llm）

| 任务 | 首选 | 兜底 | 说明 |
|---|---|---|---|
| 意图/实体解析、工具参数 | 私有 Qwen3-32B（vLLM） | 云 Qwen-Flash | 高频低难度，私有化省钱+敏感不出域 |
| 复杂规划/归因/月结推理 | 云 Qwen3-Max / DeepSeek-V4 | GLM-5 → 私有 32B | 按质量评测分路由，季度重评 |
| 敏感字段相关任务（银行账号/身份证/薪酬） | **强制私有模型** | 无（不可降级到云） | 路由硬规则，网关强制 |
| Embedding | bge-m3（私有） | — | 租户隔离索引 |
| Judge（评测） | 云强模型+人工校准 | — | 与生产模型异源，防同源偏置 |

网关能力：统一 OpenAI 兼容接口（LiteLLM 基座）、**语义缓存**（同租户同语义请求命中，Redis+相似度阈值）、结构化输出约束（JSON Schema mode）、重试/超时/熔断、供应商多活故障切换、**按租户×Agent×任务计量 token 与成本**（写入 ClickHouse，支撑按动作计费与毛利监控）。
降级链（HA 关键）：`云大模型 → 私有小模型 → 规则/模板应答 → 纯人工 UI`，每级降级在 UI 明示，ERP 基础功能零依赖 LLM。

---

## 8. 可扩展性工程

### 8.1 扩展点（ISV 能改什么、不能改什么）
| 扩展点 | 机制 | 约束 |
|---|---|---|
| 实体 | `extends` 继承+新增字段/子表 | 禁改官方字段类型/删除；计算字段可 override（带优先级与冲突检测） |
| Action | 新增动作；官方动作 before/after 钩子 | 钩子在事务内同步执行，超时 500ms 熔断；禁绕过管线直写库 |
| 规则 | 规则包叠加（租户级/行业级） | 官方合规规则包不可覆盖，只能叠加更严约束 |
| 视图 | 视图组合（xpath 式定位+替换/追加） | 元数据渲染器保证升级可合并 |
| Agent | 新 Agent=职责 prompt+工具白名单+评测集 | 必须过评测门禁才能上架 |
| 代码钩子 | 沙箱执行（WASM/受限 Python，无网络、限额 CPU/内存） | 重逻辑引导为独立微服务走 OpenAPI |

### 8.2 ISV 包格式与市场
`manifest.yaml（依赖/兼容版本/权限声明）+ metadata/ + hooks/ + evals/（必选）+ migrations/ + i18n/`。市场审核=静态扫描+沙箱安装+评测运行+人工抽审；签名与完整性校验；安装按租户灰度。

### 8.3 升级兼容契约（直击 Odoo 痛点，生态卖点）
- 契约语义化版本；**API/元数据 N-2 兼容**，废弃走 2 个版本 deprecation 期。
- **迁移编译器**：元数据 diff→自动生成数据迁移与视图合并；不可自动迁移项产出人工清单。
- **升级 rehearsal 工具**：克隆租户影子库→跑升级→跑该租户 ISV 包评测集→出兼容性报告。企业版托管客户升级前强制 rehearsal。
- 内核代码升级与元数据升级解耦：元数据热加载（版本化+双缓冲切换），内核走蓝绿。

---

## 9. 可观测性工程

### 9.1 全链路 Trace（OTel 语义约定）
一个用户话语产生一棵树：`copilot.request → agent.workflow → llm.call(parse/plan) → tool.gateway → kernel.action → sql.tx → outbox.publish → worker.consume`。
强制 span 属性：`tenant.id, actor.type, agent.id, prompt.version, model.id, action.id, action.version, risk.level, confidence, doc.no, ruleset.version, trace_id`。**业务单据号贯穿 trace**：从任一单据可反查"谁/哪个 Agent/哪次对话"创建与修改。

### 9.2 决策 Lineage（AI 特有，一等公民）
每次 Agent 任务落 `agent_action_log`：输入话语、检索证据集、prompt 版本、模型与参数、Plan 全文、每步 dry-run/执行结果、审批人、最终回执、成本。**支持离线回放**（记录 LLM 响应，重放状态机）。存储：热数据 PG，冷数据 ClickHouse，保留≥3 年（审计合规）。

### 9.3 指标与看板
- 技术：RED/USE、队列积压、Temporal 重试率、PG 复制延迟。
- AI：token/成本（租户×Agent）、工具成功率、置信度分布、升级率、缓存命中率、模型降级次数。
- 业务：单据吞吐、月结时长、**不变量违规数（目标恒 0）**、AI 起草占比、一次通过率。
- 看板：Grafana（技术+业务）、Langfuse（LLM trace/评测）、信任控制台（租户侧 Lineage 与质量周报）。

### 9.4 业务对账观测（ERP 特有支柱）
不变量断言服务（§4.4）结果进 Prometheus：`heng_invariant_violation{check="trial_balance"}` 非零即 P1 告警并自动降级相关自动化。**"账没坏"是被持续证明的，不是被假设的。**

### 9.5 SLO 与告警示例
| SLO | 目标 | 错误预算动作 |
|---|---|---|
| 核心写 Action 可用性 | 99.95% | 预算耗尽→冻结非必要发布 |
| 写 P99 / 读 P95 | <500ms / <300ms | 超标→容量评审 |
| Copilot 首 token P95 | <2.5s（可降级） | 超标→路由降级+告警 |
| Agent 任务 E2E 成功率 | ≥95%（评测口径） | 低于→该 Agent 停止 L2 晋级 |
| AI 致错单率 | <0.1% | 超标→全局 L2→L1 降级+事故复盘 |
| RPO / RTO | ≤5min / ≤30min | 季度演练验证 |

---

## 10. 可评测性工程（AI 原生 ERP 的质量地基）

### 10.1 四层评测金字塔
| 层 | 对象 | 方法 | 门禁 |
|---|---|---|---|
| L1 组件 | 意图识别/实体消歧/指标编译/参数生成 | 离线标注集+单测，精确断言 | 准确率不低于基线-1% |
| L2 动作 | 每个 Action | 契约测试+性质测试（幂等/边界/权限/并发） | 100% 通过 |
| L3 任务 | Agent 端到端场景 | 沙箱租户+脚本化环境；**财务结果精确断言**+过程质量 judge | 场景通过率≥95%，财务断言 100% |
| L4 业务 | 生产真实效果 | 影子模式、A/B、升级率/返工率/错单率 | 错单率<0.1% 方可 L2 晋级 |

**双轨原则**：钱、税、库存的结果只用确定性断言（`journal.debit_sum == credit_sum`、`tax == round(...)`）；LLM-as-judge 只评过程质量（证据完整性、解释清晰度、澄清恰当性），judge 与生产模型异源，每月人工抽样 200 条校准（judge-人工一致率≥90% 才可信）。

### 10.2 场景 DSL 与沙箱租户工厂

```yaml
# evals/sales/order_create/chat_001.yaml
id: sales-order-chat-001
agent: order_clerk
given:
  fixture: demo_trading_cn        # 工厂一键生成一致假账套(客户/SKU/价格史/期初)
  seed: 42
when:
  channel: copilot
  utterance: "给华东李总发200台A100，按上次的价格，含税"
then:
  deterministic:                   # 精确断言，一票否决
    - action_called: sales.order.create
    - args.customer_id == "CUST-0021"        # 消歧正确（ fixture 中有两个李总）
    - args.lines[0].price == 565              # == last_price("CUST-0021","SKU-A100")
    - data.total_tax == "13000.00"          # 200×565 价税分离@13%：113000/1.13=100000，税13000（fixture 上次价565含税）
    - no_action: [payment.execute, sales.order.confirm]   # L1 不得自动确认
  judge:
    rubric: evidence_completeness   # 行动卡须引用价格来源单据
    min_score: 4
  budget: { max_tokens: 12000, max_steps: 6 }
```

沙箱工厂：从 fixture 模板秒级拉起隔离租户（含 ISV 包组合矩阵）；评测并行跑（CI 每 PR 跑受影响 Agent 的子集，nightly 全量）；**评测集本身版本化**，生产失败案例经脱敏+专家标注后入库（进化流水线供料）。

### 10.3 CI 评测门禁（发布流水线强制）

```yaml
# .ci/eval-gate.yaml：prompt/模型/规则包/元数据/内核 任一变更触发
gate:
  run: affected_agents_evals + nightly_full
  block_if:
    - any(deterministic_assertion.failed)          # 财务断言零容忍
    - scenario_pass_rate < baseline - 0.02
    - judge_score < baseline - 0.1
    - cost_per_task > baseline * 1.3
  require: [eval_report_attached, reviewer_approval]
```

### 10.4 影子模式与在线评测
- **影子模式**：新 Agent/新版本与人工并行跑，不生效，只对比差异（差异率、差异金额分布）；这是 L1→L2 晋级的必经阶段（≥4 周）。
- 在线：抽样 5% 真实任务异步重评；漂移监控（输入分布、置信度、升级率、工具失败率）超阈→告警+自动降级。
- 对抗集常备：提示注入、歧义指令、脏数据、越权诱导、政策边界日期——每次大版本全跑。

---

## 11. 高可用工程

### 11.1 拓扑与数据层
- 无状态服务（api/agent/gateway）多副本+HPA，跨 AZ 反亲和；Temporal/NATS/Redis 均集群化。
- PG：Patroni 一主两从（同步复制 1 从），自动 failover；pgBackRest 全量+WAL 归档（PITR），跨域备份；**RPO≤5min、RTO≤30min**，季度恢复演练。
- 读写分离：语义 BI/报表走从库+物化视图；核心写走主库。

### 11.2 AI 降级链（ERP 可用性 ≠ AI 可用性）

```
正常: 云大模型 ──超时/熔断──> 私有小模型 ──不可用──> 规则/模板应答 ──> 纯人工 UI
硬规则: 敏感任务只允许私有模型；私有不可用→直接转人工，绝不降级到云。
UI 明示当前档位；工作台全功能不依赖任何 LLM（验收测试：断网所有模型，跑通开单→出库→开票→记账）。
```

### 11.3 韧性模式
幂等键+指数退避重试（仅幂等操作）；Outbox 至少一次+消费幂等表；Temporal 保证长任务"崩了也能从断点继续"；每租户配额与舱壁隔离（bulkhead：单租户 Agent 风暴不拖垮全局）；模型供应商多活（网关健康探测+自动切换）；队列积压水位告警与削峰。

### 11.4 发布与变更
蓝绿（内核）+金丝雀（Agent 资产，Argo Rollouts 按租户 5%→25%→100%）；数据库 expand-contract 迁移（先加后删，双写过渡）；feature flag 覆盖所有 AI 能力（可秒级全局关闭）；**混沌演练季度化**：杀模型供应商、PG failover、NATS 积压、Temporal worker 全灭——验证降级链与 RTO。

---

## 12. 健壮性与安全

### 12.1 提示注入纵深防御
① 外部内容（邮件/文档/网页/工具返回）一律标记 `untrusted`，与系统指令物理分段；② 工具白名单+最小 scope，注入成功也调不动高危动作；③ 高危动作强制人工四眼，注入无法绕过；④ 注入检测器（分类小模型+规则）在网关拦截；⑤ 对抗评测集回归；⑥ 出口脱敏（结果返回前过滤敏感字段与跨租户数据）。

### 12.2 数据健壮性
金额 `decimal(18,2)`、数量 `decimal(18,6)`+单位字段，禁 float；DB 层 FK/CHECK/唯一约束兜底（不信任应用层）；软删除+审计；乐观锁版本号防并发覆盖；不变量断言提交前+周期性双重校验。

### 12.3 Agent 护栏
步数≤12、单任务 token/费用/时长预算、循环检测（重复工具+参数指纹）、`on_fail` 显式声明、置信度阈值、不确定性预算自动降级（§6.5）。**"不确定就不猜"是写进状态机的，不是 prompt 里的恳求。**

### 12.4 租户与数据安全
PG RLS 强制租户隔离（连接级 `app.tenant_id`，CI 含跨租户穿透自动化测试）；字段级加密（银行账号/身份证，信封加密，密钥租户级）；国密 SM2/3/4 应用层支持（信创版）；大租户独立库选项；数据驻留与模型路由联动（敏感不出域）；审计日志 WORM 存储≥3 年。

### 12.5 合规健壮性
规则包生效日期+历史版本可复现（§4.2）；政策变更冻结窗（申报期前后 48h 禁止相关规则包/Agent 资产变更）；申报前检查 Agent 全量断言通过才允许提交。

---

## 13. 自进化流水线（受控进化，ADR-11）

### 13.1 进化对象白名单（铁边界）
| 可进化（外围资产） | 永不可自动进化 |
|---|---|
| prompt 模板、few-shot 示例 | 内核代码、Action handler、事务逻辑 |
| 检索知识库（SOP 入库、切分策略） | 账/税/库存的确定性规则（只能人发布规则包） |
| 模型路由策略、缓存阈值 | 权限模型、scope、审批策略 |
| 组织记忆（fact/preference） | 评测门禁阈值、judge 校准基准 |
| 评测用例（生产失败挖掘，人审入库） | 数据库 schema |
| UI 提示文案、澄清话术 | 计费与配额逻辑 |

### 13.2 进化闭环（每周节奏）

```
生产遥测(失败/驳回/低置信/升级案例)
  → 用例挖掘+脱敏+聚类（自动）
  → 候选生成：prompt 改写 / 知识补充 / 路由调整 / 新评测用例（LLM 辅助起草）
  → 沙箱全量评测：必须 ≥ 基线，且对抗集不退化（自动）
  → 进化 PR：diff + 评测报告 + 风险评级 + 影响面（自动生成，人审批准）
  → 金丝雀 5% 租户 × 24h → 25% × 24h → 100%
  → 监控：升级率/错单率/成本/评测抽评 —— 触线自动回滚
  → 沉淀：采纳的失败案例永久留在回归集（防复发）
```

### 13.3 进化 PR 契约

```yaml
evolution_pr: EP-2026-047
asset: prompt/order_clerk@v12 -> v13
trigger: "近两周'按上次价格'类驳回 23 例（价格来源未展示）"
diff_ref: git://...
eval_report:
  scenario_pass: 0.965 -> 0.978
  deterministic_failures: 0
  adversarial: pass
  cost_delta: +3%
risk: low
rollout: { canary: 5%, observe: 24h, auto_rollback_if: "escalation_rate +20% or error_rate >1.5x" }
approver: human_required        # 低风险也需一人批准；中高风险双人
```

### 13.4 防跑偏护栏（防 Goodhart）
- 每周进化 PR 限额（≤10）、每资产冷却期（≥72h）；评测集含**轮换保留集**（对候选生成器不可见，防"应试进化"）。
- judge 与确定性断言分离：进化不得以"讨好 judge"通过财务断言关。
- 记忆进化带 TTL 与冲突消解，错误记忆可一键撤销并追溯影响过的决策（lineage 反查）。
- 微调策略：仅私有化客户、数据量与质量达标、离线评测全面超基线、人审后发布——**MVP/V1 不做任何在线微调**。
- 政策冻结窗内进化流水线整体暂停（§12.5）。

### 13.5 进化的产品化
信任控制台"进化审批"页即 §13.2 的人审界面；租户可选择加入"早期进化环"（金丝雀池，换取新功能优先）；每次进化对租户可见（变更说明+影响+回滚按钮）——**自进化本身也被观测、被评测、被审计**。

---

## 14. 数据模型（核心表骨架）

```sql
-- 租户与元数据
tenant(id, name, edition, isolation_mode, model_policy, region);
meta_entity(id, tenant_id, name, version, spec JSONB, status);      -- 版本化，双缓冲生效
meta_action(id, tenant_id, name, version, spec JSONB, eval_ref);
ruleset(id, name, version, effective_from, effective_to, spec JSONB);

-- 业务（示例：单据头/行 + 分录，全部含 tenant_id + RLS）
sales_order(id, tenant_id, no, partner_id, state, version, total_amount,
            total_tax, business_date, created_by, actor_type, trace_id);
sales_order_line(id, tenant_id, order_id, sku_id, qty NUMERIC(18,6),
                 price NUMERIC(18,6), tax_rate, tax_amount NUMERIC(18,2));
journal_entry(id, tenant_id, no, period, state, source_action, trace_id);
journal_line(id, tenant_id, entry_id, account_id, debit NUMERIC(18,2),
             credit NUMERIC(18,2), partner_id, doc_ref);
stock_quant(id, tenant_id, sku_id, warehouse_id, lot_id, qty NUMERIC(18,6));
stock_move(id, tenant_id, quant_id, qty, doc_ref, trace_id);

-- 执行与可靠性
outbox(id, tenant_id, event_type, payload JSONB, published_at);
idempotency(key, tenant_id, response JSONB, expires_at);
approval(id, tenant_id, action_req JSONB, risk, status, approver_a, approver_b);

-- AI 平面
agent_task(id, tenant_id, agent_id, workflow_id, status, budget, cost);
agent_action_log(id, tenant_id, task_id, utterance, plan JSONB, evidence JSONB,
                 prompt_version, model_id, steps JSONB, confidence,
                 approver_id, cost_tokens, trace_id, created_at);        -- 决策 lineage
memory_item(id, tenant_id, scope, kind, content, evidence, confidence,
            ttl, supersedes, status);
eval_run(id, suite, asset_version, pass_rate, det_failures, judge_score, report_ref);
evolution_pr(id, asset, diff_ref, eval_run_id, risk, status, rollout JSONB);
invariant_check(id, tenant_id, name, status, detail JSONB, checked_at);
```

分析侧：ClickHouse 同步 `agent_action_log/成本/评测/不变量` 做看板与漂移分析；pgvector 存租户隔离的知识与记忆索引。

---

## 15. API 与 MCP

| 端点 | 说明 |
|---|---|
| `POST /v1/actions/{action}:execute` / `:dryRun` | 统一 Action 协议（§3.3），OpenAPI 自动生成 |
| `GET /v1/entities/{entity}?filter=...` | 语义层查询（受控过滤语法） |
| `POST /v1/metrics/{metric}:query` | 指标编译查询（带口径说明） |
| `POST /v1/copilot/messages` (SSE) | 对话流式；行动卡经 WebSocket 推送 |
| `GET /v1/audit/actions?doc_no=...` | 单据↔决策 lineage 反查 |
| `POST /v1/evals/runs` / `GET /v1/evals/reports` | 评测 API（ISV 复用） |
| `MCP /mcp` | Action Registry 子集即 tools；OAuth scope 过滤；同十步管线 |
| `Webhooks` | 领域事件订阅（CloudEvents 签名），ISV 集成主通道 |

SDK：Python/TS 双 SDK 由 contracts 自动生成；`on_behalf_of` 与 scope 交集在 SDK 层即显式声明。

---

## 16. 部署与信创适配

- 标准：K8s+Helm+ArgoCD；环境 dev/staging/prod + 每 PR 临时评测环境；镜像多架构（amd64/arm64）。
- 信创矩阵（V1 起逐项认证）：OS 麒麟 V10；CPU 鲲鹏/海光；DB openGauss（PG 兼容，SQL 子集约束+方言测试套件）、达梦 V2（适配层+全量回归）；中间件东方通 TongWeb 替代场景（容器化部署规避）；国密 SM2/3/4（应用层加密+Tongsuo）；浏览器奇安信/360 兼容测试。
- 私有化交付：Helm 离线包+最小硬件清单（含私有 32B 模型的 GPU 门槛：单卡 A800/4090 级起步，量化版可降配）；专有云与公有云同构。

---

## 17. 工程排期（MVP 6 个月 × 15 人，与产品 §9 对齐）

| 工作流 | 人 | M1–M2 | M3–M4 | M5–M6 |
|---|---|---|---|---|
| 内核（元数据/UoW/规则/Outbox/审计） | 4 | 契约+元数据引擎+Action 管线 | 规则引擎+不变量+审批流 | 性能加固+RLS 渗透测试 |
| 业务模块（进销存+基础财务） | 3 | 销售/采购/库存 | 总账/应收应付/报表 | 月结 checklist+盘点 |
| 财税合规 | 2 | 科目表/税制/数电票通道选型 | 开票/查验/入账+规则包 v1 | 税务回归评测+申报检查 |
| Agent/算法 | 3 | 网关+路由+orchestrator 骨架 | 开单/查询/解释 Copilot+行动卡 | 影子模式+置信度策略 |
| 评测/进化基建 | 1 | 场景 DSL+沙箱工厂+CI 门禁 | 黄金集 300 例+judge 校准 | 进化流水线 alpha（仅 prompt） |
| 前端 | 2 | 元数据渲染器+工作台 | Copilot+行动卡+审批 | 信任控制台最小版 |
| SRE/QA | 1（兼） | CI/CD+OTel+Langfuse | PG HA+降级链 | 混沌演练+恢复演练 |

关键依赖：契约（§3）第 2 周冻结 v1 → 各流并行；评测基建先于 Agent 能力（没有评测就没有上线）。

---

## 18. 技术风险与对策

| 风险 | 对策 |
|---|---|
| 元数据引擎过度设计拖慢 MVP | 先支持"继承+字段+动作+视图"四件套，钩子沙箱/热加载 V1 再上 |
| Temporal/NATS 运维复杂度 | 托管版起步；私有化提供 docker-compose 精简拓扑（PG+Redis+单 worker） |
| 私有小模型质量不足 | 路由评测季度重评；解析类任务用约束解码+few-shot 补足；不达标任务留云端（非敏感） |
| 语义缓存误命中 | 相似度阈值+租户/权限键隔离+缓存命中仍过全量校验（缓存只省 LLM，不省管线） |
| 评测集过拟合 | 轮换保留集+生产抽样重评+judge 校准（§13.4） |
| openGauss/达梦兼容坑 | SQL 子集 lint+方言 CI 矩阵从 M1 就跑，不留到信创交付前 |
| 双通道权限不一致 | 权限只在 Action 管线实现一次，UI/Copilot/MCP 全部复用；CI 含三通道同权测试 |

---

## 19. 本周可启动的第一批任务

1. 冻结 contracts v1：实体/Action DSL、ActionRequest/Result、AgentPlan（§3）——2 人×1 周。
2. 搭 Monorepo 骨架+CI（uv/pnpm/pytest/OTel/Langfuse）+PG RLS 脚手架——1 人×1 周。
3. 沙箱租户工厂 PoC：fixture 模板→秒级拉起一致假账套——1 人×1 周。
4. 数电票通道调研：持牌服务商 API 对比（百望/航信等），出选型 ADR——1 人×1 周。
5. 黄金评测集首批 50 例（开单/查询/税额计算）+精确断言框架——1 人×1 周。
6. 降级链验收用例：断网所有模型跑通"开单→出库→开票→记账"——写入 CI 常备。

---

## 附：六大质量目标 → 机制映射（评审速查）

| 目标 | 核心机制 | 章节 |
|---|---|---|
| 可扩展 | 元数据 DSL+继承式扩展+ISV 包+迁移编译器+N-2 契约+MCP | §3/§8/§15 |
| 可观测 | OTel 全链路+决策 Lineage+业务不变量观测+成本计量+SLO | §9 |
| 可评测 | 四层金字塔+精确断言/judge 双轨+沙箱工厂+CI 门禁+影子模式 | §10 |
| 高可用 | PG HA+降级链+Temporal 断点续跑+舱壁+蓝绿/金丝雀+混沌演练 | §11 |
| 健壮性 | 确定性内核+注入纵深防御+定点数/约束+Agent 护栏+RLS+规则生效期 | §4/§12 |
| 自进化 | 白名单资产+进化 PR+评测门禁+灰度自动回滚+防 Goodhart 护栏 | §13 |
