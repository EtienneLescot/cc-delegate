"""Task event bus: append-only per-task JSONL logs + in-process fan-out.

Every noteworthy moment of a delegation (launch, shell command, progress
note, question, answer, terminal state) is published here. Two consumers:

- the per-task log file ``<repo>/<work_dir>/logs/<task_id>.jsonl`` — the
  durable trace, readable after the fact;
- live subscribers (the SSE dashboard) via bounded queues — a slow or dead
  subscriber loses events rather than blocking the publisher.

Stdlib-only so unit tests run without any dependency install.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Any

_subscribers: set[queue.Queue] = set()
_lock = threading.Lock()

MAX_QUEUE = 1000


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=MAX_QUEUE)
    with _lock:
        _subscribers.add(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _lock:
        _subscribers.discard(q)


def log_path(repo: str, task_id: str, work_dir: str) -> Path:
    return Path(repo) / work_dir / "logs" / f"{task_id}.jsonl"


def publish(repo: str, task_id: str, event: dict[str, Any], work_dir: str) -> dict[str, Any]:
    """Stamp, persist, and fan out one event. Never raises."""
    stamped = {"ts": round(time.time(), 3), "task_id": task_id, **event}
    try:
        path = log_path(repo, task_id, work_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(stamped) + "\n")
    except OSError:
        pass
    with _lock:
        targets = list(_subscribers)
    for q in targets:
        try:
            q.put_nowait(stamped)
        except queue.Full:
            pass
    return stamped


def read_log(repo: str, task_id: str, work_dir: str, limit: int = 200) -> list[dict[str, Any]]:
    """Last ``limit`` events of a task's log; [] when absent/corrupt."""
    try:
        lines = log_path(repo, task_id, work_dir).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out
