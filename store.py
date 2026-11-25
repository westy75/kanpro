"""Visitor data kept on the box: My List and resume positions (no Enigma2 imports)."""
import json
import os
import threading

_LOCKS, _LOCKS_GUARD = {}, threading.Lock()


def _lock_for(path: str):
    # one lock per file, shared by every Store on it: worker threads write it too (player saves, import)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(os.path.abspath(path), threading.RLock())


def _empty() -> dict:
    return {"v": 1, "list": [], "resume": {}}


class Store:
    def __init__(self, path: str):
        self.path = path
        self.lock = _lock_for(path)

    def load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return _empty()
        if not isinstance(data, dict) or not isinstance(data.get("list"), list) \
                or not isinstance(data.get("resume"), dict):
            return _empty()
        return data

    def save(self, data: dict) -> None:
        with self.lock:
            self._save(data)

    def _save(self, data: dict) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        os.replace(tmp, self.path)

    def set_resume(self, key: str, seconds: float, hoster, length=None) -> None:
        with self.lock:
            data = self.load()
            value = {"t": seconds, "h": hoster}
            if length:
                value["d"] = length
            data["resume"][key] = value
            self._save(data)

    def clear_resume(self, key: str) -> None:
        with self.lock:
            data = self.load()
            if data["resume"].pop(key, None) is not None:
                self._save(data)

    def forget(self, keys: list, resume: dict) -> None:
        """Drop exactly these entries (an import sent them); anything newer stays."""
        with self.lock:
            data = self.load()
            data["list"] = [k for k in data["list"] if k not in keys]
            for key, value in resume.items():
                if data["resume"].get(key) == value:
                    del data["resume"][key]
            self._save(data)

    def resume(self, key: str):
        value = self.load()["resume"].get(key)
        return value if isinstance(value, dict) else None

    # --- FIX33: account progress saved while the network was down -------------------------------------
    def set_pending(self, key: str, value: dict) -> None:
        """An account point that could not be sent (no network): kept until it is sent (or a newer one
        replaces it). value: {"t", "d", "h", "kind", "id", "season", "episode", "source", "at"}."""
        with self.lock:
            data = self.load()
            pending = data.get("pending") if isinstance(data.get("pending"), dict) else {}
            pending[key] = dict(value)
            if len(pending) > 200:               # bounded: the oldest go
                pending = dict(list(pending.items())[-200:])
            data["pending"] = pending
            self._save(data)

    def pending(self) -> dict:
        value = self.load().get("pending")
        return {k: dict(v) for k, v in value.items() if isinstance(v, dict)} if isinstance(value, dict) else {}

    def pending_point(self, key: str):
        return self.pending().get(key)

    def clear_pending(self, key: str, value=None) -> None:
        """Drop the kept point of `key` - only `value` itself when given (a newer one stays)."""
        with self.lock:
            data = self.load()
            pending = data.get("pending") if isinstance(data.get("pending"), dict) else {}
            if key in pending and (value is None or pending[key] == value):
                del pending[key]
                data["pending"] = pending
                self._save(data)

    def toggle_list(self, key: str) -> bool:
        with self.lock:
            data = self.load()
            if key in data["list"]:
                data["list"].remove(key)
                added = False
            else:
                data["list"].insert(0, key)
                added = True
            self._save(data)
            return added

    def in_list(self, key: str) -> bool:
        return key in self.load()["list"]

    def list_keys(self) -> list:
        return [k for k in self.load()["list"] if isinstance(k, str)]
