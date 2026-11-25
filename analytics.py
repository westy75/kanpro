"""083 device analytics on the box (contract specs/083-device-analytics/contracts/083-device-analytics.md).

No Enigma2 imports here (tests run it). Best-effort by design: every public function swallows its own
errors, sending runs off the GUI thread, a failure keeps (or drops) the queue and never shows anything.
The install id is random (uuid4) — never the MAC, serial or account. Events carry public ids only: no
host, site or server names, URLs or refs.
"""
import glob
import json
import os
import re
import secrets
import threading
import time
import uuid

from . import __version__

STATE_PATH = "/etc/enigma2/kanasa_analytics.json"
RUNNING_PATH = "/etc/enigma2/kanasa_running"
EVENTS_URL = "https://kanasastream.online/api/analytics/events"
LOGS_URL = "https://kanasastream.online/api/analytics/logs"
QUEUE_CAP, BATCH_MAX, MAX_AGE_MS = 500, 100, 7 * 86400 * 1000
SEND_EVERY_S, TIMEOUT_S = 60, 10
LOG_CAP = 64 * 1024
GROUPS = ("arabic_subs", "no_subs", "french", "torrents", "device")
CRASH_DIRS = ("/home/root/logs", "/media/hdd/logs", "/media/hdd", "/media/usb", "/tmp")
_RING, _RING_MAX = [], 300
pending_crash = None          # a crash log to offer (sharing off): Home asks "Send a crash report?"
_lock = threading.Lock()
_sending = {"busy": False}
_state_path = {"path": STATE_PATH}
_ctx = {"lang": "en", "bearer": None, "auto": False}


def context(lang="en", bearer=None, auto=True) -> None:
    """Set when the plugin opens: the language and the account token sent with each batch; auto = record()
    sends by itself (at most once per SEND_EVERY_S). Tests leave auto off."""
    _ctx.update(lang=lang, bearer=bearer or None, auto=bool(auto))


def _now_ms():
    return int(time.time() * 1000)


# --- state ------------------------------------------------------------------------------------------

def _load():
    try:
        with open(_state_path["path"], encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def _save(data):
    path = _state_path["path"]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    os.replace(tmp, path)


def _state():
    data = _load()
    if not isinstance(data.get("install_id"), str) or not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", data["install_id"]):
        data["install_id"] = str(uuid.uuid4())
        try:                          # kept at once: every later read (device(), a batch) sees the same id
            _save(data)
        except Exception:  # noqa: BLE001 - a read-only /etc: the id lives for this session
            pass
    if not isinstance(data.get("seq"), int) or isinstance(data.get("seq"), bool):
        data["seq"] = 0
    if not isinstance(data.get("queue"), list):
        data["queue"] = []
    return data


def sharing() -> bool:
    return _load().get("share", True) is not False


def set_sharing(on: bool) -> None:
    with _lock:
        try:
            data = _state()
            data["share"] = bool(on)
            if not on:
                data["queue"] = []          # OFF: the queue is cleared and nothing more is sent
            _save(data)
        except Exception:  # noqa: BLE001
            pass


def notice_due() -> bool:
    """The one-time first-open notice (owner decision 2026-11-21)."""
    return not _load().get("notice_shown")


def notice_shown() -> None:
    with _lock:
        try:
            data = _state()
            data["notice_shown"] = True
            _save(data)
        except Exception:  # noqa: BLE001
            pass


# --- device record ----------------------------------------------------------------------------------

def _read(path, limit=4096):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    except OSError:
        return ""


def image_name(text=None) -> str:
    """'OpenATV 7.4' from /etc/image-version (distro + imageversion), else /etc/issue's first word."""
    text = _read("/etc/image-version") if text is None else text
    values = {}
    for line in text.splitlines():
        key, _, value = line.partition("=")
        values[key.strip().lower()] = value.strip()
    distro = values.get("distro") or values.get("creator", "").split(" ")[0]
    version = values.get("imageversion") or values.get("version", "")
    name = " ".join(part for part in (distro, version) if part).strip()
    if not name:
        name = _read("/etc/issue").split("\\")[0].strip()
    names = {"openatv": "OpenATV", "openpli": "OpenPLi", "egami": "Egami", "openvix": "OpenViX",
             "openbh": "OpenBH", "pure2": "Pure2", "openspa": "OpenSPA", "teamblue": "teamBlue"}
    first, _, rest = name.partition(" ")
    name = (names.get(first.lower(), first) + (" " + rest if rest else "")).strip()
    return _printable(name, 60)


def box_model() -> str:
    model = _read("/proc/stb/info/vumodel", 64).strip()
    if model:
        return _printable("Vu+ " + model.replace("_", " ").title().replace("4K", "4K"), 60)
    for path in ("/proc/stb/info/boxtype", "/proc/stb/info/model", "/etc/hostname"):
        model = _read(path, 64).strip()
        if model:
            return _printable(model, 60)
    return ""


def _printable(text, limit):
    text = "".join(ch for ch in str(text or "") if ch.isprintable())
    return text[:limit].strip()


def device(lang="en") -> dict:
    data = _state()
    return {"install_id": data["install_id"], "kind": "enigma2", "app_version": __version__,
            "os_family": "enigma2", "os": image_name(), "model": box_model(),
            "ui_lang": lang if lang in ("ar", "en", "fr") else "other", "share_usage": sharing()}


# --- events -----------------------------------------------------------------------------------------

def record(name, **fields) -> None:
    """Queue one event (dropped when sharing is off). Never raises."""
    with _lock:
        try:
            data = _state()
            if data.get("share", True) is False:
                return
            data["seq"] += 1
            event = {"seq": data["seq"], "ts": _now_ms(), "name": name}
            event.update({k: v for k, v in fields.items() if v is not None or k in ("season", "episode")})
            cutoff = _now_ms() - MAX_AGE_MS
            queue = [e for e in data["queue"] if isinstance(e, dict) and e.get("ts", 0) >= cutoff] + [event]
            data["queue"] = queue[-QUEUE_CAP:]
            _save(data)
            due = time.time() >= max(data.get("next_at") or 0, (data.get("sent_at") or 0) + SEND_EVERY_S)
        except Exception:  # noqa: BLE001
            return
    if _ctx["auto"] and (due or name == "app_open"):
        flush_later(_ctx["lang"], _ctx["bearer"], force=name == "app_open")


def group_of(section) -> object:
    return section if section in GROUPS else None


def title_of(target) -> object:
    target = target or {}
    if target.get("site_id"):
        return {"tmdb_kind": None, "tmdb_id": None, "site_id": str(target["site_id"])}
    try:
        tmdb_id = int(target.get("id"))
    except (TypeError, ValueError):
        return None
    kind = target.get("kind")
    return {"tmdb_kind": kind, "tmdb_id": tmdb_id, "site_id": None} if kind in ("movie", "tv") else None


def play_start(target) -> dict:
    """A new play attempt: -> its handle (play_id + start time) for play_ok/play_fail."""
    play = {"id": secrets.token_urlsafe(12)[:16], "at": time.monotonic(), "done": False}
    target = target or {}
    tv = target.get("season") is not None
    record("play_start", play_id=play["id"], group=None, title=title_of(target),
           season=target.get("season") if tv else None, episode=target.get("episode") if tv else None)
    return play


def play_ok(play, section) -> None:
    if not play or play.get("done"):
        return
    play["done"] = True
    ttff = int(max(0, min(600000, (time.monotonic() - play["at"]) * 1000)))
    record("play_ok", play_id=play["id"], group=group_of(section), ttff_ms=ttff)


def play_fail(play, section, reason) -> None:
    """One final fail per attempt; no_servers carries group null."""
    if not play or play.get("done"):
        return
    play["done"] = True
    group = None if reason == "no_servers" else group_of(section)
    if group is None and reason != "no_servers":
        reason, group = "no_servers", None       # the contract needs a group for every other reason
    record("play_fail", play_id=play["id"], group=group, reason=reason)


_DOWNLOAD_REASONS = {"no_disk": "storage", "space": "storage", "network": "network", "http": "network",
                     "no_source": "source", "source_gone": "source", "no_peers": "source", "no_video": "source",
                     "engine": "other"}


def download_event(name, item, error=None) -> None:
    """download_start (with the public title) / download_done / download_fail (reason class)."""
    item = item or {}
    group = group_of(item.get("section"))
    if name == "download_start":
        tv = item.get("season") is not None
        record(name, group=group, title=title_of({"kind": item.get("kind"), "id": item.get("tmdb_id")}),
               season=item.get("season") if tv else None, episode=item.get("episode") if tv else None)
    elif name == "download_fail":
        record(name, group=group, reason=_DOWNLOAD_REASONS.get(error, "other"))
    else:
        record(name, group=group)


def update_event(name, version, mandatory) -> None:
    record(name, to_version=str(version), mandatory=bool(mandatory))


# --- sending ----------------------------------------------------------------------------------------

def _post(url, body, bearer):
    import requests  # on the box; imported late so tests never need it
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    response = requests.post(url, data=json.dumps(body), headers=headers, timeout=TIMEOUT_S)
    try:
        answer = response.json()
    except ValueError:
        answer = {}
    return response.status_code, answer if isinstance(answer, dict) else {}


def flush(lang="en", bearer=None, post=None, force=False) -> str:
    """Send the queue (blocking; call off the GUI thread). -> 'sent'|'idle'|'kept'|'dropped'|'paused'."""
    post = post or _post
    with _lock:
        data = _state()
        now = time.time()
        if data.get("share", True) is False or not data["queue"]:
            return "idle"
        if now < (data.get("next_at") or 0) and (not force or data.get("paused")):
            return "paused"          # app_open may skip the 60 s pacing, never a pause the server asked for
        batch = data["queue"][:BATCH_MAX]
    try:
        status, answer = post(EVENTS_URL, {"device": device(lang), "events": batch}, bearer)
    except Exception:  # noqa: BLE001 - network: keep the queue for the next send
        return "kept"
    wait = answer.get("next_after_s") if isinstance(answer.get("next_after_s"), int) else SEND_EVERY_S
    with _lock:
        data = _state()
        sent = {e.get("seq") for e in batch}
        if status == 200 and answer.get("accept") is False:
            data["queue"] = []
        elif status == 200 or 400 <= status < 500 and status != 429:
            data["queue"] = [e for e in data["queue"] if e.get("seq") not in sent]   # stored, or never retried
        data["next_at"] = now + max(0, min(86400, wait))
        data["paused"] = status != 200 or answer.get("accept") is False
        data["sent_at"] = now
        try:
            _save(data)
        except Exception:  # noqa: BLE001
            pass
    return "sent" if status == 200 else "dropped" if 400 <= status < 500 and status != 429 else "kept"


def flush_later(lang="en", bearer=None, force=False) -> None:
    """Fire and forget on a worker thread (at most one at a time). Never raises, never waits."""
    if _sending["busy"]:
        return

    def run():
        try:
            flush(lang, bearer, force=force)
        except Exception:  # noqa: BLE001
            pass
        finally:
            _sending["busy"] = False

    _sending["busy"] = True
    try:
        threading.Thread(target=run, name="kanasa-analytics", daemon=True).start()
    except Exception:  # noqa: BLE001
        _sending["busy"] = False


# --- logs -------------------------------------------------------------------------------------------

def note(line) -> None:
    """Keep the plugin's last log lines in memory for Send logs."""
    try:
        _RING.append(time.strftime("%H:%M:%S ") + str(line)[:500])
        del _RING[:-_RING_MAX]
    except Exception:  # noqa: BLE001
        pass


_SECRET = re.compile(r"(?i)(bearer\s+|authorization[\"']?\s*[:=]\s*[\"']?|(?:token|key|password|secret|sig|csrf|"
                     r"session|cookie)[A-Za-z0-9_]*[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,;]+")
_QUERY = re.compile(r"(https?://[^\s?#\"']+)\?[^\s#\"']*")


def scrub(text) -> str:
    """Best effort on the box; the server scrubs again by class rule before storing."""
    text = _SECRET.sub(lambda m: m.group(1) + "[scrubbed]", str(text or ""))
    return _QUERY.sub(lambda m: m.group(1) + "?[scrubbed]", text)


def newest_crash_log(after=0.0, dirs=CRASH_DIRS):
    found = []
    for directory in dirs:
        for path in glob.glob(os.path.join(directory, "*crash*.log")):
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime > after:
                found.append((mtime, path))
    return max(found)[1] if found else None


def log_text(crash_path=None) -> str:
    """The text Send logs uploads: our recent lines, then the newest crash log's end. Last 64 KiB."""
    parts = ["KanasaStream %s on %s · %s" % (__version__, box_model() or "?", image_name() or "?")]
    if _RING:
        parts += ["", "--- plugin (this session) ---"] + _RING[-_RING_MAX:]
    crash_path = crash_path or newest_crash_log(time.time() - 14 * 86400)
    if crash_path:
        parts += ["", "--- %s ---" % os.path.basename(crash_path), _read(crash_path, 4 * LOG_CAP)]
    text = scrub("\n".join(parts))
    raw = text.encode("utf-8")
    return raw[-LOG_CAP:].decode("utf-8", "ignore") if len(raw) > LOG_CAP else text


def send_log(reason, lang="en", bearer=None, crash_path=None, post=None) -> dict:
    """Blocking upload. -> {'ref': 'L-XXXXX'} or {'error': 'later'|'network'}."""
    post = post or _post
    body = {"device": device(lang), "reason": reason, "ts": _now_ms(), "text": log_text(crash_path)}
    try:
        status, answer = post(LOGS_URL, body, bearer)
    except Exception:  # noqa: BLE001
        return {"error": "network"}
    if status == 200 and isinstance(answer.get("ref"), str):
        return {"ref": answer["ref"]}
    return {"error": "later" if status in (429, 503) else "network"}


# --- crash detection --------------------------------------------------------------------------------

def opened(running_path=RUNNING_PATH, dirs=CRASH_DIRS):
    """Call when the plugin opens. -> the crash log of an unclean previous exit, or None.
    A crash = our running marker is still there AND an Enigma2 crash log is newer than it
    (a power cut with the plugin open leaves the marker but no crash log)."""
    crash = None
    try:
        if os.path.exists(running_path):
            crash = newest_crash_log(os.path.getmtime(running_path), dirs)
        with open(running_path, "w", encoding="ascii") as handle:
            handle.write(str(int(time.time())))
    except OSError:
        pass
    return crash


def closed(running_path=RUNNING_PATH) -> None:
    try:
        os.unlink(running_path)
    except OSError:
        pass
