"""The deploy gate (infra/deploy/deploy_gate.py): no backend restart during a live show."""
import calendar
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "deploy_gate", Path(__file__).resolve().parents[2] / "infra" / "deploy" / "deploy_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def at(minute: int) -> float:
    return calendar.timegm((2026, 11, 18, 23, minute, 10, 0, 0, 0))


def test_waits_while_a_show_is_on_air():
    assert "KBR Jerusalem" in gate.reason_to_wait(at(15), shows=lambda: ["KBR Jerusalem"])


def test_waits_around_the_time_checks_and_not_between():
    for minute in (55, 59, 0, 4, 27, 30, 32):
        assert gate.reason_to_wait(at(minute), shows=lambda: []) is not None, minute
    for minute in (5, 15, 26, 33, 45, 54):
        assert gate.reason_to_wait(at(minute), shows=lambda: []) is None, minute


def test_an_api_that_does_not_answer_does_not_hold_the_deploy():
    assert gate.reason_to_wait(at(0), shows=lambda: None) is None


def test_deploy_now_skips_the_wait(monkeypatch):
    monkeypatch.setenv("COMMIT_MESSAGE", "Fix dead air [deploy-now] (#400)")
    monkeypatch.setattr(gate, "reason_to_wait", lambda: "a live show is on air")
    assert gate.main() == 0
