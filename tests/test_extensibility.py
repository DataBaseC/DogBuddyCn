# DogBuddyCn — extensibility tests (profiles / multi-admin / task runners)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.runners import RunnerRegistry, RunnerResult, TaskEnv, default_runners
from dogbuddy.config import AppConfig, LLMConfig, PathsConfig
from dogbuddy.db import Database
from dogbuddy.events.bus import EventBus
from dogbuddy.llm.profile_registry import LLMProfileRegistry
from dogbuddy.llm.provider import LLMResponse
from dogbuddy.memory.service import MemoryService
from dogbuddy.services.admin_service import AdminService
from dogbuddy.services.config_service import ConfigService
from dogbuddy.services.job_manager import JobManager
from dogbuddy.services.session_manager import SessionManager
from dogbuddy.services.window_manager import WindowManager
from dogbuddy.services.workspace_manager import WorkspaceManager
from dogbuddy.cron.jobs import JobStore
from dogbuddy.windows.queue import TaskItem

from helpers import FakeLLM, tc


def _cfg(tmp_path, **llm_kw) -> AppConfig:
    return AppConfig(
        llm=LLMConfig(**llm_kw),
        llm_profiles={
            "cheap": LLMConfig(base_url="https://cheap.example/v1", api_key="ck",
                               model="cheap-model", provider="openai_compat"),
            "local": LLMConfig(provider="ollama", model="qwen", ollama_base_url="http://localhost:11434"),
        },
        active_llm_profile="cheap",
        paths=PathsConfig(data_dir=str(tmp_path), workspaces_dir=str(tmp_path / "ws")))


# ------------------------------------------------------------- profiles


def test_profile_resolution(tmp_path):
    reg = LLMProfileRegistry(_cfg(tmp_path, api_key="dk", model="default-model"))
    assert set(reg.names()) >= {"", "cheap", "local"}
    # explicit profile
    assert reg.resolve("cheap").model == "cheap-model"
    # empty resolves to the ACTIVE profile (cheap), not the raw default section
    assert reg.resolve("").model == "cheap-model"
    # unknown name falls back to the default section
    assert reg.resolve("nope").model == "default-model"


def test_profile_registry_builds_and_caches(tmp_path):
    built = []

    class FakeReg(LLMProfileRegistry):
        def _build(self, profile):
            built.append(profile or "")
            return FakeLLM()

    reg = FakeReg(_cfg(tmp_path))
    a = reg.get("cheap")
    b = reg.get("cheap")
    assert a is b and built.count("cheap") == 1  # cached
    reg.invalidate("cheap")
    reg.get("cheap")
    assert built.count("cheap") == 2  # cache dropped on config edit
    factory = reg.factory("local")
    assert factory() is reg.get("local")


def test_window_binds_profile(tmp_path):
    db = Database(tmp_path / "t.db")
    cfg = _cfg(tmp_path)
    llm, factory = FakeLLM(), None

    def fac(profile=None):
        return llm

    bus = EventBus()
    wm = WindowManager(db=db, llm_factory=fac, bus=bus, memory=MemoryService(tmp_path / "m"))
    w = wm.create(name="dev", workspace_path=str(tmp_path / "proj"), llm_profile="cheap")
    assert w.llm_profile == "cheap"
    assert db.get_window(w.id).llm_profile == "cheap"
    w2 = wm.update(w.id, llm_profile="local")
    assert w2.llm_profile == "local"


# ---------------------------------------------------------- multi-admin


def test_multi_admin_sessions_isolated(tmp_path):
    async def main():
        db = Database(tmp_path / "t.db")
        mem = MemoryService(tmp_path / "m")
        bus = EventBus()
        cfg = AppConfig(paths=PathsConfig(data_dir=str(tmp_path)))
        llm = FakeLLM([LLMResponse(content="hi from A"),
                       LLMResponse(content="hi from B"),
                       LLMResponse(content="hi from A again")])
        wm = WindowManager(db=db, llm_factory=lambda p=None: llm, bus=bus, memory=mem)
        await wm.start()
        sessions = SessionManager(wm, db)
        jobs = JobManager(JobStore(db), wm)
        admin = AdminService(db=db, llm_factory=lambda p=None: llm, windows=wm, jobs=jobs,
                             sessions=sessions,
                             workspaces=WorkspaceManager(tmp_path / "ws", db),
                             memory=mem, config=ConfigService(db, cfg), bus=bus)

        sa = admin.session_for("alice")
        sb = admin.session_for("bob")
        assert sa is not sb
        assert sa.window.id == "admin:alice" and sb.window.id == "admin:bob"
        # default + legacy alias coexist
        assert admin.session_for("default") is admin.session

        await admin.handle_user_message("hello", sender="alice")
        await admin.handle_user_message("hello", sender="bob")
        # conversations are isolated per admin...
        assert len(sa.recent(10)) >= 2
        assert all(m.content.find("bob") == -1 for m in sa.recent(10) if m.role == "user")
        # ...but global memory is shared
        mem.append_global("fact for everyone")
        assert "everyone" in mem.recall("global")
        assert sorted(admin.active_admins()) == ["alice", "bob", "default"]
        await wm.stop()
        db.close()

    asyncio.get_event_loop().run_until_complete(main())


# ---------------------------------------------------------- task runners


def test_script_runner_executes_shell(tmp_path):
    async def main():
        (tmp_path / "ws").mkdir()
        runners = default_runners()
        assert set(runners.kinds()) >= {"prompt", "script"}
        from dogbuddy.tools.registry import ToolContext, build_registry
        reg = build_registry()
        ctx = ToolContext(window_id="w", workspace=str(tmp_path / "ws"), mode="BUILD")

        class W:
            id = "w"
            llm_profile = ""

        env = TaskEnv(window=W(), session=None, registry=reg,
                      llm_factory=lambda p=None: FakeLLM(), builder=None, ctx=ctx)
        task = TaskItem(prompt="echo script-ok", kind="script")
        result = await runners.get("script").run(task, env)
        assert result.status == "ok" and "script-ok" in result.summary

    asyncio.get_event_loop().run_until_complete(main())


def test_custom_runner_registration(tmp_path):
    """Extension point: a new task kind plugs in without touching the manager."""
    async def main():
        db = Database(tmp_path / "t.db")
        bus = EventBus()
        ran = []

        class DeployRunner:
            kind = "deploy"

            async def run(self, task, env):
                ran.append(task.prompt)
                return RunnerResult(status="ok", summary=f"deployed {task.prompt}")

        runners = default_runners()
        runners.register(DeployRunner())
        llm = FakeLLM()
        wm = WindowManager(db=db, llm_factory=lambda p=None: llm, bus=bus,
                           memory=MemoryService(tmp_path / "m"), runners=runners)
        await wm.start()
        w = wm.create(name="dev", workspace_path=str(tmp_path / "proj"))
        outcome = await wm.dispatch_and_wait(w.id, "release-1.2", kind="deploy")
        assert outcome.status == "ok" and "release-1.2" in outcome.summary
        assert ran == ["release-1.2"]

        # unknown kinds fail loudly instead of running the wrong runner
        bad = await wm.dispatch_and_wait(w.id, "x", kind="quantum")
        assert bad.status == "failed" and "unknown task kind" in bad.summary
        await wm.stop()
        db.close()

    asyncio.get_event_loop().run_until_complete(main())


def test_job_profile_and_channel_persist(tmp_path):
    db = Database(tmp_path / "t.db")
    from dogbuddy.ports import Window
    db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                            workspace_path=str(tmp_path), model="m"))
    store = JobStore(db)
    job = store.create("w1", "j", "30m", "work")
    job.llm_profile = "cheap"
    job.report_channel = "webhook-ops"
    db.upsert_job(job)
    loaded = db.get_job(job.id)
    assert loaded.llm_profile == "cheap" and loaded.report_channel == "webhook-ops"
    db.close()
