# WeRead Library Skill｜微信读书下载与 AI 解读

告诉 AI 你想读哪本书，它会帮你在微信读书找书，下载成 EPUB，再根据书中原文解读和回答问题。

你可以问核心观点、值得读的章节，也可以追问某个概念或例子。书会保存在本地，下次接着聊时可以直接使用。

## 直接这样用

安装后，在 Codex、Claude Code 等支持 Agent Skills 的工具里说：

> 帮我找《掌控习惯》，下载成 EPUB，再讲讲它最值得读的地方。

> 作者说的“两分钟规则”是什么意思？结合原文，给我一个可以试试的做法。

> 我已经下载过这本书了，帮我查查作者怎么讨论“坚持不下去”。

## 你能拿到什么

- **一本本地电子书。** 默认生成 EPUB，也可以选 Markdown、TXT 或 HTML。有同名书或不同版本时，会先和你确认。
- **有原文出处的解读。** AI 会阅读相关章节，解释核心观点、重要例子和适用范围，并注明出处和实际阅读范围。
- **可以接着问的书库。** 下载过的完整书籍优先从本地读取。换格式也可以直接转换，不必再下整本。
- **已有文件也能读。** 可以导入 EPUB、文字 PDF、Markdown 和 TXT。扫描版 PDF 需要另做文字识别。

## 安装

需要 Node.js 22.20 或更新版本。在终端执行：

```bash
npx skills add qso/weread-library -g
```

`-g` 表示安装后可以在不同项目中使用。安装时可以选择使用的 AI 工具；只装到 Codex 可以用：

```bash
npx skills add qso/weread-library -g -a codex
```

安装后新开一轮对话，告诉 AI：

> 使用 weread-library，帮我找《掌控习惯》，下载并解读这本书。

技能会检查取书需要的依赖，并在缺少时尝试安装。解读使用你当前的 AI 工具，不用再配置另一套大模型 API Key。

如果你要修改源码，适合把仓库放在 Projects 中，再用软链接入技能目录。具体步骤和更新方法见[安装与命令说明](references/usage.md)。

## 第一次取书

需要登录时，AI 会给你二维码。用自己的微信扫码，并在微信中确认即可；已有可用登录时会复用。

下载在后台进行，正常使用不会弹出浏览器窗口。书籍下载完成后会给你本地文件，再开始解读。中途停下时会保留已经取得的章节，下次可以继续。

能下载哪些内容，取决于你的账号对这本书的阅读权限。遇到付费、试读结束或平台验证时会停下并说明原因；只有部分内容时，会标明缺失范围。

微信读书找不到书或无法读取原文时，AI 会先询问你是否尝试在网上寻找 PDF 或 EPUB。你同意后才会继续找。

## 账号和书存在哪里

登录凭据和下载的书都保存在你的电脑上，不会上传到这个 GitHub 仓库或发送给项目维护者。

- 账号凭据默认放在 `~/.config/weread/`。
- 书库和专用浏览器登录态默认放在 `~/.local/share/weread-library/`。

AI 解读时，相关正文可能发送给你正在使用的模型服务，取决于该工具的运行方式和隐私设置。凭据是本地文件，并非系统钥匙串加密存储。具体路径和注意事项见[本地数据说明](references/privacy.md)。

## 使用范围

取书流程目前主要在 macOS 与 Codex 上验证，其他系统尚未完成实测。支持下载 EPUB、Markdown、TXT 和 HTML；PDF 目前支持导入，不支持直接导出。

本项目是非官方工具，与微信读书、腾讯没有关联。请只处理你有权使用的内容；工具不会绕过付费或验证限制，也不保证不会触发平台限制。生成的电子书会检查目录和文件，不能保证与原版逐字、逐图或排版完全一致。

## 文档与来源

- [安装、更新与命令](references/usage.md)
- [登录态与本地数据](references/privacy.md)
- [运行、故障处理与实测范围](references/operations.md)
- [技能说明](SKILL.md)

代码采用 [MIT License](LICENSE)。登录和搜索使用 [weread-omni](https://github.com/teng-lin/weread-omni)；正文提取参考了 [weread-exporter](https://github.com/drunkdream/weread-exporter)，会话复用参考了 [moon](https://github.com/AnkioTomas/moon)。本项目内置取书与电子书生成实现，不需要安装 weread-exporter。详细致谢见[第三方说明](THIRD_PARTY_NOTICES.md)。
