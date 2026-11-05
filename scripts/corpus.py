"""EPUB packaging, completeness accounting, and local Chinese/English retrieval."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sqlite3
import zipfile
import xml.etree.ElementTree as ET

from runtime import Failure, atomic_text, lock, private_dir, read_json, write_json

FORMATS = ("epub", "md", "txt", "html")
BOOK_FIELDS = ("book_id", "web_id", "title", "author", "translator", "publisher", "isbn", "source_url", "account")


def metadata_digest(meta):
    data={k:meta.get(k) for k in (*BOOK_FIELDS,"chapters")}
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def load_manifest(directory):
    """Join authoritative metadata with rebuildable local state, including v1 caches."""
    directory = Path(directory)
    state = read_json(directory/"manifest.json")
    if state is None:
        return None
    meta = read_json(directory/"meta.json", {})
    joined = {**state, **{k:meta[k] for k in BOOK_FIELDS if k in meta}}
    titles = {str(c["id"]):c["title"] for c in meta.get("chapters",[])}
    joined["chapters"] = [{**c,"title":titles.get(str(c["id"]),c.get("title","未命名"))} for c in state.get("chapters",[])]
    return joined


def write_manifest(directory, manifest):
    state = {k:v for k,v in manifest.items() if k not in BOOK_FIELDS}
    state["version"] = 2
    state["metadata_file"] = "meta.json"
    state["chapters"] = [{k:v for k,v in c.items() if k != "title"} for c in state["chapters"]]
    write_json(Path(directory)/"manifest.json",state)


def cache_status(directory):
    directory = Path(directory)
    state = load_manifest(directory)
    if not state or state.get("status") != "catalogue_complete":
        return {"usable":False,"reason":"missing_or_partial"}
    meta = read_json(directory/"meta.json", {})
    expected = [str(c["id"]) for c in meta.get("chapters",[])]
    if expected != [str(c["id"]) for c in state.get("chapters",[])]:
        return {"usable":False,"reason":"catalogue_changed"}
    invalid = []
    for c in state["chapters"]:
        path = Path(c["file"])
        if c.get("status") != "captured" or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=c.get("sha256"):
            invalid.append(c["index"])
    if invalid:
        return {"usable":False,"reason":"chapter_changed_or_missing","chapters":invalid}
    return {"usable":True,"reason":"verified_local_chapters"}


def plain_text(text):
    text = re.sub(r"(?m)^#{1,6}\s+", "", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"[插图：\1]", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return re.sub(r"</?(?:sup|sub)>","",text).replace("`","")


def make_html(path, title, author, chapters, directory):
    import base64
    sections=[]
    for i,(name,text) in enumerate(chapters,1):
        body=to_xhtml(name,text).split("<body>",1)[1].split("</body>",1)[0]
        def embedded(match):
            asset=directory/match[1]
            if not asset.is_file():
                raise Failure("MISSING_IMAGE","本地插图缺失，不能生成完整 HTML。")
            mime={"jpg":"jpeg","png":"png","gif":"gif","webp":"webp"}[asset.suffix[1:]]
            return 'src="data:image/'+mime+';base64,'+base64.b64encode(asset.read_bytes()).decode()+'"'
        body=re.sub(r'src="(images/[a-f0-9]{64}\.(?:jpg|png|gif|webp))"',embedded,body)
        sections.append(f'<section id="c{i}">{body}</section>')
    nav=''.join(f'<li><a href="#c{i}">{html.escape(name)}</a></li>' for i,(name,_) in enumerate(chapters,1))
    document='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    document+='<title>'+html.escape(title)+'</title><style>body{max-width:48rem;margin:3rem auto;padding:0 1.5rem;font:18px/1.9 serif;color:#232323}h1,h2,h3{line-height:1.4}img{max-width:100%}section{margin-top:4rem}@media print{nav{display:none}section{break-before:page}body{margin:0;max-width:none;font-size:12pt}}</style>'
    document+='<h1>'+html.escape(title)+'</h1><p>'+html.escape(author)+'</p><nav><ol>'+nav+'</ol></nav>'+''.join(sections)+'</html>'
    atomic_text(path,document)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def body_text(text):
    text = re.sub(r"^#{1,6}\s+.*$", "", text, flags=re.M)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    return re.sub(r"\s+", "", text)


def quality(text, expected_words=0):
    body = body_text(text)
    image_count = len(re.findall(r"!\[[^\]]*\]\([^)]*\)", text))
    image_only_frontmatter = image_count > 0 and expected_words <= 20
    # The reader renders its own short rights pages for both Originals and licensed
    # books. Require a complete recognizable template, including the closing notice;
    # catalogue wordCount can describe a different page and is not sufficient here.
    original_rights = all(label in body for label in ("书名：", "作者：", "出品时间：",
        "本书由微信读书出品并制作与发行", "版权所有·侵权必究"))
    licensed_rights = (
        all(re.search(r"(?m)^" + label + r"[：:]\s*\S+", text)
            for label in ("书名", "作者", "出版社", "品牌方"))
        and re.search(r"(?m)^出版时间[：:]\s*\d{4}-\d{2}-\d{2}\s*$", text)
        and re.search(r"(?m)^ISBN[：:]\s*97[89][\d-]{10,14}\s*$", text)
        and re.search(r"本书由[^\n]{2,100}授权微信读书进行制作与发行\s*版权所有·侵权必究\s*$", text))
    platform_copyright = (bool(re.search(r"(?m)^#{1,6}\s+版权信息\s*$", text))
                          and 50 <= len(body) < 400
                          and bool(original_rights or licensed_rights))
    issues = []
    # Only classify short boilerplate as a wall, not these words in ordinary prose.
    if len(body) < 350 and re.search(r"购买本书|开通会员.*阅读|扫码.*登录|暂不支持.*阅读|免费试读.*结束|验证后.*继续", body):
        issues.append("access_prompt")
    if not body and not image_only_frontmatter:
        issues.append("empty_body")
    # Titles/figure labels are counted differently by the catalogue. Allow a small
    # absolute difference for short frontmatter, while rejecting long truncations.
    deficit = int(expected_words or 0) - len(body)
    if expected_words and deficit > max(80, int(expected_words) * 0.10) and not image_only_frontmatter and not platform_copyright:
        issues.append("shorter_than_catalogue")
    return {"body_characters": len(body), "image_count": image_count, "issues": issues,
            **({"source_note":"reader_generated_copyright"} if platform_copyright else {})}


def chunk_text(text, size=1600, overlap=200):
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + size // 2, end)
            if boundary > start:
                end = boundary + 1
        yield start, end, text[start:end]
        if end == len(text):
            break
        start = max(start + 1, end - overlap)


def tokens(text):
    out = re.findall(r"[a-z0-9_]+", text.lower())
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        out.extend(run[i:i+2] for i in range(len(run)-1))
        out.extend(run)
    return out


def to_xhtml(title, text):
    blocks = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        match = re.fullmatch(r"(#{1,6})\s+([^\n]+)", paragraph)
        if match:
            n = len(match[1]); blocks.append(f"<h{n}>{html.escape(match[2])}</h{n}>")
        else:
            pieces = re.split(r"(!\[[^\]]*\]\([^)]*\))", paragraph)
            rendered = []
            for piece in pieces:
                image = re.fullmatch(r"!\[([^\]]*)\]\((images/[a-f0-9]{64}\.(?:png|jpg|gif|webp))\)", piece)
                if image:
                    rendered.append(f'<img src="{image[2]}" alt="{html.escape(image[1])}" style="max-width:100%"/>')
                elif re.fullmatch(r"!\[[^\]]*\]\([^)]*\)", piece):
                    rendered.append("[插图未嵌入，请参阅原书]")
                else:
                    rendered.append(html.escape(piece).replace("\n", "<br/>"))
            blocks.append("<p>" + "".join(rendered) + "</p>")
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN"><head><title>'
            + html.escape(title) + '</title></head><body>' + "\n".join(blocks) + '</body></html>')


def make_epub(path, title, author, chapters, identity, source_dir=None):
    """Text-focused EPUB 3. Keep raw Markdown/images references alongside it."""
    esc = html.escape
    items = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>']
    spine, nav, bodies, assets = [], [], [], {}
    for i, (name, text) in enumerate(chapters, 1):
        items.append(f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{i}"/>')
        nav.append(f'<li><a href="c{i}.xhtml">{esc(name)}</a></li>')
        bodies.append((f"OEBPS/c{i}.xhtml", to_xhtml(name, text)))
        for asset in re.findall(r"!\[[^\]]*\]\((images/[a-f0-9]{64}\.(?:png|jpg|gif|webp))\)", text):
            if source_dir and (Path(source_dir) / asset).is_file():
                assets[asset] = (Path(source_dir) / asset).read_bytes()
    mime = {"png":"image/png", "jpg":"image/jpeg", "gif":"image/gif", "webp":"image/webp"}
    for i, asset in enumerate(assets, 1):
        items.append(f'<item id="image{i}" href="{asset}" media-type="{mime[asset.rsplit(".",1)[-1]]}"/>')
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    opf = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:identifier id="book-id">{esc(identity)}</dc:identifier><dc:title>{esc(title)}</dc:title>'
           f'<dc:creator>{esc(author)}</dc:creator><dc:language>zh-CN</dc:language>'
           f'<meta property="dcterms:modified">{modified}</meta></metadata>'
           '<manifest>' + ''.join(items) + '</manifest><spine>' + ''.join(spine) + '</spine></package>')
    navigation = ('<?xml version="1.0" encoding="UTF-8"?>'
                  '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh-CN">'
                  '<head><title>目录</title></head><body><nav epub:type="toc" id="toc"><h1>目录</h1><ol>'
                  + ''.join(nav) + '</ol></nav></body></html>')
    temp = Path(str(path) + ".tmp")
    with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/nav.xhtml", navigation)
        for name, body in bodies:
            z.writestr(name, body)
        for name, data in assets.items():
            z.writestr("OEBPS/" + name, data)
    validate_epub(temp, len(chapters))
    temp.chmod(0o600)
    temp.replace(path)


def validate_epub(path, expected=None):
    import posixpath
    with zipfile.ZipFile(path) as z:
        if z.testzip() or z.read("mimetype") != b"application/epub+zip":
            raise Failure("INVALID_EPUB", "EPUB 压缩包校验失败。")
        first = z.infolist()[0]
        if first.filename != "mimetype" or first.compress_type != zipfile.ZIP_STORED:
            raise Failure("INVALID_EPUB", "EPUB mimetype 顺序或压缩方式不正确。")
        container = ET.fromstring(z.read("META-INF/container.xml"))
        rootfile = next(e for e in container.iter() if e.tag.endswith("rootfile"))
        opf = ET.fromstring(z.read(rootfile.attrib["full-path"]))
        refs = [e.attrib["idref"] for e in opf.iter() if e.tag.endswith("itemref")]
        items = {e.attrib["id"]: e.attrib["href"] for e in opf.iter() if e.tag.endswith("}item")}
        root = posixpath.dirname(rootfile.attrib["full-path"])
        if len(refs) != len(set(refs)) or any(ref not in items for ref in refs):
            raise Failure("INVALID_EPUB", "EPUB spine 引用了不存在或重复章节。")
        for href in items.values():
            if posixpath.normpath(posixpath.join(root, href)) not in z.namelist():
                raise Failure("INVALID_EPUB", "EPUB 声明了不存在的内容文件。")
        if expected is not None and len(refs) != expected:
            raise Failure("INVALID_EPUB", "EPUB 章节数与导出内容不符。")
        for name in z.namelist():
            if name.endswith((".xml", ".opf", ".xhtml")):
                tree = ET.fromstring(z.read(name))
                for element in tree.iter():
                    if element.tag.endswith("}img") and element.get("src"):
                        resource = posixpath.normpath(posixpath.join(posixpath.dirname(name), element.get("src")))
                        if resource not in z.namelist():
                            raise Failure("INVALID_EPUB", "EPUB 插图引用丢失。")
        return {"valid": True, "spine_chapters": len(refs)}


def build(directory, formats=None):
    directory = Path(directory).resolve()
    with lock("book-" + hashlib.sha256(str(directory).encode()).hexdigest()[:20]):
        return _build(directory, formats)


def _build(directory, formats=None):
    directory = Path(directory).resolve()
    meta = read_json(directory / "meta.json")
    if not meta or not meta.get("chapters"):
        raise Failure("NO_CATALOGUE", "没有可信目录，无法建立书籍。")
    # Enrich older caches from their already-saved API response; no online request.
    api = read_json(directory/"api-info.json", {})
    if str(api.get("bookId", "")) == str(meta.get("book_id")):
        extra = {k:api[k] for k in ("translator","publisher","isbn","publishTime") if api.get(k) is not None and not meta.get(k)}
        if extra:
            meta.update(extra); write_json(directory/"meta.json",meta)
    checkpoints = read_json(directory / "checkpoints.json", {})
    previous = read_json(directory/"manifest.json", {})
    formats = set(formats or ["epub"]) | (set(previous.get("artifacts",{})) & set(FORMATS))
    if not formats <= set(FORMATS):
        raise Failure("FORMAT_UNSUPPORTED", "可选格式为 epub、md、txt、html。")
    entries, available, chunks = [], [], []
    for i, chapter in enumerate(meta["chapters"], 1):
        uid = str(chapter["id"])
        if not re.fullmatch(r"[A-Za-z0-9_-]+", uid):
            raise Failure("INVALID_CHAPTER_ID", "目录章节 ID 不合法。")
        file = directory / "chapters" / f"{i:04d}-{uid}.md"
        entry = {"index": i, "id": uid, "title": chapter["title"], "file": str(file), "status": "missing"}
        if file.exists():
            text = file.read_text(encoding="utf-8")
            q = quality(text, chapter.get("words", 0))
            fingerprint = hashlib.sha256(text.encode()).hexdigest()
            point = checkpoints.get(uid, {})
            verified = point.get("finished") is True and point.get("sha256") == fingerprint
            entry.update(q, sha256=fingerprint, finished=verified, status="captured" if verified and not q["issues"] else "suspect")
            if (q["body_characters"] or q["image_count"]) and "access_prompt" not in q["issues"]:
                available.append((chapter["title"], text))
                for start, end, excerpt in chunk_text(text):
                    chunks.append({"chunk_id": f"{uid}:{start}-{end}", "chapter_index": i,
                                   "chapter_id": uid, "chapter_title": chapter["title"], "path": str(file),
                                   "chapter_status":entry["status"], "quality_issues":q["issues"],
                                   "char_start": start, "char_end": end, "line_start": text.count("\n", 0, start)+1,
                                   "line_end": text.count("\n", 0, end)+1, "text": excerpt})
        entries.append(entry)
    counts = Counter(e["status"] for e in entries)
    all_captured = counts["captured"] == len(entries)
    status = "catalogue_complete" if all_captured else ("partial" if available else "unavailable")
    manifest = {"version": 2, "book_id": meta.get("book_id"), "web_id": meta.get("web_id"),
                "title": meta.get("title", "未命名"), "author": meta.get("author", ""),
                "source_url": meta.get("source_url"), "account": meta.get("account"), "updated_at": stamp(),
                "status": status, "completeness": "unverified", "expected_chapters": len(entries),
                "captured_chapters": counts["captured"], "suspect_chapters": counts["suspect"], "missing_chapters": counts["missing"],
                "chapter_coverage": counts["captured"] / len(entries),
                "note": "覆盖率指目录章节覆盖，不保证逐字或图表完整。EPUB 为提取文本版。", "chapters": entries,
                "artifacts": {}}
    manifest["metadata_sha256"]=metadata_digest(meta)
    if checkpoints.get("_last_attempt"):
        manifest["last_attempt"] = checkpoints["_last_attempt"]
    if available:
        # Always publish under status-dependent names; a partial book cannot replace a complete one.
        base = "book" if all_captured else "book.partial"
        merged = "\n\n".join(text for _, text in available)
        if "md" in formats:
            atomic_text(directory / f"{base}.md", merged)
        if "txt" in formats:
            atomic_text(directory / f"{base}.txt", plain_text(merged))
        if "epub" in formats:
            make_epub(directory / f"{base}.epub", meta["title"], meta.get("author") or "", available,
                      "weread:" + str(meta.get("web_id", meta.get("book_id", "local"))), directory)
        if "html" in formats:
            make_html(directory/f"{base}.html",meta["title"],meta.get("author") or "",available,directory)
        manifest["artifacts"] = {ext: str(directory / f"{base}.{ext}") for ext in sorted(formats)}
        manifest["artifact_hashes"] = {ext:hashlib.sha256(Path(path).read_bytes()).hexdigest() for ext,path in manifest["artifacts"].items()}
    # Rebuild an atomic index, not an append-only one that returns stale passages.
    atomic_text(directory / "chunks.jsonl", "".join(json.dumps(c, ensure_ascii=False)+"\n" for c in chunks))
    temp = directory / "index.tmp.sqlite"
    temp.unlink(missing_ok=True)
    db = sqlite3.connect(temp)
    try:
        db.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, terms TEXT NOT NULL)")
        try:
            db.execute("CREATE VIRTUAL TABLE search USING fts5(terms)")
            fts = True
        except sqlite3.OperationalError:
            fts = False
        for rowid, c in enumerate(chunks, 1):
            terms = " ".join(tokens(c["chapter_title"] + " " + c["text"]))
            db.execute("INSERT INTO chunks VALUES (?,?,?)", (rowid, json.dumps(c, ensure_ascii=False), terms))
            if fts:
                db.execute("INSERT INTO search(rowid,terms) VALUES (?,?)", (rowid, terms))
        db.commit()
    finally:
        db.close()
    temp.chmod(0o600); temp.replace(directory / "index.sqlite")
    write_manifest(directory, manifest)
    from catalogue import register
    register(directory)
    return manifest


def retrieve(directory, question, limit=5):
    directory = Path(directory).resolve()
    manifest = load_manifest(directory)
    if not manifest or not (directory / "index.sqlite").exists():
        raise Failure("INDEX_MISSING", "尚未建立本地原文索引；先 export 或 ingest。")
    query = list(dict.fromkeys(tokens(question)))[:100]
    if not query:
        return {"status": "no_evidence", "matches": []}
    db = sqlite3.connect((directory/'index.sqlite').as_uri()+"?mode=ro", uri=True)
    try:
        try:
            expression = " OR ".join('"'+t.replace('"', '""')+'"' for t in query)
            rows = db.execute("SELECT c.payload, c.terms FROM search s JOIN chunks c ON c.id=s.rowid WHERE search MATCH ? ORDER BY bm25(search) LIMIT 80", (expression,)).fetchall()
        except sqlite3.OperationalError:
            rows = db.execute("SELECT payload, terms FROM chunks").fetchall()
    finally:
        db.close()
    scores = []
    for payload, terms in rows:
        c = json.loads(payload)
        bag = Counter(terms.split())
        score = sum(min(bag[t], 5) * (2 if len(t)>1 else 0.15) for t in query)
        if score > 0:
            scores.append((score, c))
    scores.sort(key=lambda x: -x[0])
    selected = []
    for score, c in scores:
        if any(c["chapter_id"] == p["chapter_id"] and abs(c["char_start"]-p["char_start"]) < 700 for p in selected):
            continue
        c["score"] = round(score, 3); selected.append(c)
        if len(selected) >= limit:
            break
    return {"status": "evidence_found" if selected else "no_evidence", "title": manifest["title"],
            "book_status": manifest["status"], "chapter_coverage": manifest["chapter_coverage"], "matches": selected}


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in ("p", "div", "h1", "h2", "h3", "br", "li"):
            self.parts.append("\n")
    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip-1)
        if tag in ("p", "div", "h1", "h2", "h3", "li"):
            self.parts.append("\n")
    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def ingest(source, directory, *, title_override=None, author_override=None, source_url=None, formats=None, pdf_data=None):
    """User-supplied EPUB/Markdown/TXT fallback; never extract paths from EPUB to disk."""
    source, directory = Path(source).resolve(), private_dir(directory)
    if source.stat().st_size > 200_000_000:
        raise Failure("FILE_TOO_LARGE", "单书导入上限 200 MB。")
    chapters, title, author = [], source.stem, ""
    pages_needing_review = []
    if source.suffix.lower() == ".epub":
        import posixpath
        from urllib.parse import unquote
        with zipfile.ZipFile(source) as z:
            if sum(i.file_size for i in z.infolist()) > 250_000_000:
                raise Failure("FILE_TOO_LARGE", "EPUB 解压体积超过限制。")
            if "META-INF/encryption.xml" in z.namelist():
                enc = z.read("META-INF/encryption.xml").decode("utf-8", errors="replace")
                # Font obfuscation is common and doesn't encrypt book text.
                if "EncryptedData" in enc and any(x in enc for x in (".xhtml", ".html", ".xml")):
                    raise Failure("ENCRYPTED_EPUB", "这本 EPUB 的正文受保护，无法建立原文索引。")
            root = ET.fromstring(z.read("META-INF/container.xml"))
            opf_name = next(e.attrib["full-path"] for e in root.iter() if e.tag.endswith("rootfile"))
            opf = ET.fromstring(z.read(opf_name))
            items = {e.attrib["id"]: e.attrib for e in opf.iter() if e.tag.endswith("}item")}
            for e in opf.iter():
                if e.tag.endswith("}title") and e.text:
                    title = e.text
                if e.tag.endswith("}creator") and e.text:
                    author = e.text
            for e in opf.iter():
                if not e.tag.endswith("}itemref"):
                    continue
                item = items[e.attrib["idref"]]
                if "nav" in item.get("properties", "").split():
                    continue
                name = posixpath.normpath(posixpath.join(posixpath.dirname(opf_name), unquote(item["href"].split("#")[0])))
                parser = TextExtractor(); parser.feed(z.read(name).decode("utf-8-sig"))
                text = "".join(parser.parts).strip()
                if text:
                    chapters.append((text.splitlines()[0][:100], text))
    elif source.suffix.lower() == ".pdf":
        if not pdf_data:
            raise Failure("PDF_DEPENDENCY_MISSING","通过 library.py ingest 导入 PDF，以自动准备解析环境。")
        title=pdf_data.get("title") or title; author=pdf_data.get("author") or ""
        for i,text in enumerate(pdf_data["pages"],1):
            chapters.append((f"第 {i} 页",text))
            if not text.strip():
                pages_needing_review.append(i)
    elif source.suffix.lower() in (".md", ".txt"):
        text = source.read_text(encoding="utf-8-sig")
        parts = re.split(r"(?m)(?=^#{1,2}\s+)", text) if source.suffix.lower() == ".md" else [text]
        chapters = [(p.strip().splitlines()[0].lstrip("# ")[:100], p.strip()) for p in parts if p.strip()]
    else:
        raise Failure("FORMAT_UNSUPPORTED", "本地导入支持 EPUB、文字 PDF、UTF-8 Markdown 和 TXT。")
    if not chapters:
        raise Failure("NO_TEXT", "未提取到可读正文；图片型书籍需要另行 OCR。")
    private_dir(directory / "chapters")
    checkpoint, entries = {}, []
    for i, (name, text) in enumerate(chapters, 1):
        uid = str(i)
        entries.append({"id": uid, "title": name, "words": 0})
        if not text.strip():
            (directory/"chapters"/f"{i:04d}-{uid}.md").unlink(missing_ok=True)
            continue
        text = f"# {name}\n\n{text}\n"
        atomic_text(directory / "chapters" / f"{i:04d}-{uid}.md", text)
        checkpoint[uid] = {"finished": True, "sha256": hashlib.sha256(text.encode()).hexdigest()}
    write_json(directory / "meta.json", {"title": title_override or title, "author": author_override or author,
                "source_url":source_url or str(source),"source_file":str(source),"source_sha256":hashlib.sha256(source.read_bytes()).hexdigest(),
                "source_format":source.suffix.lower()[1:], "book_id": "local", "chapters": entries,
                "pages_needing_review":pages_needing_review})
    write_json(directory / "checkpoints.json", checkpoint)
    return build(directory,formats)
