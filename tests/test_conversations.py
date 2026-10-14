# DogBuddyCn — 单管理员 + 多对话（v2 口径，2026-10-14 用户拍板）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""用户口径：**只有一个管理员**与用户对接；**用户可以选择新建对话**。

"新建对话"的意义：换一件事就开一个对话，各自独立上下文、互不串味——
而不是把所有历史挤在一条会话里让管理员反复被无关内容干扰。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig  # noqa: E402
from dogbuddy.db import SCHEMA_VERSION, Database  # noqa: E402


def _app(tmp_path, monkeypatch, token="tok"):
    from dogbuddy.main import DogBuddyApp
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(llm=LLMConfig(base_url="", api_key="", model=""),
                    web=WebConfig(auth_token=token, port=8140),
                    paths=PathsConfig(data_dir=str(tmp_path / "data"),
                                      workspaces_dir=str(tmp_path / "ws")))
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    client = TestClient(app.web_app)
    client.headers["X-Auth-Token"] = token
    return app, client


# --------------------------------------------------------------------------- schema
def test_迁移_v9_建对话表且幂等(tmp_path):
    assert SCHEMA_VERSION >= 9
    p = tmp_path / "a.db"
    db = Database(p)
    try:
        assert db.schema_version == SCHEMA_VERSION
        cols = {r[1] for r in db._conn.execute("PRAGMA table_info(conversations)")}
        assert {"id", "title", "archived", "created_at", "updated_at",
                "last_message_at"} <= cols
        assert db.list_conversations() == []
    finally:
        db.close()
    db2 = Database(p)
    try:
        assert db2.schema_version == SCHEMA_VERSION and db2.list_conversations() == []
    finally:
        db2.close()


def test_默认对话自愈(tmp_path, monkeypatch):
    """升级后/删光后必须仍有一个可用对话，用户不会面对空白。"""
    app, _client = _app(tmp_path, monkeypatch)
    svc = app.admin
    convs = svc.list_conversations()
    assert len(convs) == 1 and convs[0]["id"] == svc.DEFAULT_CONVERSATION
    # 删掉唯一一个 → 自愈出新的
    assert svc.delete_conversation(svc.DEFAULT_CONVERSATION) is True
    assert len(svc.list_conversations()) == 1


def test_接管老版本会话历史(tmp_path, monkeypatch):
    """升级自愈：v9 之前会话记在 `admin:console`，接管后**历史不能看起来丢了**。

    这是真事——线上生产库升级时正好有 28 条历史挂在 admin:console 下。
    """
    app, _client = _app(tmp_path, monkeypatch)
    # 造出"老版本残留"：对话表清空，历史只挂在 admin:console / admin:system
    app.db._conn.execute("DELETE FROM conversations")
    app.db._conn.commit()
    for i in range(5):
        app.db.add_message("admin:console", "user", f"第 {i} 条老消息")
    app.db.add_message("admin:console", "assistant", "老回复")
    app.db.add_message("admin:system", "user", "[system event] 噪音")

    convs = app.admin.list_conversations()
    assert len(convs) == 1, f"应恰好接管出一个对话，实际 {[c['id'] for c in convs]}"
    conv = convs[0]
    assert conv["id"] == "console", "要接管消息最多的那个会话，而不是新建空的"
    assert conv["title"] == "默认对话"
    assert conv["message_count"] == 6, "历史条数必须完整"
    assert conv["preview"] == "第 4 条老消息"
    assert all(c["id"] != "system" for c in convs), "系统通知会话不是用户的对话"
    assert len(app.admin.history("console", 50)) == 6


def test_没有老会话时用默认对话(tmp_path, monkeypatch):
    app, _client = _app(tmp_path, monkeypatch)
    app.db._conn.execute("DELETE FROM conversations")
    app.db._conn.commit()
    convs = app.admin.list_conversations()
    assert len(convs) == 1 and convs[0]["id"] == "default"


def test_补登记老会话并清掉空占位(tmp_path, monkeypatch):
    """线上实测踩到的第二种形态：上一版已经先建了空的 default，
    老历史挂在 admin:console 下 → 必须**补登记** + 清掉空占位，
    否则用户打开对话页只看到一个空对话，以为历史丢了。"""
    app, _client = _app(tmp_path, monkeypatch)
    app.db._conn.execute("DELETE FROM conversations")
    app.db._conn.commit()
    app.db.insert_conversation("default", "默认对话")      # 上一版留下的空占位
    for i in range(3):
        app.db.add_message("admin:console", "user", f"老消息 {i}")

    convs = app.admin.list_conversations()
    ids = [c["id"] for c in convs]
    assert "console" in ids, f"老会话必须被补登记，实际 {ids}"
    assert "default" not in ids, f"空的占位对话应被清掉，实际 {ids}"
    assert [c["message_count"] for c in convs if c["id"] == "console"] == [3]
    # 幂等：再调一次不重复、也不误删
    assert app.admin.sync_legacy_conversations() == 0
    assert [c["id"] for c in app.admin.list_conversations()] == ids


def test_空的手建对话不会被误删(tmp_path, monkeypatch):
    """用户刚点"新建对话"还没说话 → 不能因为"0 条消息"就被清掉。"""
    app, _client = _app(tmp_path, monkeypatch)
    conv = app.admin.create_conversation("我的新对话")
    assert app.admin.sync_legacy_conversations() == 0
    ids = [c["id"] for c in app.admin.list_conversations()]
    assert conv["id"] in ids, f"用户手建的对话被误删了：{ids}"


# --------------------------------------------------------------------------- 新建/命名
def test_新建对话_自动编号(tmp_path, monkeypatch):
    app, _client = _app(tmp_path, monkeypatch)
    if True:
        svc = app.admin
        svc.ensure_default_conversation()
        a = svc.create_conversation()
        b = svc.create_conversation()
        assert a["id"] != b["id"]
        titles = {c["title"] for c in svc.list_conversations()}
        assert a["title"] == "对话 1" and b["title"] == "对话 2", titles
        # 指定标题
        c = svc.create_conversation("登录功能")
        assert c["title"] == "登录功能"


def test_首条消息自动成为标题_不覆盖手改(tmp_path, monkeypatch):
    app, _client = _app(tmp_path, monkeypatch)
    if True:
        svc = app.admin
        conv = svc.create_conversation()          # 自动名"对话 N"
        svc._touch_conversation(conv["id"], "帮我给模型管理页加批量导入")
        assert svc.db.get_conversation(conv["id"])["title"].startswith("帮我给模型管理页")

        # 用户手动改名后，后续消息不能把它覆盖回去
        svc.rename_conversation(conv["id"], "批量导入")
        svc._touch_conversation(conv["id"], "再改一下别的")
        assert svc.db.get_conversation(conv["id"])["title"] == "批量导入"


def test_重命名与删除(tmp_path, monkeypatch):
    app, _client = _app(tmp_path, monkeypatch)
    if True:
        svc = app.admin
        svc.ensure_default_conversation()
        conv = svc.create_conversation("旧名")
        assert svc.rename_conversation(conv["id"], "新名")["title"] == "新名"
        with pytest.raises(KeyError):
            svc.rename_conversation("nope", "x")
        assert svc.delete_conversation(conv["id"]) is True
        assert svc.delete_conversation(conv["id"]) is False


# --------------------------------------------------------------------------- 上下文隔离
def test_对话之间的上下文互不串味(tmp_path, monkeypatch):
    """这是"新建对话"的核心价值：A 对话的消息不能出现在 B 对话里。"""
    app, _client = _app(tmp_path, monkeypatch)
    if True:
        svc = app.admin
        db = app.db
        svc.ensure_default_conversation()
        a = svc.create_conversation("A 对话")
        b = svc.create_conversation("B 对话")
        db.add_message(f"admin:{a['id']}", "user", "只有 A 能看到")
        db.add_message(f"admin:{b['id']}", "user", "只有 B 能看到")

        ha = [m["content"] for m in svc.history(a["id"], 50)]
        hb = [m["content"] for m in svc.history(b["id"], 50)]
        assert "只有 A 能看到" in ha and "只有 B 能看到" not in ha
        assert "只有 B 能看到" in hb and "只有 A 能看到" not in hb


def test_删对话连消息一起删(tmp_path, monkeypatch):
    app, _client = _app(tmp_path, monkeypatch)
    if True:
        svc = app.admin
        db = app.db
        svc.ensure_default_conversation()
        conv = svc.create_conversation("要删的")
        db.add_message(f"admin:{conv['id']}", "user", "这条也该被删")
        assert svc.history(conv["id"], 10)

        svc.delete_conversation(conv["id"])
        assert svc.history(conv["id"], 10) == [], "对话删了消息也要清掉"


def test_列表带消息数与最近用户消息(tmp_path, monkeypatch):
    app, _client = _app(tmp_path, monkeypatch)
    if True:
        svc = app.admin
        db = app.db
        svc.ensure_default_conversation()
        conv = svc.create_conversation("有内容的")
        db.add_message(f"admin:{conv['id']}", "user", "第一条")
        db.add_message(f"admin:{conv['id']}", "assistant", "回复")
        row = [c for c in svc.list_conversations() if c["id"] == conv["id"]][0]
        assert row["message_count"] == 2
        assert row["preview"] == "第一条"


# --------------------------------------------------------------------------- HTTP
def test_对话接口_需要鉴权(tmp_path, monkeypatch):
    app, client = _app(tmp_path, monkeypatch)
    with client:
        assert client.get("/api/admin/chat/conversations",
                          headers={"X-Auth-Token": ""}).status_code == 401


def test_对话接口_增改删查(tmp_path, monkeypatch):
    app, client = _app(tmp_path, monkeypatch)
    with client:
        listed = client.get("/api/admin/chat/conversations").json()
        assert len(listed["conversations"]) >= 1, "至少有一个默认对话"
        assert "只有一个管理员" in listed["note"]

        created = client.post("/api/admin/chat/conversations",
                              json={"title": "登录任务"}).json()
        cid = created["conversation"]["id"]
        assert created["conversation"]["title"] == "登录任务"
        assert len(created["conversations"]) == 2

        renamed = client.patch(f"/api/admin/chat/conversations/{cid}",
                               json={"title": "登录任务（新）"}).json()
        assert renamed["conversation"]["title"] == "登录任务（新）"
        assert client.patch("/api/admin/chat/conversations/nope",
                            json={"title": "x"}).status_code == 404

        assert client.delete(f"/api/admin/chat/conversations/{cid}").status_code == 200
        assert client.delete(f"/api/admin/chat/conversations/{cid}").status_code == 404


def test_消息接口_新老参数都认(tmp_path, monkeypatch):
    """老客户端传 sender、新客户端传 conversation，指向同一个对话。"""
    app, client = _app(tmp_path, monkeypatch)
    with client:
        app.db.add_message("admin:console", "user", "老参数可见")
        by_sender = client.get("/api/admin/chat/messages", params={"sender": "console"}).json()
        by_conv = client.get("/api/admin/chat/messages",
                             params={"conversation": "console"}).json()
        assert by_sender["conversation"] == "console"
        assert by_conv["conversation"] == "console"
        assert any("老参数可见" in m["content"] for m in by_sender["messages"])
        assert any("老参数可见" in m["content"] for m in by_conv["messages"])
        # 不传参数 → 默认对话
        assert client.get("/api/admin/chat/messages").json()["conversation"] == "default"


def test_单管理员声明(tmp_path, monkeypatch):
    app, client = _app(tmp_path, monkeypatch)
    with client:
        info = client.get("/api/admin/chat/admins").json()
        assert info["single_admin"] is True
        assert client.get("/api/admins").status_code == 404, "v2 没有多管理员接口"


def test_清空某个对话_不影响别的(tmp_path, monkeypatch):
    app, client = _app(tmp_path, monkeypatch)
    with client:
        keep = client.post("/api/admin/chat/conversations", json={"title": "保留"}).json()
        drop = client.post("/api/admin/chat/conversations", json={"title": "清空"}).json()
        kid, did = keep["conversation"]["id"], drop["conversation"]["id"]
        app.db.add_message(f"admin:{kid}", "user", "保留内容")
        app.db.add_message(f"admin:{did}", "user", "待清空内容")

        r = client.post("/api/admin/chat/reset", json={"conversation": did}).json()
        assert r["conversation"] == did and r["dropped"] >= 1
        assert client.get("/api/admin/chat/messages",
                          params={"conversation": did}).json()["messages"] == []
        left = client.get("/api/admin/chat/messages",
                          params={"conversation": kid}).json()["messages"]
        assert any("保留内容" in m["content"] for m in left), "别的对话不能被连带清空"


# ------------------------------------------------------- P0-3 数据丢失回归
def test_删对话不能误删同名真实工位的消息(tmp_path, monkeypatch):
    """审查报告 P0-3（不可逆数据丢失）。

    `delete_conversation` 初版用 `window_id IN (?,?)` 传 `(f"admin:{conv_id}", conv_id)`，
    而 `conv_id` 零校验 → `DELETE /api/admin/chat/conversations/<真实工位id>`
    会抹掉该工位的全部历史（工位仍在、历史空白、无法恢复）。

    注意：**必须让 `<id>` 作为真实工位存在**才测得到这个洞。原有测试只往
    `admin:<id>` 写消息，第二个占位符从不被触碰——这正是该 bug 能在
    375 条全绿测试下存活的原因。
    """
    from dogbuddy.db import Window, new_id

    app, _client = _app(tmp_path, monkeypatch)
    db = app.db
    wid = new_id()
    db.insert_window(Window(id=wid, name="真实工位", role="worker_programmer",
                            workspace_path="", model="", mode="BUILD"))
    for content in ("工位历史1", "工位历史2", "工位历史3"):
        db.add_message(wid, "user", content)
    assert len(db.list_messages(wid, 10)) == 3

    # 制造"该 id 也是一个对话行"的前置条件——初版只要 conv 行存在就放行
    db.insert_conversation(wid, "冒名对话")

    app.admin.delete_conversation(wid)

    assert len(db.list_messages(wid, 10)) == 3, "真实工位的消息绝不能被对话删除连带清掉"
    assert db.get_window(wid) is not None, "工位本身也不该受影响"


def test_删真实对话仍然会清掉它自己的消息(tmp_path, monkeypatch):
    """反向保证：修 P0-3 不能把"删对话就该清消息"这个正常能力也修没了。"""
    app, _client = _app(tmp_path, monkeypatch)
    db = app.db
    app.admin.ensure_default_conversation()
    conv = app.admin.create_conversation("要删的")
    cid = conv["id"]
    db.add_message(f"admin:{cid}", "user", "这条该被删")

    app.admin.delete_conversation(cid)

    assert db.list_messages(f"admin:{cid}", 10) == []
    # 未被真实工位兜底误伤
    assert db.get_conversation(cid) is None


def test_对话_id_白名单拒非法值(tmp_path, monkeypatch):
    """审查报告 P0-3：初版 `_conv_id` 只做 strip()，对话 id 可以为任意字符串。"""
    app, client = _app(tmp_path, monkeypatch)
    with client:
        bad = ["a b", "a/b", "../x", "x" * 65, "a:b", "a$b", "a.b", "对话"]
        for cid in bad:
            r = client.get("/api/admin/chat/messages", params={"conversation": cid})
            assert r.status_code == 400, f"非法对话 id 应 400: {cid!r} → {r.status_code}"
        # 合法值放行；空字符串按既有兜底 = 默认对话（不是错误）
        assert client.get("/api/admin/chat/messages",
                          params={"conversation": "ok-id_1"}).status_code == 200
        assert client.get("/api/admin/chat/messages",
                          params={"conversation": ""}).json()["conversation"] == "default"


def test_system_是保留频道不可被对话接口操作(tmp_path, monkeypatch):
    """`admin:system` 是员工提问/危险命令确认/逐级上报的落点，不是用户对话。

    初版 `POST /api/admin/chat/reset {"conversation":"system"}` 会把它清空，
    而那些消息不依赖 conversations 行（`reset` 走 `session_for()`）。
    """
    app, client = _app(tmp_path, monkeypatch)
    with client:
        app.db.add_message("admin:system", "user", "员工提问：要删库吗")
        assert client.get("/api/admin/chat/messages",
                          params={"conversation": "system"}).status_code == 400
        assert client.post("/api/admin/chat/reset",
                           json={"conversation": "system"}).status_code == 400
        assert client.delete("/api/admin/chat/conversations/system").status_code == 400
        assert client.patch("/api/admin/chat/conversations/system",
                            json={"title": "x"}).status_code == 400
        # 消息必须原封不动
        assert len(app.db.list_messages("admin:system", 10)) == 1

