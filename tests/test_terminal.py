"""
Unit Tests for Terminal Logging & Spinners
"""

from core.terminal import get_spinner, term_log, print_banner_box, SPINNER_FRAMES


def test_spinner():
    first = get_spinner()
    assert first in SPINNER_FRAMES
    second = get_spinner()
    assert second in SPINNER_FRAMES


def test_term_log_stages(capsys):
    term_log("RECEIVE", "Receiving test video", task_id="test1", progress=50.0, extra="15 MB/s")
    term_log("PROBE", "Probing video streams", task_id="test1")
    term_log("PROCESS", "Transcoding video", task_id="test1", progress=75.0, extra="speed 2.5x")
    term_log("UPLOAD", "Uploading to Telegram", task_id="test1", progress=90.0)
    term_log("DONE", "Delivered successfully", task_id="test1")

    captured = capsys.readouterr().out
    assert "[RECEIVE]" in captured
    assert "[PROBE]" in captured
    assert "[PROCESS]" in captured
    assert "[UPLOAD]" in captured
    assert "[DONE]" in captured
    assert "test1" in captured


def test_banner_box(capsys):
    items = [("Key 1", "Value 1"), ("Key 2", "Value 2")]
    print_banner_box("TEST BANNER", items)
    captured = capsys.readouterr().out
    assert "TEST BANNER" in captured
    assert "Key 1" in captured
    assert "Value 1" in captured
