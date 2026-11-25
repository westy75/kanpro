"""Measured standalone Episodes geometry and data shaping (no Enigma2 imports)."""
from . import layout

SEASON_X, SEASON_Y, SEASON_W, SEASON_H, SEASON_STEP = 72, 229, 540, 58, 70
EPISODE_X, EPISODE_Y, EPISODE_W, EPISODE_H, EPISODE_STEP = 646, 229, 1104, 180, 198
STILL_X, STILL_Y, STILL_W, STILL_H = 660, 242, 270, 152
WATCHED_X, WATCHED_W, WATCHED_H = 1765, 84, 84
BUTTON_X, BUTTON_W, BUTTON_H, BUTTON_DY = 1500, 236, 46, (66, 122)   # Watched, then Download under it


def season_card(index: int, rtl: bool):
    return layout.x(SEASON_X, SEASON_W, rtl), SEASON_Y + index * SEASON_STEP, SEASON_W, SEASON_H


def episode_card(index: int, rtl: bool):
    return layout.x(EPISODE_X, EPISODE_W, rtl), EPISODE_Y + index * EPISODE_STEP, EPISODE_W, EPISODE_H


def still_box(index: int, rtl: bool):
    return layout.x(STILL_X, STILL_W, rtl), STILL_Y + index * EPISODE_STEP, STILL_W, STILL_H


def watched_box(index: int, rtl: bool):
    return layout.x(WATCHED_X, WATCHED_W, rtl), EPISODE_Y + index * EPISODE_STEP, WATCHED_W, WATCHED_H


def button_box(slot: int, which: str, rtl: bool):
    """The focus frame's box round an episode row's Watched ("watched") or Download ("download") button."""
    top = EPISODE_Y + slot * EPISODE_STEP + BUTTON_DY[0 if which == "watched" else 1]
    return layout.x(BUTTON_X, BUTTON_W, rtl), top, BUTTON_W, BUTTON_H


def episode_view(episode: dict, season: int) -> dict:
    progress = episode.get("progress") if isinstance(episode.get("progress"), dict) else {}
    name, number = (episode.get("name") or "").strip(), episode.get("number")
    label = ((name + " — ") if name else "") + "S%s,E%s" % (season, number)
    watched = bool(progress.get("watched"))
    percent = progress.get("percent")
    if not isinstance(percent, (int, float)):
        position, duration = progress.get("position"), progress.get("duration")
        percent = 100.0 * position / duration if isinstance(position, (int, float)) \
            and isinstance(duration, (int, float)) and duration > 0 else 0
    # The website episode row draws either its watched check or the partial progress bar, never both.
    percent = 0 if watched else max(0, min(100, int(percent)))
    return {"number": number, "label": label, "air_date": episode.get("air_date") or "",
            "overview": episode.get("overview") or "", "still": episode.get("still"),
            "released": episode.get("released", True), "percent": percent,
            "watched": watched}


def on_disk(row) -> bool:
    """FIX30: the episode has a copy the player plays from the box's disk (downloaded, or a growing file that can
    already play): its row shows the green "Downloaded" mark - never the check, which means watched."""
    from . import downloadrules
    path, _ = downloadrules.local_play(row)
    return bool(path)


def disk_numbers(find, kind, tmdb_id, season, episodes) -> set:
    """FIX31: the episode numbers of `episodes` (one season) that have a copy the player plays from the disk -
    the title page's rule (downloadmanager.manager().find + on_disk / downloadrules.local_play), for the
    player's Episodes panel. find(kind, tmdb_id, season, episode) -> the download row or None."""
    try:
        int(tmdb_id), int(season)
    except (TypeError, ValueError):
        return set()
    out = set()
    for episode in episodes or []:
        number = episode.get("number") if isinstance(episode, dict) else None
        if number is not None and on_disk(find(kind, tmdb_id, season, number)):
            out.add(number)
    return out


def plural(count: int, singular: str, plural_word: str) -> str:
    return "%d %s" % (count, singular if count == 1 else plural_word)
