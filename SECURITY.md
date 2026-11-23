# 安全政策

## 报告漏洞
请**不要**在公开 issue 披露安全漏洞。通过 GitHub 私密漏洞报告
（Security → Report a vulnerability）或邮件 tianqishi1@163.com 联系维护者。
我们承诺 72 小时内响应、修复后公开致谢（可选匿名）。

## 演示环境声明
本仓库默认配置为**内网演示**：演示账号（chen/boss123 等）、SQLite 演示库、
沙箱发票通道、加盐 SHA256 口令均为演示级，**不得用于生产**。
生产化清单见 docs/能力与风险清单.md 与 README 的 M2 替代项。

## supported versions
| 版本 | 支持 |
|---|---|
| master（M1） | ✅ |
| 历史 tag | 仅严重漏洞 |
