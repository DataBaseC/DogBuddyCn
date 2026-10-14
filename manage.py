#!/usr/bin/env python3
# DogBuddyCn — operations CLI (L1 Interface)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""manage.py — process & operations entry point.

Commands
--------
start | stop | restart | status | logs | backup | restore | version | update

`update` implements the self-upgrade chain (same shape as AegisServerPanel's
`serverpanel --update`): lock → dirty-tree check → config backup → fetch /
unpack → dependency install → restart → health check asserting the NEW
version → automatic rollback when it does not come up.

Update sources
--------------
* git      : default when the app dir contains `.git` (`--ref origin/main`)
* bundle   : `--from-bundle <tar.gz|zip>` (offline / air-gapped upgrades)
* download : `--from-url <https://…>` (fetch a bundle, then same as above)

Environment knobs
-----------------
DOGBUDDY_CONFIG          config file path
DOGBUDDY_RESTART_CMD     exact command used to restart the service (highest priority)
DOGBUDDY_PANEL_APP_ID    panel-managed app id → restart through AegisServerPanel
DOGBUDDY_PANEL_URL       panel base URL (defaults to `panel.base_url` in config)
DOGBUDDY_PANEL_TOKEN     agent token  (defaults to `panel.agent_token` in config)
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dogbuddy import __version__  # noqa: E402
from dogbuddy.config import default_data_dir, load_config, config_path  # noqa: E402
from dogbuddy.updater import write_result  # noqa: E402

IS_WINDOWS = sys.platform == "win32"
TMP = Path(tempfile.gettempdir())
DEFAULT_CONFIG = os.environ.get("DOGBUDDY_CONFIG", str(default_data_dir() / "config.yaml"))
PID_FILE = TMP / "dogbuddy.pid"
OUT_FILE = TMP / "dogbuddy.out"

APP_DIR = Path(__file__).resolve().parent
LOCK_NAME = "update.lock"
VERSION_FILE = "VERSION"
SERVICE_NAME = "dogbuddy"

HEALTH_TRIES = 30
HEALTH_DELAY = 1.0
PIP_TIMEOUT = 900

# never copied/compared when snapshotting code for rollback
SKIP_NAMES = {".venv", "venv", ".git", "__pycache__", ".pytest_cache", "node_modules", ".mypy_cache"}


# --------------------------------------------------------------------------- utils
def _data_dir(config: str) -> Path:
    try:
        import yaml

        with open(config, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        d = (data.get("paths") or {}).get("data_dir") or ""
        return Path(d) if d else default_data_dir()
    except Exception:  # noqa: BLE001
        return default_data_dir()


def _venv_python(app_dir: Path = APP_DIR) -> Path:
    exe = app_dir / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    return exe if exe.exists() else Path(sys.executable)


def _spawn_kwargs() -> dict:
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:  # noqa: BLE001
        return None


def _proc_cmdline(pid: int) -> str | None:
    """读 `/proc/<pid>/cmdline`；读不到返回 None（= 无法判定，不是"不存在"）。"""
    if IS_WINDOWS:
        return None
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    return " ".join(p.decode("utf-8", "replace") for p in raw.split(b"\0") if p)


def _looks_like_dogbuddy(pid: int) -> bool | None:
    """这个 pid 是不是我们的进程。None = 无法判定。

    审查报告 P2-4：`/tmp/dogbuddy.pid` 在多用户机器上可写，PID 也会被复用，
    而初版 `_alive` 只做 `os.kill(pid, 0)`——于是 `stop` 可能 TERM 掉一个
    恰好复用了该 pid 的无关进程。
    """
    text = _proc_cmdline(pid)
    if text is None:
        return None
    if not text.strip():
        return False  # 僵尸进程
    return ("dogbuddy" in text) or (str(APP_DIR) in text)


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    if IS_WINDOWS:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                             capture_output=True, text=True, timeout=15).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    verdict = _looks_like_dogbuddy(pid)
    # 无法判定时保守认为"还活着"：误判为活只会拒绝启动（安全），
    # 误判为死会导致重复启动 + 端口冲突（不安全）。
    return True if verdict is None else verdict



# --------------------------------------------------------------------------- process
def cmd_start(args: argparse.Namespace) -> int:
    if PID_FILE.exists() and _alive(_read_pid()):
        print(f"already running (pid {_read_pid()})")
        return 1
    try:
        cfg = load_config(args.config)
        if _health(cfg.web.host, cfg.web.port, expect_version=None, tries=1, delay=0) is not None:
            print(f"already running (another instance is serving "
                  f"http://{cfg.web.host}:{cfg.web.port}/healthz — supervised by the panel?)")
            print("  use `manage.py update` to upgrade it, or `manage.py stop --force` first")
            return 1
    except Exception:  # noqa: BLE001 - config problems must not block a start attempt
        pass
    env = dict(os.environ, DOGBUDDY_CONFIG=args.config)
    log = open(OUT_FILE, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "dogbuddy.main"],
        env=env, stdout=log, stderr=log, cwd=str(Path(__file__).resolve().parent),
        **_spawn_kwargs())
    PID_FILE.write_text(str(proc.pid))
    print(f"started (pid {proc.pid}); logs: {OUT_FILE} (manage.py logs)")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    pid = _read_pid()
    if getattr(args, "force", False):
        extra = _kill_app_processes()
        if extra:
            print(f"terminated {extra} supervised dogbuddy process(es)")
    if not _alive(pid):
        print("not running")
        PID_FILE.unlink(missing_ok=True)
        return 0
    if IS_WINDOWS:
        # kill the whole tree (cmd.exe children included)
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=30)
    else:
        import signal

        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            if not _alive(pid):
                break
            time.sleep(0.5)
        if _alive(pid):
            os.kill(pid, signal.SIGKILL)
    for _ in range(20):
        if not _alive(pid):
            break
        time.sleep(0.5)
    PID_FILE.unlink(missing_ok=True)
    print("stopped")
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    rc = cmd_stop(args)
    if rc != 0:
        return rc
    time.sleep(1.0)
    return cmd_start(args)


def cmd_status(args: argparse.Namespace) -> int:
    pid = _read_pid()
    running = _alive(pid)
    print(f"version: {__version__}")
    print(f"process: {'running pid=' + str(pid) if running else 'stopped'}")
    try:
        cfg = load_config(args.config)
        live = _health(cfg.web.host, cfg.web.port, expect_version=None, tries=1, delay=0)
    except Exception:  # noqa: BLE001
        live = None
    if live is not None:
        print(f"service: up  pid={live.get('pid')} version={live.get('version')} "
              f"build={live.get('build')} schema={live.get('schema_version')}")
        running = True
    else:
        print("service: /healthz not answering")
    data = _data_dir(args.config)
    db = data / "dogbuddy.db"
    print(f"database: {db} ({'ok' if db.exists() else 'missing'})")
    log = data / "logs" / "dogbuddy.log"
    print(f"log: {log} ({'ok' if log.exists() else 'missing'})")
    if db.exists():
        import sqlite3

        conn = sqlite3.connect(str(db))
        try:
            w = conn.execute("SELECT COUNT(*) FROM windows").fetchone()[0]
            j = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            r = conn.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0]
            try:
                sv = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            except sqlite3.Error:
                sv = None
            print(f"windows={w} jobs={j} job_runs={r} schema_version={sv}")
        finally:
            conn.close()
    return 0 if running else 1


def cmd_logs(args: argparse.Namespace) -> int:
    data = _data_dir(args.config)
    log = data / "logs" / "dogbuddy.log"
    if not log.exists():
        print("no log file yet")
        return 1
    # pure-python tail (no `tail` binary dependency — Windows friendly)
    with open(log, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    for line in lines[-args.lines:]:
        print(line.rstrip())
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    data = _data_dir(args.config)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(args.output or str(TMP / f"dogbuddy-backup-{stamp}.tar.gz"))
    shutil.make_archive(str(out).removesuffix(".tar.gz"), "gztar", root_dir=str(data))
    print(f"backup written: {out}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    data = _data_dir(args.config)
    src = Path(args.input)
    if not src.exists():
        print(f"not found: {src}")
        return 1
    data.mkdir(parents=True, exist_ok=True)
    shutil.unpack_archive(str(src), str(data))
    print(f"restored into {data}")
    return 0


# --------------------------------------------------------------------------- version
def _installed_version(app_dir: Path) -> str:
    """Version of the code currently on disk (subprocess → never the imported copy)."""
    try:
        r = subprocess.run([str(_venv_python(app_dir)), "-c", "import dogbuddy;print(dogbuddy.__version__)"],
                           cwd=str(app_dir), capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _git_version(app_dir: Path) -> str:
    try:
        return subprocess.run(["git", "describe", "--tags", "--always", "--dirty"], cwd=str(app_dir),
                              capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _write_version_stamp(app_dir: Path) -> str:
    stamp = _git_version(app_dir) or _installed_version(app_dir) or __version__ or "source"
    try:
        (app_dir / VERSION_FILE).write_text(stamp + "\n", encoding="utf-8")
    except OSError:
        pass
    return stamp


def cmd_version(args: argparse.Namespace) -> int:
    app_dir = Path(args.app_dir).resolve() if getattr(args, "app_dir", None) else APP_DIR
    disk = _installed_version(app_dir) or "(unknown)"
    print(f"running (this process): {__version__}")
    print(f"on disk             : {disk}")
    print(f"git                 : {_git_version(app_dir) or '(no .git)'}")
    vfile = app_dir / VERSION_FILE
    print(f"VERSION file        : {vfile.read_text(encoding='utf-8').strip() if vfile.exists() else '(none)'}")
    print(f"app dir             : {app_dir}")
    print(f"python              : {sys.executable}")
    print(f"config              : {config_path(args.config)}")
    if args.json:
        print(json.dumps({
            "running": __version__, "on_disk": disk, "git": _git_version(app_dir),
            "app_dir": str(app_dir), "config": str(config_path(args.config)),
        }, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- update
@contextlib.contextmanager
def _update_lock(app_dir: Path):
    """Exclusive, non-blocking update lock. Windows has no fcntl → best effort."""
    lock_path = app_dir / LOCK_NAME
    fh = open(lock_path, "w", encoding="utf-8")
    locked = False
    try:
        import fcntl  # POSIX only
    except ImportError:
        fcntl = None  # type: ignore[assignment]
    if fcntl is not None:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            fh.close()
            raise RuntimeError("另一个 update 正在进行中（update.lock 被占用）")
    try:
        yield
    finally:
        if locked and fcntl is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def _backup_config(config_file: str, stamp: str) -> Path | None:
    p = config_path(config_file)
    if not p.exists():
        return None
    dest = Path(str(p) + f".bak.{stamp}")
    shutil.copy2(p, dest)
    print(f"==> 配置已备份: {dest}")
    return dest


def _pip_install(app_dir: Path) -> int:
    req = app_dir / "requirements.txt"
    if not req.exists():
        print("    (no requirements.txt — skipped)")
        return 0
    base = [str(_venv_python(app_dir)), "-m", "pip", "install", "--quiet",
            "--disable-pip-version-check", "-r", str(req)]
    constraints = app_dir / "constraints.txt"
    if constraints.exists():
        base += ["-c", str(constraints)]
    mirror = os.environ.get("DOGBUDDY_PIP_INDEX")
    if mirror:
        base += ["-i", mirror]

    # offline-first: wheels/ shipped next to the code
    if (app_dir / "wheels").is_dir():
        offline = base + ["--no-index", "--find-links", str(app_dir / "wheels")]
        try:
            if subprocess.run(offline, timeout=PIP_TIMEOUT).returncode == 0:
                return 0
            print("    本地 wheels 不满足新依赖，改为联网安装…")
        except subprocess.SubprocessError as e:
            print(f"    本地安装失败（{e}），改为联网安装…")
    try:
        return subprocess.run(base, timeout=PIP_TIMEOUT).returncode
    except subprocess.SubprocessError as e:
        print(f"    pip failed: {e}")
        return 1


def _health(host: str, port: int, expect_version: str | None = None,
            tries: int = HEALTH_TRIES, delay: float = HEALTH_DELAY) -> dict | None:
    """Poll /healthz until it reports `expect_version` (or just answers, if None)."""
    if host in ("", "0.0.0.0", "::", "::0"):
        host = "127.0.0.1"
    url = f"http://{host}:{port}/healthz"
    last: dict | None = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            last = data
            if expect_version is None or data.get("version") == expect_version:
                return data
        except Exception:  # noqa: BLE001 - connection refused while restarting is normal
            pass
        if i + 1 < tries:
            time.sleep(delay)
    if last is not None:
        print(f"    /healthz 在跑但版本不符: got={last.get('version')} expect={expect_version}")
    else:
        print(f"    /healthz 无响应（{url}，{tries} 次尝试）")
    return None


def _panel_restart() -> bool:
    """Restart through the AegisServerPanel managed-app API.

    The panel's `/api/panel/apps/{id}/restart` route is guarded by
    `require_internal` (session + internal mode) — an **agent token is not
    enough**, it returns 401. So this only fires when a session is available
    (`DOGBUDDY_PANEL_PASSWORD`), and `_kill_app_processes` is the default path
    for agent-token deployments.
    """
    app_id = os.environ.get("DOGBUDDY_PANEL_APP_ID", "")
    password = os.environ.get("DOGBUDDY_PANEL_PASSWORD", "")
    if not app_id or not password:
        return False
    try:
        cfg = load_config(DEFAULT_CONFIG)
    except Exception:  # noqa: BLE001
        cfg = None
    base = os.environ.get("DOGBUDDY_PANEL_URL") or (cfg.panel.base_url if cfg else "")
    if not base:
        return False

    import http.cookiejar

    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    try:
        login = urllib.request.Request(
            f"{base.rstrip('/')}/api/auth/login", method="POST",
            data=json.dumps({"password": password}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with opener.open(login, timeout=20):
            pass
        req = urllib.request.Request(f"{base.rstrip('/')}/api/panel/apps/{app_id}/restart",
                                     method="POST", data=b"{}",
                                     headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=30) as resp:
            ok = 200 <= resp.status < 300
        print(f"    已请求面板重启托管应用 {app_id}: {'ok' if ok else 'failed'}")
        return ok
    except Exception as e:  # noqa: BLE001
        print(f"    面板重启失败: {e}")
        return False


def _kill_app_processes(app_dir: Path | None = None) -> int:
    """TERM every `python -m dogbuddy.main` process except ourselves.

    Used when the app is supervised externally (panel managed app / supervisor
    script): killing it is how we hand the restart to the supervisor, which
    brings it back **with the new code on disk**. Without this, a supervised
    deployment keeps running the old process and the port never frees up.
    """
    if IS_WINDOWS:
        return 0
    import signal

    me = os.getpid()
    killed = 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == me:
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if "dogbuddy.main" not in cmdline and "dogbuddy/main.py" not in cmdline:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed += 1
        except OSError:
            pass
    return killed


def _port_answers(host: str, port: int) -> bool:
    return _health(host, port, expect_version=None, tries=1, delay=0) is not None


def _restart_service(args: argparse.Namespace, host: str = "", port: int = 0) -> int:
    cmd = os.environ.get("DOGBUDDY_RESTART_CMD", "")
    if cmd:
        print(f"==> 重启（DOGBUDDY_RESTART_CMD）: {cmd}")
        rc = subprocess.run(cmd, shell=True).returncode
        if port:
            _health(host, port, expect_version=None, tries=15, delay=1.0)
        return rc

    if _panel_restart():
        print("==> 已委托面板重启")
        return 0

    if not IS_WINDOWS and Path("/run/systemd/system").is_dir():
        print(f"==> systemctl restart {SERVICE_NAME}")
        rc = subprocess.run(["systemctl", "restart", SERVICE_NAME]).returncode
        if port:
            _health(host, port, expect_version=None, tries=15, delay=1.0)
        return rc

    # externally supervised (panel managed app): kill it and wait for the supervisor
    supervised = bool(os.environ.get("DOGBUDDY_PANEL_APP_ID"))
    if supervised or _port_answers(host, port):
        killed = _kill_app_processes()
        if killed:
            print(f"==> 已终止 {killed} 个 dogbuddy 进程，等待外部守护拉起（新代码已在磁盘）")
            if port and _health(host, port, expect_version=None, tries=20, delay=1.0) is not None:
                return 0
            print("    外部守护未在 20s 内拉起，改由 manage.py 自行启动")

    print("==> 本地 stop/start 重启")
    if port and not os.environ.get("DOGBUDDY_PANEL_APP_ID"):
        _kill_app_processes()
    rc = cmd_stop(args)
    if rc != 0:
        return rc
    time.sleep(1.0)
    return cmd_start(args)


def _copy_tree(src: Path, dst: Path, skip: set[str] | None = None) -> None:
    """Overlay-copy `src` onto `dst`, ignoring runtime dirs."""
    skip = skip or SKIP_NAMES
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in skip:
            continue
        target = dst / item.name
        if item.is_dir():
            _copy_tree(item, target, skip)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def _snapshot_code(app_dir: Path, dest: Path) -> None:
    """Copy the current code tree (no .venv/.git) so a failed upgrade can roll back."""
    dest.mkdir(parents=True, exist_ok=True)
    for item in app_dir.iterdir():
        if item.name in SKIP_NAMES or item.name == LOCK_NAME:
            continue
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(*SKIP_NAMES))
        else:
            shutil.copy2(item, target)


def _git(app_dir: Path, *argv: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *argv], cwd=str(app_dir), capture_output=True, text=True,
                          timeout=timeout)


def _find_code_root(base: Path) -> Path | None:
    """Locate the directory holding the DogBuddyCn source inside an unpacked bundle."""
    if (base / "dogbuddy" / "__init__.py").exists():
        return base
    for child in sorted(base.iterdir()):
        if child.is_dir() and (child / "dogbuddy" / "__init__.py").exists():
            return child
    return None


def _safe_member_target(dest: Path, name: str, root_resolved: Path) -> Path:
    """解包前校验成员落在解包目录内（成员名是攻击者可控的）。

    审查报告 P0-4：初版用 `str(target).startswith(str(dest.resolve()))`。
    `startswith` 是**字符串前缀**判断，会放过同前缀的兄弟目录——
    成员名 `../unpacked-escape/pwned.txt` 解析成 `.../unpacked-escape/pwned.txt`，
    前缀确实是 `.../unpacked`，于是守卫通过、文件写到解包目录之外。
    这里改用 `Path.is_relative_to`（`tools/file_ops.py` 早就用对了）。
    """
    if not name or name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", name):
        raise RuntimeError(f"bundle 含非法绝对路径成员: {name}")
    try:
        target = (dest / name).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError(f"bundle 含无法解析的成员: {name}") from exc
    if target != root_resolved and not target.is_relative_to(root_resolved):
        raise RuntimeError(f"bundle 含越界路径: {name}")
    return target


def _assert_no_schema_downgrade(code_root: Path, args: argparse.Namespace) -> None:
    """升级前拦截"新代码的 schema 比现有库更旧"（审查报告 P2-1）。

    ROADMAP 与 `db.py` 的注释都声称 `manage.py update` 会在缺迁移时拒绝前进，
    但 manage.py 里从来没有过任何版本检查。这里补上：宁可中止升级，
    也不能让降级代码去写一个更高版本的库。
    """
    new_schema = _bundle_schema_version(code_root)
    cur = _db_schema_version(_data_dir(args.config))
    if new_schema is None or cur is None:
        return
    if new_schema < cur:
        raise RuntimeError(
            f"新代码的 SCHEMA_VERSION={new_schema} 低于现有库的 schema_version={cur}："
            f"降级会导致字段静默丢失与写入失败，已中止升级。"
            f"若确要回退，请先从备份恢复一个 ≤{new_schema} 的库。")
    if new_schema > cur:
        print(f"    现有库 schema_version={cur}，新代码 {new_schema}：启动时将自动迁移")


def _extract_bundle(path: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    root_resolved = dest.resolve()
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            for member in z.namelist():
                # path-traversal guard (zip entry names are attacker-controlled)
                if member.endswith("/"):
                    continue
                _safe_member_target(dest, member, root_resolved)
            z.extractall(dest)
    else:
        with tarfile.open(path) as t:
            for member in t.getmembers():
                _safe_member_target(dest, member.name, root_resolved)
                if member.issym() or member.islnk() or member.isdev():
                    # 名字可能合法，但**链接目标**能逃逸，而按名字判断的守卫看不见；
                    # 且 extractall 仍会把链接建出来。直接不提取这个成员。
                    print(f"    跳过链接/设备成员（不提取）: {member.name}", file=sys.stderr)
                    continue
                try:
                    # filter="data"（3.12+）会清掉 setuid/setgid、拒绝绝对路径与链接；
                    # 也是 3.14 的默认行为，显式传入以免将来行为突变。
                    t.extract(member, dest, set_attrs=False, filter="data")
                except TypeError:  # 更老的 Python 没有 filter 参数
                    t.extract(member, dest, set_attrs=False)

    root = _find_code_root(dest)
    if root is None:
        raise RuntimeError("bundle 里找不到 dogbuddy/__init__.py，不是有效的 DogBuddyCn 包")
    return root


def _bundle_schema_version(code_root: Path) -> int | None:
    """读即将安装的代码里的 SCHEMA_VERSION（不 import，直接读源码）。

    用于升级前拦截"新代码的 schema 比现有库旧"这种降级（审查报告 P2-1：
    ROADMAP 声称 `manage.py update` 缺迁移会拒绝前进，但 manage.py 里
    从来没有过任何版本检查）。
    """
    f = code_root / "dogbuddy" / "db.py"
    try:
        text = f.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"^SCHEMA_VERSION\s*=\s*(\d+)", text, re.MULTILINE)
    return int(m.group(1)) if m else None


def _db_schema_version(data_dir: Path) -> int | None:
    """现有库的 schema_version；库不存在/读不到返回 None（不阻断升级）。"""
    dbf = Path(data_dir) / "dogbuddy.db"
    if not dbf.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{dbf}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT MAX(version) FROM schema_version").fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        finally:
            con.close()
    except sqlite3.Error:
        return None



def _download(url: str, dest: Path) -> Path:
    print(f"==> 下载 {url}")
    suffix = ".zip" if url.split("?")[0].endswith(".zip") else ".tar.gz"
    target = dest / f"dogbuddy-bundle{suffix}"
    with urllib.request.urlopen(url, timeout=60) as resp, open(target, "wb") as f:
        shutil.copyfileobj(resp, f, length=1024 * 256)
    print(f"    {target} ({target.stat().st_size} bytes)")
    return target


def _update_git(args: argparse.Namespace, app_dir: Path, stamp: str) -> int:
    ref = args.ref or "origin/main"
    current_sha = _git(app_dir, "rev-parse", "HEAD").stdout.strip()

    dirty = _git(app_dir, "status", "--porcelain")
    if dirty.returncode != 0:
        print("错误: git status 失败，仓库状态异常", file=sys.stderr)
        return 1
    # update.lock is our own artifact — it must not permanently block upgrades
    dirty_lines = [ln for ln in dirty.stdout.splitlines() if not ln.endswith(LOCK_NAME)]
    if dirty_lines:
        if not args.force:
            print("错误: 仓库存在本地改动，为避免丢失已中止（可用 --force 自动 stash）:", file=sys.stderr)
            for ln in dirty_lines:
                print("    " + ln, file=sys.stderr)
            return 1
        stash = _git(app_dir, "stash", "push", "-u", "-m", f"dogbuddy update autostash {stamp}")
        if stash.returncode != 0:
            print(f"错误: stash 失败: {stash.stderr.strip()}", file=sys.stderr)
            return 1
        print("==> 本地改动已 stash")

    fetch = _git(app_dir, "fetch", "origin", "--prune")
    if fetch.returncode != 0:
        # 国内链路上到 github 的 HTTPS 经常被 TLS 中断（GnuTLS recv error -110），
        # SSH 走 22/443 反而稳定；这里重试一次，避免一次抖动就把升级判死。
        print(f"    首次 fetch 失败，5s 后重试：{fetch.stderr.strip()[:160]}")
        time.sleep(5)
        fetch = _git(app_dir, "fetch", "origin", "--prune")
    if fetch.returncode != 0:
        print(f"错误: git fetch 失败（正在运行的服务不受影响）: {fetch.stderr.strip()}", file=sys.stderr)
        print("    若报 TLS/GnuTLS 错误，把 origin 换成 SSH："
              "git remote set-url origin git@github.com:<owner>/<repo>.git", file=sys.stderr)
        return 1

    target = _git(app_dir, "rev-parse", "--verify", ref)
    if target.returncode != 0:
        print(f"错误: 目标 {ref} 不存在: {target.stderr.strip()}", file=sys.stderr)
        return 1
    target_sha = target.stdout.strip()
    if target_sha == current_sha:
        print(f"已是最新版本（{current_sha[:10]}）。")
        return 0

    print(f"==> 切换代码 {current_sha[:10]} -> {target_sha[:10]}")
    checkout = _git(app_dir, "checkout", target_sha)
    if checkout.returncode != 0:
        print(f"错误: git checkout 失败: {checkout.stderr.strip()}", file=sys.stderr)
        return 1

    try:
        _assert_no_schema_downgrade(app_dir, args)
    except RuntimeError as e:
        _git(app_dir, "checkout", current_sha)
        print(f"错误: {e}", file=sys.stderr)
        print("    已回退代码（服务未重启，仍运行旧版本）", file=sys.stderr)
        return 1

    print("==> 安装依赖")
    if _pip_install(app_dir) != 0:
        _git(app_dir, "checkout", current_sha)
        print("错误: 依赖安装失败，已回退代码（服务未重启，仍运行旧版本）", file=sys.stderr)
        return 1

    return _finish(app_dir, args, stamp, rollback=lambda: (_git(app_dir, "checkout", current_sha),
                                                           _pip_install(app_dir)))


def _update_bundle(args: argparse.Namespace, app_dir: Path, stamp: str) -> int:
    work = Path(tempfile.mkdtemp(prefix="dogbuddy-update-"))
    rollback_dir = work / "rollback"
    try:
        src = Path(args.from_url) if args.from_url else Path(args.from_bundle)
        if args.from_url:
            src = _download(args.from_url, work)
        if not src.exists():
            print(f"错误: bundle 不存在: {src}", file=sys.stderr)
            return 1

        print(f"==> 解包 {src.name}")
        code_root = _extract_bundle(src, work / "unpacked")
        print(f"    代码根目录: {code_root}")
        _assert_no_schema_downgrade(code_root, args)

        print("==> 备份当前代码（用于回滚）")
        _snapshot_code(app_dir, rollback_dir)

        print("==> 覆盖代码")
        _copy_tree(code_root, app_dir)

        print("==> 安装依赖")
        if _pip_install(app_dir) != 0:
            print("错误: 依赖安装失败，回退代码", file=sys.stderr)
            _copy_tree(rollback_dir, app_dir)
            _pip_install(app_dir)
            print("    已回退（服务未重启，仍运行旧版本）", file=sys.stderr)
            return 1

        return _finish(app_dir, args, stamp,
                       rollback=lambda: (_copy_tree(rollback_dir, app_dir), _pip_install(app_dir)))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _finish(app_dir: Path, args: argparse.Namespace, stamp: str, rollback) -> int:
    """Restart, assert the new version, and roll back when it does not come up."""
    new_version = _installed_version(app_dir)
    if not new_version:
        print("警告: 无法读取新版本号，继续（健康检查将只校验可达性）", file=sys.stderr)

    cfg = load_config(args.config)
    host, port = cfg.web.host, cfg.web.port
    data_dir = _data_dir(args.config)
    previous = args._previous_version if getattr(args, "_previous_version", None) is not None \
        else _installed_version(app_dir)

    if args.no_restart:
        print(f"==> 已跳过重启（--no-restart）。盘上版本 {new_version or '?'}")
        _write_version_stamp(app_dir)
        write_result(data_dir, status="installed", ok=True, previous=previous,
                     version=new_version, detail="installed without restart (--no-restart)",
                     source=_source_label(args))
        return 0

    print(f"==> 重启服务（新版本 {new_version or '未知'}）")
    if _restart_service(args, host, port) != 0:
        print("错误: 重启命令返回非 0", file=sys.stderr)

    print("==> 健康检查")
    health = _health(host, port, expect_version=new_version or None,
                     tries=args.health_tries, delay=args.health_delay)
    if health is None:
        print("新版本未通过健康检查，回滚中…", file=sys.stderr)
        rollback()
        _restart_service(args, host, port)
        rolled = _health(host, port, expect_version=None,
                         tries=args.health_tries, delay=args.health_delay)
        if rolled is None:
            print("错误: 回滚后健康检查仍失败！请立即查看日志排查。", file=sys.stderr)
            write_result(data_dir, status="rollback-failed", ok=False, previous=previous,
                         version=new_version, rolled_back_to="",
                         detail="rollback did not come up either — manual intervention needed",
                         source=_source_label(args))
            return 1
        print(f"已回滚并恢复运行（版本 {rolled.get('version')}）", file=sys.stderr)
        write_result(data_dir, status="rolled-back", ok=False, previous=previous,
                     version=new_version, rolled_back_to=rolled.get("version", ""),
                     detail="new version failed the health check; previous code restored and healthy",
                     source=_source_label(args))
        return 1

    stamp_value = _write_version_stamp(app_dir)
    print(f"==> 升级完成: version={health.get('version')} build={stamp_value} "
          f"schema={health.get('schema_version')}")
    write_result(data_dir, status="success", ok=True, previous=previous,
                 version=health.get("version", new_version), build=stamp_value,
                 schema_version=health.get("schema_version"), source=_source_label(args),
                 detail="healthy after restart")
    return 0


def _source_label(args: argparse.Namespace) -> str:
    if getattr(args, "from_bundle", None):
        return f"bundle:{Path(args.from_bundle).name}"
    if getattr(args, "from_url", None):
        return f"url:{args.from_url}"
    return f"git:{getattr(args, 'ref', '') or 'origin/main'}"


def cmd_update(args: argparse.Namespace) -> int:
    app_dir = Path(args.app_dir).resolve() if args.app_dir else APP_DIR
    if not IS_WINDOWS and hasattr(os, "geteuid") and os.geteuid() != 0:
        print("警告: 未以 root 运行；若代码/数据目录受限，升级可能失败", file=sys.stderr)

    if getattr(args, "start_delay", 0):
        # launched detached by the agent: give the current turn time to finish and
        # persist its reply before we restart the process underneath it
        print(f"==> 延迟 {args.start_delay}s 后开始（留时间给当前对话落盘）")
        time.sleep(int(args.start_delay))

    try:
        cfg = load_config(args.config)
    except Exception as e:  # noqa: BLE001
        print(f"错误: 配置无法加载: {e}", file=sys.stderr)
        return 1

    # CLI flags win; otherwise config `update:` section supplies the defaults
    if not args.ref:
        args.ref = cfg.update.ref
    if args.health_tries is None:
        args.health_tries = cfg.update.health_tries
    if args.health_delay is None:
        args.health_delay = cfg.update.health_delay

    has_git = (app_dir / ".git").exists()
    if not (args.from_bundle or args.from_url or has_git):
        print("错误: 当前是 zip/快照安装（无 .git），无法增量升级。\n"
              "    请用 --from-bundle <包> 或 --from-url <地址> 做离线升级。", file=sys.stderr)
        return 1

    current = _installed_version(app_dir) or __version__
    print(f"==> 当前版本 {current}（{app_dir}）")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    args._previous_version = current

    if args.check:
        source = (args.from_url or args.from_bundle) if (args.from_bundle or args.from_url) \
            else f"git {args.ref}"
        print(f"    更新源: {source}")
        print("    --check 模式：不执行任何变更。")
        return 0

    write_result(_data_dir(args.config), status="running", ok=False, previous=current,
                 version="", source=_source_label(args), detail="upgrade in progress")

    try:
        with _update_lock(app_dir):
            if not args.no_backup:
                _backup_config(args.config, stamp)
            if args.from_bundle or args.from_url:
                return _update_bundle(args, app_dir, stamp)
            return _update_git(args, app_dir, stamp)
    except RuntimeError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1


# --------------------------------------------------------------------------- cli
def main() -> int:
    p = argparse.ArgumentParser(prog="manage.py", description="DogBuddyCn operations")
    p.add_argument("--config", default=DEFAULT_CONFIG)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("start").set_defaults(func=cmd_start)
    st = sub.add_parser("stop")
    st.add_argument("--force", action="store_true",
                    help="also terminate externally supervised dogbuddy processes")
    st.set_defaults(func=cmd_stop)
    sub.add_parser("restart").set_defaults(func=cmd_restart)
    sub.add_parser("status").set_defaults(func=cmd_status)

    lg = sub.add_parser("logs")
    lg.add_argument("--lines", type=int, default=50)
    lg.set_defaults(func=cmd_logs)

    bk = sub.add_parser("backup")
    bk.add_argument("--output")
    bk.set_defaults(func=cmd_backup)

    rs = sub.add_parser("restore")
    rs.add_argument("input")
    rs.set_defaults(func=cmd_restore)

    vr = sub.add_parser("version", help="show running / on-disk / git version")
    vr.add_argument("--app-dir")
    vr.add_argument("--json", action="store_true")
    vr.set_defaults(func=cmd_version)

    up = sub.add_parser("update", help="self-upgrade (git / bundle / url) with health-check rollback")
    up.add_argument("--app-dir", help="app directory (default: this file's directory)")
    up.add_argument("--ref", help="git ref to install (default origin/main)")
    up.add_argument("--from-bundle", help="offline .tar.gz/.zip bundle to install")
    up.add_argument("--from-url", help="URL of a .tar.gz/.zip bundle to install")
    up.add_argument("--force", action="store_true", help="stash local git changes instead of aborting")
    up.add_argument("--no-backup", action="store_true", help="skip the config backup step")
    up.add_argument("--no-restart", action="store_true", help="install only, do not restart the service")
    up.add_argument("--check", action="store_true", help="report the update source and exit")
    up.add_argument("--start-delay", type=int, default=0,
                    help="seconds to wait before touching anything (agent-triggered upgrades "
                         "use this so the current reply is persisted before the restart)")
    up.add_argument("--health-tries", type=int, default=None,
                    help="/healthz poll attempts (default: config update.health_tries = 30)")
    up.add_argument("--health-delay", type=float, default=None,
                    help="seconds between /healthz polls (default: config update.health_delay = 1.0)")
    up.set_defaults(func=cmd_update)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
