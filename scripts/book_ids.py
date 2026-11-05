"""WeRead URL identifiers, implemented locally without a browser or exporter package."""
import hashlib
import re

from runtime import Failure


def reader_hash(value):
    digits = str(value)
    if not re.fullmatch(r"[0-9]{1,18}", digits):
        raise Failure("INVALID_BOOK_ID", "微信读书数字 ID 格式不正确。")
    digest = hashlib.md5(digits.encode("ascii")).hexdigest()
    fields = [format(int(digits[offset:offset+9]), "x") for offset in range(0, len(digits), 9)]
    encoded = digest[:3] + "32" + digest[-2:] + "g".join(f"{len(field):02x}{field}" for field in fields)
    encoded += digest[:max(0, 20-len(encoded))]
    return encoded + hashlib.md5(encoded.encode("ascii")).hexdigest()[:3]


def web_id(value):
    value = str(value).strip()
    if value.startswith("https://"):
        match = re.fullmatch(r"https://weread\.qq\.com/web/(?:bookDetail|reader)/([a-z0-9]+)(?:[?#].*)?", value)
        if not match:
            raise Failure("INVALID_BOOK_ID", "请提供微信读书 bookDetail/reader 链接或书籍 ID。")
        value = match[1].split("k", 1)[0]
    if re.fullmatch(r"\d{1,18}", value):
        return reader_hash(value)
    if re.fullmatch(r"[a-f0-9g]{20,80}", value):
        return value
    raise Failure("UNSUPPORTED_BOOK_ID", "该 ID 不是受支持的书城电子书；个人导入书请使用原始文件 ingest。")
