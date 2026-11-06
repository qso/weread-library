# 手动安装、命令与检查

## 自己修改源码时

把源码放在 Projects 中，再从技能目录建软链。下面的命令适用于尚未克隆、也没有同名技能目录的情况；已有安装请先核对路径，不要覆盖源码。

```bash
git clone https://github.com/qso/weread-library.git "$HOME/Projects/weread-library"
mkdir -p "$HOME/.agents/skills"
ln -s "$HOME/Projects/weread-library" "$HOME/.agents/skills/weread-library"
```

开发时直接修改 Projects 中的文件，软链指向同一份源码。更新这类安装时，在源码仓库检查自己的改动后再同步 Git；不要用安装器覆盖正在开发的目录。

## 通过 skills CLI 安装与更新

普通使用者可以按 README 中的命令安装。`-g` 表示当前用户的所有项目可用；省略它则安装到当前项目。可用 `-a codex` 或 `-a claude-code` 指定工具。

```bash
npx skills add qso/weread-library --list
npx skills add qso/weread-library --skill weread-library -g -a codex
npx skills check
npx skills update weread-library -g
```

上面的检查与更新命令用于 skills CLI 管理的安装。更多参数见 [skills CLI 文档](https://github.com/vercel-labs/skills/blob/main/docs/commands.md)。

## 直接运行命令

在源码目录执行：


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

目前 45 项检查通过；已实测会员可读流程、非会员受限流程、会话复用、无窗口读取、双页模式适配、缓存与部分 EPUB，以及授权版权页和官方插图跳转。其余权益和操作系统场景的具体范围见 [运行与验收记录](operations.md)。
