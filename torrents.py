"""The Torrents group on the box: list state, ranking, row text, the auto-play rule (no Enigma2 imports).

v1 list path: GET /api/torrents (the server's lookup). The app's 049 device path (bundle
device.torrents(job)) is not used on the box: the bundle ships top-level `Plugins` / `Components`
shim packages that would collide with Enigma2's own inside the GUI process.
"""
import re

from . import i18n

GROUP = "torrents"
SORTS = ("default", "seeds", "size_asc", "size_desc", "quality")
FILTERS = ("all", "4k", "1080p", "720p", "le480p")
MAX_SIZE = 25 * 1024 ** 3           # a bigger release cannot stream on a 2-core box with 1.3 GB RAM
WEAK_SEEDERS = 3                    # fewer seeders (or unknown): after every better-seeded release
STAR_WAIT_S = 20                    # the website waits as long for the list before the hosters start
UNPLAYABLE_CODECS = ("av1", "vp9", "vc1", "wmv3", "xvid", "divx", "h264 10bit", "h264 hi10p")
_HASH = re.compile(r"^[0-9a-fA-F]{40}$")
_CODECS = {"hevc": "HEVC", "h265": "HEVC", "x265": "HEVC", "h264": "H.264", "avc": "H.264", "x264": "H.264",
           "av1": "AV1", "xvid": "XviD", "divx": "DivX", "vp9": "VP9", "mpeg4": "MPEG-4"}


# --- the list ----------------------------------------------------------------------------------------
def loading() -> dict:
    return {"state": "loading", "rows": []}


def from_payload(payload, is_episode: bool) -> dict:
    """GET /api/torrents body -> {"state": ok | none | unavailable, "rows": ranked rows}."""
    outcome = payload.get("outcome") if isinstance(payload, dict) else None
    if outcome == "ok":
        return {"state": "ok", "rows": rank(payload.get("candidates"), is_episode)}
    if outcome == "not_identified":
        return {"state": "none", "rows": []}
    return {"state": "unavailable", "rows": []}


def from_error(code) -> dict:
    """400/404 are final answers (nothing to list); 429, 5xx and network trouble: 'unavailable'."""
    if code in ("http_400", "http_404", "plan_limit"):      # 084: a free plan without torrents: no group
        return {"state": "none", "rows": []}
    return {"state": "unavailable", "rows": []}


def _int(value, low=0):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= low else None


def quality_tier(quality) -> int:
    """1080p first, then 720p, then 4K (the Zero 4K plays it, but it needs far more peers), then the rest."""
    value = str(quality or "").strip().lower()
    if "1080p" in value:
        return 0
    if "720p" in value:
        return 1
    if "2160p" in value or "1440p" in value or re.search(r"(^|[^a-z0-9])(4k|uhd)([^a-z0-9]|$)", value):
        return 2
    if "576p" in value or "480p" in value:
        return 3
    return 4


def quality_height(quality):
    """A row's advertised height; None means that the quality is genuinely unknown."""
    value = str(quality or "").strip().lower()
    if re.search(r"(^|[^a-z0-9])(4k|uhd)([^a-z0-9]|$)", value):
        return 2160
    match = re.search(r"(\d{3,4})p(?:\b|$)", value)
    return int(match.group(1)) if match else None


def clean_sort(value) -> str:
    return value if value in SORTS else "default"


def clean_filter(value) -> str:
    return value if value in FILTERS else "all"


def next_sort(value) -> str:
    value = clean_sort(value)
    return SORTS[(SORTS.index(value) + 1) % len(SORTS)]


def next_filter(value, empty=False) -> str:
    """Cycle the filter; an empty result always has the promised one-press route back to All."""
    value = clean_filter(value)
    return "all" if empty and value != "all" else FILTERS[(FILTERS.index(value) + 1) % len(FILTERS)]


def display_rows(rows, sort="default", quality_filter="all") -> list:
    """Viewer-only ordering/filtering. Input order is never changed, so automatic choices keep server order."""
    sort, quality_filter = clean_sort(sort), clean_filter(quality_filter)
    shown = []
    for row in rows or []:
        height = quality_height(row.get("quality"))
        if quality_filter == "4k" and height != 2160:
            continue
        if quality_filter == "1080p" and height != 1080:
            continue
        if quality_filter == "720p" and height != 720:
            continue
        if quality_filter == "le480p" and (height is None or height > 480):
            continue
        shown.append(row)
    if sort == "seeds":
        return sorted(shown, key=lambda row: (row.get("seeders") is None, -(row.get("seeders") or 0)))
    if sort == "size_asc":
        return sorted(shown, key=lambda row: (row.get("size") is None, row.get("size") or 0))
    if sort == "size_desc":
        return sorted(shown, key=lambda row: (row.get("size") is None, -(row.get("size") or 0)))
    if sort == "quality":
        return sorted(shown, key=lambda row: _display_quality_key(row.get("quality")))
    return shown


def _display_quality_key(quality) -> tuple:
    height = quality_height(quality)
    if height is None:
        return 5, 0
    if height >= 2160:
        return 0, -height
    if height >= 1080:
        return 1, -height
    if height >= 720:
        return 2, -height
    if height >= 480:
        return 3, -height
    return 4, -height


def validated(candidate, is_episode: bool):
    if not isinstance(candidate, dict):
        return None
    info_hash, file_idx = candidate.get("info_hash"), candidate.get("file_idx")
    if not isinstance(info_hash, str) or not _HASH.match(info_hash):
        return None
    if file_idx is not None and _int(file_idx) is None:
        return None
    if is_episode and file_idx is None:          # an episode must name its file inside the torrent
        return None
    size = _int(candidate.get("size_bytes"))
    if size is not None and size > MAX_SIZE:
        return None
    codec = str(candidate.get("codec") or "").strip().lower()
    codec_key = re.sub(r"[._-]+", " ", codec)
    if any(mark in codec_key for mark in UNPLAYABLE_CODECS):
        return None
    return {"name": str(candidate.get("name") or "").strip(),
            "filename": candidate.get("filename") if isinstance(candidate.get("filename"), str) else None,
            "hash": info_hash.lower(), "file_idx": file_idx, "quality": candidate.get("quality"),
            "size": size, "seeders": _int(candidate.get("seeders")), "codec": codec,
            "container": str(candidate.get("container") or "").strip().lower(),
            "hdr": str(candidate.get("hdr") or "").strip().lower(), "is_pack": candidate.get("is_pack") is True,
            "source": str(candidate.get("source") or "").strip(),
            "languages": [x for x in (candidate.get("audio_languages") or candidate.get("languages") or [])
                          if isinstance(x, str) and x.strip()]}


def rank(candidates, is_episode: bool) -> list:
    """Valid, de-duplicated rows: well-seeded first, then 1080p > 720p > 4K > SD, then seeders, smaller size."""
    seen, rows = set(), []
    for candidate in candidates if isinstance(candidates, list) else []:
        row = validated(candidate, is_episode)
        if row is None or (row["hash"], row["file_idx"]) in seen:
            continue
        seen.add((row["hash"], row["file_idx"]))
        rows.append(row)
    return sorted(rows, key=lambda r: (r["seeders"] is None or r["seeders"] < WEAK_SEEDERS,
                                       quality_tier(r["quality"]), -(r["seeders"] or 0),
                                       r["size"] if r["size"] is not None else MAX_SIZE))


# --- one row -----------------------------------------------------------------------------------------
def key(row) -> str:
    """The resume/progress source key: torrent:<hash>:<torrentio file index> (empty for a single file)."""
    return "torrent:%s:%s" % (row["hash"], "" if row.get("file_idx") is None else row["file_idx"])


def parse_key(value):
    """(hash, file_idx) from a key(); None when it is not one."""
    match = re.match(r"^torrent:([0-9a-f]{40}):(\d*)$", value.strip().lower()) if isinstance(value, str) else None
    if not match:
        return None
    return match.group(1), int(match.group(2)) if match.group(2) else None


def resume_row(resume_hoster, is_episode: bool, season=None, episode=None):
    """FIX19: a row built from a saved key alone, so a resume starts before the list comes. The engine needs
    only the hash (magnet) and the file; the release name/quality come from the list when it arrives
    (merge_listed). No filename: an episode is found by its SxxEyy name, a film is the largest video
    (torrentrules.match_file). None when the key is not a torrent, or an episode key names no file."""
    parsed = parse_key(resume_hoster)
    if not parsed or (is_episode and parsed[1] is None):
        return None
    return {"name": "", "filename": None, "hash": parsed[0], "file_idx": parsed[1], "quality": None,
            "size": None, "seeders": None, "codec": "", "container": "", "hdr": "", "is_pack": False,
            "languages": [], "from_key": True,
            "episode": (season, episode) if is_episode and isinstance(season, int) and isinstance(episode, int)
            else None}


def resume_start(resume_hoster, start_at, autoplay, muted, is_episode, season=None, episode=None):
    """FIX19: the row to start AT ONCE when the player opens for a resume on a saved torrent (resume
    position > 0, Auto on, Torrents group not muted), else None (today's order decides)."""
    if not autoplay or GROUP in (muted or []):
        return None
    if isinstance(start_at, bool) or not isinstance(start_at, (int, float)) or start_at <= 0:
        return None
    return resume_row(resume_hoster, is_episode, season, episode)


def merge_listed(row, state) -> bool:
    """When the list arrives, the started key-only row takes the listed row's fields IN PLACE (the engine
    thread holds this dict: a filename that lands before the file list is used). True when it merged."""
    if not isinstance(row, dict) or not row.get("from_key"):
        return False
    listed = next((r for r in (state or {}).get("rows") or [] if key(r) == key(row)), None)
    if listed is None:
        return False
    row.update(listed)
    row["from_key"] = False
    return True


def source_object(row) -> dict:
    """The website's resume-source object for a torrent (accounts/progress.source_value)."""
    return {"kind": "torrent", "info_hash": row["hash"], "file_idx": row.get("file_idx")}


def size_text(size) -> str:
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        return ""
    if size >= 1024 ** 3:
        return "%.1f GB" % (size / 1024.0 ** 3)
    return "%d MB" % max(1, round(size / 1024.0 ** 2))


def quality_text(row) -> str:
    quality = str(row.get("quality") or "").strip()
    low = quality.lower()
    parts = ["4K" if low in ("2160p", "4k", "uhd") else quality] if quality else []
    hdr = row.get("hdr") or ""
    if hdr == "dv" or "dv" in hdr.split("+"):
        parts.append("DV")
    elif hdr:
        parts.append("HDR")
    return " ".join(parts) or "Torrent"


def meta_text(row, lang: str) -> str:
    """codec · container · size · seeders · languages (each only when known)."""
    parts = []
    codec = row.get("codec") or ""
    if codec:
        parts.append(_CODECS.get(re.sub(r"[ ._-]", "", codec), codec.upper()))
    if row.get("container"):
        parts.append(row["container"].upper())
    if size_text(row.get("size")):
        parts.append(size_text(row.get("size")))
    if row.get("seeders") is not None:
        parts.append(i18n.t("torrents.seeders", lang) % row["seeders"])
    langs = [code.upper() for code in row.get("languages") or []][:4]
    if langs:
        parts.append("/".join(langs))
    if row.get("is_pack"):
        parts.append(i18n.t("torrents.pack", lang))
    return " · ".join(parts)


def card_lines(row, lang: str) -> tuple:
    """The download page's detailed torrent card below the quality and release lines."""
    specs = []
    codec = row.get("codec") or ""
    if codec:
        specs.append(_CODECS.get(re.sub(r"[ ._-]", "", codec), codec.upper()))
    if row.get("container"):
        specs.append(row["container"].upper())
    if size_text(row.get("size")):
        specs.append(size_text(row.get("size")))
    if row.get("seeders") is not None:
        specs.append(i18n.t("torrents.seeders", lang) % row["seeders"])
    extra = []
    langs = [code.upper() for code in row.get("languages") or []][:6]
    if langs:
        extra.append(i18n.t("torrents.audio", lang) % "/".join(langs))
    if row.get("is_pack"):
        extra.append(i18n.t("torrents.pack", lang))
    return " · ".join(specs), " · ".join(extra)


def tv_card_lines(row, lang: str) -> tuple:
    """Android-TV Servers-row order: seeders, size, source, then codec/pack chips; languages last."""
    specs = []
    if row.get("seeders") is not None:
        specs.append("♟ %d" % row["seeders"])
    if size_text(row.get("size")):
        specs.append("▤ %s" % size_text(row.get("size")))
    if row.get("source"):
        specs.append("◈ %s" % row["source"])
    codec = row.get("codec") or ""
    if codec:
        specs.append("[%s]" % _CODECS.get(re.sub(r"[ ._-]", "", codec), codec.upper()))
    if row.get("is_pack"):
        specs.append("[%s]" % i18n.t("torrents.pack", lang))
    langs = [code.upper() for code in row.get("languages") or []][:6]
    tracks = "Languages: %s" % " / ".join(langs) if langs else ""
    return " · ".join(specs), tracks


def candidate(row, lang: str) -> dict:
    """A Servers-panel row: the shape the hoster rows have, plus the torrent itself."""
    info, tracks = tv_card_lines(row, lang)
    return {"cid": key(row), "hoster": key(row), "section": GROUP, "label": row["name"] or quality_text(row),
            "check": "ok", "meta": meta_text(row, lang), "card_info": info, "card_tracks": tracks,
            "torrent": row}


def group(state: dict, lang: str, sort="default", quality_filter="all") -> dict:
    """The Torrents group card: its count (… while the list loads) and its rows. None = no group."""
    status = (state or {}).get("state")
    rows = (state or {}).get("rows") or []
    if status == "ok" and rows:
        shown = display_rows(rows, sort, quality_filter)
        servers = [candidate(row, lang) for row in shown]
        if not servers:
            servers = [{"cid": "torrent:empty", "info": True, "section": GROUP,
                        "label": i18n.t("torrents.no_quality", lang)}]
        return {"id": GROUP, "title": i18n.t("torrents.group", lang), "count": len(shown),
                "servers": servers}
    if status == "loading":
        return {"id": GROUP, "title": i18n.t("torrents.group", lang), "count": "…",
                "servers": [{"cid": "torrent:info", "info": True, "section": GROUP,
                             "label": i18n.t("torrents.loading", lang)}]}
    if status == "unavailable":
        return {"id": GROUP, "title": i18n.t("torrents.group", lang), "count": "!",
                "servers": [{"cid": "torrent:info", "info": True, "section": GROUP,
                             "label": i18n.t("torrents.unavailable", lang)}]}
    return None


# --- when a torrent starts by itself ------------------------------------------------------------------
def auto_step(stage: str, state: dict, muted=None, starred=None, resume_hoster=None, attempted=(),
              auto_tried=False, waited_s=0.0):
    """("play", row) | ("wait", None) | (None, None). The website's torrent auto-switch:

    stage "before" (no hoster tried yet): a saved torrent resumes first; else only the STARRED Torrents
    group goes before the hosters, unless a saved hoster resumes. Both wait STAR_WAIT_S for the list.
    stage "after" (every hoster failed): the first torrent starts by itself, once.
    Never when the Torrents group is muted."""
    if GROUP in (muted or []):
        return None, None
    status = (state or {}).get("state")
    rows = [row for row in (state or {}).get("rows") or [] if key(row) not in attempted]
    pending = status == "loading"
    resume = resume_hoster.strip().lower() if isinstance(resume_hoster, str) else ""
    if stage == "before":
        if auto_tried:
            return None, None
        if parse_key(resume):
            if pending and waited_s < STAR_WAIT_S:
                return "wait", None
            saved = [row for row in rows if key(row) == resume]
            if saved:
                return "play", saved[0]
        elif resume:
            return None, None                    # a saved hoster resumes on its own server
        if starred != GROUP:
            return None, None
        if pending and waited_s < STAR_WAIT_S:
            return "wait", None
        return ("play", rows[0]) if rows else (None, None)
    if auto_tried:
        return None, None
    if pending:
        return "wait", None
    return ("play", rows[0]) if rows else (None, None)
