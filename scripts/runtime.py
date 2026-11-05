"""Private, relocatable runtimes and bounded subprocesses; no global installs."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from access import entitlement

OMNI_VERSION = "0.1.2"
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_HOME = Path(os.environ.get("WEREAD_LIBRARY_HOME", Path.home() / ".local/share/weread-library")).expanduser().resolve()
RUNTIME = DATA_HOME / "runtime"


class Failure(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details


def emit(data):
    print(json.dumps(data, ensure_ascii=False), flush=True)


def private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def atomic_text(path, value):
    path = Path(path)
    private_dir(path.parent)
    fd, temp = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(value)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (ValueError, UnicodeError):
        raise Failure("CORRUPT_STATE", "本地状态文件损坏，请保留现场后修复。", path=str(path))


def account_name(value):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value):
        raise Failure("INVALID_ACCOUNT", "账号别名只能用小写字母、数字、短横线、下划线，长度 1–64。")
    return value


def default_account():
    """Honor Omni's selected alias instead of accidentally asking it to log in again."""
    root = Path(os.environ.get("WEREAD_CONFIG_DIR", Path.home() / ".config/weread")).expanduser()
    config = read_json(root / "config.json", {})
    if config.get("defaultAccount"):
        return account_name(config["defaultAccount"])
    aliases = sorted(p.parent.name for p in (root / "accounts").glob("*/account.json")
                     if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", p.parent.name))
    if len(aliases) == 1:
        return aliases[0]
    if len(aliases) > 1:
        raise Failure("ACCOUNT_SELECTION_REQUIRED", "Omni 已有多个账号且未指定默认账号，请使用 --account 选择，无需重新登录。", accounts=aliases)
    return "default"


def omni_web_session(account, expected_vid=None):
    """Read only the selected Omni account; keep its token in process memory.

    WeRead accepts the E-Ink vid/accessToken as wr_vid/wr_skey. Refresh tokens
    remain exclusively owned by Omni and must never be installed as wr_rt.
    """
    root = Path(os.environ.get("WEREAD_CONFIG_DIR", Path.home() / ".config/weread")).expanduser()
    directory = root / "accounts" / account_name(account)
    descriptor = read_json(directory / "account.json", {})
    if descriptor.get("version") != 1 or descriptor.get("client") != "eink":
        return None
    path = directory / "clients/eink.json"
    if not path.exists():
        return None
    if path.stat().st_size > 1024 * 1024:
        raise Failure("CORRUPT_STATE", "Omni 登录状态大小异常，请检查指定账号。")
    state = read_json(path, {})
    if state.get("version") != 1 or not isinstance(state.get("eink"), dict):
        raise Failure("CORRUPT_STATE", "Omni 登录状态格式不受支持。")
    credentials = state["eink"]
    vid, token = credentials.get("vid"), credentials.get("accessToken")
    if not isinstance(vid, str) or not re.fullmatch(r"\d+", vid) or not isinstance(token, str) or not token:
        return None
    if re.search(r"[\s;,\x00-\x1f\x7f]", token):
        raise Failure("CORRUPT_STATE", "Omni 会话令牌格式异常。")
    if expected_vid and vid != str(expected_vid):
        raise Failure("ACCOUNT_MISMATCH", "Omni 当前账号与本次导出账号不同，已停止。")
    return {"vid": vid, "access_token": token}


@contextlib.contextmanager
def lock(name):
    """OS-released advisory lock: crashed jobs never leave a stale lock owner."""
    p = private_dir(DATA_HOME / "locks") / (name + ".lock")
    with p.open("a+b") as f:
        p.chmod(0o600)
        try:
            if os.name == "nt":
                import msvcrt
                if p.stat().st_size == 0:
                    f.write(b"0"); f.flush()
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Failure("BUSY", "同一账号或安装环境正在被另一任务使用，请等待它完成。")
        try:
            yield
        finally:
            if os.name == "nt":
                f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


def run(args, *, timeout=90, cwd=None, env=None, capture=True):
    kwargs = {"start_new_session": True} if os.name != "nt" else {}
    proc = subprocess.Popen([str(x) for x in args], cwd=cwd, env=env,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None,
                            text=True, encoding="utf-8", errors="replace", **kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        if os.name != "nt":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
            proc.communicate()
        raise Failure("TIMEOUT", "操作已超时或取消；已停止本次进程，已有章节缓存会保留。")
    return subprocess.CompletedProcess(args, proc.returncode, stdout or "", stderr or "")


def download(url, path, limit=180_000_000):
    request = urllib.request.Request(url, headers={"User-Agent": "weread-library/1"})
    with urllib.request.urlopen(request, timeout=45) as response, Path(path).open("wb") as out:
        count = 0
        while chunk := response.read(1024 * 1024):
            count += len(chunk)
            if count > limit:
                raise Failure("DOWNLOAD_TOO_LARGE", "依赖下载超过预期大小。")
            out.write(chunk)


def unpack(archive, dest):
    """Extract regular files only. No links or traversal from downloaded archives."""
    private_dir(dest)
    def target(name):
        p = (dest / name).resolve()
        if not p.is_relative_to(dest.resolve()):
            raise Failure("INVALID_ARCHIVE", "依赖压缩包包含越界路径。")
        private_dir(p.parent)
        return p
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for item in z.infolist():
                if not item.is_dir():
                    target(item.filename).write_bytes(z.read(item))
    else:
        with tarfile.open(archive) as t:
            for item in t:
                if item.isfile():
                    p = target(item.name)
                    with t.extractfile(item) as src, p.open("wb") as out:
                        shutil.copyfileobj(src, out)
                    p.chmod(item.mode & 0o755)


def node_path():
    candidates = [shutil.which("node")]
    candidates += [str(x) for x in (RUNTIME / "node").glob("**/node.exe" if os.name == "nt" else "**/bin/node")]
    for candidate in candidates:
        if candidate:
            try:
                version = tuple(int(x) for x in run([candidate, "--version"]).stdout.strip().lstrip("v").split("."))
                if version >= (22, 13, 0):
                    return candidate
            except (ValueError, OSError, Failure):
                pass
    return None


def env_for_node():
    env = os.environ.copy()
    node = node_path()
    if node:
        env["PATH"] = str(Path(node).parent) + os.pathsep + env.get("PATH", "")
    env.setdefault("WEREAD_CONNECT_ATTEMPT_TIMEOUT_MS", "20000")
    return env


def provision_node():
    if node_path():
        return
    machine = platform.machine().lower()
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}.get(machine)
    system = {"darwin": "darwin", "linux": "linux", "win32": "win"}.get(sys.platform)
    if not arch or not system:
        raise Failure("NODE_REQUIRED", "请安装 Node.js >=22.13，再运行 setup。")
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        download("https://nodejs.org/dist/index.json", temp / "index.json", 5_000_000)
        releases = read_json(temp / "index.json")
        version = next(r["version"] for r in releases if r.get("lts") and int(r["version"].split(".")[0][1:]) >= 22)
        filename = f"node-{version}-{system}-{arch}." + ("zip" if system == "win" else "tar.gz")
        download(f"https://nodejs.org/dist/{version}/SHASUMS256.txt", temp / "sha.txt", 500_000)
        checksums = dict(line.split()[::-1] for line in (temp / "sha.txt").read_text().splitlines() if len(line.split()) == 2)
        download(f"https://nodejs.org/dist/{version}/{filename}", temp / filename)
        if checksums.get(filename) != hashlib.sha256((temp / filename).read_bytes()).hexdigest():
            raise Failure("CHECKSUM_MISMATCH", "Node 下载校验失败，未安装。")
        unpack(temp / filename, RUNTIME / "node")


def python_path():
    return RUNTIME / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def browser_path():
    explicit = os.environ.get("WEREAD_BROWSER_PATH")
    if explicit:
        if not Path(explicit).is_file():
            raise Failure("BROWSER_NOT_FOUND", "WEREAD_BROWSER_PATH 指向的浏览器不存在。")
        return explicit
    candidates = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        if os.environ.get(key):
            candidates.append(str(Path(os.environ[key]) / "Google/Chrome/Application/chrome.exe"))
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        if path := shutil.which(name):
            candidates.append(path)
    for p in candidates:
        if Path(p).is_file():
            return p
    if python_path().exists():
        r = run([python_path(), "-c", "from playwright.sync_api import sync_playwright\nwith sync_playwright() as p: print(p.chromium.executable_path)"])
        if r.returncode == 0 and Path(r.stdout.strip()).is_file():
            return r.stdout.strip()
    return None


def setup():
    os.umask(0o077)
    with lock("setup"):
        private_dir(RUNTIME)
        emit({"event": "setup", "message": "检查并安装私有运行依赖"})
        provision_node()
        node = node_path()
        npm_cli = Path(node).resolve().parent.parent / "lib/node_modules/npm/bin/npm-cli.js"
        if os.name == "nt":
            npm_cli = Path(node).parent / "node_modules/npm/bin/npm-cli.js"
        npm = [node, str(npm_cli)] if npm_cli.exists() else [shutil.which("npm") or "npm"]
        manifest = read_json(RUNTIME / "npm/node_modules/weread-omni/package.json", {})
        if manifest.get("version") != OMNI_VERSION or not (RUNTIME / "npm/node_modules/qrcode").exists():
            r = run(npm + ["install", "--prefix", str(RUNTIME / "npm"), "--no-audit", "--no-fund", "--ignore-scripts",
                           f"weread-omni@{OMNI_VERSION}", "qrcode@1.5.4"], timeout=600, env=env_for_node())
            if r.returncode:
                raise Failure("INSTALL_FAILED", "Node 依赖安装失败；检查网络或 npm 源后重试。")
        py = python_path()
        if not py.exists():
            if uv := shutil.which("uv"):
                result = run([uv, "venv", "--python", "3.11", str(RUNTIME / "venv")], timeout=600)
            else:
                compatible = next((p for name in ("python3.11", "python3.12", "python3.10") if (p := shutil.which(name))), None)
                if compatible is None and (3, 10) <= sys.version_info[:2] <= (3, 12):
                    compatible = sys.executable
                if compatible is None:
                    # Install uv into an isolated bootstrap venv; uv then provisions Python.
                    boot = RUNTIME / "bootstrap"
                    r = run([sys.executable, "-m", "venv", str(boot)], timeout=90)
                    bp = boot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                    if r.returncode or not bp.exists():
                        raise Failure("PYTHON_REQUIRED", "需要 Python 3.10–3.12 或 uv；当前 Python 无法创建私有环境。")
                    r = run([bp, "-m", "pip", "install", "uv"], timeout=300)
                    if r.returncode:
                        raise Failure("INSTALL_FAILED", "私有 uv 安装失败，请检查网络。")
                    uv = boot / ("Scripts/uv.exe" if os.name == "nt" else "bin/uv")
                    result = run([uv, "venv", "--python", "3.11", str(RUNTIME / "venv")], timeout=600)
                else:
                    result = run([compatible, "-m", "venv", str(RUNTIME / "venv")], timeout=90)
            if result.returncode:
                raise Failure("INSTALL_FAILED", "Python 私有环境创建失败。")
        probe = run([py, "-c", "import pyppeteer, aiohttp, PIL"])
        if probe.returncode:
            installer = [shutil.which("uv"), "pip", "install", "--python", str(py)] if shutil.which("uv") else [str(py), "-m", "pip", "install"]
            if not shutil.which("uv"):
                run([py, "-m", "ensurepip"], timeout=90)
            r = run(installer + ["pyppeteer==2.0.0", "aiohttp>=3.10,<4", "Pillow>=10,<13"], timeout=600)
            if r.returncode:
                raise Failure("INSTALL_FAILED", "Python 导出依赖安装失败，请检查网络。")
        if not browser_path():
            emit({"event": "setup", "message": "未发现 Chrome，安装独立 Chromium（下载可能较大）"})
            installer = [shutil.which("uv"), "pip", "install", "--python", str(py)] if shutil.which("uv") else [str(py), "-m", "pip", "install"]
            r = run(installer + ["playwright"], timeout=600)
            if r.returncode or run([py, "-m", "playwright", "install", "chromium"], timeout=900).returncode:
                raise Failure("BROWSER_REQUIRED", "独立浏览器安装失败，请安装 Chrome 或设置 WEREAD_BROWSER_PATH。")
        write_json(RUNTIME / "installed.json", {"omni": OMNI_VERSION, "reader": "builtin-canvas-v1", "python": str(py), "installed_at": time.time()})
    return doctor()


def omni_path():
    return RUNTIME / "npm/node_modules/weread-omni/dist/cli.js"


def doctor():
    node = node_path()
    py = python_path()
    deps = False
    if py.exists():
        deps = run([py, "-c", "import pyppeteer,aiohttp,PIL"]).returncode == 0
    result = {"node": node, "omni_installed": omni_path().exists(), "python": str(py) if py.exists() else None,
              "python_dependencies": deps, "builtin_reader": (SCRIPT_DIR / "capture_canvas.js").is_file(),
              "browser": browser_path(), "home": str(DATA_HOME)}
    result["ready"] = bool(node and result["omni_installed"] and deps and result["builtin_reader"] and result["browser"])
    if not result["ready"]:
        result["next_action"] = "setup"
    return result


def json_output(text):
    try:
        return json.loads(text)
    except ValueError:
        for line in reversed(text.splitlines()):
            try:
                return json.loads(line)
            except ValueError:
                pass
    return None


def error_message(data):
    if not isinstance(data, dict):
        return ""
    inner = data.get("error", data)
    if isinstance(inner, str):
        return inner[:2000]
    if isinstance(inner, dict):
        return " ".join(str(inner.get(k, ""))[:1000] for k in ("message", "errMsg", "errmsg", "msg"))
    return ""


def classify_error(data, text=""):
    text += " " + error_message(data)
    status = None
    if isinstance(data, dict):
        inner = data.get("error", data)
        if isinstance(inner, str):
            text += " " + inner
            inner = data
        if isinstance(inner, dict):
            code = inner.get("errCode")
            status = inner.get("status")
            name = inner.get("name", inner.get("type", ""))
            if code == -2041 or inner.get("status") == 429:
                return "HUMAN_VERIFICATION" if code == -2041 else "RATE_LIMITED"
            if name == "AuthError" or inner.get("status") == 401 or code == -2012:
                return "LOGIN_REQUIRED"
    if re.search(r"-2041|captcha|安全验证|人机验证|验证码|拖动滑块|human.verification", text, re.I):
        return "HUMAN_VERIFICATION"
    if re.search(r"\b429\b|rate.?limit|频繁", text, re.I):
        return "RATE_LIMITED"
    if entitlement(text):
        return "ACCESS_LIMITED"
    if re.search(r"credentials|log.?in|authentication|AuthError|未登录|登录已过期|account .*could not be read", text, re.I):
        return "LOGIN_REQUIRED"
    if status == 403:
        return "ACCESS_DENIED"
    if re.search(r"TransportError|network|timeout|ENOTFOUND|ECONN|EAI_AGAIN", text, re.I):
        return "NETWORK_ERROR"
    return "UPSTREAM_ERROR"


def omni(args, account="default", retry=True):
    # Login and SDK renewal both write the same account state.
    with lock("api-" + account_name(account)):
        return _omni(args, account, retry)


def _omni(args, account="default", retry=True):
    if not node_path() or not omni_path().exists():
        raise Failure("DEPENDENCY_MISSING", "先运行 setup 安装运行依赖。")
    for attempt in range(2 if retry else 1):
        r = run([node_path(), omni_path(), "--account", account_name(account), "--json", *args],
                env=env_for_node(), timeout=180 if args[:1] == ["ai"] else 75)
        data = json_output(r.stdout)
        if r.returncode == 0 and data is not None:
            return data
        error = json_output(r.stderr)
        code = classify_error(error, r.stderr)
        if retry and attempt == 0 and code == "NETWORK_ERROR":
            time.sleep(2)
            continue
        hints = {"LOGIN_REQUIRED": "需要 API 扫码登录；运行 login api。",
                 "HUMAN_VERIFICATION": "微信读书要求人工验证；请在官方客户端完成后再继续。",
                 "RATE_LIMITED": "微信读书限制了请求频率；已停止自动重试，请稍后再试。",
                 "ACCESS_DENIED": "服务端拒绝访问，但未说明原因；不能据此判定未登录、非会员或风控。"}
        access = entitlement(error_message(error) + " " + r.stderr) if code == "ACCESS_LIMITED" else None
        if access:
            access["access"]["source"] = "explicit_api_error"
            raise Failure(code, access["message"], access=access["access"])
        raise Failure(code, hints.get(code, "微信读书请求未成功，未把失败结果当作书籍内容。"))
