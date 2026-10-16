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
    """按 **(端点, key)** 记住"上次退避多久才成功"，下次直接从该量级起跳（T8.7③）。

    落在一个小 JSON 文件里（默认 data_dir/retry_memory.json）；读失败一律当空，
    写失败只记日志——退避记忆坏掉不该影响正常调用。

    v0.10.28 三处修正（生产实测暴露）：
    1. **分桶键**以前是 profile 名（默认全是 `""`）→ 管理员被限速会把 600s 传染给
       所有工位。现在由调用方传 **(端点|key 尾)**，各把 key 互不影响。
    2. **TTL**：距离上次被限速超过 `TTL_SECONDS` 就作废，惩罚不该无限期挂着。
    3. **schema 版本**：旧文件（没有时间戳、可能就是那个被灌到 600s 的）直接丢弃。
    """

    #: 距上次等待超过这么久 → 该桶作废（回到基准间隔）
    TTL_SECONDS = 900.0
    SCHEMA = 2

    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else None
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("__schema") != self.SCHEMA:
                logger.info("retry memory schema 不符（旧文件），丢弃重建")
                return
            self._data = {str(k): dict(v) for k, v in raw.items()
                          if isinstance(v, dict) and not str(k).startswith("__")}
        except Exception:  # noqa: BLE001 — 记忆损坏就当没有
            logger.warning("retry memory unreadable, starting fresh", exc_info=True)
            self._data = {}

    def _flush(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"__schema": self.SCHEMA, **self._data}
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception:  # noqa: BLE001 — 写失败不影响调用
            logger.warning("retry memory write failed", exc_info=True)

    def _stale(self, key: str, entry: dict[str, Any]) -> bool:
        """老到可以忘掉了？（没有任何"上次等待时间"的旧桶一律丢弃）"""
        at = entry.get("at")
        if at is None:
            self._data.pop(key, None)
            return True
        try:
            return (time.time() - float(at)) > self.TTL_SECONDS
        except (TypeError, ValueError):
            self._data.pop(key, None)
            return True

    def last_interval(self, profile: str) -> float | None:
        key = profile or ""
        entry = self._data.get(key)
        if not entry:
            return None
        if self._stale(key, entry):
            self._flush()
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
        entry["at"] = time.time()
        self._flush()

    def note_success(self, profile: str, interval: float) -> None:
        key = profile or ""
        entry = self._data.setdefault(key, {})
        entry["last_success_interval"] = round(float(interval), 3)
        entry["successes"] = int(entry.get("successes", 0)) + 1
        entry["at"] = time.time()
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
        on_success: Callable[[], Any] | None = None,
        inline_cap: float | None = None,
        wait_budget: float | None = None,
        memory_key: str | None = None,
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
        #: v0.10.28：退避记忆的**分桶键**（(端点,key 尾)）；默认退回 profile 名。
        #: 以前只有 profile，而所有工位/管理员共用 `""` —— 一个调用者被限速，
        #: 600s 会传染给全部调用者（生产实测）。
        self.memory_key = memory_key if memory_key is not None else self.profile
        #: v0.10.28：在线等待上限 / 累计等待预算。超过就**不睡**，抛 RateLimitPaused
        #: 交给调度器（定时任务会自己排到 5 分钟后；交互路径立刻拿到明确答复）。
        self.inline_cap = None if inline_cap is None else float(inline_cap)
        self.wait_budget = None if wait_budget is None else float(wait_budget)
        self.waited_total = 0.0
        # v0.10.21：限速徽标以前"只亮不灭"——唯一清它的钩子是 `window.run_finished`
        # 事件，而**群组运行、管理员对话、工具调用都不发这个事件**（owner 实测：
        # 退避早结束了，顶部还挂着"限速等待中"）。真正的结束信号是"这次调用成功了"，
        # 所以补 `on_success`：等待过至少一次之后，一旦调用成功就回调（清徽标）。
        self.on_success = on_success
        self.adaptive = adaptive
        # 加性恢复步长：默认半个基准间隔（连续成功就把间隔往回收）
        self.recovery_step = float(recovery_step if recovery_step is not None
                                   else max(1.0, self.base_interval / 2))
        self.heavy_bonus = max(0, int(heavy_bonus))
        self.banned_bonus = max(0, int(banned_bonus))
        self._sleep = sleep or asyncio.sleep
        # AIMD 当前间隔：起跳点优先用**该分桶**的历史记忆（(端点,key)，不是 profile）
        remembered = self.memory.last_interval(self.memory_key) if self.memory else None
        self.current_interval = self._clamp(remembered if remembered is not None
                                            else self.base_interval)
        self.last_severity: RateLimitSeverity | None = None
        self.waits = 0
        #: 比例恢复用：距上次被限速后连续成功了几次
        self._successes_since_wait = 0
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
        """**比例式**恢复：连续 2 次成功后间隔减半（不会一步回到基准）。

        v0.10.28：原来是**加性**的 `interval -= max(1, base/2)` = 5 秒/次 ——
        从 600s 回到基准要约 118 次成功，而每次成功前还得先等一次，实际上
        **几乎永不恢复**（生产实测：重试记忆被灌到 600s 后再也没降下来）。
        """
        self._successes_since_wait += 1
        if self.waits == 0:
            return
        if self._successes_since_wait % 2 == 0:
            self.current_interval = self._clamp(
                max(self.base_interval, self.current_interval * 0.5))
        if self.memory:
            self.memory.note_success(self.memory_key, self.current_interval)

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
                self.waits += 0  # (waits 在 _bump_interval 里 +1)
                self._successes_since_wait = 0
                if self.memory:
                    self.memory.note_wait(self.memory_key, self.current_interval,
                                          severity=severity.value)

                # v0.10.28：**长等待不睡在请求里**。判断顺序很重要：
                #   1) 服务端要等 > inline_cap  → 暂停，交给调度（定时任务 5 分钟后重试）
                #   2) 本次调用累计等待将超过 wait_budget → 同上
                #   3) 封禁档（≥30min）→ 同上（原有行为）
                # 只有在 cap/budget 之内的"短等待"才原地 sleep 重试。
                pause_reason = ""
                if self.inline_cap is not None and delay > self.inline_cap:
                    pause_reason = (f"服务端要求等待 {delay:.0f}s，超过在线等待上限 "
                                    f"{self.inline_cap:.0f}s")
                elif (self.wait_budget is not None
                        and self.waited_total + delay > self.wait_budget):
                    pause_reason = (f"本次调用已等待 {self.waited_total:.0f}s，再加 "
                                    f"{delay:.0f}s 会超过等待预算 {self.wait_budget:.0f}s")
                elif severity is RateLimitSeverity.BANNED:
                    pause_reason = f"封禁级限速（需等待 {delay:.0f}s）"

                logger.warning("LLM retryable failure (%s) → %s, wait %.0fs "
                               "(attempt %d, server_hint=%s)%s",
                               e, severity.value, delay, attempt,
                               f"{server_wait:.0f}s" if server_wait is not None else "none",
                               f" → 暂停：{pause_reason}" if pause_reason else "")
                self._emit_wait(attempt, delay, severity, e,
                                source="server-retry-after" if server_wait is not None else "aimd",
                                server_wait=server_wait,
                                action="paused" if pause_reason else "slept",
                                reason=pause_reason)

                if pause_reason:
                    exc = RateLimitPaused(delay, last, severity)
                    exc.reason = pause_reason  # 供上层原样转述给用户
                    raise exc

                if attempt >= self.effective_max_retries():
                    break
                self.waited_total += delay
                await self._sleep(delay)
            else:
                self._note_success()
                self.waited_total = 0.0
                if self.waits and self.on_success:
                    try:
                        self.on_success()
                    except Exception:  # noqa: BLE001 — 状态上报绝不能打断成功路径
                        logger.debug("on_success hook failed", exc_info=True)
                return result
        raise RetryExhausted(last or RuntimeError("unknown"), attempt)

    def _emit_wait(self, attempt: int, delay: float,
                   severity: RateLimitSeverity, exc: BaseException | None,
                   *, source: str = "", server_wait: float | None = None,
                   action: str = "", reason: str = "") -> None:
        """把"为什么等、等多久、是睡还是暂停"一并抛给上层（可解释性）。"""
        info = {"attempt": attempt, "delay": float(delay), "severity": severity.value,
                "source": source, "retry_after": server_wait,
                "interval": round(float(self.current_interval), 3),
                "memory_key": self.memory_key, "waited_total": round(self.waited_total, 3),
                "action": action or "slept", "reason": reason}
        if self.on_wait:
            try:
                self.on_wait(attempt, delay, info)
            except TypeError:  # 旧的两参数钩子
                try:
                    self.on_wait(attempt, delay)
                except Exception:  # noqa: BLE001 — 状态上报绝不能打断等待
                    pass
            except Exception:  # noqa: BLE001
                pass
        if self.on_severity:
            try:
                self.on_severity(severity, delay, exc, info)
            except TypeError:  # 旧的三参数钩子
                try:
                    self.on_severity(severity, delay, exc)
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass

    @classmethod
    def from_config(cls, cfg: Any, on_wait: Callable[[int, float], Any] | None = None,
                    *, memory: RetryMemory | None = None, profile: str = "",
                    memory_key: str | None = None,
                    on_severity: Any = None, on_success: Callable[[], Any] | None = None,
                    sleep: Callable[[float], Awaitable[None]] | None = None) -> "RetryPolicy":
        return cls(
            base_interval=cfg.base_interval,
            max_interval=cfg.max_interval,
            max_retries=cfg.max_retries,
            backoff=cfg.backoff,
            on_wait=on_wait,
            memory=memory,
            profile=profile,
            memory_key=memory_key,
            inline_cap=getattr(cfg, "inline_cap", None),
            wait_budget=getattr(cfg, "wait_budget", None),
            on_severity=on_severity,
            on_success=on_success,
            sleep=sleep,
        )
