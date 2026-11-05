"""Built-in Canvas reader in an isolated, bounded browser session."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path
import random
import re
import sys
import time
from urllib.parse import urlparse

from runtime import (DATA_HOME, SCRIPT_DIR, Failure, account_name, atomic_text, browser_path,
                     emit, lock, omni_web_session, private_dir, read_json, write_json)
from corpus import build, quality, stamp

import pyppeteer
import aiohttp
from book_ids import reader_hash as wr_hash, web_id
from access import reader_issue, fallback_for

logging.disable(logging.CRITICAL)  # Upstream debug output may contain session material.


async def save_images(text, directory):
    allowed = ("weread.qq.com", "qpic.cn", "qlogo.cn")
    private_dir(directory / "images")
    for url in dict.fromkeys(re.findall(r"!\[[^\]]*\]\((https://[^)]+)\)", text)):
        host = urlparse(url).hostname or ""
        if not any(host == d or host.endswith("."+d) for d in allowed):
            continue
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.get(url, allow_redirects=False) as response:
                    if response.status != 200:
                        continue
                    data = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        data.extend(chunk)
                        if len(data) > 12_000_000:
                            raise ValueError("image too large")
            from PIL import Image
            import io
            with Image.open(io.BytesIO(data)) as im:
                ext = {"JPEG":"jpg", "PNG":"png", "GIF":"gif", "WEBP":"webp"}.get(im.format)
                im.verify()
            if not ext:
                continue
            relative = "images/" + hashlib.sha256(data).hexdigest() + "." + ext
            (directory / relative).write_bytes(data)
            (directory / relative).chmod(0o600)
            text = text.replace("]("+url+")", "]("+relative+")")
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
            continue
    return text


class Reader:
    def __init__(self, account, expected_vid=None, visible=False):
        self.account = account_name(account)
        self.directory = private_dir(DATA_HOME / "auth" / account)
        self.expected_vid = str(expected_vid) if expected_vid else None
        self.visible = visible
        self.session_source = "web_profile"
        self.browser = self.page = None
        self.last_access_screen = None

    async def open(self, url="https://weread.qq.com/"):
        browser = browser_path()
        if not browser:
            raise Failure("BROWSER_REQUIRED", "未找到浏览器，请运行 setup。")
        self.browser = await pyppeteer.launch(
            executablePath=browser, headless=not self.visible, userDataDir=str(private_dir(self.directory / "browser")),
            # Upstream uses a low pixel ratio so long chapters fit within Canvas limits.
            defaultViewport={"width": 1920, "height": 1080, "deviceScaleFactor": 0.3}, dumpio=False,
            autoClose=False,
            args=["--no-first-run", "--no-default-browser-check"],
            handleSIGINT=False, handleSIGTERM=False, handleSIGHUP=False)
        self.page = (await self.browser.pages())[0]
        session = omni_web_session(self.account, self.expected_vid)
        if session:
            self.expected_vid = session["vid"]
            existing = await self.page.cookies("https://weread.qq.com")
            if any(c["name"] == "wr_vid" and c["value"] != session["vid"] for c in existing):
                raise Failure("ACCOUNT_MISMATCH", "专用浏览器与 Omni 账号不同，已停止。")
            # Remove only this profile's old auth cookies, including duplicate domains.
            # A web refresh token cannot renew an Omni access token.
            for cookie in existing:
                if cookie["name"] in ("wr_vid", "wr_skey", "wr_rt", "wr_localvid", "wr_ql"):
                    await self.page.deleteCookie({k:cookie[k] for k in ("name", "domain", "path")})
            await self.page.setCookie(*[
                {"url":"https://weread.qq.com", "name":name, "value":value, "secure":True}
                for name, value in (("wr_vid", session["vid"]), ("wr_skey", session["access_token"]), ("wr_ql", "0"))
            ])
            self.session_source = "omni"
        hook = (SCRIPT_DIR / "capture_canvas.js").read_text(encoding="utf-8")
        await self.page.evaluateOnNewDocument("() => {\n" + hook + "\n}")
        await self.page.setCookie({"url": "https://weread.qq.com", "name": "wr_useHorizonReader", "value": "0", "secure": True})
        await self.page.goto(url, {"waitUntil": "domcontentloaded", "timeout": 45000})

    async def identity(self):
        cookies = {x["name"]: x["value"] for x in await self.page.cookies("https://weread.qq.com")}
        vid = cookies.get("wr_vid")
        # Even a stale session must not silently select another account for paid content.
        if vid and self.expected_vid and vid != self.expected_vid:
            raise Failure("ACCOUNT_MISMATCH", "API 与网页登录的微信账号不同，请使用同一账号重新登录。")
        if not vid:
            return None
        # The server's current-user response is stronger evidence than an avatar or
        # cookie presence. Return only non-secret status across the browser boundary.
        result = await self.page.evaluate("""async () => {
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), 12000);
          try {
            const r = await fetch('/api/userInfo', {credentials:'same-origin', redirect:'error', signal:controller.signal});
            const d = await r.json();
            return {status:r.status, vid:String(d.userVid || d.vid || ''), code:d.errCode};
          } catch (_) { return {status:0}; }
          finally { clearTimeout(timer); }
        }""")
        if result.get("code") == -2041:
            raise Failure("HUMAN_VERIFICATION", "微信读书要求人工验证，已停止自动请求。")
        if result.get("status") == 429:
            raise Failure("RATE_LIMITED", "微信读书限制了请求频率，已停止自动重试。")
        if result.get("status") == 0 or result.get("status", 0) >= 500:
            raise Failure("NETWORK_ERROR", "网页身份核验未成功返回，暂不能判断登录或会员权益。")
        if result.get("status") == 403:
            raise Failure("ACCESS_DENIED", "网页拒绝身份核验但未说明原因，未将其判断为会员不足或登录过期。")
        if result.get("status") == 200 and result.get("vid"):
            if result["vid"] != vid or (self.expected_vid and result["vid"] != self.expected_vid):
                raise Failure("ACCOUNT_MISMATCH", "网页返回的账号与 Omni 账号不同，已停止。")
            write_json(self.directory / "web-session.json", {"vid":vid, "source":self.session_source, "verified_at":stamp()})
            return result["vid"]
        return None

    async def login(self, timeout=300):
        if vid := await self.identity():
            return vid
        await self.page.waitForFunction("document.readyState === 'complete'", {"timeout": 30000})
        # DOM nodes can precede hydration; use a real pointer click and verify the QR image.
        for attempt in range(2):
            handle = await self.page.evaluateHandle("""() => [...document.querySelectorAll('button,a')].find(
              x=>x.offsetParent!==null && /^(微信)?(扫码)?登录$/.test(x.innerText.trim())) || null""")
            element = handle.asElement()
            if element is None:
                raise Failure("LOGIN_UI_CHANGED", "未定位到登录入口，请在微信读书窗口手动点击登录。")
            await element.click()
            try:
                await self.page.waitForFunction("() => { const img=document.querySelector('img.wr_login_modal_qr_img'); return img && img.complete && img.naturalWidth>0 && img.offsetParent!==null; }", {"timeout": 12000})
                break
            except pyppeteer.errors.TimeoutError:
                if attempt:
                    raise Failure("LOGIN_UI_CHANGED", "登录二维码未加载成功，请在网页检查网络或验证提示。")
        qr = self.directory / f"web-login-{int(time.time())}.png"
        qr_element = await self.page.querySelector('img.wr_login_modal_qr_img')
        await qr_element.screenshot({"path": str(qr)})
        qr.chmod(0o600)
        emit({"event": "scan_required", "stage": "web", "qr_image": str(qr), "expires_in_seconds": timeout})
        deadline = time.monotonic() + timeout
        prior = {c["name"]:c["value"] for c in await self.page.cookies("https://weread.qq.com")}
        try:
            while time.monotonic() < deadline:
                current = {c["name"]:c["value"] for c in await self.page.cookies("https://weread.qq.com")}
                # Poll local browser state while waiting; do not hit userInfo every
                # two seconds for the full QR lifetime.
                if any(current.get(k) != prior.get(k) for k in ("wr_vid", "wr_skey", "wr_localvid")):
                    self.session_source = "web_login"
                    if vid := await self.identity():
                        return vid
                    prior = current
                await asyncio.sleep(2)
        finally:
            qr.unlink(missing_ok=True)
        raise Failure("LOGIN_TIMEOUT", "网页登录超时，重新运行 login web 可刷新二维码。")

    async def metadata(self, ident):
        # Parse the server's snapshot before SPA hydration clears it.
        response = await self.page.goto(f"https://weread.qq.com/web/bookDetail/{ident}", {"waitUntil": "domcontentloaded", "timeout": 45000})
        source = await response.text()
        match = re.search(r"window\.__INITIAL_STATE__\s*=\s*", source)
        if not match:
            raise Failure("CATALOGUE_UNAVAILABLE", "网页未返回可解析书目；使用 omni 搜索得到的数字 book_id 重试。")
        initial, _ = json.JSONDecoder().raw_decode(source[match.end():])
        r = initial.get("reader", {}); b = r.get("bookInfo", {})
        state = {"book_id": str(b.get("bookId", "")), "title": b.get("title"), "author": b.get("author"),
                 "intro": b.get("intro"), "cover": b.get("cover"), "soldout": b.get("soldout"),
                 "auto_purchase_enabled": b.get("isAutoPay") in (1, True, "1"),
                 "chapters": [{"id": str(c["chapterUid"]), "title": c["title"], "words": c.get("wordCount",0), "level":c.get("level",1)} for c in r.get("chapterInfos", [])]}
        if not state.get("title") or not state.get("chapters"):
            raise Failure("CATALOGUE_UNAVAILABLE", "书籍页面未返回可用目录，可能为格式限制或页面变化。")
        if web_id(state["book_id"]) != ident:
            raise Failure("BOOK_ID_MISMATCH", "网页书籍身份与请求不符，已停止导出。")
        state.update(web_id=ident, account=self.account, source_url=f"https://weread.qq.com/web/bookDetail/{ident}")
        return state

    async def screen(self):
        return await self.page.evaluate("""() => {
          const h=window.__wereadLibraryCapture;
          const visible=e=>e.getClientRects().length>0 && getComputedStyle(e).visibility!=='hidden'
            && getComputedStyle(e).display!=='none';
          const elements=s=>[...document.querySelectorAll(s)].filter(visible);
          const horizontal=elements('button.readerControls_item.isHorizontalReader').length>0;
          // A spread may contain two different chapters. Never merge it into one chapter.
          const capture=h && !horizontal ? h.snapshot() : {};
          const content=s=>elements(s).map(e=>e.innerText).join(' ').slice(0,4000);
          const buttons=elements('button.readerFooter_button').map(e=>e.innerText.trim());
          const walls='.readerPayWall, .payDialog, .payChapterDialog, .wr_horizontal_reader_needPay_container';
          const dialogs=content('[role=dialog], .dialog, .wr_dialog, .login_dialog_content');
          return {text:capture.markdown||'',ready:!!capture.complete,overflow:!!capture.overflow,buttons,dialogs,horizontal_reader:horizontal,
            footer:content('.readerFooter'), paywalls:content(walls),paywall_visible:elements(walls).length>0,
            trial_end_visible:elements('.readerFooter_trial_end').length>0,
            title:document.title,body:document.body.innerText.slice(-2500)};
        }""")

    async def use_scroll_reader(self):
        # Verified control in WeRead's live double-page layout. Hydration can precede
        # binding its click handler, so allow one bounded retry after checking the result.
        selector = 'button.readerControls_item.isHorizontalReader'
        for _ in range(2):
            try:
                if not await self.page.querySelector(selector):
                    return
            except pyppeteer.errors.NetworkError:
                await asyncio.sleep(1)
                continue
            await self.page.evaluate("() => window.__wereadLibraryCapture?.reset()")
            await self.page.click(selector)
            deadline = time.monotonic()+8
            while time.monotonic() < deadline:
                try:
                    changed = await asyncio.wait_for(self.page.evaluate("() => !document.querySelector('button.readerControls_item.isHorizontalReader') && !!document.querySelector('button.readerControls_item.isNormalReader')"),timeout=3)
                    if changed:
                        emit({"event":"reader_layout_changed", "layout":"scroll", "visible":self.visible})
                        return
                except (pyppeteer.errors.NetworkError, asyncio.TimeoutError):
                    # Switching layouts may reload the document and invalidate its context.
                    pass
                await asyncio.sleep(0.5)
        raise Failure("READER_LAYOUT_UNSUPPORTED", "未能切换到可核验的滚动阅读模式，已停止，未把双页混合内容记为一个章节。")

    async def chapter(self, ident, uid, last=False, timeout=150):
        await self.page.goto(f"https://weread.qq.com/web/reader/{ident}k{wr_hash(str(uid))}",
                             {"waitUntil": "domcontentloaded", "timeout": 45000})
        deadline, pages, previous, stable, latest = time.monotonic()+timeout, [], None, 0, None
        scrolled, previous_gate = False, None
        while time.monotonic() < deadline:
            try:
                state = await self.screen()
            except pyppeteer.errors.NetworkError as e:
                if "context" not in str(e).lower():
                    raise
                # SPA redirects can replace the context after DOMContentLoaded.
                await asyncio.sleep(1)
                continue
            if state.get("overflow"):
                raise Failure("INCOMPLETE_CHAPTER", "章节绘图数据超过上限，未把截断内容认作完整章节。")
            issue = reader_issue(state)
            if issue:
                signature = (issue["code"], issue.get("access",{}).get("reason"))
                if issue["code"] not in ("HUMAN_VERIFICATION", "RATE_LIMITED") and signature != previous_gate:
                    previous_gate = signature
                    await asyncio.sleep(1)
                    continue
                self.last_access_screen = {k:v for k,v in state.items() if k not in ("text","body")}
                raise Failure(issue["code"], issue["message"], chapter_id=str(uid),
                              **{k:v for k,v in issue.items() if k not in ("code","message")})
            previous_gate = None
            if state.get("horizontal_reader"):
                await self.use_scroll_reader()
                pages, previous, stable, latest, scrolled = [], None, 0, None, False
                continue
            text = state["text"].strip()
            fingerprint = hashlib.sha256(text.encode()).hexdigest()
            stable = stable + 1 if text and fingerprint == latest else 0
            latest = fingerprint
            if not state["ready"] or stable < 2:
                await asyncio.sleep(1)
                continue
            if not scrolled:
                await self.page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                scrolled = True
                await asyncio.sleep(1)
                continue
            if "下一页" in state["buttons"]:
                if fingerprint == previous:
                    # A clicked page must produce changed content; don't repeatedly click through it.
                    await asyncio.sleep(1); continue
                pages.append(text); previous = fingerprint
                await self.page.evaluate("""() => {
                  const h=window.__wereadLibraryCapture; h.reset();
                  const b=[...document.querySelectorAll('button.readerFooter_button')].find(e=>e.innerText.trim()==='下一页');
                  if(b)b.click();
                }""")
                stable, latest, scrolled = 0, None, False
                await asyncio.sleep(1)
                continue
            finished = any(re.search(r"下一章|全\s*书\s*完|已读完|阅读完成", b) for b in state["buttons"])
            # The real last-page label is spaced ("全 书 完") and has no footer button.
            finished = finished or (last and bool(re.search(r"全\s*书\s*完|已读完|阅读完成", state["body"])))
            if finished:
                current = urlparse(self.page.url).path.rsplit("/",1)[-1]
                if current != ident + "k" + wr_hash(str(uid)):
                    raise Failure("CHAPTER_REDIRECTED", "阅读器跳转到其他章节，未将内容记入原章节。", requested_chapter=str(uid))
                if not pages or text != pages[-1]:
                    pages.append(text)
                return "\n\n".join(pages)
            if state["buttons"]:
                raise Failure("READER_UI_CHANGED", "阅读器出现未知的章节结束按钮，未把当前内容认作完整章节。")
            await asyncio.sleep(1)
        raise Failure("CHAPTER_TIMEOUT", "章节正文或翻页状态未在限定时间内稳定，缓存已保留。")

    async def close(self):
        if self.browser:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.browser.close(), timeout=15)


async def execute(args):
    if args.mode == "resolve":
        return {"ok": True, "web_id": web_id(args.book)}
    reader = Reader(args.account, args.expected_vid, visible=args.visible)
    directory = Path(args.directory).resolve() if args.directory else None
    failure, current_chapter = None, None
    try:
        await reader.open()
        if args.mode == "login":
            vid = await reader.login()
            return {"ok": True, "stage": "web", "account": args.account, "vid": vid}
        ident = web_id(args.book)
        if args.mode == "probe":
            return {"ok": True, "metadata": await reader.metadata(ident)}
        # Reuse a valid private web session when present, but anonymous readable chapters
        # do not require another login. Ask only after the reader actually gates access.
        if await reader.identity():
            emit({"event":"session_reused", "source":reader.session_source, "verified":True})
        elif reader.session_source == "omni":
            raise Failure("WEB_LOGIN_REQUIRED", "网页未接受当前 Omni 会话；先由 Omni 检查或续期后再试，不能据此认定会员权益不足。")
        catalogue = read_json(directory / "api-catalogue.json", {})
        info = read_json(directory / "api-info.json", {})
        if info.get("bookId") and catalogue.get("chapters"):
            if web_id(str(info["bookId"])) != ident:
                raise Failure("BOOK_ID_MISMATCH", "API 元数据与导出 ID 不一致。")
            expected_chapters = catalogue["chapters"]
            if any(c.get("chapterUid") is None for c in expected_chapters):
                raise Failure("CATALOGUE_CHANGED", "API 目录缺少章节 ID，无法核验完整性。")
            meta = {"book_id": str(info["bookId"]), "web_id": ident, "title":info["title"], "author": info.get("author",""),
                    "cover":info.get("cover"), "account":args.account, "source_url":f"https://weread.qq.com/web/bookDetail/{ident}"}
            meta.update({k:info[k] for k in ("translator","publisher","isbn","publishTime") if info.get(k) is not None})
            meta["reader"] = "builtin-canvas-v1"
            meta["chapters"] = [{"id": str(c["chapterUid"]), "title": c.get("title", "未命名章节"), "words": c.get("wordCount", 0)}
                for c in expected_chapters]
            meta["catalogue_source"] = "omni_api"
        else:
            meta = await reader.metadata(ident)
            meta["catalogue_source"] = "web_only"
        private_dir(directory / "chapters")
        old = read_json(directory / "meta.json", {})
        if old.get("web_id") and old["web_id"] != ident:
            raise Failure("BOOK_ID_MISMATCH", "目标目录属于另一本书，请使用新的目录。")
        if old.get("account") and old["account"] != args.account:
            raise Failure("ACCOUNT_MISMATCH", "目标缓存属于另一个账号，已停止。")
        write_json(directory / "meta.json", meta)
        if info.get("isAutoPay") in (1, True, "1") or meta.get("auto_purchase_enabled"):
            raise Failure("AUTO_PURCHASE_ENABLED", "此书已开启自动购买；请先在官方客户端关闭，再继续在线读取。未更改你的购买设置。")
        checkpoints = read_json(directory / "checkpoints.json", {})
        processed = 0
        for i, chapter in enumerate(meta["chapters"], 1):
            uid = chapter["id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", uid):
                raise Failure("INVALID_CHAPTER_ID", "目录章节 ID 不合法。")
            path = directory / "chapters" / f"{i:04d}-{uid}.md"
            previous = checkpoints.get(uid, {})
            if path.exists() and previous.get("finished") and previous.get("sha256") == hashlib.sha256(path.read_bytes()).hexdigest():
                continue
            if args.limit and processed >= args.limit:
                break
            if processed:
                # Pause only between newly captured chapters, never after the last
                # one or while skipping already cached chapters.
                delay = args.interval if args.interval is not None else random.uniform(3, 7)
                await asyncio.sleep(delay)
            current_chapter = {"chapter_id":uid, "chapter_index":i, "chapter_title":chapter["title"]}
            emit({"event": "chapter", "index": i, "total": len(meta["chapters"]), "title": chapter["title"]})
            if chapter["title"].strip() == "封面" and meta.get("cover") and chapter.get("words",0) <= 20:
                text = f"![封面]({meta['cover']})"
            else:
                for attempt in range(2):
                    try:
                        text = await reader.chapter(ident, uid, last=i==len(meta["chapters"]))
                        break
                    except (pyppeteer.errors.TimeoutError, pyppeteer.errors.NetworkError):
                        if attempt:
                            raise Failure("CHAPTER_TIMEOUT", "章节加载连续失败，已停止；重试将从缓存继续。")
                        await asyncio.sleep(3)
            text = f"# {chapter['title']}\n\n{text}\n"
            text = await save_images(text, directory)
            q = quality(text, chapter.get("words", 0))
            if q["issues"]:
                atomic_text(path.with_suffix(".suspect.md"), text)
                raise Failure("INCOMPLETE_CHAPTER", "检测到可能缺失的章节正文，未计入完整章节。", chapter=chapter["title"], issues=q["issues"])
            atomic_text(path, text)
            checkpoints[uid] = {"finished": True, "sha256": hashlib.sha256(text.encode()).hexdigest(), "captured_at": stamp()}
            write_json(directory / "checkpoints.json", checkpoints)
            processed += 1
    except Failure as e:
        failure = {"code": e.code, "message": str(e), **e.details}
        if current_chapter:
            failure.update(current_chapter)
        if directory and reader.page:
            with contextlib.suppress(Exception):
                shot = private_dir(directory / "diagnostics") / "last-screen.png"
                await reader.page.screenshot({"path": str(shot)})
                shot.chmod(0o600)
                failure["screenshot_path"] = str(shot)
                if reader.last_access_screen:
                    diagnostic = shot.with_name("access-ui.json")
                    write_json(diagnostic, reader.last_access_screen)
                    failure["ui_diagnostic_path"] = str(diagnostic)
    except Exception as e:
        # No raw browser exception/HTML/Cookie output.
        failure = {"code": "BROWSER_ERROR", "message": "后台阅读器未完成；请根据诊断结果检查网络或页面变化。", "error_type": type(e).__name__}
    finally:
        await reader.close()
    result = {"ok": failure is None}
    if failure:
        result["error"] = failure
        result.update(fallback_for(failure))
    if directory and (directory / "meta.json").exists():
        # The checkpoint is the acquisition record; manifest derives this diagnostic.
        # Never rewrite another book/account's checkpoint after rejecting its directory.
        if not failure or failure["code"] not in ("BOOK_ID_MISMATCH","ACCOUNT_MISMATCH"):
            points = read_json(directory / "checkpoints.json", {})
            points["_last_attempt"] = {"at":stamp(), "account":args.account,
                                      "outcome":"stopped" if failure else "finished",
                                      **({"error":failure} if failure else {})}
            write_json(directory / "checkpoints.json", points)
        manifest = build(directory,args.format)
        result["manifest_path"] = str(directory / "manifest.json")
        result["manifest"] = {k:manifest[k] for k in ("title", "status", "completeness", "expected_chapters", "captured_chapters", "suspect_chapters", "missing_chapters", "chapter_coverage", "artifacts")}
        result["manifest"]["last_attempt"] = manifest.get("last_attempt")
    return result


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("login", "export", "probe", "resolve"))
    parser.add_argument("--account", default="default")
    parser.add_argument("--book")
    parser.add_argument("--directory")
    parser.add_argument("--expected-vid")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--interval", type=float, default=None,
                        help="指定固定章节间隔（秒）；默认每次随机等待 3～7 秒，仅在新章节之间等待。")
    parser.add_argument("--format",action="append",choices=("epub","md","txt","html"))
    parser.add_argument("--visible", action="store_true", help="需要人工操作或排查时显示专用浏览器，默认后台运行。")
    args = parser.parse_args()
    try:
        with lock("web-" + account_name(args.account)):
            result = asyncio.run(asyncio.wait_for(execute(args), timeout=3600))
        emit(result)
        return 0 if result["ok"] else 1
    except Failure as e:
        emit({"ok": False, "error": {"code": e.code, "message": str(e)}}); return 1
    except (asyncio.TimeoutError, KeyboardInterrupt):
        emit({"ok": False, "error": {"code": "TIMEOUT", "message": "本次任务达到时限或被取消，已有章节可继续使用。"}}); return 1


if __name__ == "__main__":
    raise SystemExit(main())
