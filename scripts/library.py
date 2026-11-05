#!/usr/bin/env python3
"""Agent-facing JSON CLI. Run --help for commands; no secrets in outputs."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import sys

import corpus
import catalogue
from book_ids import web_id
from access import fallback_for
from runtime import (DATA_HOME, RUNTIME, SCRIPT_DIR, Failure, account_name, atomic_text, default_account, doctor,
                     emit, env_for_node, json_output, lock, node_path, omni, private_dir,
                     python_path, read_json, run, setup, write_json)


def normalize(text):
    return re.sub(r"[\W_]", "", text).lower()


def search(title, author, account, pages=3):
    books, seen, cursors, cursor, terminal = [], set(), set(), 0, "limit"
    for _ in range(pages):
        page = omni(["search", "books", title, "--scope", "10", "--count", "20", "--max-idx", str(cursor)], account)
        if not isinstance(page, dict) or not isinstance(page.get("books"), list):
            raise Failure("RESPONSE_CHANGED", "搜索结果结构发生变化，未自动选择书籍。")
        entries = page["books"]
        for row in entries:
            book = row.get("bookInfo", row.get("book", row))
            if not isinstance(book, dict) or not book.get("bookId") or not book.get("title"):
                continue
            ident = str(book["bookId"])
            if ident in seen:
                continue
            seen.add(ident)
            item = {key: book.get(key) for key in ("title", "author", "translator", "publisher", "publishTime", "isbn", "intro", "soldout") if book.get(key) is not None}
            item["book_id"] = ident
            item["exact_title"] = normalize(book["title"]) == normalize(title)
            item["author_match"] = bool(author and normalize(author) in normalize(book.get("author", "")))
            item["match_score"] = (100 if item["exact_title"] else 40 if normalize(title) in normalize(book["title"]) else 0) + (30 if item["author_match"] else 0)
            books.append(item)
        if not page.get("hasMore") or not entries:
            terminal = "exhausted"; break
        next_cursor = entries[-1].get("searchIdx")
        if next_cursor is None or next_cursor in cursors or next_cursor == cursor:
            terminal = "missing_or_repeated_cursor"; break
        cursors.add(next_cursor); cursor = next_cursor
    books.sort(key=lambda b: -b["match_score"])
    return {"query": title, "candidates": books[:10], "pagination_terminal": terminal,
            "selection_required": len([b for b in books if b["exact_title"] and (not author or b["author_match"])]) != 1,
            "note": "导出前核对作者、译者、出版社；无精确匹配时不要直接采用首条结果。"}


def browser_command(mode, account, book=None, directory=None, *, capture=True, limit=0, expected_vid=None, visible=False, formats=None):
    if not python_path().exists():
        raise Failure("DEPENDENCY_MISSING", "先运行 setup。")
    cmd = [python_path(), SCRIPT_DIR / "export_runner.py", mode, "--account", account]
    if book:
        cmd += ["--book", book]
    if directory:
        cmd += ["--directory", str(directory)]
    if limit:
        cmd += ["--limit", str(limit)]
    if expected_vid:
        cmd += ["--expected-vid", str(expected_vid)]
    if visible:
        cmd += ["--visible"]
    for ext in formats or []:
        cmd += ["--format", ext]
    return run(cmd, timeout=3650 if mode == "export" else 340 if mode == "login" else 100, capture=capture)


def book_directory(book, account):
    return DATA_HOME / "books" / account_name(account) / web_id(book)


def cached_result(directory, formats=None):
    directory = Path(directory)
    if not corpus.cache_status(directory)["usable"]:
        return None
    manifest = corpus.load_manifest(directory)
    requested = set(formats or ["epub"])
    repair = manifest.get("version") != 2 or manifest.get("metadata_sha256")!=corpus.metadata_digest(read_json(directory/"meta.json",{}))
    for ext in requested:
        path = Path(manifest.get("artifacts",{}).get(ext,"/missing"))
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=manifest.get("artifact_hashes",{}).get(ext):
            repair = True
    if repair:
        manifest = corpus.build(directory, requested)
        if manifest["status"] != "catalogue_complete":
            return None
    return {"ok":True,"cached":True,"network_used":False,"title":manifest["title"],"author":manifest.get("author"),
            "directory":str(directory),"status":manifest["status"],"chapter_coverage":manifest["chapter_coverage"],
            "artifacts":{ext:manifest["artifacts"][ext] for ext in sorted(requested)},
            "next_action":"read_local_book"}


def find_book(title, author=None, account=None, online=False, refresh=False):
    local = catalogue.search(title, author, account)
    exact = [b for b in local if b["exact_title"]]
    if exact and not refresh:
        selected = exact[0] if len(exact)==1 else None
        if selected:
            selected["cache"] = corpus.cache_status(selected["directory"])
        return {"query":title,"source":"local","network_used":False,"candidates":local,
                "selection_required":len(exact)!=1,"selected":selected,
                "next_action":"confirm_edition" if not selected else "read_local_book" if selected["cache"]["usable"] else "resume_download"}
    if not online:
        return {"query":title,"source":"local","network_used":False,"candidates":local,
                "selection_required":bool(local),"next_action":"search_weread"}
    result = search(title, author, account or default_account())
    result.update(source="weread",network_used=True,local_candidates=local)
    result["next_action"] = "ask_web_fallback" if not result["candidates"] else "confirm_edition" if result["selection_required"] else "download_selected"
    if not result["candidates"]:
        result["fallback"] = {"requires_user_consent":True,"reason":"weread_not_found",
                              "question":"微信读书没有找到这本书，要尝试在网上寻找可用的 PDF 或 EPUB 版本吗？"}
    return result


def export_book(book, account=None, formats=None, refresh=False, limit=0, visible=False):
    ident = web_id(book)
    if not refresh:
        for item in catalogue.search(ident,account=account):
            if item.get("web_id") == ident:
                cached = cached_result(item["directory"],formats)
                if cached:
                    return cached
    account = account or default_account()
    directory = book_directory(book,account)
    identity = omni(["doctor"],account).get("auth",{})
    if str(book).isdigit():
        write_json(directory/"api-info.json",omni(["book","info",str(book)],account))
        write_json(directory/"api-catalogue.json",omni(["book","chapters",str(book)],account))
    return browser_command("export",account,book,directory,capture=False,limit=max(0,limit),
                           expected_vid=identity.get("vid"),visible=visible,formats=formats)


def verify(directory):
    directory = Path(directory).resolve()
    manifest = corpus.load_manifest(directory)
    if not manifest:
        raise Failure("MANIFEST_MISSING", "尚未导出或建立本地书库。")
    damaged = []
    for chapter in manifest["chapters"]:
        if chapter.get("sha256"):
            path = Path(chapter["file"])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != chapter["sha256"]:
                damaged.append(chapter["index"])
    epub = manifest.get("artifacts", {}).get("epub")
    package = corpus.validate_epub(epub) if epub and Path(epub).is_file() else None
    invalid = [ext for ext,path in manifest.get("artifacts",{}).items() if not Path(path).is_file() or
               (manifest.get("artifact_hashes",{}).get(ext) and hashlib.sha256(Path(path).read_bytes()).hexdigest()!=manifest["artifact_hashes"][ext])]
    return {"ok": not damaged and not invalid and bool(manifest.get("artifacts")), "status": manifest["status"], "completeness": manifest["completeness"],
            "chapter_coverage": manifest["chapter_coverage"], "damaged_chapters": damaged, "damaged_artifacts":invalid, "epub": package}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="微信读书找书、导出与有出处的原文检索；输出 JSON。")
    parser.add_argument("--account", default=os.environ.get("WEREAD_ACCOUNT"))
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("doctor"); p.add_argument("--online", action="store_true")
    sub.add_parser("setup")
    p = sub.add_parser("login"); p.add_argument("target", choices=("api", "web")); p.add_argument("--visible", action="store_true")
    p = sub.add_parser("search"); p.add_argument("title"); p.add_argument("--author"); p.add_argument("--pages", type=int, default=3)
    p = sub.add_parser("find",help="优先搜索本地书库，命中完整缓存无需登录")
    p.add_argument("title"); p.add_argument("--author"); p.add_argument("--online",action="store_true"); p.add_argument("--refresh",action="store_true")
    p = sub.add_parser("local-search",help="仅搜索本地书名、作者、ISBN")
    p.add_argument("query",nargs="?",default=""); p.add_argument("--author")
    for name in ("inspect", "export", "probe"):
        p = sub.add_parser(name); p.add_argument("book")
        if name == "export":
            p.add_argument("--limit", type=int, default=0, help="本次新下载的章节数；0 表示全部，缓存继续有效。")
            p.add_argument("--visible", action="store_true", help="排查时显示阅读器，默认后台连续读取。")
            p.add_argument("--refresh",action="store_true",help="主动检查远端目录；默认完整缓存直接复用")
            p.add_argument("--format",action="append",choices=(*corpus.FORMATS,"all"))
    p = sub.add_parser("format",help="只用本地章节生成所选格式")
    p.add_argument("directory"); p.add_argument("--format",action="append",choices=(*corpus.FORMATS,"all"),required=True)
    p = sub.add_parser("ask-remote"); p.add_argument("book"); p.add_argument("question")
    p = sub.add_parser("ingest"); p.add_argument("file"); p.add_argument("--out")
    p.add_argument("--title"); p.add_argument("--author"); p.add_argument("--source-url")
    p.add_argument("--format",action="append",choices=(*corpus.FORMATS,"all"))
    sub.add_parser("list")
    for name in ("index", "verify", "toc", "read", "query"):
        p = sub.add_parser(name); p.add_argument("directory")
        if name == "read":
            p.add_argument("chapter", type=int); p.add_argument("--start", type=int, default=0); p.add_argument("--length", type=int, default=6000)
        if name == "query":
            p.add_argument("question"); p.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    try:
        online = args.command in ("doctor", "login", "search", "inspect", "probe", "ask-remote")
        account = account_name(args.account) if args.account else default_account() if online else None
        requested_formats = getattr(args,"format",None)
        if requested_formats and "all" in requested_formats:
            requested_formats = list(corpus.FORMATS)
        if args.command == "setup":
            result = setup()
        elif args.command == "doctor":
            result = doctor()
            cached = read_json(DATA_HOME / "auth" / account / "web-session.json", {})
            result["web_session"] = {"status": "cached_not_verified" if cached else "not_verified",
                                     "source":cached.get("source"), "last_verified_at":cached.get("verified_at"),
                                     "next_export":"reuse_omni_session_in_background"}
            if args.online and result["omni_installed"]:
                try:
                    upstream = omni(["doctor"], account)
                    result["api"] = {"ok":upstream.get("ok"), "account":account,
                                     "status":upstream.get("auth",{}).get("status"),
                                     "version":upstream.get("cli",{}).get("version")}
                except Failure as e:
                    result["api"] = {"status": e.code, "message": str(e)}
        elif args.command == "login":
            if args.target == "api":
                if not node_path():
                    raise Failure("DEPENDENCY_MISSING", "先运行 setup。")
                with lock("api-" + account):
                    r = run([node_path(), SCRIPT_DIR / "login.mjs", RUNTIME, account, private_dir(DATA_HOME / "auth" / account)],
                            env=env_for_node(), timeout=325, capture=False)
            else:
                r = browser_command("login", account, capture=False, visible=args.visible)
            return r.returncode
        elif args.command == "search":
            result = search(args.title, args.author, account, max(1, min(args.pages, 5)))
        elif args.command == "local-search":
            result = {"source":"local","network_used":False,"candidates":catalogue.search(args.query,args.author,account)}
        elif args.command == "find":
            result = find_book(args.title,args.author,account,args.online,args.refresh)
        elif args.command == "inspect":
            if not re.fullmatch(r"[A-Za-z0-9_-]+", args.book):
                raise Failure("INVALID_BOOK_ID", "inspect 使用搜索返回的原始 book_id。网页链接请用 probe。")
            result = {"info": omni(["book", "info", args.book], account), "chapters": omni(["book", "chapters", args.book], account)}
        elif args.command == "probe":
            r = browser_command("probe", account, args.book)
            result = json_output(r.stdout)
            if not result:
                raise Failure("BROWSER_ERROR", "网页探测未返回结果。")
        elif args.command == "export":
            result = export_book(args.book,account,requested_formats,args.refresh,args.limit,args.visible)
            if hasattr(result,"returncode"):
                return result.returncode
        elif args.command == "format":
            directory = Path(args.directory).expanduser().resolve()
            result = cached_result(directory,requested_formats)
            if result is None:
                result = corpus.build(directory,requested_formats)
        elif args.command == "ask-remote":
            answer = omni(["ai", "ask-book", args.book, args.question], account, retry=False)
            result = {"source": "weread_ai", "original_text_verified": False,
                      "answer": {"text": answer.get("text", ""), "complete": answer.get("complete", False)},
                      "note": "微信读书 AI 回答是二手解读，不能当作已读取全书的证据。"}
        elif args.command == "ingest":
            source = Path(args.file).expanduser().resolve()
            if not source.is_file():
                raise Failure("FILE_NOT_FOUND", "本地电子书文件不存在。")
            if source.stat().st_size>200_000_000:
                raise Failure("FILE_TOO_LARGE","单书导入上限 200 MB。")
            identity = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
            directory = Path(args.out).expanduser().resolve() if args.out else DATA_HOME / "books" / "local" / identity
            with lock("local-" + identity):
                existing=read_json(directory/"meta.json",{})
                if existing and existing.get("source_file",existing.get("source_url")) != str(source):
                    raise Failure("OUTPUT_EXISTS", "目标目录已有其他书籍。")
                if existing.get("source_sha256")==hashlib.sha256(source.read_bytes()).hexdigest() and not any((args.title,args.author,args.source_url)):
                    result=cached_result(directory,requested_formats)
                    if result:
                        emit(result); return 0
                pdf_data=None
                if source.suffix.lower()==".pdf":
                    from pdf_support import extract_pdf
                    pdf_data=extract_pdf(source)
                result = corpus.ingest(source,directory,title_override=args.title,author_override=args.author,
                                       source_url=args.source_url,formats=requested_formats,pdf_data=pdf_data)
        elif args.command == "list":
            result = catalogue.search(account=account,limit=10000)
        else:
            directory = Path(args.directory).expanduser().resolve()
            if args.command == "index":
                with lock("index-" + hashlib.sha256(str(directory).encode()).hexdigest()[:16]):
                    result = corpus.build(directory)
            elif args.command == "verify":
                result = verify(directory)
            elif args.command == "toc":
                result = corpus.load_manifest(directory)
                if result is None:
                    raise Failure("MANIFEST_MISSING", "找不到书籍清单。")
            elif args.command == "query":
                result = corpus.retrieve(directory, args.question, max(1, min(args.limit, 12)))
            elif args.command == "read":
                manifest = corpus.load_manifest(directory)
                if not manifest or args.chapter < 1 or args.chapter > len(manifest["chapters"]):
                    raise Failure("CHAPTER_NOT_FOUND", "目录中没有这个章节序号。")
                item = manifest["chapters"][args.chapter-1]
                path = Path(item["file"])
                if not path.exists():
                    raise Failure("CHAPTER_MISSING", "该章节尚未成功导出。")
                text = path.read_text(encoding="utf-8")
                start = max(0, args.start); end = min(len(text), start + max(1, min(args.length, 12000)))
                result = {"chapter": item, "char_start": start, "char_end": end,
                          "next_start": end if end<len(text) else None, "text": text[start:end],
                          "line_start": text.count("\n",0,start)+1, "line_end": text.count("\n",0,end)+1}
        emit(result)
        return 1 if isinstance(result, dict) and result.get("ok") is False else 0
    except Failure as e:
        error = {"code": e.code, "message": str(e), **e.details}
        emit({"ok": False, "error": error, **fallback_for(error)})
        return 1
    except (OSError, ValueError, KeyError) as e:
        emit({"ok": False, "error": {"code": "LOCAL_ERROR", "error_type": type(e).__name__, "message": "本地文件或响应结构不符合预期；先运行 doctor 并检查输入。"}})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
