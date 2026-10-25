# DogBuddyCn — 模型目录导出/粘贴导入（v0.10.57）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""owner：「加一个可以将模型列表导出的功能，这样下次导入可以直接粘贴 json 文本。注意保护密钥。」

安全属性（这次改动的核心，全部进测试）：
  ① 导出物里**永远没有明文密钥**（只给 has_api_key / env 引用名）；
  ② 把导出的 JSON 原样导回去，**不会冲掉已有密钥**；
  ③ 掩码串（`sk-***abcd`）当"不改"处理（前端会把掩码原样回传）；
  ④ `api_key_ref: env:VAR` 从环境变量取值，取不到就告警并保留旧值；
  ⑤ `replace` 才会删清单外条目，默认 `merge` 只增改；
  ⑥ 审计只记数量，不记密钥。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.services.model_service import ModelService, mask_key  # noqa: E402

REAL_KEY = "sk-real-secret-abcdef123456"


def _svc(tmp_path: Path) -> tuple[ModelService, Database]:
    db = Database(str(tmp_path / "m.db"))
    return ModelService(db), db


def _seed(svc: ModelService) -> str:
    entry = svc.upsert(provider="sensenova", model="nova-pro",
                       base_url="https://api.example.com/v1", display_name="Nova Pro",
                       api_key=REAL_KEY, enabled=True, note="主力")
    return entry["id"]


# --------------------------------------------------------------- ① 导出不泄密
def test_export_never_contains_plaintext_key(tmp_path):
    svc, db = _svc(tmp_path)
    _seed(svc)
    dumped = json.dumps(svc.export_models(), ensure_ascii=False)
    assert REAL_KEY not in dumped, "导出物里出现了明文密钥！"
    assert "abcdef123456" not in dumped
    payload = svc.export_models()
    item = payload["models"][0]
    assert item["has_api_key"] is True
    assert "api_key" not in item, "导出不该带 api_key 字段（哪怕是掩码）"
    assert payload["kind"] == "dogbuddy.models" and payload["version"] == 1
    db.close()


def test_export_keeps_env_reference_name_only(tmp_path, monkeypatch):
    monkeypatch.setenv("DOGBUDDY_TEST_KEY", "sk-from-env-999")
    svc, db = _svc(tmp_path)
    svc.upsert(provider="openai_compat", model="m1", api_key="env:DOGBUDDY_TEST_KEY")
    item = svc.export_models()["models"][0]
    assert item["api_key_ref"] == "env:DOGBUDDY_TEST_KEY"
    assert "sk-from-env-999" not in json.dumps(item)
    db.close()


# --------------------------------------------------- ② 导出→导入 不冲掉密钥
def test_roundtrip_keeps_existing_key(tmp_path):
    svc, db = _svc(tmp_path)
    mid = _seed(svc)
    payload = svc.export_models()
    res = svc.import_models(payload["models"], mode="merge")
    assert res["updated"] == 1 and res["created"] == 0
    row = db.get_model(mid)
    assert row["api_key"] == REAL_KEY, "导出再导入把密钥冲掉了！"
    assert row["display_name"] == "Nova Pro"
    db.close()


# ------------------------------------------------------------- ③ 掩码 = 不改
def test_masked_key_is_treated_as_unchanged(tmp_path):
    svc, db = _svc(tmp_path)
    mid = _seed(svc)
    # 前端回传的就是这种形状（`mask_key` 是唯一的掩码口径）
    res = svc.import_models([{"provider": "sensenova", "model": "nova-pro",
                              "api_key": mask_key(REAL_KEY)}], mode="merge")
    assert res["updated"] == 1
    assert db.get_model(mid)["api_key"] == REAL_KEY, "掩码被当成真密钥写进库了"
    db.close()


# --------------------------------------------------------- ④ env 引用解析
def test_env_ref_sets_key_when_present(tmp_path, monkeypatch):
    monkeypatch.setenv("MK_ONE", "sk-env-value-1")
    svc, db = _svc(tmp_path)
    svc.import_models([{"provider": "openai_compat", "model": "mx",
                        "api_key_ref": "env:MK_ONE"}], mode="merge")
    rows = db.list_models()
    assert rows[0]["api_key"] == "sk-env-value-1"
    db.close()


def test_missing_env_ref_warns_and_keeps_old_key(tmp_path):
    svc, db = _svc(tmp_path)
    mid = _seed(svc)
    res = svc.import_models([{"provider": "sensenova", "model": "nova-pro",
                              "api_key_ref": "env:NOT_SET_AT_ALL"}], mode="merge")
    assert any("NOT_SET_AT_ALL" in w for w in res["warnings"])
    assert db.get_model(mid)["api_key"] == REAL_KEY
    db.close()


# --------------------------------------------------- ⑤ merge vs replace
def test_new_model_without_key_is_reported(tmp_path):
    svc, db = _svc(tmp_path)
    res = svc.import_models([{"provider": "openai_compat", "model": "fresh",
                              "display_name": "Fresh"}], mode="merge")
    assert res["created"] == 1 and res["need_key"] == 1
    db.close()


def test_replace_deletes_entries_not_in_payload(tmp_path):
    svc, db = _svc(tmp_path)
    _seed(svc)
    svc.upsert(provider="openai_compat", model="keep-me", api_key="sk-x")
    svc.import_models([{"provider": "openai_compat", "model": "keep-me"}], mode="replace")
    names = {r["model"] for r in db.list_models()}
    assert names == {"keep-me"}, f"replace 应该只留清单里的，实际 {names}"
    db.close()


def test_empty_payload_is_rejected(tmp_path):
    svc, db = _svc(tmp_path)
    for bad in ([], None):
        try:
            svc.import_models(bad, mode="merge")  # type: ignore[arg-type]
        except ValueError as e:
            assert "非空" in str(e)
        else:
            raise AssertionError("空清单必须拒绝（否则 replace 会把目录删空）")
    db.close()


# ------------------------------------------------------------- ⑥ 审计不泄密
def test_frontend_has_export_and_paste_import():
    """控制台：两个入口都在，且导出按钮的文案明确"不含密钥"。"""
    html = (Path(__file__).resolve().parents[1]
            / "dogbuddy/web/static/index.html").read_text(encoding="utf-8")
    js = (Path(__file__).resolve().parents[1]
          / "dogbuddy/web/static/js/views-models.js").read_text(encoding="utf-8")
    api = (Path(__file__).resolve().parents[1]
           / "dogbuddy/web/static/js/api.js").read_text(encoding="utf-8")
    assert 'id="mdl-export"' in html and 'id="mdl-import-json"' in html
    assert "不含密钥" in html, "按钮上就要写清导出不含密钥"
    assert "function exportJson()" in js and "function importJson()" in js
    assert "/api/models/export" in api and "/api/models/import-json" in api
    # 覆盖模式要二次确认（`replace` 会删条目）
    assert "覆盖模式会删除清单外的所有模型条目" in js


def test_audit_counts_only(tmp_path):
    svc, db = _svc(tmp_path)
    _seed(svc)
    svc.import_models([{"provider": "sensenova", "model": "nova-pro"}], mode="merge")
    rows = [r for r in db._conn.execute(
        "select action, detail from audit_log where action='models:import'")]
    assert rows, "导入必须留审计"
    detail = rows[0][1]
    assert "mode=merge" in detail and "upd=1" in detail
    assert REAL_KEY not in detail and "sk-" not in detail
    db.close()
