"""Optional, private PDF text extraction. Never uploads files or performs OCR."""
import json
import logging
import os
from pathlib import Path
import shutil
import sys

from runtime import RUNTIME, Failure, emit, lock, python_path, run


def extract_pdf(source):
    with lock("pdf-setup"):
        py = python_path()
        if not py.exists():
            target = RUNTIME/"pdf-venv"
            py = target/("Scripts/python.exe" if os.name=="nt" else "bin/python")
            if not py.exists() and run([sys.executable,"-m","venv",target],timeout=90).returncode:
                raise Failure("PDF_DEPENDENCY_MISSING","无法创建 PDF 解析环境。")
        if run([py,"-c","import pypdf"]).returncode:
            emit({"event":"setup","message":"安装本地 PDF 文本解析组件"})
            installer=[shutil.which("uv"),"pip","install","--python",str(py)] if shutil.which("uv") else [py,"-m","pip","install"]
            if run(installer+["pypdf==6.21.4"],timeout=300).returncode:
                raise Failure("INSTALL_FAILED","PDF 解析组件安装失败，请检查网络。")
    result = run([py,Path(__file__),str(source)],timeout=120)
    try:
        data=json.loads(result.stdout)
    except ValueError:
        raise Failure("PDF_PARSE_FAILED","PDF 未返回可用文本。")
    if not data.get("ok"):
        raise Failure(data.get("code","PDF_PARSE_FAILED"),data.get("message","PDF 解析失败。"))
    return data


def worker(source):
    logging.disable(logging.CRITICAL)
    from pypdf import PdfReader
    reader=PdfReader(source)
    if reader.is_encrypted:
        raise Failure("ENCRYPTED_PDF","PDF 受密码保护，请提供可读取的文件。")
    if len(reader.pages)>2000:
        raise Failure("PDF_TOO_LARGE","单次导入最多支持 2000 页 PDF。")
    pages=[]; total=0
    for page in reader.pages:
        stream=page.get_contents()
        if stream and len(stream.get_data())>10_000_000:
            raise Failure("PDF_TOO_LARGE","PDF 单页内容过大，需要先拆分。")
        text=page.extract_text() or ""
        total+=len(text)
        if total>10_000_000:
            raise Failure("PDF_TOO_LARGE","PDF 文本超过单次导入上限。")
        pages.append(text.strip())
    if not any(pages):
        raise Failure("OCR_REQUIRED","PDF 没有可提取文本，可能是扫描版；需要 OCR 或另找文字版。")
    return {"ok":True,"title":str((reader.metadata or {}).get('/Title','')),
            "author":str((reader.metadata or {}).get('/Author','')),"pages":pages}


if __name__=="__main__":
    try:
        emit(worker(sys.argv[1]))
    except Failure as e:
        emit({"ok":False,"code":e.code,"message":str(e)})
    except Exception as e:
        emit({"ok":False,"code":"PDF_PARSE_FAILED","message":"PDF 文件结构无法解析。","error_type":type(e).__name__})
