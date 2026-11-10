import pytest

from lead_agent.domain.call_outcome import call_outcome


@pytest.mark.parametrize(
    ("status", "direction", "duration", "expected"),
    [
        ("completed", "outbound", 95, "answered"),
        ("done", "inbound", 40, "answered"),
        ("no-answer", "inbound", None, "missed"),
        ("no-answer", "outbound", None, "no_answer"),
        ("busy", "outbound", 0, "no_answer"),
        ("completed", "inbound", 0, "missed"),
        ("ringing", "outbound", None, "in_progress"),
        ("failed", "outbound", None, "failed"),
        ("canceled", "inbound", None, "failed"),
        ("", "inbound", None, "missed"),
    ],
)
def test_call_outcome(status, direction, duration, expected):
    assert call_outcome(status, direction, duration) == expected
