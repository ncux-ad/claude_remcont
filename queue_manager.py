import json
import os
import threading
from datetime import datetime
from config import QUEUE_FILE

_lock = threading.Lock()


def _load():
    if not os.path.exists(QUEUE_FILE):
        return []
    with open(QUEUE_FILE) as f:
        return json.load(f)


def _save(q):
    with open(QUEUE_FILE, "w") as f:
        json.dump(q, f, ensure_ascii=False, indent=2)


def push(text: str, chat_id: int, message_id: int, force_new: bool = False) -> str:
    with _lock:
        q = _load()
        task_id = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{len(q)}"
        q.append({
            "id": task_id,
            "text": text,
            "chat_id": chat_id,
            "message_id": message_id,
            "force_new": force_new,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
        })
        _save(q)
        return task_id


def set_status(task_id: str, status: str):
    with _lock:
        q = _load()
        for t in q:
            if t["id"] == task_id:
                t["status"] = status
                t["updated_at"] = datetime.utcnow().isoformat()
        _save(q)


def is_running() -> bool:
    return any(t["status"] == "running" for t in _load())


def next_pending():
    for t in _load():
        if t["status"] == "pending":
            return t
    return None
