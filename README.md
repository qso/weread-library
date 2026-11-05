# weread-library

微信读书 · 找书与精读。一个面向 AI Agent 的技能：说出书名，优先找本地书库，缺书再搜索微信读书，获取当前账号可读取的内容，生成电子书，并基于原文问答与解读。

> 本项目是非官方工具，与微信读书、腾讯没有关联。平台可能限制第三方接入或外部导出；会员身份、随机等待和一次成功测试均不构成导出授权或账号安全保证。仅处理你有权使用的内容，不绕过付费、验证码或阅读限制。发布的软件许可不授予任何书籍的使用权。

## 能做什么

- 直接说「找一下《书名》」「讲讲这本书」「解读核心观点和最值得读的章节」。同名、近似标题或版本有歧义时先确认。
- 先查本地书库；完整缓存无需登录、联网或启动浏览器，缺章支持续传。
- 通过 [weread-omni](https://github.com/teng-lin/weread-omni) 搜索、查询书目和扫码登录；正文提取、格式生成、缓存与检索内置，不安装 weread-exporter。
- 默认生成 EPUB，可选 Markdown、TXT、HTML；支持导入 EPUB、文字 PDF、Markdown 和 TXT。扫描 PDF 会提示需要 OCR。
- 下载使用一个无窗口浏览器逐章处理，默认章节之间随机等待 3～7 秒。账号登录成功后复用会话，不预先要求网页和 API 各扫一次码。
- 遇到会员限制、单本付费、试读结束、人工验证或限频时停止；部分内容明确标记为不完整，不能冒充全文。
- 基于分章原文检索、引用位置和解释。微信读书找不到或不可读时，先询问是否尝试网上寻找 PDF/EPUB。

## 安装与使用

把本仓库安装到支持 `SKILL.md` 的 Agent 的技能目录，文件夹保持为 `weread-library`。以 Codex 为例，首次安装可执行：

```bash
git clone https://github.com/qso/weread-library.git "${CODEX_HOME:-$HOME/.codex}/skills/weread-library"
```

已有同名安装时更新原安装目录，不重复克隆。安装后让宿主重新加载技能，并说：

```text
使用 weread-library，帮我找《书名》，获取可读原文并解读核心观点。
```

技能会按 [SKILL.md](SKILL.md) 检查所缺依赖，必要时安装私有运行环境。使用宿主 AI 解读，不需要额外配置大模型 API Key。

需要直接运行时，在本仓库目录执行：

```bash
python3 scripts/library.py doctor
python3 scripts/library.py setup
python3 scripts/library.py find "书名"
python3 scripts/library.py login api
python3 scripts/library.py find "书名" --online
python3 scripts/library.py export BOOK_ID
```

只在缺少依赖时执行 `setup`，只在需要登录时执行 `login api`。扫码后需要在微信确认；二维码成功或过期后删除。日常获取和格式转换均无窗口运行，只有用户明确同意人工操作时才使用 `--visible`。

```bash
python3 scripts/library.py export BOOK_ID --format epub --format md
python3 scripts/library.py format /书籍目录 --format all
python3 scripts/library.py local-search "关键词" --author "作者"
python3 scripts/library.py query /书籍目录 "我的问题"
python3 scripts/library.py ingest /路径/已有书籍.pdf
```

当前支持 EPUB、Markdown、TXT、HTML 输出，PDF 为输入格式。账号选择使用全局 `--account`，放在子命令前。默认沿用 Omni 的默认账号或唯一已有账号。

## 登录态与个人文件

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

## 完整性与恢复

`meta.json` 保存书目和预期目录，`manifest.json` 保存覆盖率、文件和校验状态。缺章时生成 `book.partial.*`，并保留停止章节与原因。`catalogue_complete` 只表示目录覆盖检查通过，不保证逐字、图表或原版排版一致。

```bash
python3 scripts/library.py verify /书籍目录
python3 scripts/library.py toc /书籍目录
python3 scripts/library.py read /书籍目录 1
```

EPUB 被解析为章节文本供 AI 使用。解读需要区分重点阅读与全书精读，不能把简介、试读或远端 AI 回答当作完整原文。

## 环境与验证

真实验收环境为 macOS。在线功能使用 Node、Python 3.11 和 Chrome/Chromium；`setup` 优先复用已有浏览器，并按需准备依赖。纯本地检索和格式生成不需要浏览器或登录。其他操作系统尚未完成真实验收。

```bash
python3 -m unittest discover -s scripts/tests -v
```

系统 Python 缺少浏览器相关依赖时，导出流程模拟检查会明确跳过。安装依赖后可用技能的私有 Python 运行全部检查：

```bash
"${WEREAD_LIBRARY_HOME:-$HOME/.local/share/weread-library}/runtime/venv/bin/python" -m unittest discover -s scripts/tests -v
```

目前 45 项检查通过；已实测会员可读流程、非会员受限流程、会话复用、无窗口读取、双页模式适配、缓存与部分 EPUB，以及授权版权页和官方插图跳转。其余权益和操作系统场景的具体范围见 [运行与验收记录](references/operations.md)。

## 许可与致谢

本项目代码采用 [MIT License](LICENSE)。依赖、参考来源及各自的许可边界见 [第三方说明](THIRD_PARTY_NOTICES.md)。

- [weread-omni](https://github.com/teng-lin/weread-omni)：登录、搜索和书目接口。
- [weread-exporter](https://github.com/drunkdream/weread-exporter)：Canvas 提取思路与阅读器适配参考。
- [moon](https://github.com/AnkioTomas/moon)：会话复用实现参考。
