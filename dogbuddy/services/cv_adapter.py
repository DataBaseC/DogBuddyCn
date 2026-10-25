# DogBuddyCn — 本地视觉服务适配器（0.13.0）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""判定交给**厂内**的视觉服务，DogBuddy 只负责调用、记录和解释。

三条红线在这里落地：
  1. **图像不出厂**：我们只传 `image_ref`（边缘本地路径/句柄），从不传像素；
     `image_ref` 也不落库到日志里（只留前 80 字符，避免把内网路径写进审计）。
  2. **LLM 不做像素判定**：这条链路只走 `/detect`，返回 label/confidence/defects。
  3. **每次判定都记账**：`images=1` 进成本账本（单价表里给本地 CV 配一条即可）。

可测试性：`transport` 可注入（默认用 urllib 在线程里跑），测试里换成一个假函数
就能覆盖"超时/错误响应/字段缺失"这几种真实世界的样子。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Awaitable, Callable

logger = logging.getLogger("dogbuddy.services.cv")

DEFAULT_TIMEOUT = 15.0
DEFAULT_MODEL = "local-cv"


class CvAdapter:
    def __init__(self, base_url: str = "", *, timeout: float = DEFAULT_TIMEOUT,
                 transport: Callable[[str, dict, float], Awaitable[dict]] | None = None,
                 cost: Any = None) -> None:
        #: 优先级：步骤参数 > 构造参数 > 环境变量
        self.base_url = str(base_url or os.environ.get("DOGBUDDY_CV_URL") or "").rstrip("/")
        self.timeout = float(timeout or DEFAULT_TIMEOUT)
        self._transport = transport or self._http_post
        self.cost = cost

    def configured(self, override: str = "") -> bool:
        return bool(override or self.base_url)

    # ------------------------------------------------------------ 传输
    @staticmethod
    async def _http_post(url: str, payload: dict, timeout: float) -> dict:
        def _do() -> dict:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode() or "{}")

        try:
            return await asyncio.to_thread(_do)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:200]
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(f"视觉服务返回 {e.code}：{detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"连不上视觉服务（{e.reason}）") from e

    # ------------------------------------------------------------ 判定
    async def detect(self, params: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        url_base = str(params.get("base_url") or self.base_url).rstrip("/")
        if not url_base:
            raise RuntimeError(
                "cv_detect 未配置视觉服务：在步骤里给 params.base_url，"
                "或设置环境变量 DOGBUDDY_CV_URL")
        image_ref = str(params.get("image_ref") or (payload or {}).get("image_ref") or "")
        if not image_ref:
            raise RuntimeError("cv_detect 需要 image_ref（边缘本地引用，不是像素）")
        body = {"image_ref": image_ref,
                "threshold": float(params.get("threshold", 0.5)),
                "model": str(params.get("model") or DEFAULT_MODEL)}
        if params.get("options"):
            body["options"] = params["options"]
        t0 = time.monotonic()
        raw = await asyncio.wait_for(
            self._transport(url_base + "/detect", body, self.timeout),
            timeout=self.timeout + 5)
        latency = round((time.monotonic() - t0) * 1000, 1)
        out = self._normalize(raw, image_ref, latency)
        self._record_cost(out, params)
        return out

    @staticmethod
    def _normalize(raw: dict[str, Any], image_ref: str, latency_ms: float) -> dict[str, Any]:
        """把各家视觉服务的返回收敛成同一形状（不许把原始响应直接透传）。"""
        if not isinstance(raw, dict):
            raise RuntimeError(f"视觉服务返回的不是对象：{type(raw).__name__}")
        conf = raw.get("confidence", raw.get("score", 0))
        try:
            conf = float(conf)
        except Exception:  # noqa: BLE001
            conf = 0.0
        defects = raw.get("defects") or raw.get("boxes") or []
        if not isinstance(defects, list):
            defects = []
        return {"label": str(raw.get("label") or raw.get("class") or "unknown"),
                "confidence": round(conf, 4),
                "defects": defects[:50],
                "defect_count": len(defects),
                "latency_ms": latency_ms,
                # 路径只留头尾，别把内网全路径写进账本/审计
                "image_ref": (image_ref[:40] + "…" + image_ref[-20:]) if len(image_ref) > 64
                else image_ref,
                "model": str(raw.get("model") or DEFAULT_MODEL)}

    def _record_cost(self, out: dict[str, Any], params: dict[str, Any]) -> None:
        if self.cost is None:
            return
        try:
            self.cost.record(kind="cv", provider="local", model=out["model"],
                             images=1, idem_key="")
        except Exception:  # noqa: BLE001 — 记账失败不能影响判定结果
            logger.debug("cv cost record failed", exc_info=True)

    def health(self) -> dict[str, Any]:
        return {"configured": self.configured(), "base_url": self.base_url,
                "timeout": self.timeout,
                "hint": "" if self.configured() else
                "未配置：设置 DOGBUDDY_CV_URL 或在流水线步骤里给 params.base_url"}
