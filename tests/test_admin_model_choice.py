# DogBuddyCn — 管理员只能从模型目录里选模型（0.10.17）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 的决定（2026-10-15）：

> 要不不搞默认模型了，全从模型目录里自己配得了。管理员不能自己拉取模型，
> 只能从模型页里的模型列表选。

于是：
  · 管理员新增两个工具：`model_catalog`（只读列表）与 `model_select`（只能选目录里的条目，
    可以给自己换，也可以给某个工位换）；
  · `window_create` / `window_update` 的 `model` 必须命中目录，否则**失败并列出候选**
    （以前随手写一个名字也能过，请求就打到 profile 端点上，报错还像"模型没配好"）；
  · 管理员**没有**任何"拉取服务商模型列表"的工具（本就不存在，这里加测试钉住）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.roles import ADMIN_TOOLS  # noqa: E402
from dogbuddy.agents.management.tools import AdminServices, build_admin_tools  # noqa: E402
from dogbuddy.config import AppConfig, LLMConfig, PathsConfig  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.events.bus import EventBus  # noqa: E402
from dogbuddy.memory.service import MemoryService  # noqa: E402
from dogbuddy.services.model_service import ModelService  # noqa: E402
from dogbuddy.services.window_manager import WindowManager  # noqa: E402
from dogbuddy.tools.registry import ToolContext  # noqa: E402

from helpers import run  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _stack(tmp_path, catalog=("deepseek-flash", "qwen-coder")):
    db = Database(tmp_path / "t.db")
    cfg = AppConfig(llm=LLMConfig(provider="openai_compat", base_url="https://api.deepseek.com/v1",
                                  api_key="k-global", model="deepseek-flash"),
                    paths=PathsConfig(data_dir=str(tmp_path), workspaces_dir=str(tmp_path / "ws")))
    models = ModelService(db, cfg, persist=lambda: "cfg.yaml")
    for i, name in enumerate(catalog):
        db.upsert_model({"id": f"e{i}", "provider": "openai_compat", "model": name,
                         "base_url": "https://api.deepseek.com/v1", "enabled": 1})
    wm = WindowManager(db=db, llm_factory=lambda *a, **k: None, bus=EventBus(),
                       memory=MemoryService(tmp_path / "mem"), checkpoints_dir=tmp_path / "ckpt")
    svc = AdminServices(windows=wm, models=models)
    tools = {t.name: t for t in build_admin_tools(svc)}
    ctx = ToolContext(window_id="admin:default", workspace=str(tmp_path), db=db)
    return db, cfg, models, wm, tools, ctx


def test_window_create_rejects_a_model_that_is_not_in_the_catalog(tmp_path):
    db, _cfg, _m, _wm, tools, ctx = _stack(tmp_path)

    async def main():
        bad = await tools["window_create"].execute(
            {"name": "乱写模型", "workspace": str(tmp_path / "ws" / "a"), "model": "gpt-9-turbo"}, ctx)
        assert not bad.ok and "不在模型目录里" in bad.output
        assert "deepseek-flash" in bad.output, "失败时要列出可选项，否则模型只能瞎猜"

        good = await tools["window_create"].execute(
            {"name": "正常", "workspace": str(tmp_path / "ws" / "b"), "model": "qwen-coder"}, ctx)
        assert good.ok, good.output
        # 继承（空）仍然允许
        inherit = await tools["window_create"].execute(
            {"name": "继承", "workspace": str(tmp_path / "ws" / "c")}, ctx)
        assert inherit.ok, inherit.output

    run(main())
    db.close()


def test_window_update_rejects_unknown_model(tmp_path):
    db, _cfg, _m, wm, tools, ctx = _stack(tmp_path)

    async def main():
        w = wm.create(name="工位", role="worker_programmer", workspace_path=str(tmp_path / "ws" / "w"))
        bad = await tools["window_update"].execute({"window_id": w.id, "model": "不存在"}, ctx)
        assert not bad.ok and "不在模型目录里" in bad.output
        ok = await tools["window_update"].execute({"window_id": w.id, "model": "qwen-coder"}, ctx)
        assert ok.ok and wm.get(w.id).model == "qwen-coder"

    run(main())
    db.close()


def test_window_update_can_raise_iteration_cap(tmp_path):
    """轮次上限是工位参数：管理员必须能通过工具改，否则只能让 owner 点控制台。"""
    db, _cfg, _m, wm, tools, ctx = _stack(tmp_path)

    async def main():
        created = await tools["window_create"].execute(
            {"name": "长任务", "workspace": str(tmp_path / "ws" / "long"),
             "max_iterations": 30}, ctx)
        assert created.ok, created.output
        wid = created.data["window_id"]
        assert wm.get(wid).params["max_iterations"] == 30
        ok = await tools["window_update"].execute(
            {"window_id": wid, "max_iterations": 80,
             "compact_threshold": 0.7}, ctx)
        assert ok.ok, ok.output
        got = wm.get(wid).params
        assert got["max_iterations"] == 80 and got["compact_threshold"] == 0.7
        bad = await tools["window_update"].execute(
            {"window_id": wid, "max_iterations": 999}, ctx)
        assert not bad.ok and "max_iterations" in bad.output

    run(main())
    db.close()


def test_model_select_changes_the_admin_model(tmp_path):
    db, cfg, _m, _wm, tools, ctx = _stack(tmp_path)

    async def main():
        listed = await tools["model_catalog"].execute({}, ctx)
        assert listed.ok and "deepseek-flash" in listed.output
        assert "管理员当前" in listed.output, "列表要标出当前用的是哪个"

        r = await tools["model_select"].execute({"model": "qwen-coder"}, ctx)
        assert r.ok, r.output
        assert cfg.llm.model == "qwen-coder", "选完没真正切到管理员模型上"

        miss = await tools["model_select"].execute({"model": "凭空捏造"}, ctx)
        assert not miss.ok and "不在目录里" in miss.output

    run(main())
    db.close()


def test_model_select_can_target_one_window(tmp_path):
    db, cfg, _m, wm, tools, ctx = _stack(tmp_path)

    async def main():
        w = wm.create(name="员工", role="worker_programmer", workspace_path=str(tmp_path / "ws" / "x"))
        r = await tools["model_select"].execute({"model": "qwen-coder", "window_id": w.id}, ctx)
        assert r.ok, r.output
        assert wm.get(w.id).model == "qwen-coder"
        assert cfg.llm.model == "deepseek-flash", "给工位选模型不该顺带改管理员的模型"

    run(main())
    db.close()


def test_admin_has_no_tool_to_fetch_models_from_a_provider():
    """管理员不能自己拉取模型 —— 只能从模型页的列表里选。"""
    registered = {t.name for t in build_admin_tools(AdminServices())}
    assert "model_catalog" in registered and "model_select" in registered
    for forbidden in ("model_import", "model_fetch", "models_fetch", "provider_models"):
        assert forbidden not in registered
        assert forbidden not in ADMIN_TOOLS
    src = (REPO / "dogbuddy/agents/management/tools.py").read_text(encoding="utf-8")
    assert "fetch_models" not in src and "fetch_models_meta" not in src, \
        "管理员工具里出现了拉取服务商模型列表的调用"


def test_wizard_adopts_the_model_into_the_catalog():
    """向导填的模型必须落进目录（模型只有一个来源）。

    v0.10.26 起不再用 `adoptCurrent()`（那只登记运行时的模型、**不带密钥**），
    而是直接建**带密钥的条目**再设为当前 —— 因为全局密钥框已停用，
    密钥必须落在条目上。这里钉住新的等价（更强）契约。
    """
    js = (REPO / "dogbuddy/web/static/js/views-settings.js").read_text(encoding="utf-8")
    assert "A.models.create(entry)" in js, "向导填的模型要落进目录（模型只有一个来源）"
    assert "A.models.activate(mid)" in js, "落进目录后要设为当前"
    assert "if (state.api_key) entry.api_key = state.api_key;" in js, "密钥要跟着条目走"
