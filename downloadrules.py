"""Pure download rules: disk, space, Range resume, re-resolve, HLS, ranking, row states (no Enigma2 imports, no I/O).

The app's 047/064 rules on the box. Downloads are the device's own (the app's owner decision 2: open to every
viewer, visitors included); a row whose link is signed for the server (needs_proxy) is never downloadable and
nothing is ever fetched through the server.
"""
import posixpath
import re
from urllib.parse import urljoin

from . import failover, torrents

DIR_NAME = "kanasa-downloads"
RESERVE_BYTES = 1024 ** 3                  # keep >= 1 GB free on the disk (brief FIX9)
EXPIRY_STATUSES = (403, 404, 410)          # the source's link expired: resolve the same server again
RERESOLVE_MAX, RERESOLVE_WINDOW_S = 3, 3600  # at most 3 re-resolves per item per hour (047 FR-014)
BACKOFF_S = (10, 30, 120, 600)             # network errors: retry the same link (047 FR-014)
MAX_HEIGHT = 720                           # 047: the highest quality not above 720p by default
PARTIAL_PLAY_BYTES = 20 * 1024 ** 2        # watch while downloading: at least this much on the disk
PARTIAL_PLAY_EXT = (".mp4", ".ts")         # progressive formats GStreamer reads while the file grows
ACTIVE = ("queued", "resolving", "downloading", "waiting")
VIDEO_KINDS = ("mp4", "mkv", "hls")


def _int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


# --- where ----------------------------------------------------------------------------------------
def _eligible(disks) -> list:
    return [d for d in disks or [] if isinstance(d, dict) and d.get("mounted") and d.get("writable")
            and str(d.get("path") or "") not in ("/", "/usr/share")
            and isinstance(d.get("free", RESERVE_BYTES), int)
            and d.get("free", RESERVE_BYTES) >= RESERVE_BYTES]


def mount_for(path, disks):
    """The disk row whose mount point holds `path` (the longest matching mount point), or None."""
    path = str(path or "").rstrip("/")
    if not path.startswith("/"):
        return None
    best = None
    for disk in disks or []:
        mount = str(disk.get("path") or "").rstrip("/")
        if mount and (path == mount or path.startswith(mount + "/")):
            if best is None or len(mount) > len(str(best.get("path") or "").rstrip("/")):
                best = disk
    return best


def movie_paths(default_path=None, videodirs=None) -> list:
    """Enigma2's own movie locations, most wanted first: config.usage.default_path, then
    config.movielist.videodirs (read on the box via Components.config and passed in here)."""
    out = []
    for value in [default_path] + list(videodirs if isinstance(videodirs, (list, tuple)) else []):
        if isinstance(value, str) and value.startswith("/") and value.rstrip("/") and value not in out:
            out.append(value)
    return out


def choose_dir(disks, preferred=None, choice=None):
    """Where downloads go: the viewer's own pick (Settings → Download folder) while that disk is usable; else
    the disk of Enigma2's movie location (preferred: a path or a list of paths), /media/hdd, then the eligible
    disk with most free space. Never the flash. None without a usable disk."""
    eligible = _eligible(disks)
    if isinstance(choice, str) and choice not in ("", "auto"):
        hit = next((d for d in eligible if str(d.get("path") or "").rstrip("/") == choice.rstrip("/")), None)
        if hit:
            return posixpath.join(hit["path"], DIR_NAME)
    wanted = [preferred] if isinstance(preferred, str) else list(preferred or [])
    for path in wanted + ["/media/hdd"]:
        hit = mount_for(path, eligible)
        if hit:
            return posixpath.join(hit["path"], DIR_NAME)
    hit = max(eligible, key=lambda d: d.get("free", RESERVE_BYTES)) if eligible else None
    return posixpath.join(hit["path"], DIR_NAME) if hit else None


def folder_choices(disks) -> list:
    """The disks the Settings folder chooser lists: [(mount point, free bytes)], most free space first."""
    rows = [(d["path"], d.get("free") if isinstance(d.get("free"), int) else 0) for d in _eligible(disks)]
    return sorted(rows, key=lambda row: -row[1])


def mount_rows(text) -> list:
    """Eligible real disks/network shares from /proc/mounts; I/O checks are added by torrengine."""
    out = []
    for raw in str(text or "").splitlines():
        fields = raw.split()
        if len(fields) < 4:
            continue
        device, path, fs, opts = fields[:4]
        real = device.startswith(("/dev/sd", "/dev/mmcblk1")) or fs.lower() in ("nfs", "nfs4", "cifs")
        if real and path != "/" and "rw" in opts.split(","):
            out.append({"device": device, "path": path.replace("\\040", " "), "fs": fs,
                        "noexec": "noexec" in opts.split(",")})
    return out


def enough_space(free_bytes, expected_bytes=0, reserve=RESERVE_BYTES) -> bool:
    """Free space after the file (its known or estimated size; 0 when unknown) must stay >= reserve."""
    if not isinstance(free_bytes, int) or isinstance(free_bytes, bool):
        return False
    return free_bytes - _int(expected_bytes) >= reserve


def remaining(item) -> int:
    """Bytes still to come for the space check (an unknown size counts as 0: the reserve still applies)."""
    return max(0, _int(item.get("size")) - _int(item.get("done")))


def gb(value) -> str:
    return "%.1f GB" % (max(0, value or 0) / 1024.0 ** 3)


# --- HTTP resume ------------------------------------------------------------------------------------
def range_headers(done, referer=None, base=None) -> dict:
    """base: a device row's own headers (085 T108: the bundle's exact User-Agent + Referer), sent as they are on the
    playlist, every segment and every Range request; its Referer wins over `referer`."""
    headers = {}
    if isinstance(base, dict):
        for name in ("User-Agent", "Referer"):
            value = base.get(name)
            if isinstance(value, str) and value and not any(c in value for c in "\r\n\0"):
                headers[name] = value
        if headers.get("Referer"):
            referer = None
    if _int(done):
        headers["Range"] = "bytes=%d-" % done
    if isinstance(referer, str) and referer:
        headers["Referer"] = referer
    return headers


def resume_action(done, status) -> str:
    """write | append | restart (a host ignored Range) | reresolve (expired) | retry (anything else)."""
    if status == 206 and done:
        return "append"
    if status == 200 and done:
        return "restart"
    if status in (200, 206):
        return "write"
    return "reresolve" if status in EXPIRY_STATUSES else "retry"


def should_reresolve(status) -> bool:
    return status in EXPIRY_STATUSES


def total_size(status, headers, done) -> int:
    """The whole file's length from a 206 Content-Range (or a 200's Content-Length); 0 = unknown."""
    headers = {str(k).lower(): v for k, v in (headers or {}).items()}
    match = re.search(r"/(\d+)\s*$", str(headers.get("content-range") or ""))
    if status == 206 and match:
        return int(match.group(1))
    length = str(headers.get("content-length") or "")
    if not length.isdigit():
        return 0
    return int(length) + (_int(done) if status == 206 else 0)


def same_file(old_total, new_total) -> bool:
    """Append only to the provably same file: equal total length (047 fingerprint). Unknown = not proven."""
    return bool(old_total) and old_total == new_total


def reresolve_budget(stamps, now):
    """(allowed, stamps to keep): at most RERESOLVE_MAX per item in RERESOLVE_WINDOW_S."""
    kept = [s for s in stamps or [] if isinstance(s, (int, float)) and now - s < RERESOLVE_WINDOW_S]
    if len(kept) >= RERESOLVE_MAX:
        return False, kept
    return True, kept + [now]


def backoff(attempt) -> int:
    """Seconds before network retry number `attempt` (0-based): 10 s, 30 s, 2 min, then every 10 min."""
    return BACKOFF_S[min(_int(attempt), len(BACKOFF_S) - 1)]


# --- what a resolved row is --------------------------------------------------------------------------
def downloadable(result) -> tuple:
    """(allowed, reason). reason: proxy (signed for the server) | live | unsupported | unavailable."""
    if not isinstance(result, dict):
        return False, "unavailable"
    if result.get("device") is True:
        # 085 T108: resolved on this box for this box - downloadable direct with its own headers (SC-008); a
        # device row without its User-Agent would 403 on UA-bound tokens, so it is not downloadable
        headers = result.get("headers") if isinstance(result.get("headers"), dict) else {}
        if not isinstance(headers.get("User-Agent"), str) or not headers["User-Agent"]:
            return False, "unsupported"
    elif result.get("needs_proxy") is True:
        return False, "proxy"
    if result.get("live") is True:
        return False, "live"
    kind = str(result.get("kind") or "").lower()
    if kind not in VIDEO_KINDS or not isinstance(result.get("stream_url"), str) or not result["stream_url"]:
        return False, "unsupported"
    return True, None


def pick_quality(result, max_height=MAX_HEIGHT):
    """(stream url, height): the highest quality not above max_height, else the lowest one above it,
    else the row's own stream_url."""
    rows = [q for q in (result or {}).get("qualities") or []
            if isinstance(q, dict) and isinstance(q.get("stream_url"), str) and q["stream_url"] and _int(q.get("height"))]
    under = [q for q in rows if q["height"] <= max_height]
    if under:
        best = max(under, key=lambda q: q["height"])
        return best["stream_url"], best["height"]
    if rows:
        best = min(rows, key=lambda q: q["height"])
        return best["stream_url"], best["height"]
    return (result or {}).get("stream_url"), None


def extension(kind, url="") -> str:
    """The file's extension: hls -> .ts (.mp4 for fMP4 segments, decided later), mkv -> .mkv, else .mp4."""
    kind = str(kind or "").lower()
    if kind == "hls":
        return ".ts"
    if kind == "mkv" or str(url or "").split("?")[0].lower().endswith(".mkv"):
        return ".mkv"
    return ".mp4"


# --- HLS (VOD, not encrypted): the segments are joined into one file ----------------------------------
def hls_variants(text, base) -> list:
    """[(height, bandwidth, url)] of a master playlist; [] for a media playlist."""
    lines = [line.strip() for line in str(text or "").splitlines()]
    out = []
    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue
        uri = next((row for row in lines[index + 1:] if row and not row.startswith("#")), None)
        if not uri:
            continue
        res = re.search(r"RESOLUTION=\d+x(\d+)", line)
        bandwidth = re.search(r"(?:^|[:,])BANDWIDTH=(\d+)", line)
        out.append((int(res.group(1)) if res else 0, int(bandwidth.group(1)) if bandwidth else 0,
                    urljoin(base, uri)))
    return out


def pick_variant(variants, max_height=MAX_HEIGHT):
    """The best variant not above max_height (height, then bandwidth); else the smallest one above it."""
    if not variants:
        return None
    if all(not v[0] for v in variants):        # no RESOLUTION anywhere: the middle bandwidth
        ordered = sorted(variants, key=lambda v: v[1])
        return ordered[(len(ordered) - 1) // 2][2]
    under = [v for v in variants if v[0] and v[0] <= max_height]
    if under:
        return max(under, key=lambda v: (v[0], v[1]))[2]
    return min((v for v in variants if v[0]), key=lambda v: (v[0], v[1]))[2]


def hls_media(text, base) -> dict:
    """A media playlist: segment urls, the init (EXT-X-MAP) url, encrypted, endlist, total duration."""
    segments, init, encrypted, duration = [], None, False, 0.0
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-KEY"):
            method = re.search(r"METHOD=([A-Za-z0-9-]+)", line)
            encrypted = encrypted or not method or method.group(1).upper() != "NONE"
        elif line.startswith("#EXT-X-MAP"):
            uri = re.search(r'URI="([^"]+)"', line)
            init = urljoin(base, uri.group(1)) if uri else init
        elif line.startswith("#EXTINF:"):
            value = re.match(r"#EXTINF:\s*([0-9.]+)", line)
            duration += float(value.group(1)) if value else 0.0
        elif line and not line.startswith("#"):
            segments.append(urljoin(base, line))
    return {"segments": segments, "init": init, "encrypted": encrypted,
            "endlist": "#EXT-X-ENDLIST" in str(text or ""), "duration": round(duration, 1)}


def hls_problem(media):
    """None when the playlist can be stored, else the reason: unsupported | live | encrypted."""
    if not media.get("segments"):
        return "unsupported"
    if not media.get("endlist"):
        return "live"
    if media.get("encrypted"):
        return "encrypted"
    return None


def hls_fingerprint(media) -> dict:
    return {"segments": len(media.get("segments") or []), "duration": media.get("duration") or 0.0}


def hls_same(old, new) -> bool:
    """The same stream after a re-resolve: equal segment count and duration within 1 s (047)."""
    return bool(old) and old.get("segments") == new.get("segments") and \
        abs((old.get("duration") or 0) - (new.get("duration") or 0)) <= 1.0


def hls_estimate(done_bytes, done_segments, total_segments) -> int:
    """Expected total bytes from the segments stored so far (for the % and the space check)."""
    if not done_segments or not total_segments:
        return 0
    return int(done_bytes * total_segments / done_segments)


# --- items ------------------------------------------------------------------------------------------
def item_key(kind, tmdb_id, season=None, episode=None) -> str:
    base = "%s:%d" % ("movie" if kind == "movie" else "tv", int(tmdb_id))
    return base if season is None else "%s:%d:%d" % (base, int(season), int(episode))


def find(items, key):
    return next((row for row in items or [] if isinstance(row, dict) and row.get("key") == key), None)


def stem(item) -> str:
    """A readable, safe file name: "Title (2020)" or "Title S01E04"."""
    title = re.sub(r"[^\w .()-]+", "_", str(item.get("title") or "")).strip(" ._")[:80] or "video"
    if item.get("season") is not None:
        return "%s S%02dE%02d" % (title, int(item["season"]), int(item["episode"]))
    return "%s (%s)" % (title, item["year"]) if item.get("year") else title


def percent(item) -> int:
    if (item or {}).get("state") == "downloaded":
        return 100
    done, total = _int(item.get("done")), _int(item.get("size"))
    return max(0, min(99, int(done * 100 / total))) if total else 0


def row_state(item) -> str:
    """A button/row state: download | queued | NN% | downloaded | failed-retry."""
    if not item:
        return "download"
    state = item.get("state") or "queued"
    if state == "downloaded":
        return "downloaded"
    if state == "failed":
        return "failed-retry"
    if state in ("downloading", "waiting"):
        return "%d%%" % percent(item)
    return "queued"


def state_label(item, t, lang) -> str:
    """The text of a Download button or a Downloads row state (t = i18n.t)."""
    state = row_state(item)
    if state.endswith("%"):
        key = "download.state.waiting" if (item or {}).get("state") == "waiting" else "download.state.progress"
        return t(key, lang) % state
    return t("download.state." + state, lang)


def episode_states(episodes, downloads, kind, tmdb_id, season) -> list:
    by_key = {row.get("key"): row for row in downloads or [] if isinstance(row, dict)}
    return [dict(ep, download_state=row_state(by_key.get(item_key(kind, tmdb_id, season, ep.get("number")))))
            for ep in episodes or []]


def season_eligible(episodes, downloads, kind, tmdb_id, season) -> list:
    """Released episodes neither downloaded nor queued (a failed one may be queued again)."""
    by_key = {row.get("key"): row for row in downloads or [] if isinstance(row, dict)}
    out = []
    for ep in episodes or []:
        if not isinstance(ep, dict) or ep.get("released", True) is False or not isinstance(ep.get("number"), int):
            continue
        old = by_key.get(item_key(kind, tmdb_id, season, ep["number"]))
        if old is None or old.get("state") == "failed":
            out.append(ep)
    return out


def season_done(episodes, downloads, kind, tmdb_id, season) -> tuple:
    """(downloaded, released) for "N of M downloaded"."""
    released = [ep for ep in episodes or [] if isinstance(ep, dict) and ep.get("released", True) is not False]
    by_key = {row.get("key"): row for row in downloads or [] if isinstance(row, dict)}
    done = sum(1 for ep in released
               if (by_key.get(item_key(kind, tmdb_id, season, ep.get("number"))) or {}).get("state") == "downloaded")
    return done, len(released)


def next_queued(items):
    return next((row for row in items or [] if isinstance(row, dict) and row.get("state") == "queued"), None)


MAX_TORRENT_DOWNLOADS = 1    # the 1.3 GB box: one torrent at a time in the engine's RAM cache


def next_to_start(items, running, limit=1, free=None):
    """The next queued item a free worker may start, or None: at most `limit` (Settings → Simultaneous
    downloads, 1-4) run at once; at most MAX_TORRENT_DOWNLOADS torrents share the one engine (RAM); with
    `free` known, the bytes still to come for every running item plus this one must leave RESERVE_BYTES."""
    rows = [row for row in items or [] if isinstance(row, dict)]
    running = set(running or ())
    active = [row for row in rows if row.get("id") in running]
    if len(running) >= max(1, min(4, int(limit or 1))):
        return None
    torrents_running = sum(1 for row in active if row.get("source_kind") == "torrent")
    reserved = sum(remaining(row) for row in active)
    for row in rows:
        if row.get("state") != "queued" or row.get("id") in running:
            continue
        if row.get("source_kind") == "torrent" and torrents_running >= MAX_TORRENT_DOWNLOADS:
            continue
        if free is not None and active and not enough_space(free - reserved, remaining(row)):
            continue
        return row
    return None


def mp4_prefix_playable(prefix, contiguous_bytes, minimum=PARTIAL_PLAY_BYTES) -> bool:
    """True only when a growing MP4 has its complete ``moov`` atom in the contiguous prefix.

    Torrents fill pieces out of order, so file size is not proof that GStreamer can read the index.  The
    caller supplies the actually contiguous byte count; normal HTTP downloads use their written size.
    """
    if not isinstance(prefix, (bytes, bytearray)) or _int(contiguous_bytes) < minimum:
        return False
    data, offset = bytes(prefix), 0
    while offset + 8 <= len(data):
        size = int.from_bytes(data[offset:offset + 4], "big")
        kind = data[offset + 4:offset + 8]
        header = 16 if size == 1 else 8
        if size == 1:
            if offset + 16 > len(data):
                return False
            size = int.from_bytes(data[offset + 8:offset + 16], "big")
        elif size == 0:
            size = len(data) - offset
        if size < header or offset + size > len(data):
            return False
        if kind == b"moov":
            return True
        offset += size
    return False


def local_play(item):
    """(path, finished) to play from the disk, or (None, reason). A finished file always plays; a growing one
    only in a progressive format with enough stored (MKV and fMP4 wait until done)."""
    if not item:
        return None, "missing"
    if item.get("state") == "downloaded" and item.get("path"):
        return item["path"], True
    part = item.get("part")
    if item.get("state") in ("downloading", "waiting") and part:
        name = part[:-5] if part.endswith(".part") else part
        ext = posixpath.splitext(name)[1].lower()
        if ext == ".ts" and _int(item.get("contiguous", item.get("done"))) >= PARTIAL_PLAY_BYTES:
            return part, False
        if ext == ".mp4" and item.get("moov_at_start") is True \
                and _int(item.get("contiguous", item.get("done"))) >= PARTIAL_PLAY_BYTES:
            return part, False
        return None, "wait"
    return None, "not_ready"


def used_bytes(items) -> int:
    return sum(_int(row.get("done")) for row in items or [] if isinstance(row, dict))


# --- which rows can be picked, in the player's order --------------------------------------------------
def page_rows(sources, torrent_state, starred=None, muted=None, engine=True, refused=(), lang="en",
              rank=None, torrent_view=None) -> list:
    """The download page: the Servers panel's groups in its order (star first, muted last). A header row per
    group, then {"candidate", "enabled", "reason"}; reason: checking | failed | engine | proxy | info."""
    from . import playernav
    refused = set(refused or ())
    rows = []
    for group in playernav.server_groups(sources or {}, lang, starred, muted, torrent_state, rank=rank,
                                         torrent_view=torrent_view, complete=True):
        rows.append({"header": True, "group": group.get("title") or "", "count": group.get("count", 0),
                    "candidate": None, "enabled": False, "reason": None, "state": group.get("state")})
        for candidate in group.get("servers") or []:
            if candidate.get("info"):
                reason = "info"
            elif candidate.get("cid") in refused:
                reason = "proxy"          # resolved once: signed for the server, can't be downloaded
            elif candidate.get("torrent"):
                reason = None if engine else "engine"
            elif candidate.get("check") == "checking":
                reason = "checking"
            elif candidate.get("check") != "ok":
                reason = "failed"
            else:
                reason = None
            rows.append({"header": False, "group": group.get("title") or "", "candidate": candidate,
                         "enabled": reason is None, "reason": reason})
    return rows


def page_columns(rows, lang="en", manual=None) -> list:
    """The download page as the player's Servers panel (app 064 TV): the LEFT column of pills, each with the
    rows it shows on the right. First the actions (Auto; in a Manual season run: Use the last server for the
    rest, Skip, Stop), then one pill per group of page_rows() in its order. A pill: {"id", "title", "count",
    "action", "rows"}; count is "…" while a row of the group is still being checked."""
    from . import i18n
    pills = []
    if manual:
        if manual.get("prev"):
            pills.append({"id": "rest", "title": i18n.t("download.use_rest", lang), "count": "", "action": "rest",
                          "rows": []})
        pills += [{"id": "skip", "title": i18n.t("download.skip", lang), "count": "", "action": "skip", "rows": []},
                  {"id": "stop", "title": i18n.t("download.stop", lang), "count": "", "action": "stop", "rows": []}]
    else:
        pills.append({"id": "auto", "title": i18n.t("download.auto_pill", lang), "count": "", "action": "auto",
                      "rows": []})
    for row in rows or []:
        if row.get("header"):
            pills.append({"id": "group:" + (row.get("group") or ""), "title": row.get("group") or "",
                          "count": row.get("count", 0),
                          "action": None, "rows": [], "state": row.get("state")})
        elif pills and pills[-1]["action"] is None:
            pills[-1]["rows"].append(row)
    for pill in pills:
        if pill["action"] is None:
            real = [r for r in pill["rows"] if r.get("reason") != "info"]
            if pill["rows"] and not real:
                pill["count"] = "" if pill.get("state") == "loading" else pill.get("count", 0)
                continue                         # loading …, unavailable !, or an empty quality filter 0
            checking = any(r.get("reason") == "checking" for r in pill["rows"]) or \
                (pill["rows"] and not real)
            pill["count"] = "" if pill.get("state") == "loading" else \
                ("" if checking and not real else ("%d …" % len(real) if checking else len(real)))
    return pills


def card_view(row, lang="en", downloading_cid=None) -> dict:
    """One right-column card: line1 quality (big), line2 release/label, line3 meta, line4 a torrent's
    audio languages; note = why it is greyed, or "Downloading" for the row just queued."""
    from . import i18n, playernav
    candidate = row.get("candidate") or {}
    view = playernav.server_view(candidate)
    line2 = "" if view.get("label") == view.get("quality") else (view.get("label") or "")
    line3, line4 = view.get("meta") or "", ""
    if isinstance(candidate.get("torrent"), dict):
        line3, line4 = torrents.card_lines(candidate["torrent"], lang)
    if downloading_cid and candidate.get("cid") == downloading_cid:
        note = i18n.t("download.started", lang)
    elif row.get("reason") and row.get("reason") != "info":
        note = i18n.t("download.reason." + row["reason"], lang)
    else:
        note = ""
    return {"line1": view.get("quality") or "", "line2": line2, "line3": line3, "line4": line4, "note": note,
            "enabled": bool(row.get("enabled")), "info": row.get("reason") == "info"}


def auto_order(sources, torrent_state, starred=None, muted=None, engine=True, rank=None) -> list:
    """Auto = the auto-play ranking: failover.order (starred group first, the server's order inside), torrents
    first only when the Torrents group is starred, else after every hoster (the website's auto-switch)."""
    muted = list(muted or [])
    if rank is not None:
        sources = failover.ranked(sources or {}, starred, rank, muted)
    hosters = failover.order(sources or {}, starred, None, muted)
    rows = []
    if engine and torrents.GROUP not in muted and (torrent_state or {}).get("state") == "ok":
        rows = [torrents.candidate(row, "en") for row in torrent_state.get("rows") or []]
    return rows + hosters if starred == torrents.GROUP else hosters + rows


def same_choice(candidates, previous) -> list:
    """"Use this server for the rest": rows matching the previous pick first (a hoster: same group and host;
    a torrent: same quality), then every other row in its order (047 automatic fallback)."""
    prev = previous or {}

    def matches(c):
        if prev.get("source_kind") == "torrent":
            return bool(c.get("torrent")) and torrents.quality_text(c["torrent"]) == prev.get("quality_text")
        return not c.get("torrent") and c.get("section") == prev.get("section") and \
            failover.hoster_key(c.get("hoster")) == failover.hoster_key(prev.get("source"))
    first = [c for c in candidates or [] if matches(c)]
    return first + [c for c in candidates or [] if c not in first]


# --- subtitles saved at download time -----------------------------------------------------------------
def subtitle_tracks(tracks, languages, choice=None, limit=2) -> list:
    """At most `limit` tracks: the viewer's choice for this title first, then one per preferred language."""
    rows = [t for t in tracks or [] if isinstance(t, dict) and t.get("id")]
    out = [t for t in rows if isinstance(choice, dict) and choice.get("id") and t.get("id") == choice.get("id")][:1]
    for code in languages or []:
        code = str(code).upper()
        if len(out) >= limit:
            break
        if any(str(o.get("lang") or "").upper() == code for o in out):
            continue
        hit = next((t for t in rows if str(t.get("lang") or "").upper() == code), None)
        if hit:
            out.append(hit)
    return out[:limit]


def subtitle_name(base, lang, taken=()) -> str:
    """<media name>.<lang>.vtt next to the media; a second track of one language gets .<lang>2.vtt."""
    code = re.sub(r"[^a-z]", "", str(lang or "").lower())[:3] or "sub"
    name, number = "%s.%s.vtt" % (base, code), 2
    while name in taken:
        name, number = "%s.%s%d.vtt" % (base, code, number), number + 1
    return name
