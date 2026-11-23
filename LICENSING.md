# 许可与开放核心边界（Licensing）

## 当前仓库许可
本仓库**全部现有代码**以 **LGPL-3.0-or-later** 发布（见 LICENSE）。
- 你可以自由使用、修改、分发，包括用于商业产品；
- 你可以以**独立模块/扩展包**形式闭源你的二次开发成果（LGPL 的链接例外 + 本项目的元数据扩展包机制天然支持）；
- 修改了本仓库内核代码并分发时，需按 LGPL 公开该部分源码。

## 开放核心（open-core）边界规划
| 层 | 许可 | 位置 |
|---|---|---|
| 平台内核 / 业务模块 / 基础财务 / Agent 框架 / 评测基建 | LGPL-3.0-or-later | 现仓库全部目录 |
| 企业版增量（集团多组织/合并报表/申报直连/私有化模型/信任控制台高级能力等，尚未落地） | 专有许可 | 未来 `enterprise/` 目录（见 enterprise/README.md） |
| 文档 / 元数据 / 规则包 / 评测集 | LGPL-3.0-or-later（随仓库） | docs/ metadata/ rulesets/ evals/ |

**贡献者须知**：向现仓库（LGPL 区域）贡献即表示同意以 LGPL-3.0-or-later 授权你的贡献；
未来 `enterprise/` 目录启用时将另行要求 CLA（贡献者许可协议），届时会在该目录 README 与 CONTRIBUTING 中公告。

## 贡献签署（DCO）
本项目采用 **Developer Certificate of Origin**：提交时请加 sign-off：
```
git commit -s -m "feat: ..."
```
即声明你有权提交该代码并同意按仓库许可授权（DCO 全文见 https://developercertificate.org）。

## 商标
"衡 ERP / HengERP" 名称与标识为项目商标。代码许可不授予商标权；
 forks/发行版请使用自己的名称，或另行取得商标授权。

## 第三方
依赖组件各自许可见其上游（FastAPI/SQLAlchemy/Pydantic 等均为宽松许可）。
