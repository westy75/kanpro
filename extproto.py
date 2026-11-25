"""Our own exteplayer3 (phase 1): its command line and the JSON lines it prints on stderr. Pure, no Enigma2.

Measured on the box (Vu+ armv7, EPLAYER3_EXTENDED 183, tests/fixtures/exteplayer3):
- every status line is one JSON object with one key, on stderr; a chunk can end in the middle of a line;
- `-9 N` is an INDEX into the s_l list, while the runtime `s<id>` uses the track id;
- subtitle atoms `s_a` carry start/end in ms and UTF-8 text; Arabic starts with U+202B and breaks with \\r\\n.
"""
from collections import namedtuple
import html
import json
import re

EXE = "/usr/bin/exteplayer3"
Event = namedtuple("Event", "kind data")
# FIX29 (box 2026-11-20): our /stream/proxy rewrites every HLS segment to "/stream/proxy?url=..." - a path without
# a media extension. Newer ffmpeg HLS demuxers refuse such segment URLs before the first GET ("extension_picky",
# AVERROR_INVALIDDATA -1094995529 = the box's FF_ERROR "Invalid data found"). exteplayer3 -f hands ffmpeg options
# (e2iplayer XMP:2218-2222); an ffmpeg without them ignores unknown options.
HLS_FFOPTS = ("extension_picky=0", "allowed_segment_extensions=ALL")
MAX_PENDING = 1 << 20                     # a "line" this long without a newline is garbage: dropped

# e2iplayer's repairs of two known broken prefixes (iptvextmovieplayer.py eplayer3DataAvailable)
_REPAIRS = (('{"PLAYBACK_LENGTH":{"PLAYBACK_LENGTH":', '{"PLAYBACK_LENGTH":'),
            ('{"PLAYBACK_LENGTH":{"J":', '{"J":'))
_MARKS = re.compile("[‎‏‪-‮⁦-⁩]")
_FORMAT_TAG = re.compile(r"</?(?:i|b|u|s|font)(?:\s[^>]*)?>", re.IGNORECASE)
_ASS_TAG = re.compile(r"\{\\[^}]*\}")
_SPACE = re.compile(r"[ \t]+")


# --- the command line -------------------------------------------------------------------------------------
def _clean_value(value):
    """A header value that cannot break the -h block (no CR/LF), else None."""
    text = str(value if value is not None else "").strip()
    if not text or "\r" in text or "\n" in text or "\0" in text:
        return None
    return text


def header_block(headers) -> str:
    """Every header except User-Agent (-u) and Range as "Key: value\\r\\n" (exteplayer3 -h)."""
    out = []
    for name, value in (headers or {}).items():
        name = str(name or "").strip()
        if not name or name.lower() in ("user-agent", "range") or not re.match(r"^[A-Za-z0-9-]+$", name):
            continue
        value = _clean_value(value)
        if value is not None:
            out.append("%s: %s\r\n" % (name, value))
    return "".join(out)


def build_argv(url, headers=None, audio_index=None, sub_index=None, exe=EXE, hls=False, progressive=False) -> list:
    """The exteplayer3 argument list (argv[0] first, the URL last) - never a shell string.

    audio_index -> -t (an INDEX into a_l), sub_index -> -9 (an INDEX into s_l), never the track ids: see
    track_index(); hls -> -f
    HLS_FFOPTS; progressive -> -o 1 (box 11-24: a buffer .ts still growing - without it exteplayer3 takes the length
    the file had at open and STOPS there; with it, it played the growing file to the end at <1% CPU). A "#..." fragment (Enigma2's service-path header syntax) never reaches the process: headers go
    in -u/-h, and a fragment is never sent to a server anyway."""
    url = str(url or "").split("#", 1)[0]
    if not url or url.startswith("-") or any(c in url for c in "\r\n\0"):
        raise ValueError("unplayable url")
    argv = [exe]
    agent = None
    for name, value in (headers or {}).items():
        if str(name).lower() == "user-agent":
            agent = _clean_value(value)
    if agent:
        argv += ["-u", agent]
    block = header_block(headers)
    if block:
        argv += ["-h", block]
    for flag, value in (("-t", audio_index), ("-9", sub_index)):
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            argv += [flag, str(value)]
    if progressive:
        argv += ["-o", "1"]
    if hls:
        for option in HLS_FFOPTS:
            argv += ["-f", option]
    argv.append(url)
    return argv


def track_index(tracks, track_id):
    """The position of track_id in a track list, or None. -9 (s_l) and -t (a_l) both take this INDEX, never
    the id: box-measured for -9 (`-9 7` = id 10), and e2iplayer stores the row index for -t
    (selectAudioTrackCallback track_idx -> setAudioTrackIdx -> `-t %d`, XMP:879-885, 2198-2201)."""
    if track_id is None or isinstance(track_id, bool):
        return None
    for index, row in enumerate(tracks or []):
        if isinstance(row, dict) and row.get("id") == track_id:
            return index
    return None


def sub_index(tracks, track_id):
    """The position of track_id in the s_l list (what -9 wants), or None."""
    return track_index(tracks, track_id)


def audio_index(tracks, track_id):
    """The position of track_id in the a_l list (what -t wants), or None (a_l id 2 = -t 1 on the box file)."""
    return track_index(tracks, track_id)


# --- the stderr lines ---------------------------------------------------------------------------------------
def _int(value, default=None):
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == value:
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def clean_atom_text(text) -> str:
    """A subtitle atom's text for our own line drawing.

    The direction marks go (our labels lay Arabic out themselves), \\r\\n becomes a line break, SubRip/ASS
    formatting tags go, and any other "<...>" is real text (a German line reads "<Die neuvermählte Kaiserin>"):
    it is entity-escaped so that subs.plain_text, which removes tags, gives it back unchanged."""
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = _MARKS.sub("", value)
    value = _ASS_TAG.sub("", _FORMAT_TAG.sub("", value))
    value = value.replace("\\N", "\n").replace("\\n", "\n")
    lines = [_SPACE.sub(" ", line).strip() for line in value.split("\n")]
    return html.escape("\n".join(line for line in lines if line), quote=False)


def _track(obj):
    if not isinstance(obj, dict):
        return None
    ident = _int(obj.get("id"))
    if ident is None:
        return None
    return {"id": ident, "codec": str(obj.get("e") or ""), "lang": str(obj.get("n") or "")}


def _tracks(obj):
    return [row for row in (_track(item) for item in (obj if isinstance(obj, list) else [])) if row is not None]


def _sts(obj):
    return _int(obj.get("sts"), 0) if isinstance(obj, dict) else 0


def _event(key, obj):
    """One decoded {key: obj} line as an Event, or None when it is malformed."""
    d = obj if isinstance(obj, dict) else {}
    if key == "EPLAYER3_EXTENDED":
        return Event("version", {"version": _int(d.get("version"))})
    if key == "PLAYBACK_OPEN":
        return Event("open", {"sts": _sts(d), "file": str(d.get("file") or "")})
    if key == "OUTPUT_OPEN":
        return Event("output_open", {"sts": _sts(d)})
    if key == "PLAYBACK_PLAY":
        return Event("play", {"sts": _sts(d)})
    if key == "PLAYBACK_STOP":
        return Event("stop", {"sts": _sts(d)})
    if key == "PLAYBACK_LENGTH":
        length = _int(d.get("length"))
        return None if length is None else Event("length", {"sts": _sts(d), "length": length})
    if key == "PLAYBACK_CURRENT_TIME":
        sec = _int(d.get("sec"))
        return None if sec is None else Event("time", {"sts": _sts(d), "sec": sec})
    if key in ("PLAYBACK_SEEK_ABS", "PLAYBACK_SEEK"):
        return Event("seek_abs" if key == "PLAYBACK_SEEK_ABS" else "seek", {"sts": _sts(d), "sec": _int(d.get("sec"))})
    if key == "PLAYBACK_INFO":
        return Event("status", {"paused": bool(_int(d.get("isPaused"), 0)), "playing": bool(_int(d.get("isPlaying"), 0)),
                                "forwarding": bool(_int(d.get("isForwarding"), 0)), "raw": d})
    if key == "J":
        ms = _int(d.get("ms"))
        if ms is None:
            return None
        return Event("position", {"ms": ms, "lms": _int(d.get("lms"))})
    if key == "T":
        ms = _int(d.get("c"))
        return None if ms is None else Event("trigger", {"ms": ms})
    if key == "v_c":
        fps = _int(d.get("f"))
        return Event("video", {"codec": str(d.get("e") or ""), "width": _int(d.get("w"), 0),
                               "height": _int(d.get("h"), 0), "fps": fps / 1000.0 if fps else None,
                               "progressive": _int(d.get("p")), "aspect": (_int(d.get("an")), _int(d.get("ad")))})
    if key == "v_e":
        return Event("video_change", {"width": _int(d.get("w"), 0), "height": _int(d.get("h"), 0)})
    if key == "a_l":
        return Event("audio_list", _tracks(obj))
    if key == "s_l":
        return Event("sub_list", _tracks(obj))
    if key in ("a_c", "s_c"):
        row = _track(d)
        return None if row is None else Event("audio_current" if key == "a_c" else "sub_current", row)
    if key in ("a_s", "s_s"):
        ident = _int(d.get("id"))
        return None if ident is None else Event("audio_selected" if key == "a_s" else "sub_selected",
                                                {"id": ident, "sts": _sts(d)})
    if key == "s_a":
        ident, start, end = _int(d.get("id")), _int(d.get("s")), _int(d.get("e"))
        if ident is None or start is None or end is None or end <= start:
            return None
        text = clean_atom_text(d.get("t"))
        if not text:
            return None
        return Event("sub_atom", {"id": ident, "start": start / 1000.0, "end": end / 1000.0, "text": text})
    if key == "s_f":
        return Event("sub_flush", {})
    if key == "N":
        return Event("loop", {"loop": bool(_int(d.get("isLoop"), 0))})
    if key == "FF_ERROR":
        return Event("error", {"msg": str(d.get("msg") or ""), "code": _int(d.get("code"))})
    return Event("unknown", {"key": key, "value": obj})


def parse_line(line):
    """One stderr line -> an Event, or None (empty, not JSON, or malformed)."""
    item = str(line or "").strip()
    for bad, good in _REPAIRS:
        item = item.replace(bad, good)
    if item.endswith(":"):
        item = item[:-1]
    if not item.startswith("{"):
        return None                           # exteplayer3's own log text
    try:
        obj = json.loads(item)
    except ValueError:
        return None
    if not isinstance(obj, dict) or len(obj) != 1:
        return None
    key = next(iter(obj))
    return _event(key, obj[key])


class Parser:
    """stderr chunks -> Events. Bytes are buffered (a chunk may end inside a line, even inside a UTF-8 letter)."""

    def __init__(self):
        self.pending = b""

    def feed(self, chunk) -> list:
        if chunk is None:
            return []
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8", "surrogateescape")
        data = self.pending + bytes(chunk)
        lines = data.split(b"\n")
        self.pending = lines.pop()
        if len(self.pending) > MAX_PENDING:
            self.pending = b""
        events = []
        for raw in lines:
            event = parse_line(raw.decode("utf-8", "replace"))
            if event is not None:
                events.append(event)
        return events
