from __future__ import annotations

import json
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


PROCESS_STATUS_PATH = Path("/opt/output/process_status.json")
MAX_MESSAGES = 50


class ProcessAlreadyRunning(RuntimeError):
    pass


@dataclass
class ProcessState:
    process_id: str | None = None
    process_name: str | None = None
    status: str = "ready"
    started_at: str | None = None
    finished_at: str | None = None
    progress_current: int = 0
    progress_total: int = 0
    counters: dict[str, int] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    selection: dict[str, Any] = field(default_factory=dict)
    last_messages: list[str] = field(default_factory=list)
    report_path: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "process_id": self.process_id,
            "process_name": self.process_name,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "progress_current": self.progress_current,
            "progress_total": self.progress_total,
            "counters": dict(self.counters),
            "options": dict(self.options),
            "selection": dict(self.selection),
            "last_messages": list(self.last_messages[-MAX_MESSAGES:]),
            "report_path": self.report_path,
            "error_message": self.error_message,
            "running": self.status == "running",
        }


_lock = threading.RLock()
_state = ProcessState()


def get_process_status() -> dict[str, Any]:
    with _lock:
        if _state.status == "ready":
            loaded = _load_persisted_status()
            if loaded:
                return loaded
        return _state.to_dict()


def start_process(
    process_name: str,
    *,
    options: dict[str, Any] | None = None,
    selection: dict[str, Any] | None = None,
    progress_total: int = 0,
) -> dict[str, Any]:
    with _lock:
        if _state.status == "running":
            raise ProcessAlreadyRunning(f"Es läuft bereits ein Prozess: {_state.process_name or _state.process_id}")
        now = _now()
        _state.process_id = uuid.uuid4().hex
        _state.process_name = process_name
        _state.status = "running"
        _state.started_at = now
        _state.finished_at = None
        _state.progress_current = 0
        _state.progress_total = int(progress_total or 0)
        _state.counters = {}
        _state.options = options or {}
        _state.selection = selection or {}
        _state.last_messages = [f"[{now}] Prozess gestartet: {process_name}"]
        _state.report_path = None
        _state.error_message = None
        _persist_status()
        return _state.to_dict()


def update_process(
    *,
    message: str | None = None,
    progress_current: int | None = None,
    progress_total: int | None = None,
    counters: dict[str, int] | None = None,
    report_path: str | None = None,
) -> dict[str, Any]:
    with _lock:
        if _state.status != "running":
            return _state.to_dict()
        if progress_current is not None:
            _state.progress_current = int(progress_current)
        if progress_total is not None:
            _state.progress_total = int(progress_total)
        if counters is not None:
            _state.counters = dict(counters)
        if report_path:
            _state.report_path = report_path
        if message:
            _state.last_messages.append(f"[{_now()}] {message}")
            _state.last_messages = _state.last_messages[-MAX_MESSAGES:]
        _persist_status()
        return _state.to_dict()


def finish_process(*, status: str = "success", message: str | None = None, report_path: str | None = None, counters: dict[str, int] | None = None) -> dict[str, Any]:
    with _lock:
        _state.status = status
        _state.finished_at = _now()
        if counters is not None:
            _state.counters = dict(counters)
        if report_path:
            _state.report_path = report_path
        if message:
            _state.last_messages.append(f"[{_now()}] {message}")
        _state.last_messages = _state.last_messages[-MAX_MESSAGES:]
        _persist_status()
        return _state.to_dict()


def fail_process(error_message: str, *, report_path: str | None = None) -> dict[str, Any]:
    with _lock:
        _state.status = "error"
        _state.finished_at = _now()
        _state.error_message = error_message
        if report_path:
            _state.report_path = report_path
        _state.last_messages.append(f"[{_now()}] Fehler: {error_message}")
        _state.last_messages = _state.last_messages[-MAX_MESSAGES:]
        _persist_status()
        return _state.to_dict()


@contextmanager
def process_guard(
    process_name: str,
    *,
    options: dict[str, Any] | None = None,
    selection: dict[str, Any] | None = None,
    progress_total: int = 0,
) -> Iterator[dict[str, Any]]:
    state = start_process(process_name, options=options, selection=selection, progress_total=progress_total)
    try:
        yield state
    except Exception as exc:
        fail_process(str(exc))
        raise


def reset_process_status_for_tests() -> None:
    with _lock:
        global _state
        _state = ProcessState()
        try:
            PROCESS_STATUS_PATH.unlink()
        except FileNotFoundError:
            pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _persist_status() -> None:
    PROCESS_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROCESS_STATUS_PATH.write_text(json.dumps(_state.to_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _load_persisted_status() -> dict[str, Any] | None:
    try:
        return json.loads(PROCESS_STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
