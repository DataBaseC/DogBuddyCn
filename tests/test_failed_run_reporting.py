"""0.10.16：人工派单失败必须汇报（定时任务仍按连败降噪）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.events.bus import JOB_FAILED, Event  # noqa: E402
from dogbuddy.services.report_service import ReportService  # noqa: E402

from helpers import run  # noqa: E402


class Sink:
    name = "console"

    def __init__(self):
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


def _svc():
    sink = Sink()
    return sink, ReportService(bus=_NoBus(), notifiers=[sink], llm_factory=None)


class _NoBus:
    def subscribe(self, *_a, **_k):
        pass

    def emit(self, *_a, **_k):
        pass


def test_manual_failure_is_reported_to_the_user():
    sink, svc = _svc()
    run(svc._on_job_failed(Event(type=JOB_FAILED, payload={
        "window_id": "w1", "name": "掉了线的工位", "summary": "LLM 调用失败",
        "error_code": "permanent", "error": "LLM error 400: model not found",
        "manual": True})))
    assert sink.sent, "人工派单失败被静默了 —— 用户等不到任何汇报"
    assert "permanent" in sink.sent[0], "汇报里没有错误码"
    assert "掉了线的工位" in sink.sent[0]


def test_scheduled_single_failure_stays_quiet():
    """定时任务单次失败保持降噪（连败到阈值才报警）。"""
    sink, svc = _svc()
    run(svc._on_job_failed(Event(type=JOB_FAILED, payload={
        "window_id": "j1", "name": "每日任务", "summary": "boom", "job_id": "j1"})))
    assert sink.sent == []
    # 连败到阈值 → 必须是"火警"
    for _ in range(2):
        run(svc._on_job_failed(Event(type=JOB_FAILED, payload={
            "window_id": "j1", "name": "每日任务", "summary": "boom", "job_id": "j1"})))
    assert sink.sent, "连败到阈值还不报警"
    assert any("每日任务" in s for s in sink.sent)
