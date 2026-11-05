"""Incremental, offline book catalogue. Metadata is indexed, never book text."""
import difflib
import json
from pathlib import Path
import re
import sqlite3

from runtime import DATA_HOME, private_dir, read_json


def normalize(text):
    return re.sub(r"[\W_]", "", str(text)).casefold()


def connection():
    private_dir(DATA_HOME)
    path = DATA_HOME / "catalogue.sqlite"
    db = sqlite3.connect(path, timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS books (directory TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload TEXT NOT NULL)")
    path.chmod(0o600)
    return db


def fingerprint(directory):
    parts = []
    for name in ("meta.json", "manifest.json"):
        p = directory/name
        if p.exists():
            s = p.stat(); parts.append(f"{name}:{s.st_mtime_ns}:{s.st_size}")
    return "|".join(parts)


def upsert(db, directory):
    meta = read_json(directory/"meta.json", {})
    state = read_json(directory/"manifest.json", {})
    if not meta.get("title"):
        return
    item = {key:meta.get(key) for key in ("title","author","translator","publisher","isbn","book_id","web_id","account","source_url")}
    item.update(directory=str(directory), status=state.get("status","not_downloaded"),
                chapter_coverage=state.get("chapter_coverage",0), artifacts=state.get("artifacts",{}),
                updated_at=state.get("updated_at"), local=True)
    db.execute("INSERT OR REPLACE INTO books VALUES (?,?,?)",(str(directory),fingerprint(directory),json.dumps(item,ensure_ascii=False)))


def register(directory):
    with connection() as db:
        upsert(db,Path(directory).resolve())


def sync():
    """Stat small metadata files; parse only changed entries, including custom imports."""
    with connection() as db:
        known = dict(db.execute("SELECT directory,fingerprint FROM books"))
        paths = {p.parent.resolve() for p in (DATA_HOME/"books").glob("*/*/meta.json")}
        paths.update(Path(p) for p in known)
        for directory in paths:
            if not (directory/"meta.json").exists():
                db.execute("DELETE FROM books WHERE directory=?",(str(directory),)); continue
            if known.get(str(directory)) != fingerprint(directory):
                upsert(db,directory)


def search(query="", author=None, account=None, limit=20):
    sync()
    with connection() as db:
        entries = [json.loads(row[0]) for row in db.execute("SELECT payload FROM books")]
    needle = normalize(query)
    results = []
    for item in entries:
        if account and item.get("account") not in (account, None):
            continue
        if author and normalize(author) not in normalize(item.get("author") or ""):
            continue
        title = normalize(item["title"])
        identity = needle and needle in {normalize(item.get(k) or "") for k in ("book_id","web_id","isbn")}
        exact = bool(needle and (needle == title or identity))
        if not needle:
            score=1
        elif exact:
            score=100
        elif needle in title or needle in normalize(item.get("author") or ""):
            score=80
        else:
            similarity=difflib.SequenceMatcher(None,needle,title).ratio()
            score=round(60*similarity,2) if similarity>=0.55 else 0
        if score:
            results.append({**item,"exact_title":exact,"match_score":score})
    results.sort(key=lambda item:(-item["match_score"],item["title"],item.get("author") or ""))
    return results[:limit]
