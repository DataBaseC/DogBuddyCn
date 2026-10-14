# DogBuddyCn — v2 workbench params (T1.1)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Per-window params: 四级解析链 + persistence + runtime application."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dogbuddy.config import AppConfig
WINDOW_PARAM_KEYS = AppConfig.WINDOW_PARAM_KEYS
from dogbuddy.ports import Window


def _cfg(**kw):
    c = AppConfig()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _win(**kw):
    return Window(id="w1", name="w", role="worker_programmer",
                  workspace_path="/tmp", model="", mode="BUILD", **kw)


# ------------------------------------------------------------ 解析链
def test_global_defaults_everywhere():
    cfg = _cfg()
    p = cfg.effective_params(_win())
    assert p["max_context_tokens"] == 128000
    assert p["compact_threshold"] == 0.8
    assert p["max_iterations"] == 25
    assert p["retry"]["max_retries"] == 5
    assert p["request_timeout"] == 120
    assert p["temperature"] == 0.7


def test_window_override_beats_all():
    cfg = _cfg()
    cfg.llm_profiles["fancy"] = SimpleNamespace(
        temperature=0.1, request_timeout=5, retry={"max_retries": 0},
        compact_threshold=0.2, max_context_tokens=1000)
    w = _win(llm_profile="fancy", params={"temperature": 0.99, "max_context_tokens": 7000})
    p = cfg.effective_params(w)
    assert p["temperature"] == 0.99       # window wins
    assert p["max_context_tokens"] == 7000  # window wins
    assert p["compact_threshold"] == 0.2  # profile wins over global
    assert p["request_timeout"] == 5      # profile wins over global


def test_active_profile_inherits():
    cfg = _cfg()
    cfg.llm_profiles["fancy"] = SimpleNamespace(temperature=0.3)
    cfg.active_llm_profile = "fancy"
    p = cfg.effective_params(_win())
    assert p["temperature"] == 0.3


def test_bound_profile_beats_active_profile():
    cfg = _cfg()
    cfg.active_llm_profile = "a"
    cfg.llm_profiles["a"] = SimpleNamespace(temperature=0.1)
    cfg.llm_profiles["b"] = SimpleNamespace(temperature=0.2)
    p = cfg.effective_params(_win(llm_profile="b"))
    assert p["temperature"] == 0.2


def test_param_sources_labels():
    cfg = _cfg()
    cfg.llm_profiles["fancy"] = SimpleNamespace(temperature=0.1)
    w = _win(llm_profile="fancy", params={"retry": {"max_retries": 9}})
    s = cfg.param_sources(w)
    assert s["temperature"] == "inherited"
    assert s["retry"] == "window"
    assert set(s.keys()) == set(WINDOW_PARAM_KEYS)


def test_full_window_override_keys():
    cfg = _cfg()
    w = _win(params={k: 1 for k in WINDOW_PARAM_KEYS if k != "retry"})
    w.params["retry"] = {"max_retries": 1}
    p = cfg.effective_params(w)
    assert all(p[k] == 1 for k in WINDOW_PARAM_KEYS if k != "retry")
    assert p["retry"]["max_retries"] == 1


# ------------------------------------------------------------ 持久化
def test_params_persist_roundtrip(tmp_path):
    from dogbuddy.db import Database
    db = Database(str(tmp_path / "t.db"))
    w = _win(params={"temperature": 0.5, "max_iterations": 7})
    db.insert_window(w)
    got = db.get_window("w1")
    assert got.params == {"temperature": 0.5, "max_iterations": 7}

    db.update_window("w1", params={"retries": 3})
    got = db.get_window("w1")
    assert got.params == {"retries": 3}   # 整体替换语义

    db.update_window("w1", params={})
    assert db.get_window("w1").params == {}  # 清空 → 回落继承


def test_params_survive_migration_v2(tmp_path):
    """v1 库（无 params_json）migrate 后 params 回落空 dict。"""
    import sqlite3
    from dogbuddy.db import Database
    p = str(tmp_path / "old.db")
    conn = sqlite3.connect(p)
    conn.executescript("""
    CREATE TABLE windows(id TEXT PRIMARY KEY, name TEXT, role TEXT, workspace_path TEXT,
      model TEXT, mode TEXT DEFAULT 'BUILD', status TEXT DEFAULT 'idle', group_id TEXT,
      llm_profile TEXT DEFAULT '', created_at TEXT, updated_at TEXT);
    INSERT INTO windows VALUES('w1','old','worker_programmer','/tmp','','BUILD','idle',NULL,'',NULL,NULL);
    """)
    conn.commit()
    conn.close()
    db = Database(p)  # 触发迁移
    w = db.get_window("w1")
    assert w is not None
    assert w.params == {}


# ------------------------------------------------------------ 运行时应用
def test_window_manager_uses_param_resolver(tmp_path):
    from dogbuddy.db import Database
    from dogbuddy.services.window_manager import WindowManager
    db = Database(str(tmp_path / "t.db"))
    seen = {}

    def resolver(w):
        seen["hit"] = True
        return {"max_context_tokens": 5000, "compact_threshold": 0.33, "max_iterations": 3}

    wm = WindowManager(db, llm_factory=lambda *a, **k: None, bus=None,
                       max_iterations=25, threshold=0.8, max_context_tokens=128000,
                       param_resolver=resolver)
    w = wm.create("p1", role="worker_programmer", workspace_path=str(tmp_path / "ws"))
    p = wm.params_for(w)
    assert seen.get("hit") is True
    assert p["max_context_tokens"] == 5000
    comp = wm._compactor_for(w)
    assert comp.max_context_tokens == 5000
    assert comp.threshold == 0.33


def test_window_manager_without_resolver_falls_back():
    from dogbuddy.services.window_manager import WindowManager
    wm = WindowManager(None, llm_factory=lambda *a, **k: None, bus=None,
                       max_iterations=25, threshold=0.8, max_context_tokens=128000)
    w = _win()
    p = wm.params_for(w)
    assert p["max_iterations"] == 25 and p["max_context_tokens"] == 128000


def test_profile_registry_overrides_provider(tmp_path):
    """overrides 进 cache key；temperature/timeout 生效到 provider。"""
    from dogbuddy.config import LLMConfig
    from dogbuddy.llm.profile_registry import LLMProfileRegistry
    cfg = AppConfig(llm=LLMConfig(api_key="sk-1", model="m1"))
    reg = LLMProfileRegistry(cfg)
    a = reg.get(None, None, {"temperature": 0.1})
    b = reg.get(None, None, {"temperature": 0.9})
    c = reg.get(None, None, {"temperature": 0.1})
    assert a is not b          # 不同 overrides 不共享
    assert a is c              # 相同 overrides 复用
    assert a.temperature == 0.1
    assert b.temperature == 0.9
    assert reg.get(None, None).temperature == cfg.llm.temperature  # 无覆盖走配置


def test_profile_registry_retry_override(tmp_path):
    from dogbuddy.config import LLMConfig
    from dogbuddy.llm.profile_registry import LLMProfileRegistry
    cfg = AppConfig(llm=LLMConfig(api_key="sk-1", model="m1"))
    reg = LLMProfileRegistry(cfg)
    p = reg.get(None, None, {"retry": {"max_retries": 1}})
    assert p.retry.max_retries == 1
    assert reg.get(None, None).retry.max_retries == 5
