"""Search text clean-up and result rows (no Enigma2 imports)."""
import re


def clean(text):
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) >= 2 else None


def rows(items: list, label: str, per_row: int = 9) -> list:
    return [{"id": "search%d" % n, "label": label if n == 0 else "", "tab": "search", "next_page": None,
             "items": items[start:start + per_row]}
            for n, start in enumerate(range(0, len(items), per_row))]


def result_rows(payload, query: str, per_row: int = 9) -> list:
    """TMDb first, then the independent Turkish-sites row (also when TMDb found nothing)."""
    if not isinstance(payload, dict):
        return rows(payload or [], query, per_row)
    out = rows(payload.get("items") or [], query, per_row)
    turkish = payload.get("turkish") or []
    for n, start in enumerate(range(0, len(turkish), per_row)):
        out.append({"id": "turkish-search%d" % n,
                    "label": (payload.get("turkish_label") or "From Turkish sites") if n == 0 else "",
                    "tab": "search", "next_page": None, "items": turkish[start:start + per_row]})
    return out


def recent_rows(items: list, lang: str) -> list:
    """The Recent searches list: [(label, kind, item)] = New search…, each query (newest first), Clear all."""
    from . import i18n
    rows = [(i18n.t("search.new", lang), "new", None)]
    rows += [(item["query"], "run", item) for item in items or []]
    rows.append((i18n.t("search.clear_all", lang), "clear", None))
    return rows


_SITE_ID = re.compile(r"^[A-Za-z0-9_-]{16,40}$")


def site_id_ok(value) -> bool:
    """A 018 site handle (opaque, [A-Za-z0-9_-]{16,40}); anything else never reaches a URL path."""
    return isinstance(value, str) and bool(_SITE_ID.match(value))


def site_rows(payload, lang: str, focus_id=None):
    """The site title page (018 §5.2): ([(label, episode handle | None)], focused index). A movie is one
    Play row on its own handle; a series lists each season's title, then its episodes."""
    from . import i18n
    data = payload if isinstance(payload, dict) else {}
    if data.get("kind") == "movie" or not data.get("seasons"):
        handle = data.get("id") if site_id_ok(data.get("id")) else None
        return [(i18n.t("site.play", lang), handle)], 0
    out, focus = [], None
    for season in data.get("seasons") or []:
        if not isinstance(season, dict):
            continue
        number = season.get("season")
        out.append(("—  %s  —" % (season.get("label") or (i18n.t("site.season", lang) % number)), None))
        for ep in season.get("episodes") or []:
            if not isinstance(ep, dict) or not site_id_ok(ep.get("id")):
                continue
            label = i18n.t("site.episode", lang) % ep["episode"] if isinstance(ep.get("episode"), int) else ""
            title = (ep.get("title") or "").strip()
            out.append(("   ".join(part for part in (label, title) if part) or "▶", ep["id"]))
            if focus is None and ep["id"] == focus_id:
                focus = len(out) - 1
    if focus is None:   # FIX28: a series handle opens on the episode the viewer is up to (6.2 `current`)
        focus = next((i for i, (_, handle) in enumerate(out) if handle and handle == data.get("current")), None)
    if focus is None:
        focus = next((i for i, (_, handle) in enumerate(out) if handle), 0)
    return out, focus


def _site_start(progress, up_next=False):
    """The second a site title resumes at: the account's point unless watched, too early or at the end."""
    from . import playback
    if up_next or not isinstance(progress, dict):
        return 0
    offer = playback.resume_offer({"t": progress.get("position"), "d": progress.get("duration"),
                                   "watched": progress.get("watched")})
    return offer or 0


def site_continue_target(item, row_id):
    """FIX28: a site film/episode in Continue watching plays its own handle at the saved second (like FIX19
    for TMDb cards); None = open SiteTitle as before (other rows, a series card, a bad handle)."""
    if row_id != "continue" or not isinstance(item, dict) or item.get("kind") != "site":
        return None
    if item.get("category") not in ("episode", "movie") or not site_id_ok(item.get("id")):
        return None
    progress = item.get("progress") if isinstance(item.get("progress"), dict) else {}
    series = item.get("series_id") if site_id_ok(item.get("series_id")) else None
    return {"kind": "site", "id": item["id"], "site_id": item["id"], "series_site": series,
            "title": item.get("title") or "",
            "season": progress.get("season", item.get("season")), "episode": progress.get("episode", item.get("episode")),
            "start_at": _site_start(progress, bool(item.get("up_next"))), "episodes": [], "seasons": [],
            "has_next": False}


def site_target(data, handle, title=None) -> dict:
    """The player target for one site movie/episode handle (no torrents). FIX28: it resumes the account's
    point (6.2 `progress`) and names the series handle the player saves progress under."""
    episode, season, progress = None, None, None
    data = data or {}
    for s in data.get("seasons") or []:
        for ep in (s or {}).get("episodes") or []:
            if isinstance(ep, dict) and ep.get("id") == handle:
                season, episode, progress = s.get("season"), ep.get("episode"), ep.get("progress")
    if progress is None and handle == data.get("id"):
        progress = data.get("progress")
    series = data.get("series_id") if site_id_ok(data.get("series_id")) and data.get("series_id") != handle \
        else None
    return {"kind": "site", "id": handle, "site_id": handle, "series_site": series,
            "title": title or data.get("title") or "", "season": season, "episode": episode,
            "start_at": _site_start(progress), "episodes": [], "seasons": [], "has_next": False}
