# DogBuddyCn — LLM retry policy (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""限速自适应退避（T8.7）+ 限速严重度分档（T8.8）。

规则沿革（用户需求 2026-10-12 / ROADMAP Batch 8 组三）：
  - 触发：HTTP 429 / 5xx / 超时 / 连接错误
  - **绝不换 key**：同一 key 原地等（换 key 只会把惩罚均摊到所有 key）
  - 等待本身不算任务失败，只有"重试真正用尽"才算一次失败

v0.7.0 升级（针对"限速非常慢"）：
  1. **优先读服务端 `Retry-After`** —— 不再瞎猜指数。服务端说等 47 秒就等 47 秒。
  2. **AIMD 自适应** —— 连续被限速则乘性放缓（间隔 ×2），连续成功则加性恢复
     （间隔 -= recovery_step）。这样既不会在轻度限速时无谓地等很久，也不会
     在重度限速时一路撞墙。
  3. **每 profile 记忆** —— 记住"上次退避多久才成功"，下次直接从那个量级起跳，
     避免每次都从 10 秒重新试错。
  4. **重度限速动态扩展重试预算** —— 原来 5 次 × 5 分钟就放弃；现在重/封禁档
     会自动追加预算，避免"明明等到就好了却判失败"。
  5. **严重度分档 + 联动**（T8.8）：
     轻（<60s）原地等 / 重（<30min）等待 + 通知放慢迭代 /
     封禁（≥30min）**抛 `RateLimitPaused` 暂停任务并通知**——不是 fail，
     调度器会标成 `rate_limited`（区别见 `cron/scheduler.py`）。
"""
from __future__ import annotations

import asyncio
import enum
import json
import logging
import os
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from ..errors import RETRYABLE_STATUS, TransientError, classify, is_retryable as _is_retryable  # noqa: F401
from .provider import LLMResponse  # noqa: F401  (re-export convenience)

logger = logging.getLogger("dogbuddy.llm.retry")

T = TypeVar("T")

#: 轻/重分界（秒）：低于此值原地等待即可
LIGHT_MAX = 60.0
#: 重/封禁分界（秒）：达到此值不再原地等，改为"暂停任务 + 通知"
HEAVY_MAX = 1800.0


# --------------------------------------------------------------------------- 严重度
class RateLimitSeverity(str, enum.Enum):
    """限速严重度分档（T8.8）。"""

    LIGHT = "light"    # 秒级：原地等
    HEAVY = "heavy"    # 分钟级：等待 + 通知放慢迭代节奏
    BANNED = "banned"  # 小时级：暂停任务并通知（**不是失败**）


def severity_for(wait_seconds: float) -> RateLimitSeverity:
    if wait_seconds < LIGHT_MAX:
        return RateLimitSeverity.LIGHT
    if wait_seconds < HEAVY_MAX:
        return RateLimitSeverity.HEAVY
    return RateLimitSeverity.BANNED


# --------------------------------------------------------------------------- 异常
class RateLimitWaiting(RuntimeError):
    """Emitted as a status event while backing off; not a task failure."""

    def __init__(self, attempt: int, wait_seconds: float, cause: str = "",
                 severity: RateLimitSeverity = RateLimitSeverity.LIGHT):
        self.attempt = attempt
        self.wait_seconds = wait_seconds
        self.severity = severity
        self.cause = cause
        super().__init__(f"rate limited ({severity.value}), retry {attempt} "
                         f"after {wait_seconds:.0f}s")


class RateLimitPaused(TransientError):
    """封禁级限速：任务应被**暂停**（不是失败），稍后由调度器重试。

    与 `RetryExhausted` 的区别：RetryExhausted = 试过了、放弃了（算一次失败）；
    RateLimitPaused = 服务端明确要求长时间别来（算 `rate_limited`）。

    分类上是瞬态（稍后重试就能好），但**调用方必须显式处理它、不能按普通瞬态续跑**
    ——否则会先白等好几轮退避才暂停（`AgentLoop` 里有专门的 except）。
    """

    def __init__(self, wait_seconds: float, last_error: BaseException | None = None,
                 severity: RateLimitSeverity = RateLimitSeverity.BANNED):
        self.wait_seconds = wait_seconds
        self.severity = severity
        self.last_error = last_error
        super().__init__(f"rate limit {severity.value}: server asks to wait "
                         f"{wait_seconds:.0f}s; task paused (not a failure)",
                         cause=last_error)


class RetryExhausted(RuntimeError):
    """All retries used up — this DOES count as one task failure."""

    def __init__(self, last_error: BaseException, attempts: int):
        self.last_error = last_error
        self.attempts = attempts
        super().__init__(f"retry exhausted after {attempts} attempts: {last_error}")


def is_retryable(exc: BaseException) -> bool:
    """Batch 6 · A1：判定收敛到 `errors.classify` —— "什么算可重试"只有一个定义。

    语义 = 分类为瞬态（超时 / 连接错误 / 429 / 5xx / 522·524）。
    """
    return _is_retryable(exc)


# --------------------------------------------------------------------------- Retry-After
def parse_retry_after(value: str | None, now: float | None = None) -> float | None:
    """解析 `Retry-After` 头，两种格式都支持：秒数 或 HTTP-date。

    返回 None = 无法解析（调用方回退到自适应退避）。负值归零。
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    # 纯数字（可带小数）
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    # HTTP-date (RFC 7231)
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    try:
        if dt.tzinfo is None:
            delta = dt.timestamp() - (now if now is not None else time.time())
        else:
            import datetime as _dt
            ref = _dt.datetime.fromtimestamp(now if now is not None else time.time(),
                                             tz=_dt.timezone.utc)
            delta = (dt - ref).total_seconds()
    except (OverflowError, OSError, ValueError):
        return None
    return max(0.0, delta)


def retry_after_from(exc: BaseException) -> float | None:
    """从异常里挖出服务端要求的等待时间（httpx.HTTPStatusError 的响应头）。

    也认得被包装过的情形（`RetryExhausted.last_error` / `__cause__`）。
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        resp = getattr(cur, "response", None)
        headers = getattr(resp, "headers", None)
        if headers is not None:
            try:
                got = headers.get("Retry-After") or headers.get("retry-after")
            except Exception:  # noqa: BLE001 — headers 可能是怪对象
                got = None
            parsed = parse_retry_after(got)
            if parsed is not None:
                return parsed
        nxt = getattr(cur, "last_error", None)
        if not isinstance(nxt, BaseException):
            nxt = getattr(cur, "__cause__", None)
        cur = nxt if isinstance(nxt, BaseException) else None
    return None


# --------------------------------------------------------------------------- 记忆
class RetryMemory:
    """按 profile 记住"上次退避多久才成功"，下次直接从该量级起跳（T8.7③）。

    落在一个小 JSON 文件里（默认 data_dir/retry_memory.json）；读失败一律当空，
    写失败只记日志——退避记忆坏掉不该影响正常调用。
    """

    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else None
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}
        except Exception:  # noqa: BLE001 — 记忆损坏就当没有
            logger.warning("retry memory unreadable, starting fresh", exc_info=True)
            self._data = {}

    def _flush(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception:  # noqa: BLE001 — 写失败不影响调用
            logger.warning("retry memory write failed", exc_info=True)

    def last_interval(self, profile: str) -> float | None:
        entry = self._data.get(profile or "")
        if not entry:
            return None
        val = entry.get("last_interval")
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    def note_wait(self, profile: str, interval: float, severity: str = "") -> None:
        key = profile or ""
        entry = self._data.setdefault(key, {})
        entry["last_interval"] = round(float(interval), 3)
        entry["last_severity"] = severity
        entry["waits"] = int(entry.get("waits", 0)) + 1
        self._flush()

    def note_success(self, profile: str, interval: float) -> None:
        key = profile or ""
        entry = self._data.setdefault(key, {})
        entry["last_success_interval"] = round(float(interval), 3)
        entry["successes"] = int(entry.get("successes", 0)) + 1
        self._flush()

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {k: dict(v) for k, v in self._data.items()}


# --------------------------------------------------------------------------- 策略
class RetryPolicy:
    """带 AIMD 自适应 + Retry-After + 每 profile 记忆的退避策略。"""

    def __init__(
        self,
        base_interval: float = 10.0,
        max_interval: float = 300.0,
        max_retries: int = 5,
        backoff: str = "exp",
        on_wait: Callable[[int, float], Any] | None = None,
        *,
        memory: RetryMemory | None = None,
        profile: str = "",
        on_severity: Callable[[RateLimitSeverity, float, BaseException | None], Any] | None = None,
        adaptive: bool = True,
        recovery_step: float | None = None,
        heavy_bonus: int = 5,
        banned_bonus: int = 10,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ):
        self.base_interval = float(base_interval)
        self.max_interval = float(max_interval)
        self.max_retries = int(max_retries)
        self.backoff = backoff
        self.on_wait = on_wait
        self.memory = memory
        self.profile = profile or ""
        self.on_severity = on_severity
        self.adaptive = adaptive
        # 加性恢复步长：默认半个基准间隔（连续成功就把间隔往回收）
        self.recovery_step = float(recovery_step if recovery_step is not None
                                   else max(1.0, self.base_interval / 2))
        self.heavy_bonus = max(0, int(heavy_bonus))
        self.banned_bonus = max(0, int(banned_bonus))
        self._sleep = sleep or asyncio.sleep
        # AIMD 当前间隔：起跳点优先用该 profile 的历史记忆
        remembered = self.memory.last_interval(self.profile) if self.memory else None
        self.current_interval = self._clamp(remembered if remembered is not None
                                            else self.base_interval)
        self.last_severity: RateLimitSeverity | None = None
        self.waits = 0
        self.severities: list[RateLimitSeverity] = []

    # ---- helpers
    def _clamp(self, value: float) -> float:
        # 上限允许被重度限速突破（否则 max_interval=300s 永远等不到服务端的 1 小时）
        return max(self.base_interval, min(float(value), self.max_interval))

    def delay_for(self, attempt: int) -> float:
        """attempt is 1-based。adaptive 模式下用 AIMD 当前间隔，不再瞎猜指数。

        adaptive 时**不再夹到 max_interval**：`_bump_interval` 已经管住上限，
        而重度限速下服务端给的量级本来就可能超过配置的 max_interval。
        """
        if self.backoff == "fixed":
            return self.base_interval
        if self.adaptive:
            return max(self.base_interval, float(self.current_interval))
        return min(self.base_interval * (2 ** (attempt - 1)), self.max_interval)

    def effective_max_retries(self) -> int:
        """重度/封禁档自动追加预算——避免"明明等到就好了却判失败"。"""
        if self.last_severity is RateLimitSeverity.BANNED:
            return self.max_retries + self.banned_bonus
        if self.last_severity is RateLimitSeverity.HEAVY:
            return self.max_retries + self.heavy_bonus
        return self.max_retries

    # ---- AIMD 状态机
    def _bump_interval(self, server_wait: float | None) -> float:
        """乘性放缓，并返回**本次该等多久**。

        有服务端 `Retry-After` 时以它为准（权威），但内部间隔仍按 AIMD 抬高，
        保证下次没有提示时起跳点更保守；没有提示时就用"乘性放缓后的间隔"
        ——注意是**先放缓再等**，否则第一次被限速还只等基准时长。
        """
        self.waits += 1
        doubled = max(self.current_interval * 2, self.base_interval * 2)
        ceiling = self.max_interval * 10
        if server_wait is not None:
            self.current_interval = max(self.base_interval,
                                        min(max(doubled, float(server_wait)), ceiling))
            return float(server_wait)
        self.current_interval = max(self.base_interval, min(doubled, ceiling))
        return float(self.current_interval)

    def _note_success(self) -> None:
        """加性恢复：连续成功就把间隔往回收（不会一步回到基准）。"""
        if self.waits == 0:
            return
        self.current_interval = self._clamp(self.current_interval - self.recovery_step)
        if self.memory:
            self.memory.note_success(self.profile, self.current_interval)

    # ---- 主循环
    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Run fn with retries. Only exhausted retries raise RetryExhausted."""
        last: BaseException | None = None
        attempt = 0
        while True:
            attempt += 1
            try:
                result = await fn()
            except BaseException as e:  # noqa: BLE001 — filtered below
                if not is_retryable(e):
                    raise
                last = e
                server_wait = retry_after_from(e)
                delay = self._bump_interval(server_wait)
                severity = severity_for(delay)
                self.last_severity = severity
                self.severities.append(severity)
                if self.memory:
                    self.memory.note_wait(self.profile, self.current_interval,
                                          severity=severity.value)

                logger.warning("LLM retryable failure (%s) → %s, wait %.0fs "
                               "(attempt %d, server_hint=%s)",
                               e, severity.value, delay, attempt,
                               f"{server_wait:.0f}s" if server_wait is not None else "none")
                self._emit_wait(attempt, delay, severity, e)

                # 封禁级：不原地等，交给调度器暂停 + 通知（不算失败）
                if severity is RateLimitSeverity.BANNED:
                    raise RateLimitPaused(delay, last, severity)

                if attempt >= self.effective_max_retries():
                    break
                await self._sleep(delay)
            else:
                self._note_success()
                return result
        raise RetryExhausted(last or RuntimeError("unknown"), attempt)

    def _emit_wait(self, attempt: int, delay: float,
                   severity: RateLimitSeverity, exc: BaseException | None) -> None:
        if self.on_wait:
            try:
                self.on_wait(attempt, delay)
            except Exception:  # noqa: BLE001 — 状态上报绝不能打断等待
                pass
        if self.on_severity:
            try:
                self.on_severity(severity, delay, exc)
            except Exception:  # noqa: BLE001
                pass

    @classmethod
    def from_config(cls, cfg: Any, on_wait: Callable[[int, float], Any] | None = None,
                    *, memory: RetryMemory | None = None, profile: str = "",
                    on_severity: Any = None,
                    sleep: Callable[[float], Awaitable[None]] | None = None) -> "RetryPolicy":
        return cls(
            base_interval=cfg.base_interval,
            max_interval=cfg.max_interval,
            max_retries=cfg.max_retries,
            backoff=cfg.backoff,
            on_wait=on_wait,
            memory=memory,
            profile=profile,
            on_severity=on_severity,
            sleep=sleep,
        )
