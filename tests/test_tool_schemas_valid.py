# DogBuddyCn — 每个工具的函数定义都必须是**合法 JSON Schema**（0.10.18）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 实测（切到 DeepSeek 后）：

    [LLM error/permanent] permanent: RuntimeError: LLM error 400:
    {"error":{"message":"Invalid schema for function 'approve_decision':
     true is not of type \\"array\\"" ...}}

不是"DeepSeek 预设不对"，是我们**发出去的函数定义非法**：`_p()` 把
`required` 当布尔塞进属性里（`"required": true`），而 JSON Schema 里 `required`
只能是**字符串数组**。SiliconFlow/Sensenova 不校验，DeepSeek 严格校验整轮 → 400。

这条测试把**所有注册表**里的工具都过一遍校验器，防止以后再有人写出这种形状。
校验器按 DeepSeek/OpenAI 的严格口径手写（不引入新依赖）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.management.tools import (  # noqa: E402
    AdminServices, build_admin_tools, build_management_tools, build_supervisor_tools)
from dogbuddy.agents.supervisor.tools import build_supervisor_registry  # noqa: E402
from dogbuddy.agents.worker_programmer.tools import build_worker_registry  # noqa: E402
from dogbuddy.tools.registry import build_registry  # noqa: E402

from dogbuddy.tools.schema_check import schema_problems  # noqa: E402

_JSON_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}


def schema_errors(params: dict, path: str = "parameters") -> list[str]:
    """测试里直接用仓库的校验器（和运行时注册时用的是同一份实现）。"""
    return schema_problems(params, path)


def _all_tools() -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    svc = AdminServices()
    for t in build_admin_tools(svc):
        out.append(("admin/" + t.name, t))
    for t in build_management_tools(svc, scopes=None):
        out.append(("management/" + t.name, t))
    for t in build_supervisor_tools(svc, scopes=None):
        out.append(("supervisor/" + t.name, t))
    def _from_registry(prefix: str, registry) -> None:
        for name in registry.names():
            tool = registry.get(name) if hasattr(registry, "get") else None
            if tool is not None:
                out.append((prefix + name, tool))

    _from_registry("worker/", build_worker_registry("worker_programmer"))
    try:
        _from_registry("supervisor-registry/", build_supervisor_registry(svc))
    except Exception:  # noqa: BLE001 — 单个注册表构造失败不该掩盖其它问题
        pass
    try:
        _from_registry("generic/", build_registry())
    except Exception:  # noqa: BLE001
        pass
    # 去重（按名字）
    seen: dict[str, object] = {}
    for name, tool in out:
        seen.setdefault(name, tool)
    return sorted(seen.items())


def test_every_tool_schema_is_valid_json_schema():
    problems: list[str] = []
    tools = _all_tools()
    assert len(tools) >= 30, f"只收集到 {len(tools)} 个工具，注册表接入可能变了"
    for name, tool in tools:
        for err in schema_errors(getattr(tool, "parameters", {}), "parameters"):
            problems.append(f"{name}: {err}")
    assert not problems, "发出去的函数定义非法，服务商会整轮 400：\n" + "\n".join(problems[:20])


def test_no_property_carries_a_boolean_required():
    """钉死这次的具体形状（DeepSeek: true is not of type "array"）。"""
    offenders = []
    for name, tool in _all_tools():
        for key, sub in (getattr(tool, "parameters", {}) or {}).get("properties", {}).items():
            if isinstance(sub, dict) and ("required" in sub or "_required" in sub):
                offenders.append(f"{name}.{key}")
    assert not offenders, f"属性里还有布尔 required：{offenders[:10]}"


def test_approve_decision_schema_matches_the_reported_failure():
    tools = {t.name: t for t in build_admin_tools(AdminServices())}
    params = tools["approve_decision"].parameters
    assert params["required"] == ["decision_id", "approve"]
    assert "required" not in params["properties"]["decision_id"]
    assert params["properties"]["approve"]["type"] == "boolean"


def test_validator_catches_the_original_bug():
    """校验器本身要能复现原故障（避免测试变成永远为真的摆设）。"""
    broken = {"type": "object",
              "properties": {"decision_id": {"type": "integer", "required": True}},
              "required": ["decision_id"]}
    errs = schema_errors(broken)
    assert any("属性里出现了 required" in e for e in errs)
    assert not schema_errors({"type": "object", "properties": {}, "required": []})
