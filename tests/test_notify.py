# DogBuddyCn — notifier channel plugins & report routing tests (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Tests for build_notifiers / Console/Webhook/Gateway channels / ReportService routing."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import pytest

from dogbuddy.config import NotifyChannelConfig, NotifyConfig
from dogbuddy.cron.scheduler import CronScheduler, JobOutcome
from dogbuddy.events.bus import (
    CONFIRM_REQUEST,
    JOB_COMPLETED,
    JOB_FAILED,
    RATE_LIMIT_STATUS,
    WORKER_QUESTION,
    Event,
    EventBus,
)
from dogbuddy.gateway.adapters.base import PushTarget
from dogbuddy.notify import build_notifiers
from dogbuddy.notify.console import ConsoleNotifier
from dogbuddy.notify.gateway import GatewayNotifier
from dogbuddy.notify.webhook import WebhookNotifier
from dogbuddy.services.report_service import ReportService


@pytest.fixture(autouse=True)
def _reset_event_loop_policy():
    """asyncio.run() marks the thread's loop as explicitly set (to a closed one),
    which would break later tests calling asyncio.get_event_loop(); restore a
    pristine policy after every test so suites can run in any order."""
    yield
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())


# ------------------------------------------------------------------ helpers
class FakeAdapter:
    """Records adapter.push(target, content) calls."""

    def __init__(self) -> None:
        self.pushes: list[tuple] = []

    async def push(self, target, content) -> None:
        self.pushes.append((target, content))


class FailingAdapter:
    async def push(self, target, content) -> None:
        raise RuntimeError("adapter down")


class FakeNotifier:
    """Notifier stub recording send() payloads; optionally raises."""

    def __init__(self, name: str, fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        if self.fail:
            raise RuntimeError(f"{self.name} down")
        self.sent.append(text)


class _FakeResp:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


def _patch_httpx_client(monkeypatch, *, status: int = 200, error: Exception | None = None):
    """Replace httpx.AsyncClient with a recording fake; returns the record."""
    record: dict = {"client_kwargs": [], "posts": []}

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            record["client_kwargs"].append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        async def post(self, url, json=None, **kwargs):
            record["posts"].append({"url": url, "json": json, "kwargs": kwargs})
            if error is not None:
                raise error
            return _FakeResp(status)

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    return record


# ------------------------------------------------------- build_notifiers
def test_build_empty_config_defaults_to_console():
    for cfg in (NotifyConfig(), None):
        notifiers = build_notifiers(cfg)
        assert len(notifiers) == 1
        assert isinstance(notifiers[0], ConsoleNotifier)
        assert notifiers[0].name == "console"


def test_build_three_types_and_name_dedupe():
    cfg = NotifyConfig(channels=[
        NotifyChannelConfig(type="console"),                       # -> console
        NotifyChannelConfig(type="console"),                       # -> console-2
        NotifyChannelConfig(type="webhook", name="x", url="http://a"),  # -> x
        NotifyChannelConfig(type="webhook", name="x", url="http://b"),  # -> x-2
        NotifyChannelConfig(type="gateway", name="x",
                            target_type="group", target_id="123"),  # -> x-3
    ])
    adapter = FakeAdapter()
    notifiers = build_notifiers(cfg, gateway_adapter=adapter,
                                gateway_default_target={"type": "private", "id": 9})
    assert [n.name for n in notifiers] == ["console", "console-2", "x", "x-2", "x-3"]
    assert isinstance(notifiers[0], ConsoleNotifier)
    assert isinstance(notifiers[1], ConsoleNotifier)
    assert isinstance(notifiers[2], WebhookNotifier)
    assert isinstance(notifiers[3], WebhookNotifier)
    assert isinstance(notifiers[4], GatewayNotifier)


def test_build_skips_disabled_channels():
    cfg = NotifyConfig(channels=[
        NotifyChannelConfig(type="console", enabled=False),
        NotifyChannelConfig(type="webhook", url="http://a", enabled=False),
        NotifyChannelConfig(type="console", name="alive"),
    ])
    notifiers = build_notifiers(cfg)
    assert [n.name for n in notifiers] == ["alive"]


def test_build_gateway_without_adapter_skipped():
    cfg = NotifyConfig(channels=[
        NotifyChannelConfig(type="gateway", name="gw",
                            target_type="group", target_id="123"),
        NotifyChannelConfig(type="console"),
    ])
    notifiers = build_notifiers(cfg)  # no adapter → gateway dropped
    assert [n.name for n in notifiers] == ["console"]
    # with adapter + default target it is kept
    notifiers = build_notifiers(cfg, gateway_adapter=FakeAdapter(),
                                gateway_default_target={"type": "private", "id": 1})
    assert [n.name for n in notifiers] == ["gw", "console"]


def test_build_invalid_channels_skipped():
    cfg = NotifyConfig(channels=[
        NotifyChannelConfig(type="webhook", name="no-url"),          # missing url
        NotifyChannelConfig(type="gateway", name="no-target"),       # no adapter/target
        NotifyChannelConfig(type="smoke-signal", name="unknown"),    # unknown type
        NotifyChannelConfig(type="console", name="ok"),
    ])
    notifiers = build_notifiers(cfg, gateway_adapter=FakeAdapter(),
                                gateway_default_target={})
    assert [n.name for n in notifiers] == ["ok"]


# ---------------------------------------------------------- ConsoleNotifier
def test_console_never_raises():
    async def run():
        n = ConsoleNotifier()
        await n.send("hello world")

        # even a broken logger must not propagate
        class Boom:
            def info(self, *a, **k):
                raise RuntimeError("logger broken")

        import dogbuddy.notify.console as console_mod
        old, console_mod.logger = console_mod.logger, Boom()
        try:
            await ConsoleNotifier().send("still fine")
        finally:
            console_mod.logger = old

    asyncio.run(run())


# ---------------------------------------------------------- WebhookNotifier
def test_webhook_posts_json_body(monkeypatch):
    record = _patch_httpx_client(monkeypatch, status=200)

    async def run():
        n = WebhookNotifier(name="webhook-a", url="http://hook.test/x")
        await n.send("任务完成")

    asyncio.run(run())
    assert len(record["posts"]) == 1
    post = record["posts"][0]
    assert post["url"] == "http://hook.test/x"
    assert post["json"] == {"text": "任务完成", "source": "dogbuddy"}
    assert record["client_kwargs"][0].get("timeout") == 10.0


def test_webhook_failure_does_not_raise(monkeypatch):
    _patch_httpx_client(monkeypatch, error=RuntimeError("conn refused"))

    async def run():
        await WebhookNotifier(name="w", url="http://hook.test").send("text")

    asyncio.run(run())  # must not raise

    _patch_httpx_client(monkeypatch, status=500)

    async def run_500():
        await WebhookNotifier(name="w", url="http://hook.test").send("text")

    asyncio.run(run_500())  # HTTP error must not raise either


# ---------------------------------------------------------- GatewayNotifier
def test_gateway_push_target_from_channel():
    adapter = FakeAdapter()
    cfg = NotifyConfig(channels=[
        NotifyChannelConfig(type="gateway", name="gw",
                            target_type="group", target_id="123"),
    ])
    (n,) = build_notifiers(cfg, gateway_adapter=adapter,
                           gateway_default_target={"type": "private", "id": 999})
    asyncio.run(n.send("hi"))
    assert len(adapter.pushes) == 1
    target, content = adapter.pushes[0]
    assert (target.type, target.id) == ("group", "123")  # channel's own target wins
    assert content == "hi"


def test_gateway_push_target_from_default():
    adapter = FakeAdapter()
    cfg = NotifyConfig(channels=[NotifyChannelConfig(type="gateway", name="gw")])
    (n,) = build_notifiers(cfg, gateway_adapter=adapter,
                           gateway_default_target={"type": "private", "id": 456})
    asyncio.run(n.send("hello"))
    target, content = adapter.pushes[0]
    assert (target.type, target.id) == ("private", "456")
    assert content == "hello"


def test_gateway_failure_does_not_raise():
    n = GatewayNotifier(name="gw", adapter=FailingAdapter(),
                        target=SimpleNamespace(type="private", id="1"))
    n.target = __import__("dogbuddy.gateway.adapters.base",
                          fromlist=["PushTarget"]).PushTarget(type="private", id="1")
    asyncio.run(n.send("text"))  # must not raise


# ------------------------------------------------- ReportService: routing
def _report(notifiers):
    return ReportService(bus=EventBus(), notifiers=notifiers, llm_factory=None)


def test_report_routes_to_named_channel_only():
    a, b = FakeNotifier("webhook-a"), FakeNotifier("webhook-b")
    svc = _report([a, b])
    asyncio.run(svc.bus.publish(Event(type=JOB_COMPLETED, payload={
        "name": "job1", "summary": "done", "report_channel": "webhook-a"})))
    assert len(a.sent) == 1 and b.sent == []


def test_report_empty_channel_sends_all():
    a, b, c = FakeNotifier("console"), FakeNotifier("webhook-a"), FakeNotifier("gw")
    svc = _report([a, b, c])
    asyncio.run(svc.bus.publish(Event(type=JOB_COMPLETED, payload={
        "name": "job1", "summary": "done", "report_channel": ""})))
    assert len(a.sent) == len(b.sent) == len(c.sent) == 1


def test_report_unknown_channel_falls_back_to_all():
    a, b = FakeNotifier("console"), FakeNotifier("webhook-a")
    svc = _report([a, b])
    asyncio.run(svc.bus.publish(Event(type=JOB_COMPLETED, payload={
        "name": "job1", "summary": "done", "report_channel": "nope"})))
    assert len(a.sent) == 1 and len(b.sent) == 1


def test_report_channel_failure_does_not_block_others():
    bad, good = FakeNotifier("bad", fail=True), FakeNotifier("good")
    svc = _report([bad, good])
    asyncio.run(svc.bus.publish(Event(type=JOB_COMPLETED, payload={
        "name": "job1", "summary": "done"})))
    assert bad.sent == []
    assert len(good.sent) == 1  # the failing channel didn't stop delivery


# --------------------------------------------- ReportService: regression
def test_report_silent_not_delivered():
    a = FakeNotifier("console")
    svc = _report([a])
    asyncio.run(svc.bus.publish(Event(type=JOB_COMPLETED, payload={
        "name": "job1", "summary": "noise", "report_strategy": "silent"})))
    assert a.sent == []


def test_report_critical_always_delivered():
    a = FakeNotifier("console")
    svc = _report([a])
    # auto_paused failure is critical: even silent strategy surfaces it
    asyncio.run(svc.bus.publish(Event(type=JOB_FAILED, payload={
        "name": "job1", "summary": "boom", "auto_paused": True,
        "fail_count": 3, "report_strategy": "silent"})))
    # question & confirm requests are critical by definition
    asyncio.run(svc.bus.publish(Event(type=WORKER_QUESTION, payload={
        "name": "dev", "question": "怎么处理 X？"})))
    asyncio.run(svc.bus.publish(Event(type=CONFIRM_REQUEST, payload={
        "name": "dev", "reason": "需要删除文件"})))
    assert len(a.sent) == 3


def test_report_template_contains_key_info():
    a = FakeNotifier("console")
    svc = _report([a])
    asyncio.run(svc.bus.publish(Event(type=JOB_COMPLETED, payload={
        "name": "夜间构建", "summary": "全部通过", "report_strategy": "brief"})))
    asyncio.run(svc.bus.publish(Event(type=JOB_FAILED, payload={
        "name": "备份任务", "summary": "磁盘满", "auto_paused": True, "fail_count": 3})))
    completed, failed = a.sent
    assert "夜间构建" in completed and "全部通过" in completed and "✅" in completed
    assert "备份任务" in failed and "磁盘满" in failed
    assert "自动暂停" in failed and "3" in failed


def test_report_rate_limit_only_logs():
    a = FakeNotifier("console")
    svc = _report([a])
    asyncio.run(svc.bus.publish(Event(type=RATE_LIMIT_STATUS, payload={
        "job_id": "j1", "summary": "waiting"})))
    assert a.sent == []


# ------------------------------- scheduler emits report_channel in payload
def test_scheduler_emits_report_channel():
    events: list[Event] = []

    class FakeBus:
        def emit(self, event: Event) -> None:
            events.append(event)

    class FakeRepo:
        def start_run(self, job_id, window_id):
            return SimpleNamespace(id="run1")

        def finish_run(self, *args, **kwargs):
            pass

    class FakeStore:
        repo = FakeRepo()

        def mark_success(self, job, next_run_at=None):
            # T8.3: 调度器会把"自调度覆盖值"作为第二个参数传进来
            self.last_next_run_at = next_run_at

        def mark_failure(self, job):
            return (1, True)

        def mark_rate_limited(self, job):
            pass

    job = SimpleNamespace(id="j1", name="job", window_id="w1",
                          report_strategy="brief", report_channel="webhook-a")

    async def run():
        async def run_job(_job):
            return JobOutcome(status="ok", summary="fine")

        sched = CronScheduler(store=FakeStore(), run_job=run_job, bus=FakeBus())
        await sched._execute(job)

    asyncio.run(run())
    done = [e for e in events if e.type == JOB_COMPLETED]
    assert done and done[0].payload["report_channel"] == "webhook-a"
