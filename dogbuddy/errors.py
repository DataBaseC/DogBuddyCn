# DogBuddyCn — 统一错误分类体系 (Batch 6 · A1)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""错误分类 + 标准处置。**所有"出错了怎么办"的判断都收敛到这里。**

设计（ROADMAP Batch 6 · A1）：

| 分类 | 含义 | 典型来源 | 标准处置 |
|---|---|---|---|
| `Transient` | 瞬态，等一会儿可能就好了 | 超时 / 连接错误 / 429 / 5xx | `RETRY`（仅幂等操作） |
| `Permanent` | 永久，重试无用 | 认证失败 / 4xx / 配置错误 | `STOP`（停下来，别烧 token） |
| `PolicyDenied` | 策略拒绝 | 权限门控 / 审批被拒 / 越界 | `ASK`（交给人决策，绝不重试） |
| `ToolFailure` | 工具失败 | 参数不对 / 工具内部报错 | `FEEDBACK`（把原因回传模型自改） |

调用方不该再各自写 `try/except Exception` 然后自己猜是"重试还是放弃"：

    cls = errors.classify(exc)
    if errors.disposition_for(cls) is errors.Disposition.RETRY: ...

`llm.retry.is_retryable()` 也改为委托这里，保证"什么算可重试"只有一个定义。
"""
from __future__ import annotations

import enum
import socket
from typing import Any

try:  # httpx 是运行依赖；这里做软导入，让 errors 在最小环境（工具/脚本）也能用
    import httpx  # type: ignore
except Exception:  # pragma: no cover - 仅防御
    httpx = None  # type: ignore[assignment]


# 需要重试的 HTTP 状态：408 请求超时 / 425 太早 / 429 限速 / 5xx / 522·524 Cloudflare
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 522, 524, 529})

# 认作瞬态的系统级 errno
_TRANSIENT_ERRNOS = frozenset({
    4,    # EINTR
    11,   # EAGAIN
    32,   # EPIPE
    54,   # ECONNRESET (BSD/mac)
    104,  # ECONNRESET (Linux)
    110,  # ETIMEDOUT
    111,  # ECONNREFUSED
})


class ErrorClass(str, enum.Enum):
    """错误分类（决定"怎么办"）。"""

    TRANSIENT = "transient"
    PERMANENT = "permanent"
    POLICY_DENIED = "policy_denied"
    TOOL_FAILURE = "tool_failure"


class Disposition(str, enum.Enum):
    """标准处置。"""

    RETRY = "retry"        # 退避后重试
    STOP = "stop"          # 立即停止，不要再试
    ASK = "ask"            # 交给人决策
    FEEDBACK = "feedback"  # 把原因回传模型，让它自己改


_DISPOSITION: dict[ErrorClass, Disposition] = {
    ErrorClass.TRANSIENT: Disposition.RETRY,
    ErrorClass.PERMANENT: Disposition.STOP,
    ErrorClass.POLICY_DENIED: Disposition.ASK,
    ErrorClass.TOOL_FAILURE: Disposition.FEEDBACK,
}


def disposition_for(cls: ErrorClass) -> Disposition:
    return _DISPOSITION.get(cls, Disposition.STOP)


def dispositions() -> dict[ErrorClass, Disposition]:
    return dict(_DISPOSITION)


# --------------------------------------------------------------------------- 异常
class DogBuddyError(Exception):
    """所有本项目的自定义异常基类；自带分类，不必调用方再判断。"""

    error_class: ErrorClass = ErrorClass.PERMANENT

    def __init__(self, message: str = "", *, cause: BaseException | None = None):
        super().__init__(message)
        self.cause = cause
        if cause is not None:
            self.__cause__ = cause

    @property
    def disposition(self) -> Disposition:
        return disposition_for(self.error_class)


class TransientError(DogBuddyError):
    error_class = ErrorClass.TRANSIENT


class PermanentError(DogBuddyError):
    error_class = ErrorClass.PERMANENT


class PolicyDeniedError(DogBuddyError):
    error_class = ErrorClass.POLICY_DENIED


class ToolFailureError(DogBuddyError):
    error_class = ErrorClass.TOOL_FAILURE


class ToolArgumentError(ToolFailureError):
    """模型传的工具参数不合法（缺必填 / 未知参数 / 不是对象）。

    单独成类是因为它必须走"回传模型自改"这条路，而不是当成工具崩溃。
    """

    def __init__(self, message: str = "", *, tool: str = "", schema: dict[str, Any] | None = None):
        super().__init__(message)
        self.tool = tool
        self.schema = schema or {}


class LoopGuardError(ToolFailureError):
    """同一调用反复失败被熔断（Batch 6 · C2）。"""


# --------------------------------------------------------------------------- classify
def unwrap(exc: BaseException) -> BaseException:
    """把 RetryExhausted 这类"包装异常"剥到真正的根因。

    避免 `errors` 反向 import `llm.retry` 造成循环依赖，这里按鸭子类型识别
    （`llm.retry.RetryExhausted` 有 `.last_error`）。
    """
    seen: set[int] = set()
    cur = exc
    while id(cur) not in seen:
        seen.add(id(cur))
        inner = getattr(cur, "last_error", None)
        if isinstance(inner, BaseException):
            cur = inner
            continue
        cause = getattr(cur, "__cause__", None)
        if isinstance(cause, BaseException) and isinstance(cur, DogBuddyError) is False:
            cur = cause
            continue
        break
    return cur


def classify(exc: BaseException) -> ErrorClass:
    """任何异常 → 四类之一。**永远不抛**（分类失败按 PERMANENT 处理）。

    先 `unwrap` 再分类：`RetryExhausted(ConnectTimeout)` 会按根因判为**瞬态**，
    于是 loop 层的 C1 续跑才有机会再试（provider 内部的重试次数与 loop 层的续跑
    是两层，loop 层的次数由 `AgentLoop.transient_retries` 收紧）。
    """
    if isinstance(exc, DogBuddyError):
        return exc.error_class

    root = unwrap(exc)
    if root is not exc and not isinstance(root, DogBuddyError):
        return classify(root)

    # 1) httpx 家族
    if httpx is not None:
        if isinstance(root, httpx.HTTPStatusError):
            code = getattr(root.response, "status_code", 0)
            if code in RETRYABLE_STATUS or code >= 500:
                return ErrorClass.TRANSIENT
            if code in (401, 403):
                return ErrorClass.PERMANENT
            return ErrorClass.PERMANENT  # 其余 4xx：请求本身有问题，重试无用
        if isinstance(root, (httpx.TimeoutException, httpx.TransportError)):
            return ErrorClass.TRANSIENT

    # 2) 标准库瞬态
    if isinstance(root, (TimeoutError, ConnectionError, ConnectionResetError,
                         ConnectionAbortedError, BrokenPipeError, socket.timeout)):
        return ErrorClass.TRANSIENT
    if isinstance(root, OSError) and root.errno in _TRANSIENT_ERRNOS:
        return ErrorClass.TRANSIENT

    # 3) 模型给的参数不合法 → 回传自改
    if isinstance(root, (KeyError, TypeError, ValueError, AttributeError, IndexError)):
        return ErrorClass.TOOL_FAILURE

    return ErrorClass.PERMANENT


def is_transient(exc: BaseException) -> bool:
    return classify(exc) is ErrorClass.TRANSIENT


def is_retryable(exc: BaseException) -> bool:
    """向后兼容名（`llm.retry` 用）：语义 = 分类为瞬态。"""
    return is_transient(exc)


def describe(exc: BaseException) -> str:
    """一行摘要，用于日志/审计/回传模型。"""
    cls = classify(exc)
    root = unwrap(exc)
    return f"{cls.value}: {type(root).__name__}: {root}"


def action_hint(exc: BaseException) -> str:
    """给模型/用户看的一句话处置建议（坏参数时最有价值）。"""
    cls = classify(exc)
    d = disposition_for(cls)
    if d is Disposition.RETRY:
        return "这是瞬态失败，会退避重试；不要重复调用同一路径。"
    if d is Disposition.ASK:
        return "这被策略拒绝，重试无效；需要人来决定或换一种合规做法。"
    if d is Disposition.FEEDBACK:
        return "参数或调用方式有问题，请按报错修正后重新调用（不要原样重试）。"
    return "属于永久错误，重试无用；请停止该路径并说明原因。"
