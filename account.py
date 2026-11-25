"""The signed-in account on the box and the one-time visitor import (no Enigma2 imports)."""
import json
import os

DEFAULT_PATH = "/etc/enigma2/kanasa_account.json"


class Account:
    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("bearer"), str) or not data["bearer"]:
            return None
        pro_until = data.get("pro_until")
        return {"bearer": data["bearer"], "name": data.get("name") if isinstance(data.get("name"), str) else "",
                "is_pro": data.get("is_pro") is True,
                "pro_until": pro_until if isinstance(pro_until, int) and not isinstance(pro_until, bool) else None}

    def save(self, bearer: str, name: str, flags=None) -> None:
        """flags: the sign-in answer's account flags (is_pro, pro_until when the server sends one)."""
        flags = flags if isinstance(flags, dict) else {}
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        try:
            os.remove(tmp)               # O_CREAT keeps an old file's mode: start from nothing
        except FileNotFoundError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # the bearer is a password
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"bearer": bearer, "name": name, "is_pro": flags.get("is_pro") is True,
                       "pro_until": flags.get("pro_until")}, handle)
        os.replace(tmp, self.path)

    def clear(self) -> None:
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass


def _key(text):
    parts = text.split(":") if isinstance(text, str) else []
    if len(parts) not in (2, 4) or parts[0] not in ("movie", "tv") or not all(p.isdigit() for p in parts[1:]):
        return None
    if parts[0] == "movie" and len(parts) != 2:
        return None
    return parts[0], [int(p) for p in parts[1:]]


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def import_payload(data: dict, now: int):
    my_list, progress = [], []
    for text in data.get("list") or []:
        key = _key(text)
        if key and len(key[1]) == 1:
            my_list.append({"kind": key[0], "tmdb_id": key[1][0], "added_at": now})
    for text, value in (data.get("resume") or {}).items():
        key = _key(text)
        if not key or not isinstance(value, dict) or not _number(value.get("t")):
            continue
        entry = {"kind": key[0], "tmdb_id": key[1][0]}
        if key[0] == "tv":
            if len(key[1]) != 3:
                continue
            entry.update(season=key[1][1], episode=key[1][2])
        entry.update(position=value["t"], duration=value.get("d") if _number(value.get("d")) else 0, at=now)
        progress.append(entry)
    if not my_list and not progress:
        return None
    return {"v": 1, "my_list": my_list, "votes": [], "hidden": [], "watched": [], "progress": progress}


def account_flags(account: dict) -> dict:
    """What the box keeps from the sign-in answer's account besides the name (feature 002 poll)."""
    account = account if isinstance(account, dict) else {}
    until = account.get("pro_until")
    return {"is_pro": account.get("is_pro") is True,
            "pro_until": until if isinstance(until, int) and not isinstance(until, bool) else None}


def initial(name) -> str:
    """The first letter of the account's name for the avatar circle ("K" without a name)."""
    for char in (name or "").strip():
        if char.isalnum():
            return char.upper()
    return "K"


def profile_badge(account, now: int) -> dict:
    """The top-bar profile button: {"state": out | in | pro, "initial"}. Pro needs is_pro, and a pro_until
    (when the server sent one) still ahead; an expired Pro is a plain signed-in look."""
    if not isinstance(account, dict) or not account.get("bearer"):
        return {"state": "out", "initial": ""}
    until = account.get("pro_until")
    pro = account.get("is_pro") is True and (until is None or (isinstance(until, int) and until > now))
    return {"state": "pro" if pro else "in", "initial": initial(account.get("name"))}


def account_name(account: dict) -> str:
    name = account.get("display_name") or ""
    if not name and isinstance(account.get("email"), str):
        name = account["email"].split("@", 1)[0]
    return name
