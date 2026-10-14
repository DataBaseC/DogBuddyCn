# DogBuddyCn — shell 越界守卫 (Batch 7 · T7.4)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""把 shell 命令锁在工作区内（Batch 7 · T7.4，"必做"）。

**为什么需要**：文件工具（`read_file`/`write_file`/…）都用 `_resolve_in_workspace`
锁死了边界，但 `shell` 是整条命令交给 `/bin/sh` 执行——一句 `cd /` 或
`echo x > /etc/passwd` 就绕过了全部文件边界。这是原设计明确的"逃逸缝"。

**检测范围**（刻意保守，只抓能明确判定的两类越界，避免误杀正常命令）：

1. `cd` 到工作区之外（含 `cd ..` 一路爬出去、`cd /abs/path`、`cd ~`）
2. **写出**工作区之外：重定向目标（`>` `>>` `2>` `&>`）、以及
   `rm/mv/cp/mkdir/touch/chmod/chown/ln/tee/truncate/install/dd of=` 的绝对路径参数

**不管**：工作区外的**读**（`cat /etc/os-release`、`uname`、`df -h`…）。
那些由既有的 PermissionGate（模式矩阵 + 危险命令黑名单）负责；一刀切禁读会
把正常运维命令全废掉。本模块只负责"边界"，不负责"危险"。

**放行方式**：工位信任级为 `elevated` 时放行，但**必须留痕**（审计
`shell:boundary`）。这与 T7.4 的"越界访问必须走 ELEVATED 信任级 + 审批留痕才放行"一致。

注意：这不是内核级沙箱。`python -c "open('/etc/x','w')"` 这类间接写入检测不到
——本模块的目标是关掉"顺手就越界"的缝，而不是对抗刻意绕行。
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

# 有"写"语义、且参数指到工作区外就要拦的命令
_WRITE_COMMANDS = frozenset({
    "rm", "rmdir", "mv", "cp", "mkdir", "touch", "chmod", "chown", "chgrp",
    "ln", "tee", "truncate", "install", "mkfifo", "mknod", "dd", "shred",
})

# 命令分段：&& || ; | 换行（够用即可；不追求完整 shell 语法树）
_SPLIT_RE = re.compile(r"&&|\|\||[;|\n]")
# 重定向：> / >> / 2> / &> / 1> 后面跟的目标
_REDIRECT_RE = re.compile(r"(?:\d?|&)>>?\s*([^\s;|&<>]+)")


def _looks_absolute(p: str) -> bool:
    """跨平台判断"这是不是一个绝对/根路径"。

    不能用 `Path.is_absolute()`：Windows 上 `/etc/passwd` 返回 False（它有 root 没盘符），
    而本项目要在 Linux 跑、在 Windows 开发测试——判定必须两边一致。
    """
    if not p:
        return False
    if p.startswith(("/", "~", "\\")):
        return True
    return bool(re.match(r"^[A-Za-z]:[\\/]", p))


def _norm(p: str) -> str:
    """把路径规范化成可比较的字符串（不要求存在）。"""
    return str(Path(p).expanduser())


def _is_within(path: str, root: str) -> bool:
    p = str(path)
    if p.startswith("~"):
        p = str(Path(p).expanduser())
    try:
        r = Path(root).resolve()
        t = Path(p)
        t = t.resolve() if _looks_absolute(p) else (r / t).resolve()
    except (OSError, RuntimeError, ValueError):
        return True  # 解析不了就别误报
    return t == r or r in t.parents


def _tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:  # 引号不闭合等 → 退化为按空白切
        return segment.split()


def analyze_command(command: str, workspace: str) -> list[dict[str, Any]]:
    """返回越界风险列表；空列表 = 命令留在工作区内。"""
    risks: list[dict[str, Any]] = []
    if not command or not str(command).strip():
        return risks
    root = str(workspace or ".")

    for segment in _SPLIT_RE.split(str(command)):
        segment = segment.strip()
        if not segment:
            continue
        toks = _tokens(segment)
        if not toks:
            continue
        head = toks[0]
        is_write_cmd = head in _WRITE_COMMANDS

        # ---- 1) cd 越界
        if head == "cd":
            # 无参数 = cd $HOME；也是越界
            target = toks[1] if len(toks) > 1 and not toks[1].startswith("-") else "~"
            if not _is_within(target, root):
                risks.append({"kind": "cd", "target": target,
                              "reason": f"cd 到工作区之外：{target}"})

        # ---- 2) 重定向写出界
        for m in _REDIRECT_RE.finditer(segment):
            target = m.group(1)
            if target in ("/dev/null", "&1", "&2") or target.startswith("&"):
                continue
            if _looks_absolute(target) and not _is_within(target, root):
                risks.append({"kind": "write", "target": target,
                              "reason": f"重定向写到工作区之外：{target}"})

        # ---- 3) 写命令的绝对路径参数越界
        if is_write_cmd:
            for tok in toks[1:]:
                if tok.startswith("-"):
                    continue
                candidate = tok.split("=", 1)[1] if tok.startswith("of=") else tok
                if not candidate:
                    continue
                if _looks_absolute(candidate) and not _is_within(candidate, root):
                    risks.append({"kind": "write", "target": candidate,
                                  "reason": f"{head} 写工作区之外：{candidate}"})

        # ---- 4) 写命令用相对路径 + .. 爬出工作区
        #     只管写命令：读越界（cat ../x）交给 PermissionGate，边界守卫不越界管读
        if is_write_cmd:
            for tok in toks[1:]:
                if tok.startswith("-") or _looks_absolute(tok) or ".." not in tok:
                    continue
                if not _is_within(tok, root):
                    risks.append({"kind": "traverse", "target": tok,
                                  "reason": f"相对路径越过工作区：{tok}"})

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
        "请把操作限制在工作区内（用相对路径），或先用工作区内的临时目录；"
        "确需越界时，请让管理员把该工位的信任级调成 elevated（会留审批痕迹）。")
    return "\n".join(lines)
