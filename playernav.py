"""Player controls like the app: a timeline, then a button row (no Enigma2 imports)."""
from . import failover, i18n, layout, panellayout, playerlayout, subs, subtitlelayout, titleinfo, torrents

MARK, PLAIN = "»  ", "   "


AREAS = ("back", "centre", "timeline", "buttons")      # top to bottom, like the app's player


class Nav:
    """The app's TV player focus: the back pill (top), the centre play/pause circle, the timeline, the
    button row. UP/DOWN walk that order; the timeline seeks with LEFT/RIGHT; it has the focus first."""
    def __init__(self, buttons: list, rtl=False):
        self.rtl, self.names, self.area, self.button = rtl, list(buttons), "timeline", 0

    def key(self, name: str):
        """An action for the player, or None when only the focus moved."""
        if name in ("up", "down"):
            pos = AREAS.index(self.area) + (1 if name == "down" else -1)
            self.area = AREAS[max(0, min(len(AREAS) - 1, pos))]
            return None
        if self.area == "buttons":
            if name in ("left", "right"):
                # names are already in physical left-to-right order (reversed by buttons() for RTL).
                self.button = max(0, min(len(self.names) - 1,
                                         self.button + (1 if name == "right" else -1)))
                return None
            if name == "ok" and self.names:
                return self.names[self.button]
            return None
        if self.area == "timeline":
            if name in ("left", "right"):
                return "seek+10" if layout.step(1 if name == "right" else -1, self.rtl) > 0 else "seek-10"
            return "toggle" if name == "ok" else None
        if self.area == "centre":
            return "toggle" if name == "ok" else None
        return "back" if name == "ok" else None


def card_area(key: str, bar_on: bool, area: str):
    """Spec 081 + owner 2026-11-21: keys while the searching card shows its Servers button (no picture yet).
    "card": the card's button takes the focus (bar off: OK opens Servers); "stay": nothing; "back"/"buttons":
    bring the bar up with the focus there; None: the bar's own keys."""
    if not bar_on:
        return {"up": "back", "down": "buttons", "ok": "card"}.get(key, "stay")
    if (key == "down" and area == "back") or (key == "up" and area == "buttons"):
        return "card"
    if key in ("up", "down") and area in ("centre", "timeline"):
        return "card"                        # nothing to seek or pause before the picture
    return None


def back_action(bar_on) -> str:
    """FIX32: Back with the bar/buttons on screen hides them ("hide": a clean picture); with nothing shown it
    leaves the player as before ("exit"). Panels are their own screens with their own Back (close)."""
    return "hide" if bar_on else "exit"


def after_toggle(paused) -> str:
    """FIX32: OK pause keeps the controls up ("show"); OK resume hides them at once ("hide"), no auto-hide wait."""
    return "show" if paused else "hide"


def none_buttons(report_id=None, flagged=False) -> list:
    """FIX26: the no-servers card's buttons (logical order). Report exists once the automatic report has an
    id, and goes away after it was pressed (the card then says we know about it)."""
    return ["retry"] + (["report"] if report_id and not flagged else []) + ["back"]


class CardNav:
    """LEFT/RIGHT/OK on the no-servers card's button row; names in physical left-to-right order."""
    def __init__(self, names: list, focus=None):
        self.names, self.index = [], 0
        self.set_names(names, focus)

    def set_names(self, names: list, focus=None):
        old = self.names[self.index] if self.names and self.index < len(self.names) else None
        self.names = list(names)
        wanted = focus or old
        self.index = self.names.index(wanted) if wanted in self.names else \
            (self.names.index("retry") if "retry" in self.names else 0)

    @property
    def name(self):
        return self.names[self.index] if self.names else None

    def key(self, name: str):
        """The focused button's name on OK, else None (LEFT/RIGHT only move the focus)."""
        if name in ("left", "right") and self.names:
            self.index = max(0, min(len(self.names) - 1, self.index + (1 if name == "right" else -1)))
            return None
        return self.name if name == "ok" else None


def audio_rows(tracks, current, lang: str):
    """FIX26: the Audio list rows and whether it is read-only. tracks = [(language, description)].
    Two or more: a pickable list. One: that track, checked, read-only. None: "Only one audio track"."""
    rows = []
    for index, (language, description) in enumerate(tracks or []):
        name = " · ".join(part for part in (language, description) if part) or \
            "%s %d" % (i18n.t("player.audio", lang), index + 1)
        rows.append((("✓  " if index == current or len(tracks) == 1 else "    ") + name, index))
    if not rows:
        return [(i18n.t("player.audio_single", lang), None)], True
    return rows, len(rows) < 2


def _audio_codec(codec) -> str:
    """exteplayer3's "A_EAC3" as "EAC3" (e2iplayer shows e[2:], XMP:1764-1772)."""
    text = str(codec or "").strip()
    return text[2:] if text.upper().startswith("A_") else text


def ext_audio_rows(tracks, current_id, lang: str):
    """Our own exteplayer3's a_l list as the Audio rows, and whether they are read-only.
    A row reads "Portuguese · EAC3"; its value is the a_l track id (what `a<id>` takes). The checked row is
    a_c's id. 0-1 tracks: today's one-track line (audio_rows)."""
    good = [t for t in tracks or [] if isinstance(t, dict) and isinstance(t.get("id"), int)
            and not isinstance(t.get("id"), bool)]
    if len(good) < 2:
        infos = [(_audio_language(t, lang), _audio_codec(t.get("codec"))) for t in good]
        return audio_rows(infos, 0 if infos else None, lang)
    rows = []
    for number, track in enumerate(good):
        name = " · ".join(part for part in (_audio_language(track, lang), _audio_codec(track.get("codec"))) if part)
        name = name or "%s %d" % (i18n.t("player.audio", lang), number + 1)
        rows.append((("✓  " if track["id"] == current_id else "    ") + name[:80], track["id"]))
    return rows, False


def _audio_language(track, lang) -> str:
    code = subs.embedded_code(track.get("lang"))
    if code:
        return subtitlelayout.lang_name(code, lang)
    raw = str(track.get("lang") or "").strip()
    return "" if raw.lower() in ("und", "unk") else raw


def audio_choice(track) -> dict:
    """What is remembered per title after a pick: the track's language (as exteplayer3 names it) and codec."""
    return {"lang": str((track or {}).get("lang") or "").strip().lower(),
            "codec": str((track or {}).get("codec") or "").strip()}


def audio_pick(tracks, choice):
    """The a_l id that matches a remembered choice (same language; the same codec first), or None."""
    if not isinstance(choice, dict):
        return None
    want = str(choice.get("lang") or "").strip().lower()
    if not want or want in ("und", "unk"):
        return None
    code = subs.embedded_code(want)
    same = [t for t in tracks or [] if isinstance(t, dict) and isinstance(t.get("id"), int)
            and not isinstance(t.get("id"), bool)
            and (str(t.get("lang") or "").strip().lower() == want
                 or (code and subs.embedded_code(t.get("lang")) == code))]
    for track in same:
        if str(track.get("codec") or "") == str(choice.get("codec") or ""):
            return track["id"]
    return same[0]["id"] if same else None


def jump_text(seconds, lang: str) -> str:
    """The number-key toast: "+1 min", "−15 s", "+1 min 30 s"."""
    value = int(seconds or 0)
    sign = "+" if value >= 0 else "\u2212"
    minutes, rest = divmod(abs(value), 60)
    parts = []
    if minutes:
        parts.append("%d %s" % (minutes, i18n.t("player.jump.min", lang)))
    if rest or not minutes:
        parts.append("%d %s" % (rest, i18n.t("player.jump.sec", lang)))
    return sign + " ".join(parts)


def with_button(names: list, name: str, order, rtl=False) -> list:
    """Add a button in its drawn place (the skin order), never at the end: remote LEFT/RIGHT follow it."""
    present = set(names) | {name}
    return playerlayout.visual_buttons([n for n in order if n in present], rtl)


def buttons(target: dict, rtl=False) -> list:
    names = []
    if target.get("has_next"):
        names.append("next")
    if target.get("season") is not None and target.get("episodes"):
        names.append("episodes")
    names.append("subtitles")
    names.append("audio")                # FIX26: always there, from the start (owner: "it should display")
    names.append("aspect")               # owner 2026-11-22: the TV app's ASPECT chip (after SPEED, before QUALITY)
    if target.get("local_path"):
        names.append("quality")
    names.append("servers")
    return playerlayout.visual_buttons(names, rtl)


def server_rows(sources: dict, current_cid, lang: str) -> list:
    """(label, candidate) rows; a group header is (label, None)."""
    rows = []
    candidates = [c for c in sources.get("candidates") or [] if isinstance(c, dict)]
    for section in sources.get("sections") or []:
        if not isinstance(section, dict):
            continue
        group = [c for c in candidates if c.get("section") == section.get("id")]
        if not group:
            continue
        rows.append((section.get("title_ar" if lang == "ar" else "title_en") or section.get("id") or "", None))
        rows.extend(((MARK if c.get("cid") == current_cid else PLAIN) + (c.get("hoster") or ""), c) for c in group)
    return rows


def server_groups(sources: dict, lang: str, starred=None, muted=None, torrent_state=None, rank=None,
                  torrent_view=None, complete=False, local=None, offline=False) -> list:
    """TV-app group cards. The server's own "torrents" section (Real-Debrid hints) is never shown: the box
    lists its own Torrents group from torrent_state (torrents.py), played by the engine on the box.
    FIX33 offline (a disk play, the server cannot be reached): the online groups say "No network — playing the
    downloaded copy" instead of "Looking…"."""
    candidates = [c for c in sources.get("candidates") or [] if isinstance(c, dict)]
    result = []
    offline = bool(offline and local)
    pending = (not sources or failover.still_coming(sources)) and not offline
    sections = [section for section in sources.get("sections") or [] if isinstance(section, dict)
                and section.get("id") != "torrents"]
    if complete:
        known = {section.get("id") for section in sections}
        for group_id in failover.DEFAULT_GROUPS:
            if group_id != torrents.GROUP and group_id not in known:
                sections.append({"id": group_id})
    if local:
        device = dict(local, cid="device", section="device", hoster=i18n.t("player.on_device", lang),
                      label=i18n.t("player.on_device", lang), check="ok", local=True)
        result.append({"id": "device", "title": i18n.t("player.on_device", lang), "count": 1,
                       "servers": [device], "state": "ready"})
    for section in sections:
        if not isinstance(section, dict) or section.get("id") == "torrents":
            continue
        rows = [c for c in candidates if c.get("section") == section.get("id")]
        group_id = section.get("id")
        if rows or complete:
            loading = pending
            title = section.get("title_ar" if lang == "ar" else "title_en") or \
                i18n.t("servers.group." + str(group_id), lang)
            if not rows:
                rows = [{"cid": "%s:info" % group_id, "section": group_id, "info": True,
                         "label": i18n.t("player.offline_local" if offline else
                                         "servers.looking" if loading else "servers.none", lang)}]
            result.append({"id": group_id, "title": title, "count": None if loading else
                           len([row for row in rows if not row.get("info")]), "servers": rows,
                           "state": "loading" if loading else "ready"})
    torrent_view = torrent_view or ("default", "all")
    box_torrents = torrents.group(torrent_state, lang, *torrent_view) if torrent_state and not offline else None
    if box_torrents:
        if box_torrents.get("count") == "…":
            box_torrents["count"] = None
            box_torrents["state"] = "loading"
        result.append(box_torrents)
    elif complete:
        result.append({"id": torrents.GROUP, "title": i18n.t("torrents.group", lang), "count": 0,
                       "servers": [{"cid": "torrent:empty", "section": torrents.GROUP, "info": True,
                                    "label": i18n.t("player.offline_local" if offline else "servers.none",
                                                    lang)}], "state": "ready"})
    muted_list = list(muted or [])
    muted = set(muted_list)
    for group in result:
        group["starred"], group["muted"] = group["id"] == starred, group["id"] in muted
    if rank is not None or complete:      # 066: star, viewer order, defaults, muted last
        wanted = failover.effective_order(starred, rank or [], muted_list)
        return sorted(result, key=lambda group: (-1 if group["id"] == "device" else
                                                 2 if group["muted"] else 0 if group["starred"] else 1,
                                                 failover.group_index(wanted, group["id"])))
    # Stable within each tier: star first, ordinary groups, muted groups at the end.
    return sorted(result, key=lambda group: (2 if group["muted"] else 0 if group["starred"] else 1))


def hoster_key(value) -> str:
    """The website's KanasaPlayer.hosterKey: lower case, no leading www., at most 100 characters."""
    key = value.strip().lower() if isinstance(value, str) else ""
    return (key[4:] if key.startswith("www.") else key)[:100]


def same_server(a, b) -> bool:
    """One server across polls: by cid; when a cid is missing or changed, by hoster + label identity."""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    if a.get("cid") and a.get("cid") == b.get("cid"):
        return True
    hoster = hoster_key(a.get("hoster"))
    if not hoster or hoster != hoster_key(b.get("hoster")):
        return False
    label_a, label_b = (a.get("label") or "").strip(), (b.get("label") or "").strip()
    return label_a == label_b and a.get("section") == b.get("section")


def find_server(groups: list, current):
    """(group index, server index) of the playing server in the panel groups, or None."""
    if not current:
        return None
    for exact in (True, False):          # a cid hit anywhere beats an identity match
        for g, group in enumerate(groups):
            for s, candidate in enumerate(group.get("servers") or []):
                if (exact and current.get("cid") and candidate.get("cid") == current.get("cid")) or \
                        (not exact and same_server(candidate, current)):
                    return g, s
    return None


def server_view(candidate: dict, playing=False) -> dict:
    if candidate.get("info"):            # "Looking for torrents…" / "Torrents unavailable": not a server
        return {"quality": candidate.get("label") or "", "label": "", "meta": "", "tracks": "", "checked": False,
                "playing": False, "candidate": candidate, "wide": True, "info": True}
    row = candidate.get("torrent")
    if isinstance(row, dict):            # quality big, release name, codec · container · size · seeders · langs
        return {"quality": torrents.quality_text(row), "label": panellayout.one_line(candidate.get("label")),
                "meta": candidate.get("card_info") or "", "tracks": candidate.get("card_tracks") or "",
                "checked": False, "playing": bool(playing),
                "candidate": candidate, "wide": True}
    qualities = sorted(set(int(q) for q in candidate.get("qualities") or []
                           if isinstance(q, (int, float)) and not isinstance(q, bool)), reverse=True)
    quality = " · ".join("%dp" % q for q in qualities) if qualities else (candidate.get("hoster") or "")
    label = (candidate.get("label") or candidate.get("hoster") or "").strip()
    if label.startswith("[") and label.endswith("]"):
        label = label[1:-1].strip()
    check = candidate.get("check")
    return {"quality": quality, "label": label, "meta": candidate.get("meta") or "", "tracks": "",
            "checked": check == "ok", "playing": bool(playing), "candidate": candidate}


def episode_views(episodes: list, season, playing) -> list:
    from .episodeslayout import episode_view
    return [dict(episode_view(ep, season), now_playing=ep.get("number") == playing)
            for ep in episodes if isinstance(ep, dict)]


def episode_rows(episodes: list, playing, lang: str) -> list:
    lines = titleinfo.episode_lines(episodes, lang) if episodes else []
    return [((MARK if ep.get("number") == playing else PLAIN) + line,
             ep.get("number") if ep.get("released", True) else None)
            for ep, line in zip(episodes, lines)]
