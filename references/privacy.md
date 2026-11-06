# 登录态与个人文件


**本仓库只提供程序、文档和测试，不附带任何账号、登录态、书籍正文或运行环境。** 每位使用者在自己的电脑上扫码；凭据用于向微信读书认证，不发送到项目维护者或 GitHub。

| 本地数据 | 默认位置 |
| --- | --- |
| Omni 账号凭据 | `~/.config/weread/accounts/<account>/clients/eink.json` |
| 专用浏览器配置，包括 Cookie | `~/.local/share/weread-library/auth/<account>/browser/` |
| 网页身份核验记录 | `~/.local/share/weread-library/auth/<account>/web-session.json` |
| 本地书籍、原文和索引 | `~/.local/share/weread-library/books/` |
| 私有运行环境 | `~/.local/share/weread-library/runtime/` |

`WEREAD_CONFIG_DIR` 可更改 Omni 配置位置，`WEREAD_LIBRARY_HOME` 可更改技能数据根目录。请放在仓库外，也不要放进公开同步目录。

Omni 的凭据是本地 JSON，**不是系统钥匙串加密存储**。在已验证的 macOS 环境中，凭据文件权限为 `0600`，技能私有目录为 `0700`；Windows 使用本机账号和目录访问权限。正常输出不打印访问令牌或刷新令牌。浏览器配置也应视为敏感数据，反馈问题时不要上传整个数据目录。

原文解读由宿主 AI 完成；正文是否发送至模型服务取决于宿主的运行方式和隐私设置。可选的 `ask-remote` 会把问题发送给微信读书 AI。工具不会自动上传书籍做外部 OCR。
