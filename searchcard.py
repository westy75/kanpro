"""The player's search card, like the website/app search status (no Enigma2 imports).

While the player looks for a server that plays, a card over the picture says what it does: looking for
servers, trying server N of M (the server's row as the Servers panel draws it, with a second counter),
that the last one did not work, or that nothing works. It stays until the picture really runs (after the
resume seek has landed), at least MIN_SHOWN_S, then a short "Playing on …" confirmation follows.
"""
from . import i18n, playernav, torrentrules

MIN_SHOWN_S, TOAST_S = 1.5, 4.0
# spec 081 §1: the phases of an automatic walk (first open, next episode, failover, the resume step), where the
# card also offers the viewer's own pick: an info line and a focused Servers button
PICK_PHASES = ("looking", "resuming", "trying", "switching", "torrent")
# FIX33: a downloaded copy that did not start: played again / is the network there? / it could not be played
# (offline: Back only, never "looking for servers")
LOCAL_PHASES = ("local_retry", "local_check", "local_failed")
# FIX27 (owner box test 2026-11-19): the position starts moving ~1 s before the first frame is on screen, so
# hiding the card on the first advancing position left a black screen. The card (with its last status) stays
# until the service reports a picture (a video size); this box often reads width 0 while a picture plays
# (FIX21), so it never stays longer than PICTURE_WAIT_S after the position started moving.
PICTURE_WAIT_S = 2.0


def server_name(candidate) -> str:
    """The website's serverName: label/hoster without the decorative brackets or a download prefix."""
    name = ((candidate or {}).get("label") or (candidate or {}).get("hoster") or "").strip()
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1].strip()
    for prefix in ("www.", "down.", "dl.", "download."):
        if name.lower().startswith(prefix):
            name = name[len(prefix):]
            break
    return name


def torrent_status(lang: str, progress) -> str:
    """The engine's step: installing N % | starting | connecting to peers | buffering (peers · speed)."""
    progress = progress or {}
    step, peers = progress.get("step"), progress.get("peers")
    if step == "install":
        return i18n.t("torrent.installing", lang) % int(progress.get("percent") or 0)
    if step == "engine":
        return i18n.t("torrent.starting", lang)
    peer_text = (i18n.t("torrent.peers", lang) % peers) if isinstance(peers, int) else ""
    if step == "buffering":
        parts = [i18n.t("torrent.buffering", lang)] + [p for p in (peer_text,
                                                                   torrentrules.speed_text(progress.get("speed"))) if p]
        return " · ".join(parts)
    return i18n.t("torrent.connecting", lang) + ((" " + peer_text) if peer_text else "")


def view(lang: str, title: str, phase: str, candidate=None, position=0, total=0, resume=False,
         failed=None, seconds=None, progress=None, auto=True) -> dict:
    """What the card shows (pick + info: spec 081's Servers button, see with_pick)."""
    return with_pick(_view(lang, title, phase, candidate, position, total, resume, failed, seconds, progress),
                     lang, phase, auto)


def with_pick(card: dict, lang: str, phase: str, auto=True) -> dict:
    """Spec 081 §1: while the automatic walk runs (auto = Settings → Servers → Auto on), the card says it looks by
    itself and that the viewer can choose, and shows the Servers button. Never on none / pick / buffering /
    missing, nor with Auto off (nothing is walked then)."""
    pick = bool(auto) and phase in PICK_PHASES
    return dict(card, pick=pick, info=i18n.t("player.card.pick_info", lang) if pick else "")


def _view(lang: str, title: str, phase: str, candidate=None, position=0, total=0, resume=False,
          failed=None, seconds=None, progress=None) -> dict:
    """phase: looking | trying | switching (a hand pick) | torrent | none |
    pick (Auto off, 066: waiting for the viewer's server) | buffering (FIX12: the buffer download fills;
    progress = {"percent": minutes ahead / the goal}) | resuming (FIX19: a saved server exists and nothing was
    tried yet; candidate = {"hoster", "label"} of that server, named on the card) | missing (FIX32: no
    /usr/bin/exteplayer3 on the box) | local_retry / local_check / local_failed (FIX33: the downloaded copy)."""
    if phase in ("missing", "player_installing"):            # 0.6.10: the plugin installs exteplayer3 itself
        key = "player.ext_installing" if phase == "player_installing" else "player.ext_missing"
        return {"title": title or "", "status": i18n.t(key, lang), "row": False, "quality": "",
                "server": "", "seconds": "", "hint": ""}
    if phase in LOCAL_PHASES:                # FIX33: a downloaded copy on the box's disk
        return {"title": title or "", "status": i18n.t("player.%s" % phase, lang), "row": False, "quality": "",
                "server": "", "seconds": "",
                "hint": i18n.t("player.local_failed_hint", lang) if phase == "local_failed" else ""}
    if phase == "resuming":
        name = server_name(candidate)
        hint = i18n.t("player.failed", lang) % server_name(failed) if failed else ""
        return {"title": title or "", "status": i18n.t("player.resuming", lang), "row": bool(name), "quality": "",
                "server": name, "seconds": "", "hint": hint}
    if phase == "pick":
        hint = i18n.t("player.failed", lang) % server_name(failed) if failed else ""
        return {"title": title or "", "status": i18n.t("player.pick", lang), "row": False, "quality": "",
                "server": "", "seconds": "", "hint": (hint + "   " if hint else "") + i18n.t("player.pick_hint", lang)}
    if phase == "torrent":
        status = torrent_status(lang, progress)
    elif phase == "buffering":
        status = i18n.t("player.buffering", lang) % int((progress or {}).get("percent") or 0)
    elif phase == "switching":
        status = i18n.t("player.switching", lang)
    elif phase == "none":
        status = i18n.t("player.none", lang)
    elif phase == "looking" or not candidate:
        status = i18n.t("player.looking", lang)
    elif resume:
        status = i18n.t("player.resuming", lang)
    elif total <= 1:
        status = i18n.t("player.trying_only", lang)
    else:
        status = i18n.t("player.trying", lang) % (position, total)
    row = playernav.server_view(candidate) if candidate and phase in ("trying", "switching", "torrent", "buffering") \
        else None
    hint = i18n.t("player.none_hint", lang) if phase == "none" else (
        i18n.t("player.failed", lang) % server_name(failed) if failed else "")
    if phase == "torrent" and resume and not hint:
        hint = i18n.t("player.resuming", lang)   # FIX19: the status line is the engine's step
    return {"title": title or "", "status": status,
            "row": bool(row), "quality": row["quality"] if row else "",
            "server": (server_name(candidate) if row else ""),
            "seconds": (i18n.t("player.seconds", lang) % int(seconds)) if row and seconds is not None else "",
            "hint": hint}


def ready_to_hide(shown_for: float, at, seek_target) -> bool:
    """The picture really runs: shown long enough, and a resume seek has landed (the player reports the
    old position until it has)."""
    if shown_for < MIN_SHOWN_S:
        return False
    return seek_target is None or (at is not None and at >= seek_target - 3)


def picture_up(width, size_event, waited) -> bool:
    """FIX27: may the card go now that the position moves? Yes once the service reports a video size (a width
    > 0 read, or evVideoSizeChanged seen), or PICTURE_WAIT_S after the position started moving (a box that never
    reports a size must not keep the card over a playing picture)."""
    has_width = isinstance(width, (int, float)) and not isinstance(width, bool) and width > 0
    return has_width or bool(size_event) or (isinstance(waited, (int, float)) and waited >= PICTURE_WAIT_S)


def toast(lang: str, candidate) -> str:
    quality = playernav.server_view(candidate)["quality"] if candidate else ""
    name = server_name(candidate)
    text = name + (" · " + quality if quality and quality != (candidate or {}).get("hoster") else "")
    return i18n.t("player.playing_on", lang) % text
