# DogBuddyCn — 流水线编排（0.12.1）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""把"多步、有依赖、可续跑"做成一等公民。

为什么不能只靠一次 run：`runs` 是"一次员工回合"，中途挂掉只能**整轮重跑**——
重跑会重复花钱、重复改文件；而工业场景（取图 → 判定 → 复核 → 报告）必须能
回答三个问题：**哪步成了、产物在哪、下次从哪继续**。

这一版的边界（说清楚，免得后面走偏）：
  * 引擎只管**调度与记账**：谁该跑、跑没跑过、失败了几次；
  * 具体干活交给 `action`（`window_run` 派给工位 / `write_file` 落产物 /
    `cv_detect` 走本地视觉服务（0.13.0）/ `noop` 占位）；
  * **不做硬实时**：这是编排层，不是产线控制器。

幂等：步骤主键是 `run_id|step_id`，另有 `idem_key = hash(run_id, step, params)`。
重跑同一 run 时，`status=done` 且 `idem_key` 相同的步骤**直接跳过**——这是"断点续跑
不重复花钱/不重复写文件"的唯一保证。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("dogbuddy.services.pipeline")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return json.dumps(str(obj), ensure_ascii=False)


class PipelineError(ValueError):
    """spec 不合法（环、缺依赖、未知 action）—— 调用方应回 400。"""


class PipelineService:
    #: 可用的步骤动作。新增动作要同时改 `_run_step`（否则跑到就报错）。
    ACTIONS = ("noop", "write_file", "window_run", "cv_detect", "sensor_read")

    def __init__(self, db: Any, *, dispatch: Callable[..., Any] | None = None,
                 cv: Any = None, sensors: Any = None,
                 workspace_root: str = "") -> None:
        self.db = db
        #: async (window_id, prompt) -> str；由 main 注入 WindowManager.dispatch_and_wait
        self.dispatch = dispatch
        #: 0.13.0 才装配：本地视觉服务适配器 / 传感器读取器
        self.cv = cv
        self.sensors = sensors
        self.workspace_root = Path(workspace_root or ".").resolve()

    # ------------------------------------------------------------- 定义
    def _normalize(self, spec: dict[str, Any]) -> dict[str, Any]:
        """校验并补默认值。**环检测在这里**，不要把坏图存进库。"""
        if not isinstance(spec, dict):
            raise PipelineError("spec 必须是对象")
        steps = spec.get("steps")
        if not isinstance(steps, list) or not steps:
            raise PipelineError("spec.steps 必须是非空数组")
        seen: set[str] = set()
        clean: list[dict[str, Any]] = []
        for idx, raw in enumerate(steps, 1):
            if not isinstance(raw, dict):
                raise PipelineError(f"第 {idx} 个步骤不是对象")
            sid = str(raw.get("id") or "").strip()
            if not sid:
                raise PipelineError(f"第 {idx} 个步骤缺 id")
            if sid in seen:
                raise PipelineError(f"步骤 id 重复：{sid}")
            seen.add(sid)
            action = str(raw.get("action") or "noop").strip()
            if action not in self.ACTIONS:
                raise PipelineError(f"步骤 {sid} 的 action={action!r} 不支持（可选：{', '.join(self.ACTIONS)}）")
            needs = raw.get("needs") or []
            if not isinstance(needs, list):
                raise PipelineError(f"步骤 {sid} 的 needs 必须是数组")
            clean.append({"id": sid, "action": action,
                          "needs": [str(n) for n in needs],
                          "params": raw.get("params") or {},
                          "timeout": float(raw.get("timeout") or 600),
                          "retries": int(raw.get("retries") or 0),
                          "continue_on_error": bool(raw.get("continue_on_error"))})
        ids = {s["id"] for s in clean}
        for s in clean:
            for n in s["needs"]:
                if n not in ids:
                    raise PipelineError(f"步骤 {s['id']} 依赖了不存在的 {n}")
                if n == s["id"]:
                    raise PipelineError(f"步骤 {s['id']} 依赖自己")
        # 拓扑排序做环检测（Kahn）
        indeg = {s["id"]: len(s["needs"]) for s in clean}
        children: dict[str, list[str]] = {s["id"]: [] for s in clean}
        for s in clean:
            for n in s["needs"]:
                children[n].append(s["id"])
        queue = [sid for sid, n in indeg.items() if n == 0]
        visited = 0
        while queue:
            cur = queue.pop()
            visited += 1
            for child in children[cur]:
                indeg[child] -= 1
                if indeg[child] == 0:
                    queue.append(child)
        if visited != len(clean):
            raise PipelineError("步骤之间存在循环依赖")
        return {"steps": clean}

    def upsert(self, payload: dict[str, Any]) -> dict[str, Any]:
        """新建/更新一条流水线定义（`id` 缺省按名字生成）。"""
        name = str(payload.get("name") or "").strip()
        pid = str(payload.get("id") or "").strip()
        if not pid:
            if not name:
                raise PipelineError("id 与 name 至少给一个")
            pid = "pl-" + hashlib.sha1(name.encode()).hexdigest()[:8]
        spec = self._normalize(payload)
        saved = self.db.upsert_pipeline({
            "id": pid, "name": name or pid,
            "description": str(payload.get("description") or ""),
            "spec_json": _dumps({"steps": spec["steps"], **{
                k: payload[k] for k in ("name", "description") if k in payload}}),
            "enabled": 1 if payload.get("enabled", True) else 0,
        })
        try:
            self.db.audit("system", "pipeline:upsert",
                          f"{pid} steps={len(spec['steps'])}")
        except Exception:  # noqa: BLE001
            logger.debug("pipeline audit failed", exc_info=True)
        return saved

    def list(self) -> list[dict[str, Any]]:
        return self.db.list_pipelines()

    def get(self, pid: str) -> dict[str, Any] | None:
        return self.db.get_pipeline(pid)

    def delete(self, pid: str) -> bool:
        return self.db.delete_pipeline(pid)

    def spec_of(self, pipeline: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            blob = json.loads(pipeline.get("spec_json") or "{}")
        except Exception:  # noqa: BLE001
            return []
        steps = blob.get("steps") or []
        return [s for s in steps if isinstance(s, dict)]

    # ------------------------------------------------------------- 执行
    def start(self, pipeline_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """建一次 run（不在这里跑；调用方决定同步 tick 还是后台跑）。"""
        pipe = self.db.get_pipeline(str(pipeline_id))
        if pipe is None:
            raise PipelineError(f"流水线不存在：{pipeline_id}")
        if not int(pipe.get("enabled") or 0):
            raise PipelineError(f"流水线已停用：{pipeline_id}")
        rid = "pr-" + uuid.uuid4().hex[:12]
        run = self.db.create_pipeline_run({
            "id": rid, "pipeline_id": pipe["id"], "status": "running",
            "input_json": _dumps(payload or {}), "started_at": _now()})
        try:
            self.db.audit("system", "pipeline:start", f"{rid} {pipe['id']}")
        except Exception:  # noqa: BLE001
            logger.debug("pipeline audit failed", exc_info=True)
        return run

    def _idem(self, run_id: str, step_id: str, params: dict[str, Any]) -> str:
        blob = f"{run_id}|{step_id}|{_dumps(params)}".encode()
        return hashlib.sha256(blob).hexdigest()[:16]

    async def tick(self, run_id: str, max_steps: int = 30) -> dict[str, Any]:
        """把当前**能跑**的步骤跑到跑不动为止（幂等：done 且 idem 相同就跳过）。"""
        run = self.db.get_pipeline_run(run_id)
        if run is None:
            raise PipelineError(f"run 不存在：{run_id}")
        if run["status"] in ("done", "failed", "cancelled"):
            return self.status(run_id)
        pipe = self.db.get_pipeline(str(run["pipeline_id"]))
        if pipe is None:
            self.db.update_pipeline_run(run_id, status="failed", error="流水线定义已删除",
                                        finished_at=_now())
            return self.status(run_id)
        spec = self.spec_of(pipe)
        payload = {}
        try:
            payload = json.loads(run.get("input_json") or "{}")
        except Exception:  # noqa: BLE001
            payload = {}
        executed = 0
        for _ in range(max(1, int(max_steps))):
            rows = {r["step_id"]: r for r in self.db.list_pipeline_steps(run_id)}
            done = {sid for sid, r in rows.items() if r["status"] == "done"}
            failed = {sid for sid, r in rows.items() if r["status"] == "failed"}
            # `continue_on_error` 的失败步骤算"满足依赖"：下游照样能跑（比如
            # "报告"步骤不该因为"抽检"步骤失败而整条停掉）。
            tolerated = {sid for sid in failed
                         if self._step_spec(spec, sid).get("continue_on_error")}
            satisfied = done | tolerated
            if len(satisfied) == len(spec):
                self.db.update_pipeline_run(
                    run_id, status="done", finished_at=_now(),
                    output_json=_dumps({sid: self._output_of(rows[sid])
                                        for sid in sorted(satisfied) if sid in rows}))
                return self.status(run_id)
            # 注意：`failed` 也要排除，否则失败步骤会在同一 tick 里被反复重试
            # （重试语义是"下一次 resume"，不是"立刻再来一遍" —— 立刻重试只会
            #  把同一个错误刷 30 次，还让 run 永远停在 running）。
            ready = [s for s in spec
                     if s["id"] not in satisfied and s["id"] not in failed
                     and all(n in satisfied for n in s["needs"])]
            if not ready:
                hard_failed = [sid for sid in failed
                               if not self._step_spec(spec, sid).get("continue_on_error")]
                status = "failed" if hard_failed else ("done" if failed else "blocked")
                self.db.update_pipeline_run(
                    run_id, status=status, finished_at=_now(),
                    error=("失败步骤：" + ",".join(hard_failed)) if hard_failed else "")
                return self.status(run_id)
            step = ready[0]
            await self._exec_step(run_id, step, rows.get(step["id"]), payload)
            executed += 1
        logger.info("pipeline tick hit max_steps=%s (run=%s, executed=%s)",
                    max_steps, run_id, executed)
        return self.status(run_id)

    @staticmethod
    def _step_spec(spec: list[dict[str, Any]], sid: str) -> dict[str, Any]:
        for s in spec:
            if s["id"] == sid:
                return s
        return {}

    @staticmethod
    def _output_of(row: dict[str, Any]) -> Any:
        try:
            return json.loads(row.get("output_json") or "null")
        except Exception:  # noqa: BLE001
            return row.get("output_json") or ""

    async def _exec_step(self, run_id: str, step: dict[str, Any],
                         prev: dict[str, Any] | None,
                         payload: dict[str, Any]) -> None:
        sid = step["id"]
        pk = f"{run_id}|{sid}"
        idem = self._idem(run_id, sid, step.get("params") or {})
        if prev and prev["status"] == "done" and prev.get("idem_key") == idem:
            logger.debug("pipeline step already done, skip: %s", pk)
            return
        attempt = int((prev or {}).get("attempt") or 0) + 1
        base = {"id": pk, "run_id": run_id, "step_id": sid,
                "action": step["action"], "attempt": attempt, "idem_key": idem,
                "params_json": _dumps(step.get("params") or {}),
                "started_at": _now()}
        self.db.upsert_pipeline_step({**base, "status": "running"})
        try:
            out = await asyncio.wait_for(
                self._run_step(step, payload, run_id),
                timeout=float(step.get("timeout") or 600))
            self.db.upsert_pipeline_step({
                **base, "status": "done", "finished_at": _now(),
                "output_json": _dumps(out)})
        except Exception as e:  # noqa: BLE001 — 单步失败要留痕、要能续跑
            logger.warning("pipeline step failed: %s (%s: %s)", pk, type(e).__name__, e)
            self.db.upsert_pipeline_step({
                **base, "status": "failed", "finished_at": _now(),
                "error": f"{type(e).__name__}: {e}"})
            try:
                self.db.audit("system", "pipeline:step_failed",
                              f"{pk} attempt={attempt} {type(e).__name__}")
            except Exception:  # noqa: BLE001
                logger.debug("pipeline audit failed", exc_info=True)

    async def _run_step(self, step: dict[str, Any], payload: dict[str, Any],
                        run_id: str) -> Any:
        action = step["action"]
        params = step.get("params") or {}
        if action == "noop":
            return {"ok": True, "payload": payload}
        if action == "write_file":
            return self._write_file(params)
        if action == "window_run":
            if self.dispatch is None:
                raise RuntimeError("window_run 未装配派活通道（dispatch=None）")
            wid = str(params.get("window_id") or "")
            prompt = str(params.get("prompt") or "")
            if not wid or not prompt:
                raise RuntimeError("window_run 需要 params.window_id 与 params.prompt")
            text = await self.dispatch(wid, prompt)
            return {"window_id": wid, "reply": str(text or "")[:4000]}
        if action == "cv_detect":
            if self.cv is None:
                raise RuntimeError("cv_detect 未装配视觉服务（0.13.0 提供）")
            return await self.cv.detect(params, payload)
        if action == "sensor_read":
            if self.sensors is None:
                raise RuntimeError("sensor_read 未装配传感器（0.13.0 提供）")
            return await self.sensors.read(params, payload)
        raise RuntimeError(f"未知 action：{action}")

    def _write_file(self, params: dict[str, Any]) -> dict[str, Any]:
        rel = str(params.get("path") or "").strip()
        if not rel:
            raise RuntimeError("write_file 需要 params.path")
        target = (self.workspace_root / rel).resolve()
        if target != self.workspace_root and self.workspace_root not in target.parents:
            raise RuntimeError(f"path 越出工作区：{rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        content = str(params.get("content") or "")
        mode = "a" if params.get("append") else "w"
        with target.open(mode, encoding="utf-8") as fh:
            fh.write(content)
        return {"path": str(target), "bytes": len(content.encode()), "mode": mode}

    # ------------------------------------------------------------- 查询
    def status(self, run_id: str) -> dict[str, Any]:
        run = self.db.get_pipeline_run(run_id)
        if run is None:
            raise PipelineError(f"run 不存在：{run_id}")
        pipe = self.db.get_pipeline(str(run["pipeline_id"])) or {}
        steps = self.db.list_pipeline_steps(run_id)
        for s in steps:
            s["output"] = self._output_of(s)
        return {"run": run, "pipeline": {"id": pipe.get("id"), "name": pipe.get("name")},
                "steps": steps, "spec": self.spec_of(pipe) if pipe else []}

    def runs(self, limit: int = 30, pipeline_id: str = "") -> list[dict[str, Any]]:
        return self.db.list_pipeline_runs(limit=limit, pipeline_id=pipeline_id)

    async def resume(self, run_id: str) -> dict[str, Any]:
        """续跑：把 run 拉回 running，再 tick 一次（已完成步骤按 idem 跳过）。"""
        run = self.db.get_pipeline_run(run_id)
        if run is None:
            raise PipelineError(f"run 不存在：{run_id}")
        self.db.update_pipeline_run(run_id, status="running", finished_at="", error="")
        # 失败/中断的步骤拉回 pending，否则它们会一直是 failed，ready 永远为空
        # （"续跑"就变成了"再判一次失败"）。已 done 的不动：靠 idem 跳过。
        reset = self.db.reset_pipeline_steps(run_id)
        if reset:
            logger.info("pipeline resume: %s 个失败/中断步骤回到 pending（run=%s）",
                        reset, run_id)
        try:
            self.db.audit("system", "pipeline:resume", f"{run_id} reset={reset}")
        except Exception:  # noqa: BLE001
            logger.debug("pipeline audit failed", exc_info=True)
        return await self.tick(run_id)

    def health(self) -> dict[str, Any]:
        runs = self.db.list_pipeline_runs(limit=200)
        return {
            "pipelines": len(self.db.list_pipelines()),
            "runs": len(runs),
            "running": len([r for r in runs if r["status"] == "running"]),
            "failed": len([r for r in runs if r["status"] == "failed"]),
            "done": len([r for r in runs if r["status"] == "done"]),
        }
