# DogBuddyCn — Batch 7 · T7.4 shell 越界守卫
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""`shell` 曾经是唯一的边界逃逸口：文件工具有 `_resolve_in_workspace` 锁死，
但 `/bin/sh` 里一句 `cd /` 就全绕过了。这些测试锁住 T7.4 的行为。

口径（2026-10 收紧，见审查报告 P0-2）：**工作区内放行，凡引用工作区之外的路径
（读或写）或含静态分析不动的构造（`$VAR`/`$( )`/`sh -c`/`sed -i`/…) 一律拦**，
需 `elevated` 信任级才放行。

初版口径是"拦 cd 越界和写越界，不拦读越界"，理由是把工作区外的读留给
PermissionGate 的危险命令黑名单。实测推翻：黑名单里一条读命令都没有，于是
`cat ~/.ssh/id_rsa` 零防线，而 48 条 payload 只拦住 18 条。读与写现在一视同仁。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.tools.registry import ToolContext, build_registry  # noqa: E402
from dogbuddy.tools.shell_guard import (analyze_command, dynamic_notes,  # noqa: E402
                                        is_elevated, refusal_message)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _ctx(tmp_path, trust: str = "", db=None) -> ToolContext:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(window_id="w1", workspace=str(ws), mode="BUILD",
                       actor="worker", db=db, checkpoint_root=str(tmp_path / "ckpt"),
                       extra={"trust": trust} if trust else {})


# --------------------------------------------------------------------------- 分析器
def test_工作区内的普通命令不报警(tmp_path):
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir(exist_ok=True)
    for cmd in [
        "ls -la",
        "python -m pytest -q",
        "git status --short",
        "echo hello > out.txt",
        "cat README.md | head -20",
        "mkdir -p src/tests && touch src/main.py",
        "npm run build 2>&1",
        "rm -rf build/",
        "cp a.txt b.txt",
    ]:
        assert analyze_command(cmd, ws) == [], f"不该报警: {cmd}"


def test_cd_越界被识别(tmp_path):
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir(exist_ok=True)
    for cmd in ["cd /etc && ls", "cd / && rm -rf tmp", "cd ..", "cd ../../..", "cd ~"]:
        risks = analyze_command(cmd, ws)
        assert risks and risks[0]["kind"] in ("cd", "traverse"), f"应识别越界: {cmd}"


def test_cd_工作区内不报警(tmp_path):
    ws = str(tmp_path / "ws")
    (tmp_path / "ws" / "src").mkdir(parents=True, exist_ok=True)
    assert analyze_command("cd src && ls", ws) == []
    assert analyze_command(f"cd {ws}/src && ls", ws) == []


def test_重定向写出界被识别(tmp_path):
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir(exist_ok=True)
    risks = analyze_command("echo x > /etc/passwd", ws)
    assert any(r["kind"] == "write" and "/etc/passwd" in r["target"] for r in risks)
    assert analyze_command("cat a > /dev/null", ws) == [], "/dev/null 是正常丢弃"


def test_写命令的绝对路径被识别(tmp_path):
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir(exist_ok=True)
    for cmd in ["rm -rf /var/log/x", "cp secret.txt /tmp/leak", "mkdir /opt/newdir",
                "chmod 777 /etc/shadow", "dd if=/dev/zero of=/dev/sda",
                "tee /etc/hosts"]:
        assert analyze_command(cmd, ws), f"应识别越界写: {cmd}"


def test_工作区外读也被拦(tmp_path):
    """口径收紧（P0-2）：工作区外的读与写一视同仁。

    旧口径把这些放给 PermissionGate，但黑名单里没有任何读命令，
    于是 `cat ~/.ssh/id_rsa` 可以直读宿主机私钥。
    """
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir(exist_ok=True)
    for cmd in ["cat /etc/os-release", "ls /var/log", "cat ~/.ssh/id_rsa",
                "cat /etc/shadow", "head -5 /etc/passwd", "cat ../../etc/passwd"]:
        assert analyze_command(cmd, ws), f"工作区外的读必须拦: {cmd}"


def test_纯工作区内的常见命令仍然放行(tmp_path):
    """收紧不能把正常工作流废掉：不引用工作区外路径的命令一律放行。"""
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir(exist_ok=True)
    for cmd in ["uname -a", "df -h", "which python3", "python3 --version",
                "git status", "git diff HEAD~1", "ruff check .",
                "pip install requests", "pnpm build", "/usr/bin/env"]:
        # /usr/bin/env 是绝对路径 → 拦（属预期）；其余应放行
        if cmd.startswith("/usr/"):
            continue
        assert analyze_command(cmd, ws) == [], f"工作区内命令不该被拦: {cmd}"


def test_审查报告_payload_全部被拦(tmp_path):
    """审查报告 P0-2 实测逃逸的 payload——初版只拦 18/48，这些是漏网的。

    每条都曾**真实执行成功并写出工作区外**，因此必须回归。
    """
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir(exist_ok=True)
    payloads = [
        # 引号让初版 _looks_absolute 判假
        'echo x >"/etc/x"',
        "echo x >'/etc/x'",
        # 相对路径 + pushd/cd 爬目录后写
        "cd .. && echo x > pwned.txt",
        "pushd ../../ && echo x > pwned.txt",
        # 变量拼接
        "D=/; cd $D && rm -f etc/hosts",
        "echo x > $HOME/x",
        # 输出重定向类工具
        "sed -i s/a/b/ /etc/hosts",
        "sort -o /etc/hosts f",
        "curl -o /etc/x http://e/x",
        "wget -O /etc/x http://e/x",
        "unzip -d /opt x.zip",
        "tar -C / -xf x.tar",
        "rsync -e ssh a /etc/",
        # 前缀/二次执行
        "env rm -rf /var/log",
        "sudo rm -rf /var/log",
        "echo /etc/x | xargs rm",
        "find . -exec rm {} ;",
        "sh -c 'rm -rf /var/log'",
        # 带引号的写命令参数（v0.10.20：引号内**像纯路径**仍参与判定）
        "rm -rf '/var/log'",
        "tee \"/etc/hosts\"",
    ]
    missed = [c for c in payloads if not analyze_command(c, ws)]
    assert not missed, f"以下 payload 仍未拦住：{missed}"


def test_重定向到无害设备仍放行(tmp_path):
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir(exist_ok=True)
    for cmd in ["echo x > /dev/null", "cmd 2>&1", "echo y >> /dev/null",
                "cmd > /dev/stdout"]:
        assert analyze_command(cmd, ws) == [], f"无害目标不该拦: {cmd}"


def test_解析不了的路径按拒绝处理(tmp_path):
    """fail-closed：判不出边界时拒绝，而不是放行。"""
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir(exist_ok=True)
    # NUL 字节会让 Path.resolve 抛错
    assert analyze_command("cat /etc/\x00x", ws), "解析失败必须拒绝"


def test_多目标去重(tmp_path):
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir(exist_ok=True)
    risks = analyze_command("rm -rf /tmp/a && rm -rf /tmp/a", ws)
    assert len(risks) == 1, "同一目标只报一次"


def test_elevated_判定(tmp_path):
    assert is_elevated(_ctx(tmp_path, trust="elevated")) is True
    assert is_elevated(_ctx(tmp_path, trust="ELEVATED")) is True
    assert is_elevated(_ctx(tmp_path, trust="standard")) is False
    assert is_elevated(_ctx(tmp_path)) is False


# --------------------------------------------------------------------------- 工具层
def test_标准信任级下越界命令被拒且留痕(tmp_path):
    from dogbuddy.db import Database
    db = Database(tmp_path / "t.db")
    reg = build_registry(audit=db)
    ctx = _ctx(tmp_path, db=db)
    (tmp_path / "ws").mkdir(exist_ok=True)

    res = _run(reg.execute("shell", {"command": "cd /etc && ls"}, ctx))
    assert not res.ok and res.data.get("boundary") is True
    assert "越界拦截" in res.output and "elevated" in res.output
    # 留痕
    entries = [e for e in db.list_audit() if e.action == "shell:boundary"]
    assert entries, "越界尝试必须进审计"
    assert "elevated=False" in entries[0].detail
    db.close()


def test_elevated_信任级放行但留痕(tmp_path):
    from dogbuddy.db import Database
    db = Database(tmp_path / "t.db")
    reg = build_registry(audit=db)
    (tmp_path / "ws").mkdir(exist_ok=True)
    ctx = _ctx(tmp_path, trust="elevated", db=db)

    # 放行：命令真的执行了（用跨平台命令，避免依赖 POSIX 工具）
    res = _run(reg.execute("shell", {"command": "cd .. && echo crossed"}, ctx))
    assert res.ok, f"elevated 应放行，实际: {res.output}"
    assert "crossed" in res.output
    entries = [e for e in db.list_audit() if e.action == "shell:boundary"]
    assert entries and "elevated=True" in entries[0].detail, "放行也要留痕"
    db.close()


def test_工作区内命令不受影响(tmp_path):
    reg = build_registry()
    ctx = _ctx(tmp_path)
    res = _run(reg.execute("shell", {"command": "echo hello > ok.txt"}, ctx))
    assert res.ok
    assert (tmp_path / "ws" / "ok.txt").exists()
    assert "hello" in (tmp_path / "ws" / "ok.txt").read_text(encoding="utf-8")


# ==========================================================================
# v0.10.20 误拦收敛（owner 交接书 §2.3 点名的圆桌实测误拦）
# ==========================================================================
def test_引号内的正则与URL不是路径(tmp_path):
    """圆桌实测误拦①：`grep -oE '"/api/...'`、`curl "http://...$c"` 里的
    `/api`、URL 是字符串字面量，不是路径引用。"""
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir(exist_ok=True)
    for cmd in [
        "grep -oE '\"/api/[a-zA-Z0-9._/{}-]+' probe/app.js",
        "grep -oE \"/api/[a-z]+\" probe/app.js",
        "curl -s http://127.0.0.1:8787/api/health",
        "curl -s \"http://127.0.0.1:8787/api/health\"",
        "echo \"== ws ==\"; date; locale",
        "for c in /api /api/health /docs; do echo $c; done",
        "echo '=== TZ ==='; date",
    ]:
        assert analyze_command(cmd, ws) == [], f"误拦：{cmd}"


def test_引号内像纯路径的参数仍然拦(tmp_path):
    """取舍的另一面：带引号的**纯路径**参数依然是路径引用（`rm -rf '/var/log'`）。"""
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir(exist_ok=True)
    for cmd in ["rm -rf '/var/log'", "cat \"/etc/passwd\"", "tee \"/etc/hosts\"",
                "cd \"/etc\""]:
        assert analyze_command(cmd, ws), f"带引号的纯路径必须拦: {cmd}"


def test_循环构造本身不拦_循环体照判(tmp_path):
    """圆桌实测误拦②：`for c in /api ...` 的词表不是路径引用；
    但循环体里的命令逐段判定——`do cp $f /etc/` 仍拦。"""
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir(exist_ok=True)
    # 词表里的路径不拦
    assert analyze_command("for c in /api /docs /tmp; do echo $c; done", ws) == []
    assert analyze_command("if [ -f /etc/os-release ]; then echo yes; fi", ws) == []
    # 循环体写外界照拦
    risks = analyze_command("for f in *.txt; do cp $f /etc/leak; done", ws)
    assert any("/etc/leak" in r["target"] for r in risks), f"循环体越界应拦: {risks}"
    # while 条件里的路径不拦，体重定向照拦
    assert analyze_command("while read -r l; do echo \"$l\"; done < list.txt", ws) == []
    risks = analyze_command("while read -r l; do echo $l > /etc/x; done", ws)
    assert any(r["kind"] == "write" for r in risks)


def test_python_c等他语言内联代码放行且记录(tmp_path):
    """圆桌实测误拦③：`python -c` 内容从来不可静态分析，且写进 .py 文件
    执行本就放行——拦它只制造噪音与绕行动机（qa-env 正是被逼去写脚本文件）。"""
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir(exist_ok=True)
    for cmd in ["python3 -c 'import platform; print(platform.platform())'",
                "python -c \"open('x.txt','w').write('p')\"",
                "perl -e \"print 1\"",
                "node -e \"console.log(1)\""]:
        assert analyze_command(cmd, ws) == [], f"他语言内联应放行: {cmd}"
    notes = dynamic_notes("python3 -c 'print(1)'")
    assert notes and "python -c" in notes[0]["target"], "放行但要留审计记录"


def test_循环体内的不可判定构造放行且记录(tmp_path):
    """owner 交接书：循环体里的命令逐个判定，无法判定就放行+记录。"""
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir(exist_ok=True)
    cmd = "for c in /api /docs; do curl -s \"http://127.0.0.1:8787$c\"; done"
    assert analyze_command(cmd, ws) == [], f"循环体内的动态构造应放行: {cmd}"
    notes = dynamic_notes(cmd)
    assert notes, "放行但要留审计记录"
    # 循环**外**的同类构造仍然拒绝
    assert analyze_command("curl -s \"http://h$x\"", ws), "循环外的 $VAR 仍拦"


def test_拒绝消息单行化带错误码且同码只详述一次(tmp_path):
    from dogbuddy.db import Database
    db = Database(tmp_path / "t.db")
    reg = build_registry(audit=db)
    (tmp_path / "ws").mkdir(exist_ok=True)
    ctx = _ctx(tmp_path, db=db)

    res = _run(reg.execute("shell", {"command": "cat /etc/passwd"}, ctx))
    assert not res.ok
    msg = res.output
    assert "\n" not in msg, "拒绝消息必须单行（5 行长文既烧 token 又被摘要继承）"
    assert "E_BOUNDARY_OUTSIDE_PATH" in msg
    assert res.data.get("error_code") == ["E_BOUNDARY_OUTSIDE_PATH"]

    # 同一会话同一错误码：第二次只回短消息
    res2 = _run(reg.execute("shell", {"command": "ls /var/log"}, ctx))
    assert "已说明过" in res2.output, f"同码第二次应只给短消息: {res2.output}"

    # 换一个错误码：重新完整说明
    res3 = _run(reg.execute("shell", {"command": "cat $HOME/x"}, ctx))
    assert "E_BOUNDARY_UNEVALUABLE" in res3.output and "已说明过" not in res3.output
    db.close()


def test_refusal_message_两条码合并(tmp_path):
    ws = str(tmp_path / "ws")
    risks = [{"kind": "path", "target": "/etc/x", "reason": "r"},
             {"kind": "dynamic", "target": "$VAR", "reason": "r"}]
    msg = refusal_message(risks, ws)
    assert "\n" not in msg
    assert "E_BOUNDARY_OUTSIDE_PATH" in msg and "E_BOUNDARY_UNEVALUABLE" in msg
    short = refusal_message(risks, ws, first_time=False)
    assert "已说明过" in short


def test_dynamic_notes_他语言外不误报(tmp_path):
    """普通命令不该被记成"放行的动态构造"。"""
    assert dynamic_notes("ls -la && git status") == []
    assert dynamic_notes("echo hi > out.txt") == []
