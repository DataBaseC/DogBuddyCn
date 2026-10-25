# DogBuddyCn — 工业信号接入（0.13.0）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""边缘只推**数字**，我们只读**最新值** —— 这是"数据不出厂"的最小实现。

为什么是 push 而不是我们去拉：
  * 产线网络通常只允许**单向**（边缘 → 管理侧），反向访问要开洞；
  * 设备协议五花八门（Modbus/OPC-UA/MQTT），我们不该做协议栈；
  * 边缘侧本来就有网关，把值推上来最省事（`POST /api/metrics/push`）。

一条硬规则：`sensor_read` **必须能判断数据是否过期**（`max_age_s`）。
拿 3 小时前的温度去调参数，比没有数据更危险——所以宁可让这一步失败。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("dogbuddy.services.sensors")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _age_seconds(ts: str) -> float:
    try:
        when = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - when).total_seconds())
    except Exception:  # noqa: BLE001 — 时间解析不了就当"很旧"
        return float("inf")


class SensorHub:
    def __init__(self, db: Any) -> None:
        self.db = db

    # ------------------------------------------------------------ 写入
    def push(self, name: str, value: float, *, unit: str = "", source: str = "",
             ts: str = "", meta: dict[str, Any] | None = None) -> dict[str, Any]:
        name = str(name or "").strip()
        if not name:
            raise ValueError("指标名不能为空")
        if len(name) > 120:
            raise ValueError("指标名太长（>120）")
        meta_json = ""
        if meta:
            try:
                meta_json = json.dumps(meta, ensure_ascii=False)[:4000]
            except Exception:  # noqa: BLE001
                meta_json = ""
        return self.db.push_metric({"name": name, "value": float(value or 0),
                                    "unit": unit, "source": source,
                                    "ts": ts or _now(), "meta_json": meta_json})

    # ------------------------------------------------------------ 读取
    def latest(self, name: str = "") -> list[dict[str, Any]] | dict[str, Any] | None:
        if name:
            rows = self.db.latest_metrics([name])
            return rows[0] if rows else None
        return self.db.latest_metrics()

    def history(self, name: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.db.metric_history(name, limit=limit)

    async def read(self, params: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        """流水线 `sensor_read` 步骤：取最新值，并**校验新鲜度**。"""
        # 支持一次读多个：params.names 优先，其次 params.name，最后用 payload 里的
        names = params.get("names") or ([params["name"]] if params.get("name") else [])
        if not names:
            raise RuntimeError("sensor_read 需要 params.name 或 params.names")
        max_age = params.get("max_age_s")
        out: dict[str, Any] = {}
        for raw in names:
            name = str(raw)
            row = self.latest(name)
            if row is None:
                raise RuntimeError(
                    f"指标 {name} 还没有任何数据：让边缘先 POST /api/metrics/push")
            age = _age_seconds(str(row.get("ts") or ""))
            if max_age is not None and age > float(max_age):
                raise RuntimeError(
                    f"指标 {name} 已过期：{age:.0f}s 前（上限 {float(max_age):.0f}s）"
                    f"—— 拒绝用陈旧数据做决策")
            out[name] = {"value": row.get("value"), "unit": row.get("unit") or "",
                         "ts": row.get("ts"), "age_s": round(age, 1),
                         "source": row.get("source") or ""}
        return out if params.get("names") else out.get(str(names[0]), {})

    # ------------------------------------------------------------ 概览
    def health(self) -> dict[str, Any]:
        latest = self.db.latest_metrics()
        stale = [r["name"] for r in latest
                 if _age_seconds(str(r.get("ts") or "")) > 3600]
        return {"metrics": len(latest), "names": [r["name"] for r in latest][:20],
                "stale_over_1h": stale}
