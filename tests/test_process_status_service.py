from __future__ import annotations

import pytest

from app.services.process_status_service import (
    ProcessAlreadyRunning,
    fail_process,
    finish_process,
    get_process_status,
    process_guard,
    reset_process_status_for_tests,
    start_process,
)


def setup_function() -> None:
    reset_process_status_for_tests()


def teardown_function() -> None:
    reset_process_status_for_tests()


def test_process_starts_and_blocks_second_start() -> None:
    state = start_process("Testprozess", options={"dry_run": True}, selection={"products": 2}, progress_total=2)

    assert state["status"] == "running"
    assert state["running"] is True
    assert state["process_name"] == "Testprozess"
    with pytest.raises(ProcessAlreadyRunning):
        start_process("Zweiter Prozess")


def test_process_finish_sets_success_and_releases_lock() -> None:
    start_process("Testprozess")
    finish_process(status="success", message="Fertig", report_path="/opt/output/report.csv", counters={"updated": 1})
    state = get_process_status()

    assert state["status"] == "success"
    assert state["running"] is False
    assert state["report_path"] == "/opt/output/report.csv"
    assert state["counters"] == {"updated": 1}
    assert start_process("Neuer Prozess")["status"] == "running"


def test_process_error_releases_lock() -> None:
    start_process("Fehlerprozess")
    fail_process("Kaputt")
    state = get_process_status()

    assert state["status"] == "error"
    assert state["error_message"] == "Kaputt"
    assert start_process("Neuer Prozess")["status"] == "running"


def test_process_guard_marks_error_on_exception() -> None:
    with pytest.raises(RuntimeError):
        with process_guard("Guard Prozess"):
            raise RuntimeError("boom")

    state = get_process_status()
    assert state["status"] == "error"
    assert state["error_message"] == "boom"
