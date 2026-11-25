"""086: sub-scene Arabic subtitles run on the box (specs/086-subscene-on-device contract). Pure: no Enigma2.

Owner rule (11-24): the apps fetch sub-scene with their own connection, never through our server. With a worker whose
bundle answers setup with a `subscene` key, /api/subtitles is asked with device_subs=1; its `local.subscene` job runs
on the host worker (`subscene`, 20 s), the tracks it finds go FIRST in the Arabic group, and a picked track's file is
its own `vtt` (series) or the worker's `subscene_file` (films). Any failure (timeout, old bundle, sub-scene down):
the server's tracks alone, no error shown, nothing cached. A clean `none` is kept 24 h (contract §2).
RAR (contract §4): the box reads RAR with libarchive; without it the bundle answers bad_zip and the player walks to
the next track of the language (subtitle_file_failed's rule), never a loop.
"""
import time

PREFIX = "ss:"                 # a panel track id of a device sub-scene track (never sent to /api/subtitles/file)
NONE_KEEP_S = 86400.0

_none = {}                     # job key -> clock value until which "none" holds (this plugin run)


def log(message):
    print("[Kanasa] devicesubs: %s" % message)


def reset() -> None:
    _none.clear()


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def capable(setup) -> bool:
    """The running bundle has device.subscene: its setup() answers a `subscene` key (bundle 32+)."""
    return isinstance(setup, dict) and isinstance(setup.get("subscene"), dict)


def rar_mode(setup) -> str:
    found = setup.get("subscene") if isinstance(setup, dict) else None
    mode = found.get("rar") if isinstance(found, dict) else None
    return mode if isinstance(mode, str) else "?"


def job_of(data):
    local = data.get("local") if isinstance(data, dict) else None
    job = local.get("subscene") if isinstance(local, dict) else None
    return job if isinstance(job, dict) else None


def _key(job) -> tuple:
    return tuple(str(job.get(name)) for name in ("format", "kind", "imdb", "season", "episode"))


def tracks_of(answer, job) -> list:
    """The worker's {"status", "tracks"} as panel tracks: id "ss:<sub_id>", source sub-scene, best first."""
    rows = answer.get("tracks") if isinstance(answer, dict) and not answer.get("error") else None
    out, seen = [], set()
    for row in rows if isinstance(rows, list) else []:
        sub_id = _int(row.get("sub_id")) if isinstance(row, dict) else None
        if not sub_id or sub_id <= 0 or sub_id in seen:
            continue
        seen.add(sub_id)
        release = row.get("release") if isinstance(row.get("release"), str) else ""
        label = row.get("label") if isinstance(row.get("label"), str) and row["label"] else "AR · sub-scene"
        track = {"id": PREFIX + str(sub_id), "lang": "AR", "label": label[:120], "release": release[:200],
                 "source": "sub-scene", "device": True, "sub_id": sub_id, "hi": bool(row.get("hi")),
                 "season_pack": bool(row.get("season_pack")),
                 "season": _int(job.get("season")) if job.get("kind") == "tv" else None,
                 "episode": _int(job.get("episode")) if job.get("kind") == "tv" else None}
        if isinstance(row.get("vtt"), str) and row["vtt"]:
            track["vtt"] = row["vtt"]
        out.append(track)
    return out


def merge(server_tracks, device_tracks) -> list:
    """Contract §3: the sub-scene tracks go just before the server's first Arabic track (after the other languages
    the viewer ranked first), or at the end when the server has no Arabic."""
    server = [t for t in server_tracks or [] if isinstance(t, dict)]
    if not device_tracks:
        return server
    at = next((i for i, t in enumerate(server) if str(t.get("lang") or "").upper() == "AR"), len(server))
    return server[:at] + list(device_tracks) + server[at:]


def is_device(track) -> bool:
    return isinstance(track, dict) and track.get("device") is True and str(track.get("id") or "").startswith(PREFIX)


def file_args(track) -> dict:
    return {"sub_id": track.get("sub_id"), "season": track.get("season"), "episode": track.get("episode")}


def file_answer(ok, value):
    """(vtt, None) or (None, code) of a subscene_file answer."""
    if ok and isinstance(value, dict) and isinstance(value.get("vtt"), str):
        return (value["vtt"], None) if value["vtt"].strip() else (None, "empty")
    if ok and isinstance(value, dict):
        code = value.get("error")
        return None, code if isinstance(code, str) and code else "error"
    return None, value if isinstance(value, str) else "error"


class FileError(Exception):
    """A device sub-scene file that did not load; `code` for subtitlelayout.failure_code's log line."""

    def __init__(self, code):
        Exception.__init__(self, code)
        self.code = "subscene:%s" % code


class Lookup:
    """One /api/subtitles answer and its sub-scene job: done(payload) once, with the device tracks merged in.
    call(op, args, callback(ok, value)) is hostclient.HostClient.call (it may refuse at once)."""

    def __init__(self, call, done, clock=time.monotonic):
        self.call, self.done, self.clock = call, done, clock
        self.payload, self.job, self.finished, self.cancelled = None, None, False, False

    def start(self, payload) -> None:
        self.payload = dict(payload) if isinstance(payload, dict) else {"status": "unavailable", "tracks": []}
        self.payload.pop("local", None)
        self.job = job_of(payload)
        if self.job is None:
            self._finish([])
            return
        until = _none.get(_key(self.job))
        if until is not None and self.clock() < until:
            self._finish([])
            return
        try:
            self.call("subscene", {"job": self.job}, self._answered)
        except Exception as error:  # noqa: BLE001 - the server's tracks still show
            log("subscene call failed: %r" % (error,))
            self._finish([])

    def _answered(self, ok, value):
        if self.cancelled or self.finished:
            return
        tracks = tracks_of(value, self.job) if ok else []
        if ok and isinstance(value, dict) and not value.get("error"):
            log("subscene %s: %s, %d track(s)" % (self.job.get("imdb"), value.get("status"), len(tracks)))
            if value.get("status") == "none" and not tracks:
                _none[_key(self.job)] = self.clock() + NONE_KEEP_S
        else:
            code = value.get("error") if isinstance(value, dict) else value
            log("subscene %s: %s (server tracks only)" % (self.job.get("imdb"), code))
        self._finish(tracks)

    def _finish(self, tracks):
        if self.finished or self.cancelled:
            return
        self.finished = True
        out = dict(self.payload)
        out["tracks"] = merge(out.get("tracks"), tracks)
        if tracks:
            out["status"] = "ok"
        self.done(out)

    def cancel(self) -> None:
        self.cancelled = True
