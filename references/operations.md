# 运行与恢复

核查日期：2026-11-05。业务依赖 weread-omni npm 0.1.2；核查源码 88bd2e095d7d7ee423eaadf8f40653e72c5be6d4。正文提取器内置，设计来源和数据职责见 [architecture.md](architecture.md)。不安装 exporter，不依赖它的 Python 包或 hook.js。

## 环境

真实验收平台为 macOS，Node >=22.13，网页提取 Python 3.10–3.12，Chrome/Chromium。setup 复用或私有安装 Node，核对官方 SHA256；uv 可准备 Python 3.11，通用库为 pyppeteer、aiohttp、Pillow。缺浏览器时安装独立 Chromium。PDF 导入另外按需安装私有 pypdf 6.21.4，纯本地找书/问答不需要浏览器、Node 或登录。

默认无界面运行。Linux 系统库或 Windows 策略限制不一定能由私有安装解决，只报告实际错误，不声称跨平台实测。目录 0700、写入文件 0600；Windows 依赖用户目录 ACL。二维码成功或过期后删除。不会自动上传书籍；ask-remote 会把问题发送给微信读书 AI。

## 会话

Omni 凭据仍在 ~/.config/weread/accounts/<account>/，尊重 WEREAD_CONFIG_DIR。在线导出先由 Omni doctor 检查/续期，再只读指定账号 vid/accessToken 并设置专用阅读器 wr_vid/wr_skey。refreshToken 不复制为 wr_rt。网页 /api/userInfo 的 userVid 必须匹配预期，不能以 Cookie 存在或头像代替核验。

默认账号与唯一已有账号可自动沿用；多账号无默认时选择别名。完整本地缓存不触发在线账号选择。正常导出一次启动后台浏览器并复用页面，结束后关闭本次浏览器且保留配置。--visible 仅用于人工操作或排查，不自动弹出。

新获取章节之间默认独立随机等待 3～7 秒，不在最后一章之后、跳过缓存时或本地转换时额外等待。页面加载、内容稳定与完整性检查另计，不压缩这些必要等待。底层 export_runner.py 的 --interval N 可为诊断指定固定间隔；未传时使用随机默认值。该范围是按用户要求缩短后的默认节奏，不是平台认可或实测保证的安全阈值；人工验证、限频和权限不足的停止逻辑继续生效。

新账号可能默认双页横向阅读，Cookie 设置不一定足以切换。内置阅读器会在后台点击已经核验的「上下滚动阅读」按钮，并等待模式改变；允许页面重载或早期点击尚未绑定的一次重试。双页可能同时展示两个章节，切换前的绘图内容全部丢弃，不能合并为一个章节。无法切换则返回 READER_LAYOUT_UNSUPPORTED，仍不自动显示窗口。

## 故障

| 状态 | 恢复动作 |
| --- | --- |
| DEPENDENCY_MISSING / INSTALL_FAILED | doctor → setup，检查网络，不重复全局安装 |
| LOGIN_REQUIRED | 运行 login api 并立即展示新二维码，成功后续跑 |
| WEB_LOGIN_REQUIRED | 先查 Omni 会话复用；有效 API 仍不可用时才考虑备用 login web |
| LOGIN_TIMEOUT | 用户准备好后刷新，使用唯一新图片 |
| ACCOUNT_MISMATCH | 核对账号，不能静默读取另一个账号的内容 |
| ACCOUNT_SELECTION_REQUIRED | 指定已有 --account 别名，无需重登 |
| HUMAN_VERIFICATION / RATE_LIMITED | 停止自动请求，官方客户端验证或等待；-2041 不能当作普通过期反复续期 |
| ACCESS_LIMITED / UNAVAILABLE | 保留缓存，询问用户是否网上找 PDF/EPUB，收到同意才兜底 |
| ACCESS_LIMITED 的 access.reason | membership_required / membership_or_purchase_required / single_purchase_required / purchase_required / reading_card_required / trial_ended / entitlement_required；只表达页面或 API 明示的原因，不要求重新扫码 |
| ACCESS_DENIED | 无明确原因的 403；保留错误，不直接认定会员问题、登录过期或风控 |
| AUTO_PURCHASE_ENABLED | 停止加载正文，请用户在官方客户端关闭该书自动购买；不修改购买设置 |
| UNSUPPORTED_READER | 网页明确要求 App 阅读；不承诺网页可导出，可在用户同意后找其他版本 |
| BOOK_ID_MISMATCH / CATALOGUE_CHANGED | inspect/probe 核对书籍和目录，不覆盖成另一本书 |
| CHAPTER_TIMEOUT / INCOMPLETE_CHAPTER | 检查实际阅读器，保留缓存，有明确新线索才续试一次 |
| LOGIN_UI_CHANGED / READER_UI_CHANGED | 观察真实页面后修复，不能随机点击 |
| READER_LAYOUT_UNSUPPORTED | 后台切换阅读模式未成功；停止并检查真实 UI，不把双页内容混成一章 |
| OCR_REQUIRED | 扫描 PDF 需要 OCR；不自动上传外部服务 |
| ENCRYPTED_PDF / ENCRYPTED_EPUB | 需要可读取版本，不尝试破解 |
| BUSY | 等待同账号任务释放锁，不并发操作浏览器配置 |
| CORRUPT_STATE | 保留损坏现场后修复指定状态，不清空书库 |

超时清理本次进程组，操作系统释放文件锁；不关闭用户的日常 Chrome。索引原子替换。verify 检查摘要和 EPUB 结构，不保证逐字准确。

权限判断仅检查可见页尾、登录/购买弹窗和试读终止区域，不扫描正文中的「会员」「购买」等词。页面处于切换时，普通权限提示连续两次一致才确认；人工验证及限频立即停止。试读页可能同时显示「下一章」，不能因此写入完成检查点。没有会员不是预先拒读理由；下架也不能仅凭 soldout 拒读已购内容。书目价格、paid=0、maxFreeChapter 等字段不单独代表最终阅读权限。

下载停止原因写入 checkpoints.json 的 _last_attempt，manifest 从此记录派生 last_attempt，包含时间、章节和错误分类。它是上次尝试结果，不是永久权益结论；再次成功获取后更新。完整本地文件仍离线复用，不为验证当前会员状态而要求重新登录。

## 验收

离线：python3 -m unittest discover -s "$SKILL_DIR/scripts/tests" -v。导出流程模拟检查需要 pyppeteer/aiohttp；使用 setup 创建的私有 Python 执行可运行全部检查，系统 Python 缺包时会明确跳过这些项。

真实流程：find 本地 → 必要时登录 → 在线搜索/确认 → export → verify → query。重新请求已有书必须返回 cached=true、network_used=false；选择其他格式仍应离线。语义歧义只确认书目，不重新扫码。

已有《如何培养孩子自主学习力》（赵周，39479676）27/27 目录条目，含 16 章正文。原文检索的字符位置与行号核对通过。升级内置提取器时只在临时诊断目录回归抽样，没有为了升级重下整书或覆盖原缓存。网页登录备用扫码与其他操作系统尚未真实验收。

本次升级的 29 项离线检查通过；移走旧 exporter 运行目录后 setup/doctor 仍通过。内置提取器对长章及末章的规范化正文与缓存逐字符一致。四种输出格式均在零在线请求下生成，章节和产物摘要检查通过。文字 PDF 的真实解析样本验证了自动安装私有依赖、提取文本和保留无文字页缺失记录。网上找书兜底本次未触发，其许可等待和来源核对由技能流程控制。

权益检查补充（2026-11-05）：43 项检查覆盖登录与权益分离、可见购买/试读提示优先于「下一章」、正文不误判、部分 EPUB、失败章节无完成检查点、停止原因持久化、自动购买阻断、双页内容隔离及已购下架书不预先拒读。真实未登录页面确实同时显示登录福利和「下一章」；会员账号第 2 章重新提取的规范化正文与原缓存一致。期间曾出现网页会话未获认可及空白加载失败，API 核验后恢复，未要求第二次扫码。

真实非会员验收书为《读毛选，走出人生困局》（温伯陵，3300235025）。该测试账号 isPaying=0、remainTime=0、payingRemainTime=0，书目 paid=0、isAutoPay=0。第 5 个目录条目《事实是最好的论据》只显示约 193 字预览，同时出现会员开通提示和「下一章」，正确返回 ACCESS_LIMITED / membership_required。这个场景不应再次登录，也不能把 193 字记作完整正文。独立账号的一次 Omni 扫码成功复用于网页，实际浏览器进程核对为 headless。

此次还实际验证了后台从双页切换到滚动模式。平台生成的版权页显示 70 个正文字符，而目录 wordCount=429；仅对具有书名、作者、出品时间、微信读书出品与版权声明的完整特定模板豁免字数差异，并记录 source_note=reader_generated_copyright，普通短章仍拒绝。

**体验卡到期、仅支持单本购买、已购下架书的真实账号场景尚未验收**；对应模板与流程模拟通过，不等于真实验收。

非会员复测使用独立 WEREAD_CONFIG_DIR、显式 --account、独立浏览器配置及空目录，保留原会员账号。先确认该书未被测试账号购买，分别检查可免费阅读部分与付费边界；不得用已有会员全文缓存或领取体验卡后的结果证明普通非会员可读。测试二维码只在用户准备好后刷新一次，不自动循环刷新。
