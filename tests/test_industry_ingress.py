# DogBuddyCn — 工业信号与事件接入（0.13.0）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""这一版的红线必须被测试钉住：

  ① 指标只有"名字+数字"，不收图、不收原始报文；
  ② `sensor_read` **拒绝陈旧数据**（`max_age_s` 超了就让这一步失败，
     而不是拿 3 小时前的温度去调参数）；
  ③ `cv_detect` **只传 image_ref**（不传像素），返回被规范化，`image_ref` 落库前截断；
  ④ 判定进成本账本（`images=1`），且记账失败不影响判定结果；
  ⑤ 边缘事件只做一件事：变成一次流水线运行。
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.services.cv_adapter import CvAdapter  # noqa: E402
from dogbuddy.services.pipeline_service import PipelineService  # noqa: E402
from dogbuddy.services.sensor_hub import SensorHub  # noqa: E402
from dogbuddy.web.routes.industry import router as industry_router  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture()
def env(tmp_path):
    db = Database(str(tmp_path / "i.db"))
    hub = SensorHub(db)
    yield hub, db, tmp_path
    db.close()


# ------------------------------------------------------------------ 指标
def test_push_and_latest(env):
    hub, db, _ = env
    hub.push("line1.temp", 61.5, unit="C", source="edge-01")
    hub.push("line1.temp", 63.0, unit="C", source="edge-01")
    hub.push("line1.rate", 42, unit="pcs/min")
    latest = {r["name"]: r for r in hub.latest()}
    assert latest["line1.temp"]["value"] == 63.0, "取最新一条"
    assert latest["line1.rate"]["unit"] == "pcs/min"
    assert hub.latest("line1.temp")["value"] == 63.0
    assert hub.latest("nope") is None
    assert len(hub.history("line1.temp", limit=10)) == 2


def test_push_validates_name(env):
    hub, db, _ = env
    with pytest.raises(ValueError):
        hub.push("", 1)
    with pytest.raises(ValueError):
        hub.push("x" * 200, 1)


def test_health_flags_stale_metric(env):
    hub, db, _ = env
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    hub.push("line1.temp", 60, ts=old)
    hub.push("line1.rate", 40)
    h = hub.health()
    assert h["metrics"] == 2
    assert "line1.temp" in h["stale_over_1h"]


# -------------------------------------------------------------- 读指标步骤
def test_sensor_read_step_ok(env):
    hub, db, tmp_path = env
    hub.push("line1.temp", 61.5, unit="C")
    svc = PipelineService(db, sensors=hub, workspace_root=str(tmp_path / "ws"))
    svc.upsert({"id": "p", "steps": [{"id": "read", "action": "sensor_read",
                                     "params": {"name": "line1.temp"}}]})
    run = svc.start("p")
    st = _run(svc.tick(run["id"]))
    assert st["run"]["status"] == "done"
    out = next(s for s in st["steps"])["output"]
    assert out["value"] == 61.5 and out["unit"] == "C"
    assert out["age_s"] >= 0


def test_sensor_read_missing_metric_fails_clearly(env):
    hub, db, tmp_path = env
    svc = PipelineService(db, sensors=hub, workspace_root=str(tmp_path / "ws"))
    svc.upsert({"id": "p", "steps": [{"id": "read", "action": "sensor_read",
                                     "params": {"name": "line9.temp"}}]})
    run = svc.start("p")
    st = _run(svc.tick(run["id"]))
    assert st["run"]["status"] == "failed"
    assert "还没有任何数据" in next(s for s in st["steps"])["error"]


def test_sensor_read_rejects_stale_data(env):
    hub, db, tmp_path = env
    old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    hub.push("line1.temp", 70, ts=old)
    svc = PipelineService(db, sensors=hub, workspace_root=str(tmp_path / "ws"))
    svc.upsert({"id": "p", "steps": [{"id": "read", "action": "sensor_read",
                                     "params": {"name": "line1.temp", "max_age_s": 60}}]})
    run = svc.start("p")
    st = _run(svc.tick(run["id"]))
    assert st["run"]["status"] == "failed"
    assert "已过期" in next(s for s in st["steps"])["error"]


def test_sensor_read_multiple_names(env):
    hub, db, tmp_path = env
    hub.push("a", 1)
    hub.push("b", 2)
    svc = PipelineService(db, sensors=hub, workspace_root=str(tmp_path / "ws"))
    svc.upsert({"id": "p", "steps": [{"id": "read", "action": "sensor_read",
                                     "params": {"names": ["a", "b"]}}]})
    out = _run(svc.tick(svc.start("p")["id"]))["steps"][0]["output"]
    assert set(out) == {"a", "b"} and out["b"]["value"] == 2


# ------------------------------------------------------------ 视觉服务
class _FakeCost:
    def __init__(self):
        self.calls = []

    def record(self, **kw):
        self.calls.append(kw)
        return {"stored": True}


def test_cv_requires_configuration():
    cv = CvAdapter(base_url="")
    with pytest.raises(RuntimeError) as exc:
        _run(cv.detect({"image_ref": "x"}, {}))
    assert "未配置视觉服务" in str(exc.value)


def test_cv_requires_image_ref():
    cv = CvAdapter(base_url="http://cv.local")
    with pytest.raises(RuntimeError) as exc:
        _run(cv.detect({}, {}))
    assert "需要 image_ref" in str(exc.value)


def test_cv_normalizes_and_records_cost():
    cost = _FakeCost()

    async def fake(url, payload, timeout):
        assert url == "http://cv.local/detect"
        assert payload["image_ref"] == "edge://cam1/frame-001"   # 只传引用
        return {"class": "dent", "score": "0.91", "boxes": [{"x": 1}, {"x": 2}]}

    cv = CvAdapter(base_url="http://cv.local", transport=fake, cost=cost)
    out = _run(cv.detect({"image_ref": "edge://cam1/frame-001"}, {}))
    assert out["label"] == "dent"
    assert out["confidence"] == 0.91
    assert out["defect_count"] == 2
    assert out["latency_ms"] >= 0
    assert cost.calls and cost.calls[0]["images"] == 1 and cost.calls[0]["kind"] == "cv"


def test_cv_truncates_long_image_ref():
    async def fake(url, payload, timeout):
        return {"label": "ok", "confidence": 0.99}

    long_ref = "edge://cam1/" + "a" * 120
    cv = CvAdapter(base_url="http://cv.local", transport=fake)
    out = _run(cv.detect({"image_ref": long_ref}, {}))
    assert len(out["image_ref"]) < len(long_ref) and "…" in out["image_ref"]


def test_cv_step_failure_is_visible(env):
    hub, db, tmp_path = env

    async def boom(url, payload, timeout):
        raise RuntimeError("连不上视觉服务（Connection refused）")

    cv = CvAdapter(base_url="http://cv.local", transport=boom)
    svc = PipelineService(db, cv=cv, workspace_root=str(tmp_path / "ws"))
    svc.upsert({"id": "p", "steps": [{"id": "d", "action": "cv_detect",
                                     "params": {"image_ref": "r1"}}]})
    st = _run(svc.tick(svc.start("p")["id"]))
    assert st["run"]["status"] == "failed"
    assert "连不上视觉服务" in next(s for s in st["steps"])["error"]


def test_cv_step_ok_in_pipeline(env):
    hub, db, tmp_path = env

    async def fake(url, payload, timeout):
        return {"label": "scratch", "confidence": 0.88, "defects": [{"x": 3}]}

    cv = CvAdapter(base_url="http://cv.local", transport=fake)
    svc = PipelineService(db, cv=cv, workspace_root=str(tmp_path / "ws"))
    svc.upsert({"id": "p", "steps": [{"id": "d", "action": "cv_detect",
                                     "params": {"image_ref": "r1"}}]})
    st = _run(svc.tick(svc.start("p")["id"]))
    assert st["run"]["status"] == "done"
    assert st["steps"][0]["output"]["label"] == "scratch"


# ------------------------------------------------------------------ 接口
@pytest.fixture()
def client(env):
    hub, db, tmp_path = env
    pl = PipelineService(db, sensors=hub, workspace_root=str(tmp_path / "ws"))
    app = FastAPI()
    app.include_router(industry_router(
        SimpleNamespace(metrics=hub, pipelines=pl), []), prefix="/api")
    return TestClient(app), hub, pl


def test_metric_routes(client):
    c, hub, pl = client
    assert c.post("/api/metrics/push", json={"name": "line1.temp", "value": 62.5,
                                             "unit": "C"}).status_code == 200
    assert c.post("/api/metrics/push", json={"name": "", "value": 1}).status_code == 400
    body = c.get("/api/metrics").json()["metrics"]
    assert body[0]["name"] == "line1.temp" and body[0]["value"] == 62.5
    hist = c.get("/api/metrics/line1.temp/history?limit=5").json()
    assert len(hist["points"]) == 1


def test_console_industry_card_wired():
    root = Path(__file__).resolve().parents[1] / "dogbuddy/web/static"
    idx = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "js/views-costs.js").read_text(encoding="utf-8")
    api = (root / "js/api.js").read_text(encoding="utf-8")
    assert 'id="cost-industry"' in idx
    assert "function renderIndustry()" in js and "function loadIndustry()" in js
    assert "/api/metrics" in api and "/api/qc/batches" in api and "/api/pipelines/runs" in api
    # 过期指标必须显式标红（"数据可能断了"是现场第一故障）
    assert "3600" in js and "数据可能断了" in js


def test_ingress_creates_pipeline_run(client):
    c, hub, pl = client
    pl.upsert({"id": "line-a", "steps": [{"id": "s", "action": "noop"}]})
    r = c.post("/api/ingress/events", json={
        "pipeline_id": "line-a", "line": "L1",
        "items": [{"ref": "img1"}, {"ref": "img2"}], "wait": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted"] == 2 and body["status"] == "done"
    # 事件内容进了 run 的 payload
    run = pl.status(body["run_id"])["run"]
    assert "img1" in run["input_json"]
    # 未知流水线 → 400
    assert c.post("/api/ingress/events",
                  json={"pipeline_id": "nope"}).status_code == 400
