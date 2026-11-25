"""Text lines for the title page (no Enigma2 imports)."""
from . import i18n, layout


def meta_line(detail: dict, lang: str) -> str:
    item = detail.get("item") or {}
    parts = []
    if item.get("year"):
        parts.append(str(item["year"]))
    if detail.get("runtime"):
        parts.append(i18n.t("title.minutes", lang) % detail["runtime"])
    if detail.get("genres"):
        parts.append(", ".join(detail["genres"][:3]))
    if item.get("rating"):
        parts.append("★ %.1f" % float(item["rating"]))
    return "  ·  ".join(parts)


def app_meta(detail: dict, lang: str) -> str:
    """The TV app's line under the title: ★ rating · year · runtime."""
    item = detail.get("item") or {}
    parts = []
    if item.get("rating"):
        parts.append("★ %.1f" % float(item["rating"]))
    if item.get("year"):
        parts.append(str(item["year"]))
    if detail.get("runtime"):
        parts.append(i18n.t("title.minutes", lang) % detail["runtime"])
    return " · ".join(parts)


def progress_coordinates(item: dict):
    """Episode coordinates carried by the title/card progress payload, when present."""
    progress = item.get("progress") if isinstance(item.get("progress"), dict) else {}
    season, episode = progress.get("season"), progress.get("episode")
    if isinstance(season, int) and isinstance(episode, int):
        return season, episode
    return None


def _episode_mark(ep: dict, lang: str) -> str:
    if not ep.get("released", True):
        return " · " + ("قريباً" if lang == "ar" else "soon")
    progress = ep.get("progress") if isinstance(ep.get("progress"), dict) else None
    if progress and progress.get("watched"):
        return "  ✓"
    if progress and progress.get("position"):
        return " · %s %s" % ("من" if lang == "ar" else "from", layout.clock(progress["position"]))
    return ""


def episode_lines(episodes: list, lang: str) -> list:
    if not episodes:
        return [i18n.t("title.no_episodes", lang)]
    return ["%s.%s%s" % (ep.get("number"), " " + ep["name"] if ep.get("name") else "", _episode_mark(ep, lang))
            for ep in episodes]
