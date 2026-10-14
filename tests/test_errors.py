# DogBuddyCn — Batch 6 · A1 错误分类体系
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""`dogbuddy/errors.py` 的分类与处置必须稳定：它是 C1-C5 的共同地基。"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy import errors  # noqa: E402
from dogbuddy.errors import Disposition, ErrorClass  # noqa: E402
from dogbuddy.llm.retry import RetryExhausted, is_retryable  # noqa: E402


def _status_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://example.test/v1/chat/completions")
    resp = httpx.Response(code, request=req)
    return httpx.HTTPStatusError(f"HTTP {code}", request=req, response=resp)


def test_四类分类各自命中():
    assert errors.classify(httpx.ConnectTimeout("slow")) is ErrorClass.TRANSIENT
    assert errors.classify(_status_error(429)) is ErrorClass.TRANSIENT
    assert errors.classify(_status_error(503)) is ErrorClass.TRANSIENT
    assert errors.classify(_status_error(500)) is ErrorClass.TRANSIENT
    assert errors.classify(_status_error(401)) is ErrorClass.PERMANENT
    assert errors.classify(_status_error(400)) is ErrorClass.PERMANENT
    assert errors.classify(ValueError("bad args")) is ErrorClass.TOOL_FAILURE
    assert errors.classify(KeyError("path")) is ErrorClass.TOOL_FAILURE
    assert errors.classify(errors.PolicyDeniedError("denied")) is ErrorClass.POLICY_DENIED
    assert errors.classify(RuntimeError("???")) is ErrorClass.PERMANENT


def test_标准处置映射():
    d = errors.dispositions()
    assert d[ErrorClass.TRANSIENT] is Disposition.RETRY
    assert d[ErrorClass.PERMANENT] is Disposition.STOP
    assert d[ErrorClass.POLICY_DENIED] is Disposition.ASK
    assert d[ErrorClass.TOOL_FAILURE] is Disposition.FEEDBACK
    assert errors.disposition_for(ErrorClass.TOOL_FAILURE) is Disposition.FEEDBACK
    assert errors.ToolArgumentError("x").disposition is Disposition.FEEDBACK


def test_RetryExhausted_剥到根因再分类():
    """provider 内部重试耗尽后，loop 层要能看出根因是瞬态（C1 才可能续跑）。

    两层重试的边界：provider 的重试次数在 provider 里，loop 层续跑次数由
    `AgentLoop.transient_retries` 收紧（默认 3），所以不会无限重试。
    """
    wrapped = RetryExhausted(httpx.ConnectTimeout("boom"), 5)
    assert errors.unwrap(wrapped).__class__ is httpx.ConnectTimeout
    assert errors.classify(wrapped) is ErrorClass.TRANSIENT
    assert errors.describe(wrapped).startswith("transient:")
    assert "ConnectTimeout" in errors.describe(wrapped)

    # 根因是永久错误时，仍然判永久
    permanent = RetryExhausted(_status_error(401), 3)
    assert errors.classify(permanent) is ErrorClass.PERMANENT


def test_自定义异常自带分类():
    assert errors.TransientError("t").error_class is ErrorClass.TRANSIENT
    assert errors.TransientError("t").disposition is Disposition.RETRY
    assert errors.ToolFailureError("f").disposition is Disposition.FEEDBACK
    assert errors.PolicyDeniedError("p").disposition is Disposition.ASK
    assert errors.PermanentError("x").disposition is Disposition.STOP

    te = errors.ToolArgumentError("缺少 path", tool="read_file", schema={"required": ["path"]})
    assert te.error_class is ErrorClass.TOOL_FAILURE
    assert te.tool == "read_file" and te.schema["required"] == ["path"]
    assert errors.LoopGuardError("loop").error_class is ErrorClass.TOOL_FAILURE


def test_retry_module_委托到同一套判定():
    """'什么算可重试'只能有一个定义（A1 的核心目的）。"""
    assert is_retryable(httpx.ConnectTimeout("x")) is True
    assert is_retryable(_status_error(429)) is True
    assert is_retryable(_status_error(401)) is False
    assert is_retryable(ValueError("x")) is False
    assert errors.RETRYABLE_STATUS >= {429, 500, 502, 503, 504, 529}


def test_处置建议是人话且对症():
    assert "退避重试" in errors.action_hint(httpx.ConnectTimeout("x"))
    assert "重试无效" in errors.action_hint(errors.PolicyDeniedError("x"))
    assert "修正" in errors.action_hint(errors.ToolArgumentError("x"))
    assert "停止" in errors.action_hint(RuntimeError("x"))


def test_分类永不抛异常():
    """分类器在任何输入下都不能自己炸（它是兜底层）。"""
    class Weird(BaseException):
        def __str__(self):  # 故意让 __str__ 也炸
            raise RuntimeError("nope")

    for exc in (Weird(), BaseException(), Exception("x"), KeyboardInterrupt()):
        try:
            errors.classify(exc)
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"classify 不该抛: {e!r}")
