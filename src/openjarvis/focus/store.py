"""File-backed state for the Focus HUD (tasks, steps, quick captures)."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_RISK_PER_TRADE = 5.0


def default_path() -> Path:
    override = os.environ.get("OPENJARVIS_FOCUS_FILE")
    if override:
        return Path(override)
    from openjarvis.core.config import DEFAULT_CONFIG_DIR

    return Path(DEFAULT_CONFIG_DIR) / "focus.json"


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


class FocusStore:
    """Small JSON store. Everything stays on the local disk."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else default_path()
        self._lock = threading.RLock()

    def _load(self) -> Dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("tasks", [])
        data.setdefault("captures", [])
        data.setdefault("risk_per_trade", DEFAULT_RISK_PER_TRADE)
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def snapshot(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """State plus derived fields: overdue flags, current task, mode."""
        now = now or datetime.now()
        with self._lock:
            data = self._load()
        for task in data["tasks"]:
            task["overdue"] = _is_overdue(task, now)
        open_tasks = [t for t in data["tasks"] if not t["done"]]
        open_tasks.sort(key=lambda t: (not t["overdue"], t.get("due") or "9999"))
        data["current"] = open_tasks[0] if open_tasks else None
        overdue = any(t["overdue"] for t in open_tasks)
        data["mode"] = "deadline" if overdue else "calm"
        return data

    def add_task(
        self, title: str, steps: List[str], due: Optional[str] = None
    ) -> Dict[str, Any]:
        task = {
            "id": _new_id(),
            "title": title,
            "due": due,
            "steps": steps or [title],
            "step": 0,
            "done": False,
        }
        with self._lock:
            data = self._load()
            data["tasks"].append(task)
            self._save(data)
        return task

    def advance(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            data = self._load()
            for task in data["tasks"]:
                if task["id"] == task_id:
                    task["step"] = min(task["step"] + 1, len(task["steps"]))
                    task["done"] = task["step"] >= len(task["steps"])
                    self._save(data)
                    return task
        return None

    def remove_task(self, task_id: str) -> bool:
        return self._remove("tasks", task_id)

    def add_capture(self, text: str, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = now or datetime.now()
        item = {"id": _new_id(), "text": text, "at": now.isoformat(timespec="minutes")}
        with self._lock:
            data = self._load()
            data["captures"].insert(0, item)
            self._save(data)
        return item

    def remove_capture(self, capture_id: str) -> bool:
        return self._remove("captures", capture_id)

    def set_risk(self, dollars: float) -> float:
        with self._lock:
            data = self._load()
            data["risk_per_trade"] = dollars
            self._save(data)
        return dollars

    def _remove(self, key: str, item_id: str) -> bool:
        with self._lock:
            data = self._load()
            kept = [i for i in data[key] if i["id"] != item_id]
            if len(kept) == len(data[key]):
                return False
            data[key] = kept
            self._save(data)
        return True


def _is_overdue(task: Dict[str, Any], now: datetime) -> bool:
    if task.get("done") or not task.get("due"):
        return False
    try:
        due = datetime.fromisoformat(task["due"])
    except ValueError:
        return False
    if due.tzinfo is not None:
        due = due.astimezone().replace(tzinfo=None)
    return now > due
