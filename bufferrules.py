"""Pure player-buffer choices for the 1.3 GB Vu+ box (no Enigma2 imports, no I/O).

How the box applies the network buffer (read in openATV 7.6 lib/service/servicemp3.cpp + GStreamer 1.22
playbin2/uridecodebin/queue2, FIX11): servicemp3 reads NO config value for its stream buffer. It hard-codes playbin
"buffer-duration" = 5 s and "buffer-size" = m_buffer_size (5 MB); the only runtime knob is
iStreamedService.setBufferSize(bytes) (service.streamed()), which sets playbin "buffer-size". playbin copies it into
uridecodebin when the group activates (READY→PAUSED), which servicemp3 does from its bus handler after start() — so
the player calls it right after playService. queue2 stops reading ahead when ANY limit is full, and with its default
rate estimate the 5 s duration limit counts too: the bytes below make the buffer a full 5 s for any bitrate (the
default 5 MB holds only ~2 s of a 20 Mbit/s stream), but servicemp3 cannot hold minutes. service_flag 1 =
BUFFERING_ENABLED (prefill: pause at 0 %, play again at 100 % of that 5 s).

So minutes of buffer come the way E2iPlayer's "play with buffering" does it (FIX12): a TEMPORARY download of the
stream into <disk>/kanasa-downloads/.buffer/ runs ahead of the player, which plays the growing file from the disk.
The rules below decide when that happens, when the file is ready, when playback pauses for it, and where a seek
lands; bufferjob.py is the download, player.py the screen.
"""
from array import array
from bisect import bisect_left, bisect_right

from . import downloadrules, i18n

OFF = 0
WHOLE = 999                # owner 11-24 (0.8.1): "Whole episode" - the job runs to the end, the start is as quick
WHOLE_GOAL_S = 600         # as any other choice; its goal (the %, the start rule, the downloads' hold) is 10 min
CHOICES = (OFF, 1, 4, 10, 20, 30, WHOLE)
DEFAULT = 4
SAFE_CAP = 256 * 1024 * 1024
ESTIMATED_BITRATE = 8 * 1000 * 1000       # bits/s; conservative 1080p estimate

STALL_MARGIN_S = 15        # playing this close to the downloaded end: pause and show "Buffering…"
RESUME_AHEAD_S = 60        # ...and play again once this much is ahead (or the file is complete)
START_WAIT_S = 8           # the first start: after this long, START_AHEAD_S ahead is enough (box 11-19:
START_AHEAD_S = 20         # a 60 s rule held a start ~50 s)
FIRST_BYTES_S = 5          # FIX29: no playable byte on the disk after this long (a proxied MP4 whose index alone
                           # took longer, box 11-20): stream at once instead of losing FILL_GIVE_UP_S every start
FILL_GIVE_UP_S = 15        # still not ready after this long: stream the same server, the buffer is dropped
MIN_SPACE = 512 * 1024 ** 2  # free space a buffer needs on top of the 1 GB reserve to start at all
HOLD_FACTOR = 2            # queued downloads wait while the buffer is less than 2x the chosen minutes ahead
MP4_MARGIN_S = 30          # an MP4 started mid-film begins this long before the seek target (key frame, audio)
SUBDIR = ".buffer"                         # <disk>/kanasa-downloads/.buffer/ (bufferjob)
ONLINE_FORMATS = ("hls", "mp4")            # the buffer file can start at the resume point (Range / segment)


def clean(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value in CHOICES else DEFAULT


def next_choice(value, direction=1) -> int:
    value = clean(value)
    return CHOICES[(CHOICES.index(value) + (1 if direction >= 0 else -1)) % len(CHOICES)]


def choice_text(minutes, lang) -> str:
    """Settings -> Buffer's value: Off | N min | Whole episode (Arabic counts 11+ in the singular)."""
    minutes = clean(minutes)
    if minutes == OFF:
        return i18n.t("settings.buffer_off", lang)
    if minutes == WHOLE:
        return i18n.t("settings.buffer_whole", lang)
    return i18n.t("settings.buffer_value_many" if minutes > 10 else "settings.buffer_value", lang) % minutes


def goal_s(minutes) -> int:
    """Seconds ahead the chosen buffer aims at (the %, the first start, the downloads' hold)."""
    minutes = clean(minutes)
    return WHOLE_GOAL_S if minutes == WHOLE else minutes * 60


def _num(value) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else 0.0


# --- whether a play buffers -------------------------------------------------------------------------
def container(result) -> str:
    """hls | mkv | ts | mp4 (a direct file the host did not name is read as MP4)."""
    result = result or {}
    kind = str(result.get("kind") or "").lower()
    path = str(result.get("stream_url") or "").split("?")[0].split("#")[0].lower()
    if kind == "hls" or path.endswith(".m3u8"):
        return "hls"
    if kind == "mkv" or path.endswith(".mkv"):
        return "mkv"
    if path.endswith(".ts"):
        return "ts"
    return "mp4"


def start_mode(fmt, start_at, minutes) -> str:
    """Where the buffer file begins: "start" (byte 0: no resume, or the resume point is within the chosen
    minutes), "offset" (at the resume point: HLS from its segment, MP4 by Range after its moov), or "stream"
    (MKV / a raw TS cannot begin mid-file with a known time: today's stream)."""
    if _num(start_at) <= goal_s(minutes):
        return "start"
    return "offset" if fmt in ONLINE_FORMATS else "stream"


def plan(minutes, directory, result, start_at=0, free=None, torrent=False, local=False) -> tuple:
    """("buffer", None) or ("stream", reason). reason: off | local | torrent | no_disk | space | proxy | live |
    unsupported | unavailable | resume. Every "stream" is today's direct streaming; nothing ever blocks playback.
    HLS buffers too since 0.8.1 (owner 11-24); what the byte-append cannot carry the job refuses (hls_refusal)."""
    if clean(minutes) == OFF:
        return "stream", "off"
    if local:
        return "stream", "local"                  # already a file on the disk
    if torrent:
        return "stream", "torrent"                # TorrServer read-aheads in its RAM cache
    if isinstance(result, dict) and result.get("live") is True:
        return "stream", "live"
    if not directory:
        return "stream", "no_disk"
    if free is not None and not downloadrules.enough_space(free, MIN_SPACE):
        return "stream", "space"
    allowed, reason = downloadrules.downloadable(result)
    # /stream/proxy is a normal range-capable HTTP file for MP4.  The buffer worker is given the absolute
    # Kanasa base URL, so unlike a device-side download it is safe and useful here.
    if reason == "proxy" and container(result) == "mp4":
        allowed, reason = True, None
    if not allowed:
        return "stream", reason
    if start_mode(container(result), start_at, minutes) == "stream":
        return "stream", "resume"
    return "buffer", None


def progressive(path, done) -> bool:
    """exteplayer3 -o 1: a buffer .ts (HLS joined, or a raw TS) that is still growing when it is opened."""
    path = str(path or "")
    return not done and path.startswith("/") and "/%s/" % SUBDIR in path and path.lower().endswith(".ts")


# --- while the file fills ---------------------------------------------------------------------------------
def ahead(end_s, at_s) -> float:
    return max(0.0, _num(end_s) - max(0.0, float(at_s or 0)))


def percent(ahead_s, goal_s, done) -> int:
    """The card's "Buffering… N%": seconds ahead of the goal (the chosen minutes, or 1 min after a stall)."""
    if done:
        return 100
    goal = _num(goal_s)
    return max(0, min(99, int(_num(ahead_s) * 100 / goal))) if goal else 0


def ready(ahead_s, minutes, done, waited_s) -> bool:
    """Play the file: the chosen minutes are ahead, the whole file is done, or after START_WAIT_S at least
    START_AHEAD_S are ahead."""
    if done:
        return True
    ahead_s = _num(ahead_s)
    return ahead_s >= goal_s(minutes) or (_num(waited_s) >= START_WAIT_S and ahead_s >= START_AHEAD_S)


def give_up_fill(ahead_s, minutes, done, waited_s, has_data=True) -> bool:
    """Buffering never holds a start: not ready after FILL_GIVE_UP_S means stream instead - and a buffer with no
    playable bytes at all (has_data False) after FIRST_BYTES_S is given up at once."""
    if ready(ahead_s, minutes, done, waited_s):
        return False
    waited = _num(waited_s)
    return waited >= FILL_GIVE_UP_S or (not has_data and waited >= FIRST_BYTES_S)


def must_pause(at_s, end_s, done) -> bool:
    """Playing within STALL_MARGIN_S of the downloaded end of a file still downloading."""
    return not done and at_s is not None and _num(end_s) - float(at_s) <= STALL_MARGIN_S


def playable_after_failure(ahead_s) -> bool:
    """A failed job's file still starts when START_AHEAD_S are on the disk past the start (it then plays until
    must_pause, where the player streams at that second): the bytes are never wasted, a start never stalls."""
    return _num(ahead_s) >= START_AHEAD_S


def may_resume(at_s, end_s, done) -> bool:
    return bool(done) or (at_s is not None and _num(end_s) - float(at_s) >= RESUME_AHEAD_S)


def seek_action(target, start_s, end_s, done, can_restart) -> str:
    """inside (seek in the file) | restart (a new buffer at the target) | stream (today's stream at the target)."""
    target = max(0.0, float(target or 0))
    if target >= _num(start_s) - 0.5 and (done or target < _num(end_s)):
        return "inside"
    return "restart" if can_restart else "stream"


def inside_seek(launched_progressive) -> str:
    """How a seek inside the file is done. "gf": in-process (c + gf<s>). "relaunch": the file again, started at
    the target - for a play launched with -o 1 (box 11-24: gf in -o 1 mode landed 36-55 s late, and near the end of
    a buffer that completed meanwhile exteplayer3 sat at EOF waiting for bytes, black, forever). The relaunch takes
    -o 1 only while the job still runs (progressive)."""
    return "relaunch" if launched_progressive else "gf"


STUCK_S = 12               # a buffer play whose J has not moved this long (not paused, not seeking): stuck
END_NEAR_S = 30            # ...this close to the job's duration with the job done: that is the episode's end
RELAUNCH_MAX, RELAUNCH_WINDOW_S = 2, 60     # stuck relaunches per minute at most (then the 30 s stall rule)


def stuck_clock(still, at_s, now, watched):
    """(position, since) of a buffer play's last progress. None while it is not watched (paused, a seek or a
    launch in flight, the fill / stall phases) or has no position; J jitter under 0.2 s is no progress."""
    if not watched or at_s is None:
        return None
    at_s = float(at_s)
    if still is None or abs(at_s - still[0]) > 0.2:
        return (at_s, now)
    return still


def stuck_action(still, now, duration, done, relaunches) -> str:
    """watch (fine) | end (stuck within END_NEAR_S of a complete job's duration: the episode ended) | relaunch
    (the file again at the stuck second) | hold (RELAUNCH_MAX already in the last minute)."""
    if still is None or now - still[1] < STUCK_S:
        return "watch"
    duration = _num(duration)
    if done and duration > 0 and duration - still[0] <= END_NEAR_S:
        return "end"
    recent = [t for t in relaunches or () if now - t < RELAUNCH_WINDOW_S]
    return "relaunch" if len(recent) < RELAUNCH_MAX else "hold"


def on_failure(played, done) -> str:
    """What a failure of the buffered play means. "stream": the same server, streamed today's way, at the same
    second (the file never showed a picture, or it ran dry while still downloading); "next": the server is bad
    (the complete file played and broke)."""
    return "next" if played and done else "stream"


def hold_downloads(running, ahead_s, minutes) -> bool:
    """Queued downloads give way (highest priority) while a buffer runs and is not yet well ahead (the whole
    episode: its 10 min goal, never the whole film - owner 11-24)."""
    hold = goal_s(minutes) if clean(minutes) == WHOLE else HOLD_FACTOR * goal_s(minutes)
    return bool(running) and _num(ahead_s) < hold


def position_base(first_at, base_s) -> float:
    """A buffer file that starts mid-film (an HLS segment at base_s): if GStreamer reports the file's own time
    (starting near 0), the player adds base_s; if it reports the stream's real time, nothing."""
    base = _num(base_s)
    if base <= 30 or first_at is None:
        return 0.0
    return 0.0 if float(first_at) >= base - 30 else base


def bitrate(size, duration, fallback=ESTIMATED_BITRATE) -> float:
    """bits/s from a known size and duration, else the estimate."""
    size, duration = _num(size), _num(duration)
    return size * 8 / duration if size and duration else float(fallback)


def seconds_of(nbytes, bits_per_s) -> float:
    rate = _num(bits_per_s)
    return _num(nbytes) * 8 / rate if rate else 0.0


def buffered_end(at_s, length_s, end_s=None, preloaded_bytes=None, file_bytes=None):
    """Best known absolute buffered end for the second (grey) timeline fill.

    A temporary buffer file has an exact end, exteplayer3 reports how far it has loaded (J.lms), and
    TorrServer reports bytes preloaded for the selected file. None means no usable measurement; callers hide
    the fill rather than inventing one.
    """
    at, length = max(0.0, float(at_s or 0)), _num(length_s)
    if end_s is not None:
        end = _num(end_s)
    elif _num(preloaded_bytes) and _num(file_bytes) and length:
        end = min(1.0, _num(preloaded_bytes) / _num(file_bytes)) * length
    else:
        return None
    return min(length, max(at, end)) if length else max(at, end)


# --- HLS: the file begins at the segment holding the resume point -----------------------------------------
def hls_durations(text) -> list:
    """Each segment's EXTINF duration, in playlist order (one per segment URI)."""
    out, pending = [], 0.0
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF:"):
            try:
                pending = float(line[8:].split(",")[0].strip() or 0)
            except ValueError:
                pending = 0.0
        elif line and not line.startswith("#"):
            out.append(max(0.0, pending))
            pending = 0.0
    return out


def hls_refusal(text, first=0):
    """None when the media playlist can be byte-appended from segment `first` on into one growing .ts, else why
    not (owner 11-24: those stay streaming): empty | live (no ENDLIST) | aes128 | sample_aes | encrypted (another
    METHOD) | fmp4 (EXT-X-MAP) | byterange | discontinuity (a new timeline after the file's first segment: the
    joined file's clock would jump)."""
    text, index = str(text or ""), 0
    segments, problem = 0, None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-KEY"):
            method = line.split("METHOD=", 1)[1].split(",")[0].strip().upper() if "METHOD=" in line else ""
            if method != "NONE":
                problem = problem or {"AES-128": "aes128", "SAMPLE-AES": "sample_aes"}.get(method, "encrypted")
        elif line.startswith("#EXT-X-MAP"):
            problem = problem or "fmp4"
        elif line.startswith("#EXT-X-BYTERANGE"):
            problem = problem or "byterange"
        elif line.startswith("#EXT-X-DISCONTINUITY") and not line.startswith("#EXT-X-DISCONTINUITY-SEQUENCE"):
            if index > first:
                problem = problem or "discontinuity"
        elif line and not line.startswith("#"):
            segments += 1
            index += 1
    if not segments:
        return "empty"
    if "#EXT-X-ENDLIST" not in text:
        return "live"
    return problem


def hls_start(durations, at_s) -> tuple:
    """(segment index, its start time) of the segment that holds at_s (the first one for 0 / before it)."""
    at_s, start = _num(at_s), 0.0
    for index, length in enumerate(durations or []):
        if at_s < start + length:
            return index, start
        start += length
    return (max(0, len(durations or []) - 1), start - (durations[-1] if durations else 0.0))


# --- MP4: the index of a moov at the start says where each second lies ----------------------------------
def _box(data, offset):
    if offset + 8 > len(data):
        return None
    size, kind, header = int.from_bytes(data[offset:offset + 4], "big"), bytes(data[offset + 4:offset + 8]), 8
    if size == 1:
        if offset + 16 > len(data):
            return None
        size, header = int.from_bytes(data[offset + 8:offset + 16], "big"), 16
    elif size == 0:
        size = None                               # to the end of the file
    return kind, size, header


def mp4_moov_span(prefix) -> tuple:
    """(state, moov offset, moov end) from the file's first bytes. state: start (the complete moov comes before
    mdat) | more (read on: needs at least `moov end` bytes, or None when unknown) | end (mdat first: the index
    is at the end, a growing file cannot play) | bad."""
    data, offset = bytes(prefix or b""), 0
    while True:
        box = _box(data, offset)
        if box is None:
            return "more", None, None
        kind, size, header = box
        if kind == b"moov":
            if size is None or size < header:
                return "bad", None, None
            return ("start" if offset + size <= len(data) else "more"), offset, offset + size
        if kind == b"mdat":
            return "end", None, None
        if size is None or size < header:
            return "bad", None, None
        offset += size


def _children(data, start, end):
    offset = start
    while offset + 8 <= end:
        box = _box(data, offset)
        if box is None:
            return
        kind, size, header = box
        size = end - offset if size is None else size
        if size < header or offset + size > end:
            return
        yield kind, offset + header, offset + size
        offset += size


def _child(data, start, end, *path):
    for name in path:
        hit = next(((s, e) for kind, s, e in _children(data, start, end) if kind == name), None)
        if hit is None:
            return None
        start, end = hit
    return start, end


def _u32(data, at):
    return int.from_bytes(data[at:at + 4], "big")


def _scale(data, span):
    """(timescale, duration) of an mvhd/mdhd payload."""
    start, end = span
    if data[start] == 1:
        if start + 32 > end:
            raise ValueError("short")
        return _u32(data, start + 20), int.from_bytes(data[start + 24:start + 32], "big")
    if start + 20 > end:
        raise ValueError("short")
    return _u32(data, start + 12), _u32(data, start + 16)


def _track_chunks(data, trak):
    """[(byte offset, seconds)] of every chunk of one track."""
    start, end = trak
    mdhd = _child(data, start, end, b"mdia", b"mdhd")
    stbl = _child(data, start, end, b"mdia", b"minf", b"stbl")
    if not mdhd or not stbl:
        return []
    scale = _scale(data, mdhd)[0]
    if not scale:
        return []
    stts, stsc = _child(data, stbl[0], stbl[1], b"stts"), _child(data, stbl[0], stbl[1], b"stsc")
    stco, wide = _child(data, stbl[0], stbl[1], b"stco"), False
    if stco is None:
        stco, wide = _child(data, stbl[0], stbl[1], b"co64"), True
    if not stts or not stsc or not stco:
        return []
    width = 8 if wide else 4
    count = _u32(data, stco[0] + 4)
    offsets = [int.from_bytes(data[stco[0] + 8 + i * width:stco[0] + 8 + (i + 1) * width], "big")
               for i in range(min(count, (stco[1] - stco[0] - 8) // width))]
    runs = [(_u32(data, stsc[0] + 8 + i * 12), _u32(data, stsc[0] + 12 + i * 12))
            for i in range(min(_u32(data, stsc[0] + 4), (stsc[1] - stsc[0] - 8) // 12))]
    times = [(_u32(data, stts[0] + 8 + i * 8), _u32(data, stts[0] + 12 + i * 8))
             for i in range(min(_u32(data, stts[0] + 4), (stts[1] - stts[0] - 8) // 8))]
    if not runs:
        return []
    out, sample, run = [], 0, 0
    entry, entry_first, ticks = 0, 0, 0          # stts walk: the entry holding `sample`, its first sample, its time
    for chunk in range(1, len(offsets) + 1):
        while run + 1 < len(runs) and runs[run + 1][0] <= chunk:
            run += 1
        while entry < len(times) and sample >= entry_first + times[entry][0]:
            ticks += times[entry][0] * times[entry][1]
            entry_first += times[entry][0]
            entry += 1
        delta = times[entry][1] if entry < len(times) else 0
        out.append((offsets[chunk - 1], (ticks + (sample - entry_first) * delta) / float(scale)))
        sample += runs[run][1]
    return out


def mp4_index(moov, total=0):
    """The chunk map of a complete moov box (bytes from its header): {"duration", "offsets", "times", "ends",
    "suffix_min", "prefix_max"} sorted by byte offset; None when it cannot be read (e.g. a compressed moov)."""
    data = bytes(moov or b"")
    box = _box(data, 0)
    if not box or box[0] != b"moov":
        return None
    try:
        body = (box[2], len(data))
        mvhd = _child(data, body[0], body[1], b"mvhd")
        scale, length = _scale(data, mvhd) if mvhd else (0, 0)
        chunks = []
        for kind, start, end in _children(data, body[0], body[1]):
            if kind == b"trak":
                chunks += _track_chunks(data, (start, end))
    except (ValueError, IndexError):
        return None
    if not chunks:
        return None
    chunks.sort()
    offsets = array("q", (c[0] for c in chunks))
    times = array("d", (c[1] for c in chunks))
    last = int(total) if _num(total) and total > offsets[-1] else 2 ** 62
    ends = array("q", list(offsets[1:]) + [last])
    suffix, low = array("d", times), float("inf")
    for i in range(len(times) - 1, -1, -1):
        low = min(low, times[i])
        suffix[i] = low
    prefix, high = array("d", times), 0.0
    for i, value in enumerate(times):
        high = max(high, value)
        prefix[i] = high
    return {"duration": (length / float(scale)) if scale else max(times), "offsets": offsets, "times": times,
            "ends": ends, "suffix_min": suffix, "prefix_max": prefix}


def mp4_offset(index, seconds, margin=MP4_MARGIN_S) -> int:
    """The byte where a buffer for `seconds` starts: the first chunk of any track at or after seconds - margin."""
    wanted = _num(seconds) - margin
    if wanted <= 0 or not index:
        return 0
    hits = [o for o, t in zip(index["offsets"], index["times"]) if t >= wanted]
    return min(hits) if hits else index["offsets"][-1]


def mp4_start_time(index, offset) -> float:
    """The first second fully present when the file holds bytes from `offset` on (the chunks before it are
    missing; by mp4_offset they all start before seconds - margin)."""
    if not index or offset <= index["offsets"][0]:
        return 0.0
    i = bisect_left(index["offsets"], offset)
    return index["prefix_max"][i - 1] + 5.0 if i else 0.0


def mp4_end_time(index, offset, end) -> float:
    """The second up to which everything is present when bytes [offset, end) are on the disk."""
    if not index:
        return 0.0
    lo = bisect_left(index["offsets"], offset)
    i = bisect_right(index["ends"], end, lo)
    if i >= len(index["times"]):
        return index["duration"]
    return index["suffix_min"][i]
