# DogBuddyCn — Batch 6 · A1 错误分类体系
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""`dogbuddy/errors.py` 的分类与处置必须稳定：它是 C1-C5 的共同地基。"""
from __future__ import annotations

import socket
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


def test_dns_解析失败按errno分瞬态与永久():
    """2026-10-22 生产：`[Errno -3] Temporary failure in name resolution` 被判 PERMANENT，
    整条流水线 1.2 秒报废（丢了 3 轮）。DNS 抖动用的是**负数** errno，不在系统 errno 集合里。

    EAI_AGAIN(-3) 是"临时失败"，几十秒内会自愈 → 必须走 C1 瞬态续跑；
    EAI_NONAME(-2) 是"域名不存在"，重试没用 → 保持永久。
    """
    transient = socket.gaierror(-3, "Temporary failure in name resolution")
    assert transient.errno == -3
    assert errors.classify(transient) is ErrorClass.TRANSIENT
    assert errors.classify(transient) is not ErrorClass.PERMANENT
    assert errors.disposition_for(errors.classify(transient)) is Disposition.RETRY

    # provider 内部重试耗尽后包了一层，仍要能剥到根因判瞬态
    assert errors.classify(RetryExhausted(transient, 3)) is ErrorClass.TRANSIENT

    # 域名真的不存在：永久
    assert errors.classify(socket.gaierror(-2, "Name or service not known")) is ErrorClass.PERMANENT


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


# ---------------------------------------------------------------- 2026-10-14 线上回归
def test_LLM层的_TypeError_不再被归成工具参数问题():
    """线上实测：框架自己抛的 TypeError 被判成 tool_failure，然后对模型说
    "参数或调用方式有问题，请按报错修正后重新调用" —— 把 harness bug 推给模型，
    模型无从修正，只会不停换参数重试（在限流账号上白烧额度）。

    真实案例：`object async_generator can't be used in 'await' expression`
    （`openai_compat.stream_chat` 把异步生成器当协程 await）。
    """
    from dogbuddy.errors import Disposition, ErrorClass, action_hint, classify, disposition_for

    exc = TypeError("object async_generator can't be used in 'await' expression")

    # 工具路径：保持原行为（模型参数问题 → 回传自改）
    assert classify(exc, assume_tool_args=True) is ErrorClass.TOOL_FAILURE
    assert "参数" in action_hint(exc, assume_tool_args=True)

    # LLM 层：框架错误，不是模型的参数问题
    assert classify(exc, assume_tool_args=False) is ErrorClass.PERMANENT
    hint = action_hint(exc, assume_tool_args=False)
    assert "参数" not in hint, f"不该让模型去改参数：{hint}"
    assert disposition_for(ErrorClass.PERMANENT) is Disposition.STOP

    # 默认值必须保持 True（不改变既有调用点的行为）
    assert classify(exc) is ErrorClass.TOOL_FAILURE


def test_LLM调用失败的消息不甩锅给模型():
    """AgentLoop 的 LLM 失败路径必须用 assume_tool_args=False。"""
    import inspect

    from dogbuddy.agents.core import loop as loop_mod
    src = inspect.getsource(loop_mod.AgentLoop)
    assert "errors.classify(e, assume_tool_args=False)" in src, \
        "LLM 调用失败路径必须按『非工具参数』分类"
    assert "action_hint(e, assume_tool_args=False)" in src


def test_执行失败不会被记成_ok():
    """`AgentResult` 没有 `ok` 属性 → 旧代码 `getattr(result, "ok", True)` 恒为 True，
    每次执行都记成 ok。运行历史里的"假成功"就是这么来的。"""
    from types import SimpleNamespace

    from dogbuddy.services.window_manager import _run_status

    # 旧写法在这三种情况下都会给出 "ok"
    for stop in ("error", "done", "max_iterations", "rate_limited"):
        got = _run_status(SimpleNamespace(stop_reason=stop))
        if stop == "error":
            assert got == "failed", "LLM 调用失败必须记成 failed，不能是 ok"
        elif stop == "rate_limited":
            assert got == "rate_limited", "限速等待不是失败，但也不能记成 ok"
        else:
            assert got == "ok"

    # 没有 stop_reason 的旧式结果对象：按 ok 属性判，默认 False（不是 True）
    assert _run_status(SimpleNamespace(ok=True)) == "ok"
    assert _run_status(SimpleNamespace(ok=False)) == "failed"
    assert _run_status(SimpleNamespace()) == "failed", "缺 ok 属性时应按失败处理，不能默认成功"
