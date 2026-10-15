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

## v0.10.20 误拦收敛（圆桌测试实测：9 次 shell 4 次被拦，每次 5 行长文，
把 qa-env 逼到 `max iterations reached` 硬中断）

三条**误拦**已修（owner 交接书 §2.3 点名）：

1. **引号内的字符串不是路径**。`grep -oE '"/api/[a-z]+' f`、`curl "http://host$c"`
   里的 `/api`、URL 是**字符串字面量**（正则图案/文本），不是路径引用。现在按 shell
   词法掩码引号内容：任一引号 region 脱引号后**像纯路径**才保留参与判定
   （`rm -rf '/var/log'`、`cat "/etc/passwd"` 依然拦）；URL（含 `://`）、正则/glob
   图案（含元字符）、普通文本一律掩码；双引号 region 含 `$`/反引号时不掩码
   （运行时会真展开，交给动态判定）。重定向目标与 `cd` 目标照旧在**原文**上判定——
   `echo x >"/etc/passwd"`、`cd "/etc"` 依然拦得住。
   ⚠️ 代价（知情决策）：带引号的 URL/正则/文本不再触发越界判定；不加引号的路径
   写法仍然拦。本守卫是静态检查器而非沙箱（见下），这条取舍换的是不再把工人的
   正常命令当越界烧 token。
2. **`for`/`while`/`until`/`if` 构造本身不是越界信号**。`for c in /api /docs` 的
   词表、`if [ -f /etc/x ]` 的条件都不做路径扫描；**循环/条件体里的命令逐段照常判**
   （`do cp $f /etc/` 仍被拦）。循环体内的不可判定构造**放行 + 记录**（audit），
   不再拒绝——拒绝只会把工人逼去写脚本文件绕行（圆桌里正是这么逃逸的）。
3. **`python -c` 等他语言内联代码从拒绝降为放行+记录**。本守卫分析的是 shell 文本；
   `python -c` 的内容从来不可分析，而**写进 .py 文件执行本来就放行**——拦
   `python -c` 不改变真实攻击面（攻击者换文件即可），只制造噪音与绕行动机。
   真正绕不过 shell 分析的构造（`sh -c`/`eval`/`source`/`xargs`/`find -exec`/
   `sudo`/`$( )`/反引号/`${ }`/`$VAR`）仍一律拒绝。

## 拦截消息（v0.10.20：单行 + 错误码）

- `E_BOUNDARY_OUTSIDE_PATH` — 引用/写入了工作区外的路径
- `E_BOUNDARY_UNEVALUABLE` — 含无法静态判定的构造

同一轮对话里同一错误码只给一次完整说明，之后只回一行带码的短消息
（ctx.extra["boundary_codes_shown"] 记录）——5 行长文每次被拦都喂回上下文，
既烧 token 又被压缩摘要继承，正是圆桌里 qa-ops 撞上限的推手之一。

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

# 静态分析不动的构造 —— 出现即要求 elevated（他语言内联代码除外，见 _NOTED）。
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
    (r"\bawk\b", "awk（可写文件）"),
    (r"\bsed\b[^&|\n]*\s-i\b", "sed -i 就地改写"),
]
_DYNAMIC_COMPILED = [(re.compile(p), why) for p, why in _DYNAMIC_PATTERNS]

# v0.10.20：**他语言**内联代码——内容从来不可静态分析，且写进文件执行本就放行，
# 拦它只制造噪音与绕行动机（圆桌实测把 qa-env 逼去写 .py 脚本文件）。
# 处理：放行 + 审计记录（shell_ops 写 shell:dynamic-allowed），不拒绝。
_NOTED_PATTERNS: list[tuple[str, str]] = [
    (r"\bpython[0-9.]*\s+-c\b", "python -c 内联代码"),
    (r"\bperl\s+-e\b", "perl -e 内联代码"),
    (r"\bnode\s+-e\b|\bnode\s+--eval\b", "node -e 内联代码"),
    (r"\bruby\s+-e\b", "ruby -e 内联代码"),
    (r"\bphp\s+-r\b", "php -r 内联代码"),
]
_NOTED_COMPILED = [(re.compile(p), why) for p, why in _NOTED_PATTERNS]

# 循环/条件构造：词表与条件段不是路径引用；其命令体逐段照常判定。
_LOOP_HEADS = frozenset({"for", "while", "until", "if"})
_LOOP_BODY_HEADS = frozenset({"do", "then"})
_LOOP_END_HEADS = frozenset({"done", "fi", "else", "elif", "esac"})

# 引号内文本"像纯路径"才参与越界判定；含正则/glob 元字符或 `://` 的一律是字符串。
_PATH_META_RE = re.compile(r"[\[\]()*+?{}|^$`\\]")

# 有意放行的无害绝对目标（不是"读文件"，是丢弃/标准流）
_BENIGN_ABS = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/zero")

#: 风险类型 → 错误码（v0.10.20 单行拦截消息用）
ERROR_CODES = {
    "path": "E_BOUNDARY_OUTSIDE_PATH",
    "cd": "E_BOUNDARY_OUTSIDE_PATH",
    "write": "E_BOUNDARY_OUTSIDE_PATH",
    "dynamic": "E_BOUNDARY_UNEVALUABLE",
}

_HINTS = {
    "E_BOUNDARY_OUTSIDE_PATH":
        "只用工作区内相对路径；确需越界请管理员把该工位信任级调成 elevated"
        "（会留审批痕迹），或改用 server_exec（仅管理员）",
    "E_BOUNDARY_UNEVALUABLE":
        "改成可静态判定的写法（避免变量展开/命令替换/sh -c）；确需越界请管理员把该工位"
        "信任级调成 elevated，或改用 server_exec（仅管理员）",
}


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


def _quoted_looks_like_path(s: str) -> bool:
    """引号内的文本是否"像一个纯文件路径"（而不是字符串/正则/URL）。

    - `://` → URL（`http://host/api` 不是路径）
    - 含正则/glob 元字符 → 图案（`/api/[a-z]+`、`{a,b}` 不是路径）
    - 不以 `/` `~` `./` `../` 开头 → 普通文本（`== ws ==`）
    """
    t = str(s or "").strip()
    if not t or "://" in t:
        return False
    if _PATH_META_RE.search(t):
        return False
    return t.startswith(("/", "~", "./", "../"))


def _mask_quoted(segment: str) -> str:
    """把"引号内的非路径文本"替换为等长占位符，保留其余结构。

    词法依据：单引号内全是字面量；双引号内除 `\\` `\\"` `$` 反引号 外也是字面量。
    - 双引号 region 含 `$`/反引号 → 不掩码（真·动态，交给动态判定）
    - 任一引号 region 脱引号后像纯路径 → 不掩码（`rm -rf '/var/log'`、
      `cat "/etc/passwd"` 仍要拦——带引号的路径参数依然是路径引用）
    - 其余（URL、正则图案、普通文本）→ 掩码：`grep -oE '"/api/[a-z]+'`、
      `curl "http://host$c"`、`echo "== ws =="` 里的内容不是路径
    """
    out = list(segment)
    i, n = 0, len(segment)
    while i < n:
        c = segment[i]
        if c not in ("'", '"'):
            i += 1
            continue
        quote = c
        j = i + 1
        while j < n:
            if quote == '"' and segment[j] == "\\":
                j += 2
                continue
            if segment[j] == quote:
                break
            j += 1
        inner = segment[i + 1:min(j, n)]
        keep = False
        if quote == '"' and ("$" in inner or "`" in inner):
            keep = True  # 动态展开，不能掩
        elif _quoted_looks_like_path(inner):
            keep = True  # 像路径，保留判定
        if not keep:
            for k in range(i + 1, min(j, n)):
                out[k] = "\x00"
        i = j + 1
    return "".join(out)


def _dynamic_hits(text: str, compiled: list[tuple[Any, str]]) -> list[dict[str, Any]]:
    return [{"kind": "dynamic", "target": why,
             "reason": f"含无法静态判定的构造（{why}），越界与否无法确定"}
            for rx, why in compiled if rx.search(text)]


def analyze_command(command: str, workspace: str) -> list[dict[str, Any]]:
    """返回越界/不可判定风险列表；空列表 = 命令确实留在工作区内。"""
    risks: list[dict[str, Any]] = []
    if not command or not str(command).strip():
        return risks
    raw = str(command)
    root = str(workspace or ".")

    in_loop_body = False
    for segment in _SPLIT_RE.split(raw):
        segment = segment.strip()
        if not segment:
            continue
        toks = _tokens(segment)
        if not toks:
            continue
        head = toks[0]

        # ---- 0) 循环/条件构造本身：词表（in 后面的单词）与测试条件不是路径引用。
        #      命令体由后续 `do`/`then` 段逐段判定，这里只查动态构造
        #      （`for c in $HOME/../etc` 仍是动态拼路径，拒）。
        if head in _LOOP_HEADS:
            in_loop_body = False
            risks += _dynamic_hits(_mask_quoted(segment), _DYNAMIC_COMPILED)
            continue
        if head in _LOOP_BODY_HEADS:
            in_loop_body = True
        elif head in _LOOP_END_HEADS:
            in_loop_body = False
            continue

        # 动态构造在**掩码后**的文本上查：单引号里的 `$VAR` 是字面量，不是动态
        # （双引号里的不掩码，仍会被查出来——它运行时会真展开）。
        # 循环/条件体里的不可判定构造按 owner 交接书"放行+记录"处理，不进 risks。
        masked_seg = _mask_quoted(segment)
        if not in_loop_body:
            risks += _dynamic_hits(masked_seg, _DYNAMIC_COMPILED)

        # ---- 1) cd / pushd 越界（无参数 = cd $HOME，也是越界）。
        #      用**原文** token：`cd "/etc"` 这种带引号的目标仍要拦。
        if head in ("cd", "pushd"):
            target = toks[1] if len(toks) > 1 and not toks[1].startswith("-") else "~"
            if not _is_within(target, root):
                risks.append({"kind": "cd", "target": target,
                              "reason": f"{head} 到工作区之外：{target}"})

        # ---- 2) 重定向写出界（先脱引号再判，`>"/etc/x"` 一样拦）
        for m in _REDIRECT_RE.finditer(segment):
            target = _strip_quotes(m.group(1))
            if not target or target.startswith("&") or _is_benign_abs(target):
                continue
            if not _is_within(target, root):
                risks.append({"kind": "write", "target": target,
                              "reason": f"重定向写到工作区之外：{target}"})

        # ---- 3) 通用路径扫描：任何 token 只要指向工作区外，读或写都拦。
        #      在**掩码引号**后的文本上做——引号里的正则/URL/文本不是路径。
        for tok in _tokens(masked_seg)[1:]:
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


def dynamic_notes(command: str) -> list[dict[str, Any]]:
    """v0.10.20：**放行但记录**的构造（他语言内联代码 + 循环体内的不可判定构造）。

    返回审计用的说明列表；调用方（shell_ops）记 `shell:dynamic-allowed`，
    不进 risks——不拒绝，只留痕。
    """
    if not command or not str(command).strip():
        return []
    raw = str(command)
    notes = _dynamic_hits(_mask_quoted(raw), _NOTED_COMPILED)
    # 循环/条件体内的不可判定构造：命令体逐段判定过路径后仍不可判定的，放行+记录。
    in_loop_body = False
    for segment in _SPLIT_RE.split(raw):
        segment = segment.strip()
        if not segment:
            continue
        toks = _tokens(segment)
        head = toks[0] if toks else ""
        if head in _LOOP_HEADS:
            in_loop_body = False
            continue
        if head in _LOOP_BODY_HEADS:
            in_loop_body = True
        elif head in _LOOP_END_HEADS:
            in_loop_body = False
            continue
        if in_loop_body:
            notes += _dynamic_hits(_mask_quoted(segment), _DYNAMIC_COMPILED)
    return notes


def is_elevated(ctx: Any) -> bool:
    """工位信任级是否为 elevated（T7.4 的放行条件）。"""
    extra = getattr(ctx, "extra", None) or {}
    return str(extra.get("trust", "")).strip().lower() == "elevated"


def refusal_message(risks: list[dict[str, Any]], workspace: str,
                    first_time: bool = True) -> str:
    """给模型看的可行动拒绝说明——**单行 + 错误码**（v0.10.20）。

    5 行长文每次被拦都喂回上下文，既烧 token 又被压缩摘要继承；现在同一轮
    对话同一错误码只完整说明一次（shell_ops 用 ctx.extra 记账），之后只回
    一行带码短消息。
    """
    codes: list[str] = []
    for r in risks:
        code = ERROR_CODES.get(r.get("kind", ""), "E_BOUNDARY")
        if code not in codes:
            codes.append(code)
    targets = "；".join(sorted({str(r.get("target", "")) for r in risks})[:3])
    head = f"[越界拦截] {','.join(codes) or 'E_BOUNDARY'}"
    if not first_time:
        return f"{head}（该错误码已说明过，见前文）目标: {targets}"
    hints = " ".join(_HINTS[c] for c in codes if c in _HINTS)
    return f"{head} 命令试图离开工作区（{workspace}），目标: {targets}。{hints}"
