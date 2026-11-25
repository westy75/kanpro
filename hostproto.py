"""085: the JSON-lines protocol between the plugin and its host worker (hostworker/main.py). Pure: no Enigma2,
no relative imports (the worker loads this file by path, outside the plugin package).

request  {"id": <int>, "op": <str>, "args": {...}, "deadline_s": <number>}
answer   {"id": <int>, "ok": true, "result": ...}  |  {"id": <int|null>, "ok": false, "error": "<code>"}

One object per line, UTF-8, no newline inside (json.dumps escapes it). A "line" longer than MAX_LINE is garbage
and dropped whole, also when it arrives in many chunks (extproto.MAX_PENDING's rule)."""
import json
import re

MAX_LINE = 4 << 20                       # 4 MiB
OPS = ("setup", "sources", "page_sources", "resolve", "hls_media", "torrents", "stats", "subscene", "subscene_file")
# FR-031 / plan: the plugin's deadlines; the worker answers "timeout" itself at these, the plugin kills at +5 s
# resolve 30 s: Vidoba resolves took 12-25 s on the owner's box (research §(g), 2026-11-23)
DEADLINES = {"setup": 20.0, "sources": 25.0, "page_sources": 25.0, "resolve": 30.0, "hls_media": 10.0,
             "torrents": 25.0, "stats": 5.0,
             "subscene": 20.0, "subscene_file": 20.0}       # 086 contract §2: 20 s, then show what you have
KILL_GRACE_S = 5.0
_ERROR = re.compile(r"^[a-z0-9_:.A-Z]{1,80}$")


def encode(value) -> bytes:
    """One protocol line. ensure_ascii keeps it pure ASCII: a chunk boundary can never split a letter."""
    return (json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n").encode("ascii")


def request(call_id: int, op: str, args=None, deadline_s=None) -> bytes:
    return encode({"id": call_id, "op": op, "args": dict(args or {}),
                   "deadline_s": float(DEADLINES.get(op, 10.0) if deadline_s is None else deadline_s)})


def ok(call_id, result) -> dict:
    return {"id": call_id, "ok": True, "result": result}


def error(call_id, code: str) -> dict:
    return {"id": call_id, "ok": False, "error": code if isinstance(code, str) and _ERROR.match(code) else "error"}


def _call_id(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def parse_request(obj):
    """(id, op, args, deadline_s) of a well-formed request, else (id-or-None, None, None, None)."""
    if not isinstance(obj, dict):
        return None, None, None, None
    call_id, op, args, deadline = _call_id(obj.get("id")), obj.get("op"), obj.get("args", {}), obj.get("deadline_s")
    if call_id is None or op not in OPS or not isinstance(args, dict):
        return call_id, None, None, None
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not 0 < deadline <= 600:
        deadline = DEADLINES[op]
    return call_id, op, args, float(deadline)


def parse_answer(obj):
    """(id, ok, result_or_error) of a well-formed answer, else None (the plugin drops it)."""
    if not isinstance(obj, dict) or not isinstance(obj.get("ok"), bool):
        return None
    call_id = _call_id(obj.get("id"))
    if obj["ok"]:
        return (call_id, True, obj.get("result")) if call_id is not None else None
    code = obj.get("error")
    return call_id, False, code if isinstance(code, str) and _ERROR.match(code) else "error"


class LineBuffer:
    """Byte chunks -> decoded JSON objects. Undecodable lines become the GARBAGE marker (the reader answers or
    drops them); a line over MAX_LINE is dropped up to its newline, never parsed."""

    GARBAGE = object()

    def __init__(self, max_line: int = MAX_LINE):
        self.max_line = max_line
        self.pending = b""
        self.skipping = False                 # inside an oversize line: drop until the next newline
        self.dropped = 0

    def feed(self, chunk) -> list:
        if not chunk:
            return []
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8", "surrogateescape")
        data = self.pending + bytes(chunk)
        parts = data.split(b"\n")
        self.pending = parts.pop()
        out = []
        for raw in parts:
            if self.skipping:
                self.skipping = False         # the tail of an oversize line ends here
                continue
            if len(raw) > self.max_line:
                self.dropped += 1
                continue
            out.append(self._decode(raw))
        if len(self.pending) > self.max_line:
            self.pending, self.skipping = b"", True
            self.dropped += 1
        return [item for item in out if item is not None]

    def _decode(self, raw: bytes):
        raw = raw.strip()
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self.GARBAGE
