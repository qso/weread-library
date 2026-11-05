# 数据和内置提取器

## 依赖边界

weread-omni 0.1.2 用于搜索、书目、目录、登录续期和远端问书。浏览器驱动为 pyppeteer，图片下载/检查使用 aiohttp、Pillow；文字 PDF 的 pypdf 按需安装。这些是通用运行库。

本技能不下载、import 或运行 weread-exporter。capture_canvas.js 独立实现绘图文本观测、按坐标重组行、合并长章节的多个 Canvas、去重与媒体插入；book_ids.py 本地实现微信读书数字 ID 的网页编码。脚本随技能发布，不依赖运行目录中的第三方项目源码。

设计参考并致谢：[weread-exporter](https://github.com/drunkdream/weread-exporter)，核查版本 80a9ccb01647951cfac70f4554eb7741089f0263。借鉴 Canvas 提取思路、低像素比设置和网页 ID 协议；没有把原 hook.js 或 Python 包原样内嵌。会话复用参考 [moon 的实现](https://github.com/AnkioTomas/moon/blob/136a0bfc0b44408e938ade56d6df1917343c0063/book.koplugin/source/wechat/auth.lua)，已在真实账户验证。

## 文件职责

| 文件 | 职责 |
| --- | --- |
| meta.json | 权威书名、作者、版本/ISBN、来源、账号、预期目录；不依赖下载成功 |
| checkpoints.json | 下载过程的逐章成功记录和正文摘要，实时写入供续传 |
| chapters/*.md、images/ | 所有格式和问答的共同正文来源 |
| manifest.json v2 | 实际覆盖、章节文件摘要、问题、交付格式及摘要，可重建 |
| book.epub/md/txt/html | 用户选择的交付格式，可从本地正文重建 |
| index.sqlite、chunks.jsonl | 单书原文检索及出处，可重建 |
| 根目录 catalogue.sqlite | 跨书的书名、作者、ISBN 索引；增量更新的缓存，不是第二份权威书目 |
| api-info.json、api-catalogue.json | 当次 API 原始响应，供诊断恢复，不代替完整性检查 |

manifest v2 不再重复存书名、作者、来源和目录标题；命令读取时与 meta 组合。旧 v1 兼容读取，下一次本地生成/索引时升级，不为迁移重下正文。metadata_sha256 保证更正书名后电子书也会本地重建。

## 缓存规则

先查 catalogue。精确且版本唯一时，再检查章节摘要、目录对应关系和覆盖状态。可用就直接读；交付文件损坏或缺格式只本地重建；正文缺失或损坏才恢复在线下载。模糊/同名候选需要确认。

缓存不按天数自动作废。export --refresh 用于主动查更新；下架或登录过期不影响已有本地文件。指定账号时按账号筛选；本地命中不强制选择在线账号。

默认书库位于 books/<account>/<web_id>/，本地导入按原文件 SHA256 去重。自定义 ingest --out 也登记目录；若 catalogue.sqlite 丢失，默认书库可扫描恢复，外部目录可用 index DIR 重新登记。

## 内置提取的验收

长章可能分为多个 Canvas，共用章节坐标，不能只取最大画布。每个 Canvas 单独处理清除事件；合并阅读区域的绘图记录。稳定正文、结束标记、实际章节 URL、目录字数和正文末尾分别检查。

低像素比 deviceScaleFactor=0.3 是真实阅读器验证所需。末章结束文字可能是「全 书 完」，无下一章按钮。长章明显偏短必须停止，不能降低阈值声称完整。

2026-11-05：内置实现对《如何培养孩子自主学习力》第 1 章及结语后台回归，正文字符数分别为 7263、1207，与原缓存一致，末尾 200 个规范化字符一致。提取或导出成功不等于模型已精读全书。
