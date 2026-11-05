# 第三方依赖与参考

本仓库分发的是 weread-library 的技能说明、独立实现、文档和测试。运行时安装的第三方软件保留各自的许可，不由本项目的 MIT License 替代。本项目的许可不覆盖微信读书平台、书籍、封面、商标或用户数据。

## weread-omni

- 项目：https://github.com/teng-lin/weread-omni
- 运行依赖：npm `weread-omni@0.1.2`。
- 核查源码：`88bd2e095d7d7ee423eaadf8f40653e72c5be6d4`。
- 许可：MIT，Copyright (c) 2026 Teng Lin；许可文件随其 npm 安装包保留。
- 用途：搜索、书目、目录、扫码登录和续期。其源代码与凭据不打包进本仓库。

## weread-exporter

- 项目：https://github.com/drunkdream/weread-exporter
- 参考版本：`80a9ccb01647951cfac70f4554eb7741089f0263`。
- 该版本的 `setup.py` 声明 MIT 许可。
- 本项目借鉴 Canvas 绘图观测、低像素比设置及网页书籍 ID 协议，重新实现正文重建与读取流程，不安装或分发其 Python 包与 `hook.js`。

## moon

- 参考：https://github.com/AnkioTomas/moon/blob/136a0bfc0b44408e938ade56d6df1917343c0063/book.koplugin/source/wechat/auth.lua
- 用于核对同账号会话复用的行为；未分发该项目的 Lua 源码。

## 通用运行依赖

`setup` 在用户本机安装 pyppeteer、aiohttp、Pillow 和 qrcode；PDF 导入按需安装 pypdf。Node、Python、uv、Chrome/Chromium 或 Playwright 可能被复用或按需准备。它们均不打包进本仓库，具体版本和安装行为见 `scripts/runtime.py`、`scripts/pdf_support.py`，许可由各自的发行包提供。
