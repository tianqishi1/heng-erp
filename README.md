# 衡 ERP（HengERP）— AI 原生开源 ERP · M1


> **生态**：底座 [agent-base](https://github.com/tianqishi1/agent-base) · 评测 [eval-pipeline](https://github.com/tianqishi1/eval-pipeline) · 技能 [marketing-skills-cn](https://github.com/tianqishi1/marketing-skills-cn)

> 铁律：**LLM 永不直接写账**；人/AI/ISV/MCP 的一切写操作收敛到同一条确定性 Action 管线。
> 设计文档见会话工作区《AI原生ERP产品设计方案》《AI原生ERP技术实现方案》；能力/风险见 docs/能力与风险清单.md。

## 快速开始

```powershell
cd heng-erp
uv venv .venv --python 3.12
uv pip install -e ".[dev]" --python .venv\Scripts\python.exe
.venv\Scripts\python.exe -m pytest                      # 44 项测试
.venv\Scripts\python.exe -m heng_eval run evals         # 17 个黄金评测（CI 门禁）
.venv\Scripts\python.exe scripts\demo.py                # 业务闭环演示
.venv\Scripts\python.exe -m uvicorn heng_api.main:app --port 8765
.venv\Scripts\python.exe scripts\smoke_api.py           # 对运行中服务做 M1 冒烟
```

浏览器打开 http://localhost:8765 ，演示账号：
`chen/boss123`（老板）· `wang/fin123`（财务）· `au_a、au_b/approve123`（审批人，四眼收款用）

## M1 已实现

| 目标 | 落地 |
|---|---|
| 确定性内核 | 11 步 Action 管线；安全表达式引擎；税率规则包生效日期；不变量断言服务 |
| 业务闭环 | 订单创建/确认/取消红冲 → 出库 → 凭证 → 数电票(沙箱) → 四眼收款 → 语义问数 |
| 认证与权限 | 登录会话+四角色；审批人=登录账号；进化审批/扩展安装仅老板 |
| Agent | 确定性编排器；工具白名单+scope 交集；L1 行动卡/L2 条件自动；决策 Lineage |
| **自进化 alpha** | 案例挖掘→候选生成→沙箱黄金集零退化门禁→进化单(人审)→租户灰度→全量/回滚；周限额+72h 冷却；只进化话术资产 |
| **扩展包机制** | 元数据 overlay（禁改官方字段）+通用处理器+强制评测集；示例 trade_priority |
| 可观测 | 审计/Outbox/Lineage/成本计量/账务体检 |
| 可评测 | 沙箱工厂+场景 DSL+17 黄金用例（含进化门禁与扩展包用例） |
| 高可用/健壮 | 降级链（云→私有→规则模板→人工）；幂等；Decimal；注入防御；复合主键多租户 |

## 自进化演示（3 分钟）

1. 工作台说：`帮王总订50个B200，单价1200` → 助手听不懂，转人工（自动留案例）
2. 进化中心 → 挖掘优化点 → 生成候选（沙箱跑 17/17 评测）→ 批准并灰度
3. 回工作台再说一遍 → 正常开单；可全量发布或一键回滚

## M0/M1 替代项（生产路径）

SQLite↔PG+RLS（deploy/pg_rls.sql）· 进程内总线↔NATS · 同步编排↔Temporal ·
规则模板↔vLLM/云模型（配 HENG_LLM_BASE_URL/KEY 启用）· 沙箱发票↔持牌服务商 ·
演示工作台↔React+AntD 元数据渲染器 · SHA256↔Argon2/SSO

## 下一步（M2）

PG+RLS 与方言 CI · Temporal 月结长任务 · MCP Server · LLM judge 校准与轮换保留集 ·
知识/路由/记忆进化 · ISV 市场与签名审核 · React 元数据 UI · 数电票真实通道

## 评测方法论外壳（兄弟项目）

eval-pipeline（衡评 EvalPipe）：归因/回流/门禁/看板/M2 钩子；模型类 badcase 可导出为 HengERP 进化中心案例（source=eval_backflow）。

## 许可与贡献
- 许可：**LGPL-3.0-or-later**（开放核心边界见 [LICENSING.md](LICENSING.md)；企业版预留 enterprise/）
- 贡献：[CONTRIBUTING.md](CONTRIBUTING.md)（DCO sign-off + 双门禁）· [行为准则](CODE_OF_CONDUCT.md) · [安全政策](SECURITY.md)
- 文档索引：[产品设计方案](docs/AI原生ERP产品设计方案.md) · [技术实现方案](docs/AI原生ERP技术实现方案.md) ·
  [演示指南](docs/DEMO_GUIDE.md) · [路线图](docs/ROADMAP.md) · [能力与风险清单](docs/能力与风险清单.md)
- 评测方法论兄弟仓库：[eval-pipeline](https://github.com/tianqishi1/eval-pipeline)（Apache-2.0）
