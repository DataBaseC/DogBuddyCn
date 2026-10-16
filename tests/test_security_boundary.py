# DogBuddyCn — 安全边界加固回归（审查报告 P0-1 / P0-2 附带）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""锁住两件事：

1. **`server_exec` 是系统级能力**：它在面板**宿主机**上执行命令，不受工作区边界
   约束。初版既没有 `shell_guard` 也没有 `required_scope`，于是普通员工工位
   （scope=none、trust 未声明、无需审批）可以直接读 `/etc/shadow`、`~/.ssh/id_rsa`、
   面板自己的 token，写 `/etc/cron.d/*`。现在声明 `required_scope="system"`，
   由 L3 的 `check_scope` fail-closed 拒绝。

2. **作用域裁决必须在注册表全链路上生效**。审查报告第六节证明：仓库里所有
   `required_scope` 断言都自己把实参传给 `gate.check`，**没有一条走
   `ToolRegistry.execute()`** → 把 `registry.py` 里的 `required_scope` 转发删掉，
   375 条测试全绿，而员工能把整篇项目记忆读出来。下面的
   `test_所有声明作用域的工具_在员工作用域下都被拒` 就是补这个缺口的。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.permissions import PermissionGate  # noqa: E402
from dogbuddy.agents.core.scope import NONE, Scope  # noqa: E402
from dogbuddy.agents.worker_programmer.tools import build_worker_registry  # noqa: E402
from dogbuddy.tools.registry import ToolContext  # noqa: E402
from dogbuddy.tools.server_exec import ServerExecTool  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakePanel:
    """记录"有没有真的把命令送到宿主机"——被拒时必须是 0 次。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def exec(self, command: str, timeout: int) -> dict:
        self.calls.append((command, timeout))
        return {"stdout": "REMOTE OK", "stderr": "", "returncode": 0}


def _ctx(tmp_path, scope=None, panel=None) -> ToolContext:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(window_id="w1", workspace=str(ws), mode="BUILD",
                       actor="worker", panel=panel, scope=scope,
                       checkpoint_root=str(tmp_path / "ckpt"), extra={})


EVE = {"command": "cat ~/.ssh/id_rsa"}


# ------------------------------------------------------------------ 声明层
def test_server_exec_声明了_system_作用域():
    assert getattr(ServerExecTool, "required_scope", "") == "system", (
        "server_exec 在宿主机上执行命令，必须声明 system 作用域")


# ------------------------------------------------------ 注册表全链路（关键）
def test_员工工位用不了_server_exec_且命令没送到宿主机(tmp_path):
    panel = _FakePanel()
    reg = build_worker_registry("worker_programmer", gate=PermissionGate())
    ctx = _ctx(tmp_path, scope=NONE, panel=panel)

    res = _run(reg.execute("server_exec", EVE, ctx))

    assert res.ok is False, "员工不该能用 server_exec"
    assert res.data.get("policy_denied") is True
    assert "system" in res.output
    assert panel.calls == [], "被拒的命令绝不能到达宿主机"


def test_主管也用不了_server_exec(tmp_path):
    """主管是 project 作用域，宿主机运维是 system 级——同样拒绝。"""
    panel = _FakePanel()
    sup = Scope(level="project", project_ids=frozenset({"p1"}), actor="sup1")
    reg = build_worker_registry("worker_programmer", gate=PermissionGate())
    res = _run(reg.execute("server_exec", EVE, _ctx(tmp_path, scope=sup, panel=panel)))
    assert res.ok is False and res.data.get("policy_denied") is True
    assert panel.calls == []


def test_未声明作用域用不了_server_exec(tmp_path):
    """fail-closed：ctx.scope 缺失时也必须拒绝，不能默认放行。"""
    panel = _FakePanel()
    reg = build_worker_registry("worker_programmer", gate=PermissionGate())
    res = _run(reg.execute("server_exec", EVE, _ctx(tmp_path, scope=None, panel=panel)))
    assert res.ok is False and res.data.get("policy_denied") is True
    assert panel.calls == []


def test_管理员作用域可以用_server_exec(tmp_path):
    """system 作用域（管理员）应放行——能力不能被一刀切砍掉。"""
    panel = _FakePanel()
    from dogbuddy.agents.core.scope import SYSTEM
    reg = build_worker_registry("admin", gate=PermissionGate())
    res = _run(reg.execute("server_exec", {"command": "uptime"}, _ctx(tmp_path, scope=SYSTEM, panel=panel)))
    assert res.ok, f"管理员应能用 server_exec，实际: {res.output}"
    assert panel.calls == [("uptime", 60)]


# ------------------------------------------------- 不变量：作用域转发不能断
def test_所有声明作用域的工具_在员工作用域下都被拒(tmp_path):
    """★ 这条测试就是审查报告 v1 变异缺的那条。

    它走 `ToolRegistry.execute()` 全链路，因此一旦 `registry.py` 里
    `required_scope=required` 的转发被删掉，作用域裁决整体失效 → 这里必红。
    """
    reg = build_worker_registry("worker_programmer", gate=PermissionGate())
    scoped = [n for n in reg.names() if getattr(reg.get(n), "required_scope", "")]
    assert scoped, "注册表里应当至少有一个声明了作用域的工具（如 project_memory_read）"

    ctx = _ctx(tmp_path, scope=NONE)
    for name in scoped:
        res = _run(reg.execute(name, {}, ctx))
        assert res.ok is False, f"{name} 声明了作用域，员工作用域下不得放行"
        assert res.data.get("policy_denied") is True, (
            f"{name} 被拒的原因不是作用域裁决——说明 required_scope 的转发断了"
            f"（output={res.output!r}）")


# ------------------------------------------------------- 面板路径保护（P0-1）
def test_面板路径在_shell_权限下也拒绝(tmp_path):
    """初版 PANEL_PATH_PATTERN 只在 permission=="write" 时生效，
    于是 `echo x > /etc/dogbuddy-panel/config.json` 以 shell 权限放行。"""
    gate = PermissionGate()
    ctx = _ctx(tmp_path, scope=NONE)
    for cmd in ["echo x > /etc/dogbuddy-panel/config.json",
                "cat /etc/dogbuddy-panel/config.json",
                "rm -rf /opt/aegis-server-panel/data"]:
        d = gate.check("shell", "shell", {"command": cmd}, ctx)
        assert d.allowed is False, f"面板路径必须拒绝: {cmd}"
        assert "Panel" in d.reason or "panel" in d.reason


def test_普通命令不受面板规则误伤(tmp_path):
    gate = PermissionGate()
    ctx = _ctx(tmp_path, scope=NONE)
    for cmd in ["ls -la", "cat README.md", "git status"]:
        assert gate.check("shell", "shell", {"command": cmd}, ctx).allowed is True


def test_管理员才拿得到_server_exec_员工工具箱里不再白给():
    """v0.10.29（owner 实测）：`server_exec` 声明 system 作用域 —— 唯一合法调用者是
    管理员。但它以前**只在员工工具箱里**：

    * 员工挂着它 → 每次调用被作用域裁决 fail-closed 拒绝（今天实测：管理员把"整理
      线上工作区"派给 worker-1 → 先被拒 server_exec、再被边界守卫拦绝对路径，
      任务根本做不到），还白烧 schema token、把模型引向"被拒→绕行"的死循环；
    * 管理员**没有**它 → 做不了宿主机级操作，只能把活派给做不了的员工
      （它自己在对话里如实报告"工具未挂载"）。
    """
    from dogbuddy.agents.core.roles import (ADMIN_TOOLS, SUPERVISOR_TOOLS,
                                            WORKER_PROGRAMMER_TOOLS)

    assert "server_exec" in ADMIN_TOOLS, "只有管理员能过 system 作用域，必须给它"
    assert "server_exec" not in WORKER_PROGRAMMER_TOOLS, "员工挂着必然是死工具"
    assert "server_exec" not in SUPERVISOR_TOOLS, "主管是 project 作用域，同样过不了"
