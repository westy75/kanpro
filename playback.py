"""Playback maths for the player screen (no Enigma2 imports)."""
from datetime import date
import re
from urllib.parse import urlencode, urljoin

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_UNSAFE = ("&", "#")
RESUME_RETRY_S, RESUME_MAX_ATTEMPTS, RESUME_GIVE_UP_S = 6, 3, 18


def _proxy(result: dict, base: str):
    query = urlencode({"url": result.get("stream_url") or "", "referer": result.get("referer") or ""})
    return base.rstrip("/") + "/stream/proxy?" + query, {}


def device_headers(result: dict) -> dict:
    """085 T106: a device row's headers exactly as the bundle's resolve gave them (Referer + the exact User-Agent:
    AnaFast/Vidoba tokens are bound to it on the master, every variant and every segment). Never BROWSER_UA."""
    out = {}
    for name in ("User-Agent", "Referer"):
        value = (result.get("headers") or {}).get(name) if isinstance(result.get("headers"), dict) else None
        if isinstance(value, str) and value and not any(c in value for c in "\r\n\0"):
            out[name] = value
    return out


def stream_target(result: dict, base: str, proxy: bool, headers_work: bool = True):
    """(url, headers) for Kanasa exteplayer3 (-u / -h). IP-signed links (needs_proxy) always go through the server.
    085: a device row (resolved by the box's own worker) is NEVER proxied - not on a retry, not for a header, not in
    a download - it plays direct with the bundle's headers."""
    if result.get("device") is True:
        return result.get("stream_url") or "", device_headers(result)
    if proxy or result.get("needs_proxy"):
        return _proxy(result, base)
    referer = result.get("referer") or ""
    if referer and (not headers_work or any(mark in referer for mark in _UNSAFE)):
        return _proxy(result, base)      # the service path cannot carry this header
    if not headers_work:
        return result.get("stream_url") or "", {}
    headers = {"User-Agent": BROWSER_UA}
    if referer:
        headers["Referer"] = referer
    return result.get("stream_url") or "", headers


def proxy_retry_worth(result: dict) -> bool:
    """A direct file that failed gets one try through the server (the website does the same)."""
    return bool(result.get("stream_url")) and not result.get("needs_proxy") and result.get("kind") != "hls" \
        and result.get("device") is not True          # 085 T107: a device row re-resolves on the box instead


def device_retry_worth(current, result, reresolved) -> bool:
    """085 T107: a device row that failed (a 403 = a token for another IP or UA, or no picture) is resolved once
    more on the box and played once more; the second failure drops it (failover goes on)."""
    return isinstance(current, dict) and current.get("device") is True and isinstance(result, dict) \
        and current.get("cid") not in (reresolved or ())


def hls_variant(text: str, base_url: str):
    """Highest-bandwidth media-playlist URL from a master, absolute for exteplayer3.

    The live brqz proxy master uses root-relative /stream/proxy children.  GStreamer resolves those, while
    exteplayer3 ends before picture.  Passing the chosen media playlist removes both master parsing and the
    relative-child ambiguity.  A media playlist (no STREAM-INF) returns None and can be played as-is.
    """
    rows, pending = [], None
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            attrs = line.split(":", 1)[1]
            bandwidth = 0
            for field in attrs.split(","):
                if field.strip().startswith("BANDWIDTH="):
                    try:
                        bandwidth = int(field.split("=", 1)[1])
                    except ValueError:
                        pass
            pending = bandwidth
        elif pending is not None and line and not line.startswith("#"):
            rows.append((pending, urljoin(base_url.rstrip("/") + "/", line)))
            pending = None
    return max(rows, key=lambda row: row[0])[1] if rows else None


def start_quality(rows, wanted):
    """The ladder row to start at: the highest at or below `wanted` (None = the highest of all), else the lowest;
    None without a ladder."""
    rows = [r for r in rows or [] if isinstance(r, dict) and r.get("stream_url") and r.get("height")]
    if not rows:
        return None
    if wanted is None:
        return max(rows, key=lambda r: r["height"])
    fits = [r for r in rows if r["height"] <= wanted]
    return max(fits, key=lambda r: r["height"]) if fits else min(rows, key=lambda r: r["height"])


def hls_ladder(text: str, base_url: str) -> list:
    """FIX33: every quality of an HLS master as Quality-panel rows {"height", "bandwidth", "stream_url", "media"},
    smallest first, one row per height (its highest bandwidth). The website lists these (hls.js levels); the box
    plays one media playlist at a time (FIX29), so each row is an absolute media-playlist URL ("media": True: it
    is played as it is, never prepared as a master again). [] for a media playlist or a master without heights
    (one unnamed row each would be no choice the viewer can read)."""
    rows, pending = {}, None
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            attrs = line.split(":", 1)[1]
            match = re.search(r"(?:^|,)RESOLUTION=(\d+)x(\d+)", attrs)
            band = re.search(r"(?:^|,)BANDWIDTH=(\d+)", attrs)
            pending = (effective_height(int(match.group(1)), int(match.group(2))) if match else None,
                       int(band.group(1)) if band else 0)
        elif pending is not None and line and not line.startswith("#"):
            height, bandwidth = pending
            pending = None
            if not height:
                continue
            url = urljoin(base_url.rstrip("/") + "/", line)
            if height not in rows or bandwidth > rows[height]["bandwidth"]:
                rows[height] = {"height": height, "bandwidth": bandwidth, "stream_url": url, "media": True}
    return [rows[h] for h in sorted(rows)] if len(rows) > 1 else []


def audio_only_failed(elapsed, width, audio_present=True, grace=8) -> bool:
    """A video candidate with audio but no decoded picture is a failed server after the grace period."""
    return bool(audio_present) and isinstance(elapsed, (int, float)) and elapsed >= grace and \
        (not isinstance(width, (int, float)) or width <= 0)


def video_probe(missing_since, now, width, audio_present=True, grace=8):
    """Accumulate real missing-video time; resolution/start latency is deliberately irrelevant."""
    if isinstance(width, (int, float)) and width > 0:
        return None, True, False
    if missing_since is None:
        return now, False, False
    failed = audio_only_failed(now - missing_since, width, audio_present, grace)
    return missing_since, False, failed


def resume_key(kind: str, tmdb_id, season=None, episode=None) -> str:
    if kind == "movie":
        return "movie:%s" % tmdb_id
    return "tv:%s:%s:%s" % (tmdb_id, season, episode)


def resume_offer(saved):
    if not isinstance(saved, dict):
        return None
    # The account flag is authoritative.  Credits often begin well before a numerical
    # 95% threshold, and the website never offers a finished episode as Resume.
    if saved.get("watched") is True:
        return None
    seconds, length = saved.get("t"), saved.get("d")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 30:
        return None
    if isinstance(length, (int, float)) and not isinstance(length, bool) and length > 0 and seconds > length - 30:
        return None
    return seconds


def pending_resume_seek(target, at):
    """The resume target while playback has not demonstrably reached it (early seek calls may be ignored)."""
    try:
        target = float(target)
    except (TypeError, ValueError):
        return None
    if target <= 0:
        return None
    return target if at is None or at < target - 3 else None


def resume_seek_started(target, at, now):
    """State for the one initial resume seek.  Further seeks are deliberately sparse: restarting a
    proxied MP4 on every player tick prevents it from ever leaving re-buffering."""
    return {"target": float(target), "attempts": 1, "started": now, "sent": now, "at": at}


def resume_seek_step(state, at, now):
    """Return (action, state): wait, resend, landed, or give_up.

    A retry is allowed only after a six-second observation window with no movement towards the target.
    Three sends total and an eighteen-second overall bound keep a rejected seek from holding playback
    in a special state forever; giving up is not a server failure.
    """
    if not state:
        return "landed", None
    state = dict(state)
    target = state["target"]
    if at is not None and abs(at - target) <= 10:
        return "landed", None
    if now - state["started"] >= RESUME_GIVE_UP_S:
        return "give_up", None
    if now - state["sent"] < RESUME_RETRY_S:
        return "wait", state
    before = state.get("at")
    moving = at is not None and before is not None and abs(target - at) + 1 < abs(target - before)
    if moving:
        state.update(sent=now, at=at)
        return "wait", state
    if state["attempts"] < RESUME_MAX_ATTEMPTS:
        state.update(attempts=state["attempts"] + 1, sent=now, at=at)
        return ("last_resend" if state["attempts"] == RESUME_MAX_ATTEMPTS else "resend"), state
    return "give_up", None


def did_not_play_message(hoster, reason):
    """The final failover line always says why this candidate was rejected."""
    return "%s did not play: %s" % (hoster or "unknown server", reason or "unspecified failure")


def refresh_resume_state(item, detail, target, saved) -> None:
    """Put a just-saved point into the title/episode objects that were already on screen."""
    if not isinstance(saved, dict):
        return
    if isinstance(detail, dict):
        detail["progress"] = dict(saved)
    season, episode = target.get("season"), target.get("episode")
    if season is None or episode is None:
        return
    item["progress"] = {"season": season, "episode": episode, "position": saved.get("t")}
    for row in target.get("episodes") or []:
        if isinstance(row, dict) and row.get("number") == episode:
            duration = saved.get("d")
            progress = dict(row.get("progress") or {}, position=saved.get("t"), duration=duration)
            if duration:
                progress["percent"] = max(0, min(100, int(100 * (saved.get("t") or 0) / duration)))
            row["progress"] = progress
            break


def latest_series_resume(entries: dict, tmdb_id):
    """Newest episode coordinate (by season/episode) with a usable local resume point."""
    prefix, found = "tv:%s:" % tmdb_id, []
    for key, value in (entries or {}).items():
        if not isinstance(key, str) or not key.startswith(prefix) or resume_offer(value) is None:
            continue
        bits = key.split(":")
        try:
            season, episode = int(bits[2]), int(bits[3])
        except (ValueError, IndexError):
            continue
        found.append((season, episode, value))
    return max(found, key=lambda row: (row[0], row[1])) if found else None


def watched_next(seasons: list, season: int, episode: int, today=None):
    """The website title-button destination after a watched episode.

    Title payloads have season counts but not episode rows, so this is deliberately the
    count-based companion to next_episode().  None means the last aired episode: rewatch.
    """
    today = today or date.today().isoformat()
    current = next((row for row in seasons or [] if isinstance(row, dict) and row.get("number") == season), None)
    count = current.get("episode_count") if current else None
    if isinstance(count, int) and not isinstance(count, bool) and episode < count:
        return season, episode + 1
    later = sorted(row.get("number") for row in seasons or []
                   if isinstance(row, dict) and isinstance(row.get("number"), int)
                   and row["number"] > season and _season_out(row, today))
    return (later[0], 1) if later else None


def series_primary(seasons: list, progress: dict, today=None):
    """(mode, coordinates): resume, next, or rewatch for a series title button."""
    progress = progress if isinstance(progress, dict) else {}
    season, episode = progress.get("season"), progress.get("episode")
    if not isinstance(season, int) or not isinstance(episode, int):
        return "episodes", None
    if progress.get("watched") is True:
        nxt = watched_next(seasons, season, episode, today)
        return ("next", nxt) if nxt else ("rewatch", (season, episode))
    return "resume", (season, episode)


# openATV 7.6's public iSeekableService only exposes setFastForward/setSlowMotion
# trick modes.  They do not preserve audio, and servicemp3 exposes no supported
# playbin rate-seek handle, so FIX18 deliberately has no Speed button.
AUDIO_PRESERVING_SPEED_API = None


def finished(position: float, length) -> bool:
    return bool(length) and length > 0 and position >= 0.95 * length


def upnext_due(position: float, length) -> bool:
    return bool(length) and length > 120 and length - position <= 60


def _season_out(season: dict, today: str) -> bool:
    # TMDb lists announced seasons: only a dated season that has started, with episodes, is "next"
    air_date, count = season.get("air_date"), season.get("episode_count")
    return isinstance(air_date, str) and bool(air_date) and air_date <= today and (count is None or count > 0)


def next_episode(seasons: list, episodes: list, season: int, episode: int, today=None):
    numbers = [e.get("number") for e in episodes if isinstance(e, dict)]
    following = [e for e in episodes if isinstance(e, dict) and isinstance(e.get("number"), int)
                 and e["number"] > episode]
    if following:
        nxt = min(following, key=lambda e: e["number"])
        return (season, nxt["number"]) if nxt.get("released", True) else None
    if episode not in numbers:
        return None
    today = today or date.today().isoformat()
    later = sorted(s.get("number") for s in seasons
                   if isinstance(s, dict) and isinstance(s.get("number"), int) and s["number"] > season
                   and _season_out(s, today))
    return (later[0], 1) if later else None


def clock(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return "%d:%02d:%02d" % (hours, minutes, secs) if hours else "%d:%02d" % (minutes, secs)


def resume_rows(offer, lang: str) -> list:
    """The resume question like the app/site: "Resume from MM:SS" (first = the default), "Play from start".
    Empty when there is nothing to resume (the player starts at 0 at once)."""
    from . import i18n
    if offer is None:
        return []
    return [(i18n.t("player.resume", lang) % clock(offer), "resume"),
            (i18n.t("player.play_from_start", lang), "start")]


def needs_episode_rows(episodes, episode) -> bool:
    """True when the episode's own row (with the account's progress) is not at hand: a fresh box keeps no
    local point, so the season is fetched before the resume question is decided."""
    return not any(isinstance(ep, dict) and ep.get("number") == episode for ep in episodes or [])


QUALITY_STEPS = (1080, 720, 576, 480, 360, 240)


def effective_height(width, height):
    """A wide film is named by its width: 1920x800 is 1080p, 3840x1600 is 4K (box 2026-11-18: it read 720p)."""
    values = [v for v in (height, (width or 0) * 9 / 16) if isinstance(v, (int, float)) and not isinstance(v, bool)]
    best = max(values) if values else 0
    return int(round(best)) if best > 0 else None


def height_label(height) -> str:
    """Label from a real picture height (same rule as the website): 4K from 1800 px, else the nearest step the
    height reaches (>= step * 0.9), else "<h>p". Unknown / nonsense height -> ""."""
    if isinstance(height, bool) or not isinstance(height, (int, float)) or height <= 0:
        return ""
    height = int(height)
    if height >= 1800:
        return "4K"
    for step in QUALITY_STEPS:
        if height >= step * 0.9:
            return "%dp" % step
    return "%dp" % height


def single_quality_label(height, fallback_text="") -> str:
    """The one line of a one-quality panel: the real picture height first, then the server's own quality text."""
    return height_label(height) or str(fallback_text or "").strip()


def quality_fallback_text(current, rows) -> str:
    """The server's own quality text when the picture height is not known: a torrent row's quality_text, or a
    hoster's single listed quality height."""
    from . import torrents
    row = (current or {}).get("torrent")
    if isinstance(row, dict):
        text = torrents.quality_text(row)
        return "" if text == "Torrent" else text
    if len(rows or []) == 1:
        return height_label(rows[0].get("height"))
    return ""


def quality_panel(rows, height, current, auto_text="Auto", playing_url=None):
    """What the Quality panel lists. (choices, read_only): more than one quality -> every row (FIX33: the one
    playing, `playing_url`, checked like the website's current level); otherwise a single checked read-only line
    (empty when nothing is known)."""
    rows = [q for q in rows or [] if isinstance(q, dict) and q.get("stream_url")]
    if len(rows) > 1:
        return [(("%sp" % q.get("height") if q.get("height") else auto_text) +
                 (" ✓" if playing_url and q.get("stream_url") == playing_url else ""), q) for q in rows], False
    label = single_quality_label(height, quality_fallback_text(current, rows))
    if not label:
        return [], True
    return [(label + " ✓", None)], True


# FIX26 item 7: holding LEFT/RIGHT repeats every ~108 ms on the box; each repeat used to send a real seekTo
# (~10 seeks/s, exteplayer3 flushes and re-requests a torrent stream every time). Now a press only moves
# the target on screen and one real seek follows SEEK_SETTLE_MS after the last press; steps grow while held.
SEEK_SETTLE_MS = 600
SEEK_HOLD_GAP_S = 0.4                      # presses closer than this are one held key
SEEK_STEPS = ((5.0, 60), (2.0, 30), (0.0, 10))      # (held for at least s, step s)


def seek_hold_since(since, last_press, now):
    """When the current hold began: a press within SEEK_HOLD_GAP_S of the last one continues it."""
    if since is None or last_press is None or not 0 <= now - last_press <= SEEK_HOLD_GAP_S:
        return now
    return since


def seek_step(held_s) -> int:
    """10 s, then 30 s after 2 s of holding, 60 s after 5 s."""
    held = held_s if isinstance(held_s, (int, float)) and held_s > 0 else 0.0
    return next(step for after, step in SEEK_STEPS if held >= after)


# Number keys 1/3, 4/6, 7/9 jump back/forward by the box's own "self defined" seek steps (Enigma2
# config.seek.selfdefined_13/46/79), exactly as e2iplayer's player does (XMP key_seek1..9, actions seekdef:N).
NUMBER_SEEK_KEYS = {1: ("selfdefined_13", -1), 3: ("selfdefined_13", 1), 4: ("selfdefined_46", -1),
                    6: ("selfdefined_46", 1), 7: ("selfdefined_79", -1), 9: ("selfdefined_79", 1)}
NUMBER_SEEK_DEFAULTS = {"selfdefined_13": 15, "selfdefined_46": 60, "selfdefined_79": 300}
NUMBER_SEEK_MAX_S = 24 * 3600


def number_seek_values(seek_config) -> dict:
    """The three step lengths in seconds from Enigma2's config.seek (any object with .selfdefined_NN.value);
    a missing, odd or non-positive value falls back to 15 / 60 / 300 s."""
    out = {}
    for name, default in NUMBER_SEEK_DEFAULTS.items():
        try:
            value = getattr(seek_config, name).value
        except Exception:  # noqa: BLE001 - an image without that setting, or no config at all
            value = None
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        ok = isinstance(value, int) and not isinstance(value, bool) and 0 < value <= NUMBER_SEEK_MAX_S
        out[name] = value if ok else default
    return out


def number_seek_seconds(key, values=None):
    """Signed seconds for number key `key` (1/3, 4/6, 7/9), None for any other key."""
    entry = NUMBER_SEEK_KEYS.get(key) if isinstance(key, int) and not isinstance(key, bool) else None
    if entry is None:
        return None
    name, sign = entry
    value = (values or {}).get(name)
    if not (isinstance(value, int) and not isinstance(value, bool) and value > 0):
        value = NUMBER_SEEK_DEFAULTS[name]
    return sign * value


def seek_jump(base, seconds, length=None) -> float:
    """A fixed jump of the on-screen target, clamped like seek_target (never before 0 nor past length - 5)."""
    target = max(0.0, float(base or 0) + float(seconds or 0))
    if isinstance(length, (int, float)) and length > 5:
        target = min(target, float(length) - 5)
    return target


def seek_target(base, direction, held_s, length=None) -> float:
    """The new on-screen target: base (the pending target, else the position) moved by one step; never before
    0 and, with a known length, never past its last 5 s."""
    target = max(0.0, float(base or 0) + (1 if direction > 0 else -1) * seek_step(held_s))
    if isinstance(length, (int, float)) and length > 5:
        target = min(target, float(length) - 5)
    return target
