# DogBuddyCn — shell 越界守卫 (Batch 7 · T7.4)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""把 shell 命令锁在工作区内（Batch 7 · T7.4，"必做"）。

**为什么需要**：文件工具（`read_file`/`write_file`/…）都用 `_resolve_in_workspace`
锁死了边界，但 `shell` 是整条命令交给 `/bin/sh` 执行——一句 `cd /` 或
`echo x > /etc/passwd` 就绕过了全部文件边界。这是原设计明确的"逃逸缝"。

## 边界口径（2026-10 收紧，见审查报告 P0-2）

初版只拦"能明确判定的两类越界"（`cd` 出去、写命令的绝对路径参数），并**刻意放行
工作区外的读**，理由是"一刀切禁读会把正常运维命令全废掉"。实测证明这条口径守不住：

- 48 条 payload 只拦 18 条；`pushd` 爬目录后的相对路径写、带引号的绝对路径重定向、
  `$VAR` 拼接、`sed -i`/`sort -o`/`curl -o`/`unzip -d`/`tar -C`、`env`/`sudo`/`xargs`/
  `sh -c` 全部放行，并且**真跑起来确实把文件写到了工作区外**。
- "工作区外的读交给 PermissionGate 管"是空头承诺——黑名单里**一条读命令都没有**，
  于是 `cat ~/.ssh/id_rsa` 零防线，再配合 `web_fetch` 就是一条外发通道。

**现在的口径**：凡引用工作区之外的路径（读或写），或含**静态分析无法判定**的构造，
一律拒绝，需 `elevated` 信任级才放行（留审计痕迹）。工作区内的读写不受影响。

需要真正的宿主机运维能力时用 `server_exec`（它声明了 `required_scope="system"`，
只有管理员可用）——两个工具边界清晰，而不是一个工具又当边界又当运维口。

## 拦什么

1. **工作区外的绝对路径**（任何位置，读或写）：`/etc/passwd`、`~/.ssh/id_rsa`、`C:\\x`
2. **`..` 爬出工作区**（相对路径也算）：`cat ../../etc/passwd`
3. **`cd` / `pushd` 到工作区之外**（含无参数的 `cd` = `cd $HOME`）
4. **重定向写到工作区之外**（先脱引号再判，`>"/etc/x"` 一样拦）
5. **静态分析不动的构造**：`$VAR` / `${...}` / `$( )` / 反引号 / `sh -c` / `env` /
   `sudo` / `xargs` / `find -exec` / `eval` / `source` / `python -c` 等

**不管**：工作区内的任何操作（含管道、重定向到工作区内、`/dev/null`）。
`/dev/null`、`/dev/stdout`、`/dev/stderr`、`/dev/fd/*` 是有意放行的无害目标。

注意：这仍然不是内核级沙箱。目标是关掉"顺手就越界"的缝 + 关掉静态可判定的逃逸，
而不是对抗一个能构造任意字节码的对手（那需要容器/只读挂载，见审查报告 P0-2 的长期建议）。
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

# 有"写"语义、且参数指到工作区外就要拦的命令。
# 注意：这只是"额外要多查一遍参数"的提示集合；**任何命令**的绝对路径参数
# 都会被下面的通用路径扫描覆盖，不依赖这张表。
_WRITE_COMMANDS = frozenset({
    "rm", "rmdir", "mv", "cp", "mkdir", "touch", "chmod", "chown", "chgrp",
    "ln", "tee", "truncate", "install", "mkfifo", "mknod", "dd", "shred",
    "pushd", "popd", "unlink", "setfacl", "chattr",
})

# 命令分段：&& || ; | 换行（够用即可；不追求完整 shell 语法树）
_SPLIT_RE = re.compile(r"&&|\|\||[;|\n]")
# 重定向：> / >> / 2> / &> / 1> 后面跟的目标（允许被引号包起来）
_REDIRECT_RE = re.compile(r"(?:\d?|&)>>?\s*(\"[^\"]*\"|'[^']*'|[^\s;|&<>]+)")

# 静态分析不动的构造 —— 出现即要求 elevated。
# 分组只为让拒绝信息能说清"为什么"，不影响判定。
_DYNAMIC_PATTERNS: list[tuple[str, str]] = [
    (r"\$\(", "命令替换 $( )"),
    (r"`", "反引号命令替换"),
    (r"\$\{", "变量展开 ${ }"),
    (r"\$[A-Za-z_0-9]", "变量展开 $VAR"),
    (r"\b(sh|bash|zsh|ksh|dash|csh|fish)\s+-c\b", "shell -c 嵌套执行"),
    (r"\beval\b", "eval"),
    (r"(^|[\s;&|])\.\s+\S", "source/. 加载脚本"),
    (r"\bsource\b", "source 加载脚本"),
    (r"\bsudo\b", "sudo 提权"),
    (r"\benv\b", "env 前缀（可伪造环境）"),
    (r"\bxargs\b", "xargs 二次执行"),
    (r"-exec\b|-execdir\b", "find -exec"),
    (r"\bpython[0-9.]*\s+-c\b", "python -c 内联代码"),
    (r"\bperl\s+-e\b", "perl -e 内联代码"),
    (r"\bnode\s+-e\b|\bnode\s+--eval\b", "node -e 内联代码"),
    (r"\bruby\s+-e\b", "ruby -e 内联代码"),
    (r"\bphp\s+-r\b", "php -r 内联代码"),
    (r"\bawk\b", "awk（可写文件）"),
    (r"\bsed\b[^&|\n]*\s-i\b", "sed -i 就地改写"),
]
_DYNAMIC_COMPILED = [(re.compile(p), why) for p, why in _DYNAMIC_PATTERNS]

# 有意放行的无害绝对目标（不是"读文件"，是丢弃/标准流）
_BENIGN_ABS = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/zero")


def _strip_quotes(p: str) -> str:
    """去掉成对的外层引号——`"/etc/x"` 必须先脱引号再判绝对路径。

    初版漏了这一条，于是 `echo x >"/etc/x"` 直接放行（审查报告 P0-2）。
    """
    s = str(p or "").strip()
    while len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1].strip()
    return s


def _looks_absolute(p: str) -> bool:
    """跨平台判断"这是不是一个绝对/根路径"。

    不能用 `Path.is_absolute()`：Windows 上 `/etc/passwd` 返回 False（它有 root 没盘符），
    而本项目要在 Linux 跑、在 Windows 开发测试——判定必须两边一致。
    """
    s = _strip_quotes(p)
    if not s:
        return False
    if s.startswith(("/", "~", "\\")):
        return True
    return bool(re.match(r"^[A-Za-z]:[\\/]", s))


def _has_dotdot(p: str) -> bool:
    """token 里是否含 `..` 路径分量（`a..b` 这种不算）。"""
    s = _strip_quotes(p)
    if not s:
        return False
    return bool(re.search(r"(^|[\\/])\.\.([\\/]|$)", s)) or s in ("..", "../", "..\\")


def _is_benign_abs(p: str) -> bool:
    s = _strip_quotes(p)
    return any(s == b or s.startswith(b + "/") for b in _BENIGN_ABS)


def _is_within(path: str, root: str) -> bool:
    p = _strip_quotes(path)
    if not p:
        return True
    if p.startswith("~"):
        p = str(Path(p).expanduser())
    try:
        r = Path(root).resolve()
        t = Path(p)
        t = t.resolve() if _looks_absolute(p) else (r / t).resolve()
    except (OSError, RuntimeError, ValueError):
        return False  # 解析不了 = 判不出边界 → 拒绝（fail-closed）
    return t == r or r in t.parents


def _tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:  # 引号不闭合等 → 退化为按空白切
        return segment.split()


def analyze_command(command: str, workspace: str) -> list[dict[str, Any]]:
    """返回越界/不可判定风险列表；空列表 = 命令确实留在工作区内。"""
    risks: list[dict[str, Any]] = []
    if not command or not str(command).strip():
        return risks
    raw = str(command)
    root = str(workspace or ".")

    # ---- 0) 静态分析不动的构造：出现即要求 elevated
    for rx, why in _DYNAMIC_COMPILED:
        if rx.search(raw):
            risks.append({"kind": "dynamic", "target": why,
                          "reason": f"含无法静态判定的构造（{why}），越界与否无法确定"})

    for segment in _SPLIT_RE.split(raw):
        segment = segment.strip()
        if not segment:
            continue
        toks = _tokens(segment)
        if not toks:
            continue
        head = toks[0]

        # ---- 1) cd / pushd 越界（无参数 = cd $HOME，也是越界）
        if head in ("cd", "pushd"):
            target = toks[1] if len(toks) > 1 and not toks[1].startswith("-") else "~"
            if not _is_within(target, root):
                risks.append({"kind": "cd", "target": target,
                              "reason": f"{head} 到工作区之外：{target}"})

        # ---- 2) 重定向写出界（先脱引号再判）
        for m in _REDIRECT_RE.finditer(segment):
            target = _strip_quotes(m.group(1))
            if not target or target.startswith("&") or _is_benign_abs(target):
                continue
            if not _is_within(target, root):
                risks.append({"kind": "write", "target": target,
                              "reason": f"重定向写到工作区之外：{target}"})

        # ---- 3) 通用路径扫描：任何 token 只要指向工作区外，读或写都拦
        for tok in toks[1:]:
            if tok.startswith("-"):
                # `--opt=/abs/path` 这种也要查等号右侧
                if "=" not in tok:
                    continue
                tok = tok.split("=", 1)[1]
            candidate = _strip_quotes(tok)
            if not candidate or _is_benign_abs(candidate):
                continue
            if _looks_absolute(candidate) or _has_dotdot(candidate):
                if not _is_within(candidate, root):
                    risks.append({"kind": "path", "target": candidate,
                                  "reason": f"{head} 引用工作区之外的路径：{candidate}"})

        # ---- 4) 写命令：相对路径 + `..` 已由 3) 覆盖，这里只补 `of=` 形式
        if head in _WRITE_COMMANDS:
            for tok in toks[1:]:
                if not tok.startswith("of="):
                    continue
                candidate = _strip_quotes(tok.split("=", 1)[1])
                if candidate and not _is_benign_abs(candidate) and not _is_within(candidate, root):
                    risks.append({"kind": "write", "target": candidate,
                                  "reason": f"{head} 写工作区之外：{candidate}"})

    # 去重（同一目标只报一次）
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for r in risks:
        key = (r["kind"], r["target"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def is_elevated(ctx: Any) -> bool:
    """工位信任级是否为 elevated（T7.4 的放行条件）。"""
    extra = getattr(ctx, "extra", None) or {}
    return str(extra.get("trust", "")).strip().lower() == "elevated"


def refusal_message(risks: list[dict[str, Any]], workspace: str) -> str:
    """给模型看的可行动拒绝说明。"""
    lines = [f"[越界拦截] 命令试图离开工作区（{workspace}）："]
    lines += [f"  - {r['reason']}" for r in risks]
    lines.append(
        "请把操作限制在工作区内（用相对路径，别引用工作区外的绝对路径，"
        "也不要使用变量展开/命令替换/`sh -c` 这类无法静态判定的写法）。"
        "确需越界时：让管理员把该工位的信任级调成 elevated（会留审批痕迹），"
        "或改用 server_exec（仅管理员可用）。")
    return "\n".join(lines)
