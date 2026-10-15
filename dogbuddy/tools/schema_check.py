# DogBuddyCn — tool schema sanity check (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""函数定义（JSON Schema）自检。

**为什么需要**：函数的 `parameters` 会原样发给服务商。DeepSeek 严格校验，形状不合法
就**整轮 400**，而且报错指向某个工具名，看起来像"模型配置不对"：

    Invalid schema for function 'approve_decision': true is not of type "array"

那次的原因是 `_p()` 把 `required` 当布尔塞进了属性里（`required` 在 JSON Schema 里
只能是字符串数组）。SiliconFlow/Sensenova 不校验，切到 DeepSeek 才炸。

这里只做**能确定判错**的检查，不追求完整 JSON Schema 校验（不引入新依赖）：
注册时发现问题 → 记 error 日志（不抛，避免起不来）；测试里当作硬失败。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("dogbuddy.tools.schema")

_JSON_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}


def schema_problems(params: Any, path: str = "parameters") -> list[str]:
    """递归检查一个 parameters schema，返回问题清单（空 = 通过）。"""
    errs: list[str] = []
    if not isinstance(params, dict):
        return [f"{path}: parameters 必须是对象，实际是 {type(params).__name__}"]
    if params.get("type") != "object":
        errs.append(f"{path}: 顶层 type 必须是 'object'，实际是 {params.get('type')!r}")
    props = params.get("properties")
    if props is None:
        errs.append(f"{path}: 缺少 properties")
        props = {}
    if not isinstance(props, dict):
        errs.append(f"{path}: properties 必须是对象")
        props = {}

    req = params.get("required")
    if req is not None:
        if not isinstance(req, list) or not all(isinstance(x, str) for x in req):
            errs.append(f"{path}: required 必须是字符串数组")
        else:
            unknown = [x for x in req if x not in props]
            if unknown:
                errs.append(f"{path}: required 里有未声明的参数 {unknown}")

    for key, sub in props.items():
        p = f"{path}.{key}"
        if not isinstance(sub, dict):
            errs.append(f"{p}: 属性定义必须是对象")
            continue
        if "required" in sub:
            errs.append(f"{p}: 属性里出现了 required（布尔）—— 非法 JSON Schema")
        if "_required" in sub:
            errs.append(f"{p}: 内部标记 _required 泄漏到了线上定义")
        st = sub.get("type")
        if st is None:
            errs.append(f"{p}: 缺少 type")
        elif isinstance(st, str):
            if st not in _JSON_TYPES:
                errs.append(f"{p}: 未知 type {st!r}")
        elif isinstance(st, list):
            bad = [x for x in st if x not in _JSON_TYPES]
            if bad:
                errs.append(f"{p}: type 列表里有未知项 {bad}")
        else:
            errs.append(f"{p}: type 必须是字符串或字符串数组")
        if "enum" in sub and not isinstance(sub["enum"], list):
            errs.append(f"{p}: enum 必须是数组")
        if st == "array" or (isinstance(st, list) and "array" in st):
            if not isinstance(sub.get("items"), dict):
                errs.append(f"{p}: array 必须给 items")
            else:
                errs += schema_problems(
                    {"type": "object", "properties": {"item": sub["items"]}}, p)
        if st == "object" or (isinstance(st, list) and "object" in st):
            if isinstance(sub.get("properties"), dict):
                errs += schema_problems(sub, p)
    return errs


def audit_tool_schema(name: str, params: Any) -> list[str]:
    """注册时调用：有问题记 error 日志（不抛），返回问题清单。"""
    problems = schema_problems(params, "parameters")
    if problems:
        logger.error("工具 %s 的函数定义不合法（服务商可能整轮 400）：%s",
                     name, "；".join(problems[:6]))
    return problems
