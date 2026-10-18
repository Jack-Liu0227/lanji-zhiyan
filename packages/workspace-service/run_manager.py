"""Run manager for tracking background/interactive agent runs, SSE replay, and cancellation."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable


class RunState:
    def __init__(self, run_id: str, task_id: str = "", idempotency_key: str = ""):
        self.run_id = run_id
        self.task_id = task_id
        self.idempotency_key = idempotency_key
        self.status = "running"
        self.cancel_requested = False
        self.event_counter = 0
        self.listeners: list[Callable[[dict[str, Any]], None]] = []
        self.lock = threading.Lock()
        self.events: list[dict[str, Any]] = []
        self.created_at = time.time()

    def add_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        with self.lock:
            self.listeners.append(listener)

    def remove_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        with self.lock:
            if listener in self.listeners:
                self.listeners.remove(listener)

    def emit(self, event: dict[str, Any], db: Any = None) -> dict[str, Any]:
        with self.lock:
            self.event_counter += 1
            eid = self.event_counter
            ev_record = {
                "event_id": eid,
                "run_id": self.run_id,
                "type": event.get("type", "unknown"),
                "payload": event,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            self.events.append(ev_record)
            ev_type = event.get("type")
            if ev_type == "done":
                self.status = "done"
            elif ev_type == "error":
                self.status = "failed"
            elif ev_type == "cancelled":
                self.status = "cancelled"

            if db is not None:
                try:
                    db.record_run_event(self.run_id, eid, ev_type or "unknown", event)
                    if ev_type in ("done", "failed", "cancelled"):
                        db.update_async_task(self.run_id, status=self.status, progress=1.0)
                        if hasattr(db, "update_background_job"):
                            db.update_background_job(self.run_id, status=self.status, progress=1.0, last_event_id=eid)
                    else:
                        if hasattr(db, "update_background_job"):
                            db.update_background_job(self.run_id, last_event_id=eid)
                except Exception:
                    pass

            for listener in list(self.listeners):
                try:
                    listener(ev_record)
                except Exception:
                    pass
            return ev_record

    def cancel(self, db: Any = None) -> None:
        with self.lock:
            self.cancel_requested = True
            self.status = "cancelled"
            if db is not None:
                try:
                    db.update_async_task(self.run_id, status="cancelled", cancel_requested=True)
                    if hasattr(db, "update_background_job"):
                        db.update_background_job(self.run_id, status="cancelled")
                except Exception:
                    pass
        self.emit({"type": "cancelled", "message": "Run cancelled by user"}, db=db)


class RunManager:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self.runs: dict[str, RunState] = {}
        self.idempotency_map: dict[str, str] = {}

    @classmethod
    def get_instance(cls) -> RunManager:
        with cls._lock:
            if cls._instance is None:
                cls._instance = RunManager()
            return cls._instance

    def create_run(self, task_id: str = "", idempotency_key: str = "", db: Any = None) -> RunState:
        with self._lock:
            if idempotency_key and idempotency_key in self.idempotency_map:
                existing_id = self.idempotency_map[idempotency_key]
                if existing_id in self.runs:
                    return self.runs[existing_id]

            run_id = f"run-{uuid.uuid4().hex[:12]}"
            state = RunState(run_id, task_id=task_id, idempotency_key=idempotency_key)
            self.runs[run_id] = state
            if idempotency_key:
                self.idempotency_map[idempotency_key] = run_id
            if db is not None:
                try:
                    db.create_async_task("agent_run", owner_id=task_id, idempotency_key=idempotency_key, task_id=run_id)
                    if hasattr(db, "create_background_job"):
                        db.create_background_job(job_id=run_id, task_id=task_id, status="running", progress=0.0, last_event_id=0)
                except Exception:
                    pass
            return state

    def get_run(self, run_id: str) -> RunState | None:
        with self._lock:
            return self.runs.get(run_id)

    def cancel_run(self, run_id: str, db: Any = None) -> bool:
        run = self.get_run(run_id)
        if run:
            run.cancel(db=db)
            return True
        if db is not None:
            try:
                db.update_async_task(run_id, status="cancelled", cancel_requested=True)
                return True
            except Exception:
                pass
        return False
