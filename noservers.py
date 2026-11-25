"""Pure, privacy-bounded construction of the 073 final no-servers report."""
import re
from urllib.parse import urlsplit

from . import __version__

_CODE = re.compile(r"^[A-Za-z0-9_.:-]{0,40}$")


def failure(reason) -> tuple:
    """Stable viewer-safe (reason, code); never copy exception text or a source URL into a report."""
    text = str(reason or "").lower()
    if "resolve" in text:
        return "resolution failed", "resolve_failed"
    if "picture" in text or "end event" in text:
        return "playback failed", "no_picture"
    if "stall" in text:
        return "playback stalled", "stalled"
    if "torrent" in text:
        return "torrent failed", "torrent_failed"
    if "network" in text:
        return "network failed", "network"
    return "playback failed", "failed"


def _name(value, maximum, fallback="Unknown"):
    value = " ".join(str(value or "").split())
    return (value or fallback)[:maximum]


def _hoster(candidate):
    value = str((candidate or {}).get("hoster") or "").strip().lower()
    if "://" in value:
        value = urlsplit(value).hostname or ""
    value = re.split(r"[/@?#]", value, 1)[0].split(":", 1)[0]
    value = re.sub(r"[^a-z0-9.-]", "", value)
    return value[4:] if value.startswith("www.") else value


def _server_name(candidate):
    value = str((candidate or {}).get("label") or (candidate or {}).get("hoster") or "").strip()
    if "://" in value:
        value = urlsplit(value).hostname or "Server"
    value = re.split(r"[?#]", value, 1)[0]
    return _name(value, 100, "Server")


def payload(target, sources, failures, lang="en"):
    """Contract-exact object, or None for the pending 073b site-title shape."""
    target, sources = target or {}, sources or {}
    if target.get("site_id") or target.get("kind") not in ("movie", "tv"):
        return None
    try:
        tmdb_id = int(target.get("id"))
    except (TypeError, ValueError):
        return None
    if not 0 < tmdb_id <= 999999999:
        return None
    attempted = {str(row.get("cid")): row for row in failures or [] if isinstance(row, dict)}
    candidates = [row for row in sources.get("candidates") or [] if isinstance(row, dict)]
    sections = [row for row in sources.get("sections") or [] if isinstance(row, dict)][:8]
    groups, used = [], 0
    for section in sections:
        group_id = section.get("id")
        servers = []
        for candidate in candidates:
            failed = attempted.get(str(candidate.get("cid")))
            if candidate.get("section") != group_id or failed is None or used >= 50:
                continue
            reason, code = failure(failed.get("failure"))
            code = code if _CODE.match(code) else "failed"
            servers.append({"name": _server_name(candidate),
                            "hoster": _name(_hoster(candidate), 100, ""),
                            "reason": reason[:80], "code": code})
            used += 1
            if len(servers) >= 20:
                break
        title_key = "title_ar" if lang == "ar" else "title_en"
        groups.append({"name": _name(section.get(title_key) or section.get("id"), 40), "servers": servers})
    # A torrent attempt is not present in catalogue candidates, but is still a tried server group.
    torrent_rows = [row for row in failures or [] if isinstance(row, dict) and row.get("torrent") and used < 50]
    if torrent_rows and len(groups) < 8:
        servers = []
        for row in torrent_rows[:20]:
            reason, code = failure(row.get("failure"))
            servers.append({"name": _name(row.get("label") or "Torrent", 100), "hoster": "torrent",
                            "reason": reason, "code": code})
            used += 1
        groups.append({"name": "Torrents", "servers": servers})
    kind = target["kind"]
    season = target.get("season") if kind == "tv" else None
    episode = target.get("episode") if kind == "tv" else None
    if kind == "tv" and (not isinstance(season, int) or not isinstance(episode, int)):
        return None
    return {"kind": kind, "tmdb_id": tmdb_id, "season": season, "episode": episode,
            "title": _name(target.get("title"), 200), "groups": groups,
            "client": "enigma2", "version": __version__}
