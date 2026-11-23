# 贡献指南（衡 ERP）

欢迎贡献！本项目是 AI 原生开源 ERP（开放核心），当前处于**可演示阶段（M1）**。

## 快速开始
```powershell
git clone https://github.com/tianqishi1/heng-erp.git
cd heng-erp
uv venv .venv --python 3.12
uv pip install -e ".[dev]" --python .venv\Scripts\python.exe
.venv\Scripts\python.exe -m pytest                    # 必须全绿
.venv\Scripts\python.exe -m heng_eval run evals       # 评测门禁，必须全绿
.venv\Scripts\python.exe -m uvicorn heng_api.main:app --port 8765   # 演示工作台
```
演示账号与流程见 docs/DEMO_GUIDE.md。

## 贡献流程
1. Fork → 分支 `feat/...` / `fix/...` / `docs/...`；
2. **提交加 DCO sign-off**：`git commit -s`（见 LICENSING.md）；
3. 本地过两道门禁：`pytest` 与 `heng_eval run evals`；
4. PR 描述：动机 / 变更 / 测试与评测证据 / 影响面；
5. CI 会复跑门禁；维护者 review 后合并。

## 硬性规则（AI 原生 ERP 的底线）
- **LLM 永不直接写账**：任何写路径必须走确定性 Action 管线；
- **新 Action 必须带评测集**（`evals/...yaml`），否则注册即失败；
- 财务/库存/税额断言只用**确定性精确断言**，不得用 LLM judge 裁决对错；
- 扩展用**元数据扩展包**（metadata/extensions/），禁止 fork 内核或覆盖官方字段；
- 进化类变更（话术/知识资产）必须过黄金集零退化门禁 + 人审 + 灰度路径；
- 金额一律 Decimal；前置条件 fail-closed；审计与 Outbox 同事务。

## 好的第一个贡献（good first issue）
- 新行业扩展包示例（参照 metadata/extensions/trade_priority）；
- 黄金评测用例补充（消歧/边界/对抗类）；
- 工作台业务化文案与无障碍改进；
- 文档翻译与演示脚本改进。

## 许可
贡献即同意以 LGPL-3.0-or-later 授权（DCO sign-off 为证）。企业版目录未来另行 CLA。
