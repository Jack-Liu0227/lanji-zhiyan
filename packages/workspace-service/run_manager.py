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
        self.worker_started = False

    def claim(self) -> bool:
        with self.lock:
            if self.worker_started or self.status in ("done", "failed", "cancelled"):
                return False
            self.worker_started = True
            return True

    def add_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        with self.lock:
            self.listeners.append(listener)

    def remove_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        with self.lock:
            if listener in self.listeners:
                self.listeners.remove(listener)

    def emit(self, event: dict[str, Any], db: Any = None) -> dict[str, Any]:
        with self.lock:
            if self.status in ("done", "failed", "cancelled"):
                return {"event_id": self.event_counter, "run_id": self.run_id, "type": self.status, "payload": event}
            self.event_counter += 1
            eid = self.event_counter
            ev_record = {
                "event_id": eid,
                "run_id": self.run_id,
                "type": event.get("type", "unknown"),
                "payload": event,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            ev_type = event.get("type")
            status = {"done": "done", "error": "failed", "failed": "failed", "cancelled": "cancelled"}.get(ev_type, self.status)
            if db is not None:
                db.record_run_event(self.run_id, eid, ev_type or "unknown", event)
                if ev_type in ("done", "error", "failed", "cancelled"):
                    db.update_async_task(self.run_id, status=status, progress=1.0)
                    if hasattr(db, "update_background_job"):
                        db.update_background_job(self.run_id, status=status, progress=1.0, last_event_id=eid)
                elif hasattr(db, "update_background_job"):
                    db.update_background_job(self.run_id, last_event_id=eid)
                if hasattr(db, 'assistant'):
                    import execution_board
                    execution_board.agent_event(db, self.task_id, self.run_id, event)
            self.status = status
            self.events.append(ev_record)

            for listener in list(self.listeners):
                try:
                    listener(ev_record)
                except Exception:
                    pass
            return ev_record

    def cancel(self, db: Any = None) -> None:
        with self.lock:
            if self.status in ("done", "failed", "cancelled"):
                return
            self.cancel_requested = True
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
        self.pending_cancels: set[str] = set()

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
                    if self.runs[existing_id].task_id != task_id:
                        raise ValueError("运行请求不属于当前项目或会话")
                    return self.runs[existing_id]

            run_id = f"run-{uuid.uuid4().hex[:12]}"
            old = db.get_async_task_by_idempotency(idempotency_key) if db is not None and idempotency_key else None
            if old:
                if old["owner_id"] != task_id:
                    raise ValueError("运行请求不属于当前项目或会话")
                run_id = old["id"]
            state = RunState(run_id, task_id=task_id, idempotency_key=idempotency_key)
            if old:
                state.status = old["status"] if old["status"] != "pending" else "running"
                state.cancel_requested = bool(old["cancel_requested"])
                last = db.db.execute("SELECT COALESCE(MAX(event_id),0) FROM run_events WHERE run_id=?", (run_id,)).fetchone()
                state.event_counter = int(last[0])
            self.runs[run_id] = state
            if idempotency_key:
                self.idempotency_map[idempotency_key] = run_id
            if db is not None and not old:
                db.create_async_task("agent_run", owner_id=task_id, idempotency_key=idempotency_key, task_id=run_id)
                if hasattr(db, "create_background_job"):
                    db.create_background_job(job_id=run_id, task_id=task_id, status="running", progress=0.0, last_event_id=0)
            if idempotency_key in self.pending_cancels:
                self.pending_cancels.discard(idempotency_key)
                state.cancel(db=db)
            return state

    def get_run(self, run_id: str) -> RunState | None:
        with self._lock:
            return self.runs.get(run_id)

    def cancel_by_key(self, key: str, db: Any = None) -> bool:
        old = db.get_async_task_by_idempotency(key) if db is not None and key else None
        with self._lock:
            run_id = self.idempotency_map.get(key) if key else None
            run_id = run_id or (old["id"] if old else None)
            if key and not run_id:
                if len(self.pending_cancels) >= 256:
                    self.pending_cancels.clear()
                self.pending_cancels.add(key)
        return self.cancel_run(run_id, db=db) if run_id else bool(key)

    def cancel_run(self, run_id: str, db: Any = None) -> bool:
        run = self.get_run(run_id)
        if run:
            run.cancel(db=db)
            return True
        if db is not None:
            task = db.get_async_task(run_id)
            if task and task["status"] not in ("done", "failed", "cancelled"):
                db.update_async_task(run_id, status="cancelled", cancel_requested=True)
                db.update_background_job(run_id, status="cancelled")
            return bool(task)
        return False
