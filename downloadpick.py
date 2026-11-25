"""What a queued download is and how Auto picks its server (no Enigma2 imports).

Auto picks when the item's turn comes (047: "each episode picks its source when its turn comes"), in the worker
thread, with the auto-play ranking (downloadrules.auto_order); "Use this server for the rest" is Auto with the
previous pick preferred (downloadrules.same_choice).
"""
from . import downloadrules as rules, failover, subs, torrents

POLL_WAITS_S = (2, 2, 3, 5, 5, 10, 10, 10, 10)      # the player's own poll rhythm, about a minute in all


class NoSource(Exception):
    pass


def item_fields(item, season=None, episode=None, source=None) -> dict:
    """The film/episode part of a download (a display copy, so it outlives the catalogue)."""
    kind = "movie" if item.get("kind") == "movie" else "tv"
    tmdb_id = int(item.get("id"))
    fields = {"key": rules.item_key(kind, tmdb_id, season, episode), "kind": kind, "tmdb_id": tmdb_id,
              "title": item.get("title") or "", "year": item.get("year"), "poster": item.get("poster"),
              "season": None if season is None else int(season), "episode": None if episode is None else int(episode)}
    if source is not None:
        order, muted = source.server_group_order(), source.server_group_muted()
        fields.update(starred=order[0] if order else None, muted=list(muted), rank=source.server_group_rank())
        if not source.signed_in():                       # visitors: the box's own subtitle choices
            key = subs.content_key({"kind": kind, "id": tmdb_id, "season": season, "episode": episode})
            fields.update(sub_langs=source.prefs.subtitle_prefs()["languages"],
                          sub_choice=source.prefs.subtitle_choice(key) if key else None)
    return fields


def source_fields(candidate) -> dict:
    """The server part: a hoster row (cid + ref) or a torrent release (hash + file name)."""
    row = candidate.get("torrent") if isinstance(candidate, dict) else None
    if isinstance(row, dict):
        return {"source_kind": "torrent", "info_hash": row.get("hash"), "torrent_filename": row.get("filename"),
                "release": row.get("filename") or row.get("name"), "size": row.get("size") or 0,
                "source": torrents.key(row), "section": torrents.GROUP, "quality_text": torrents.quality_text(row),
                "label": candidate.get("label") or ""}
    fields = {"source_kind": "server", "cid": candidate.get("cid"), "ref": candidate.get("ref"),
              "source": candidate.get("hoster") or "", "section": candidate.get("section"),
              "label": (candidate.get("label") or candidate.get("hoster") or "").strip()}
    if candidate.get("device") is True:     # 085 T108: its ref is the box worker's; re-resolved there, never on the server
        fields.update(device=True, site=candidate.get("site") or "")
        if isinstance(candidate.get("device_job"), dict):   # a resume after a worker restart searches again
            fields["device_job"] = candidate["device_job"]
    return fields


def auto_fields(prefer=None) -> dict:
    """Auto: the source is chosen when the item starts. prefer: the fields of the pick to repeat."""
    fields = {"source_kind": "auto", "source": "", "section": None}
    if prefer:
        fields["prefer"] = {k: prefer.get(k) for k in ("source_kind", "source", "section", "quality_text")}
    return fields


def wait_sources(client, item, wait):
    """The episode's sources once nothing is checking any more (or after about a minute)."""
    data = {}
    for pause in (0,) + POLL_WAITS_S:
        if pause:
            wait(pause)
        try:
            data = client.sources(item["kind"], int(item["tmdb_id"]), item.get("season"), item.get("episode"))
        except Exception:  # noqa: BLE001 - one failed poll is not the end
            continue
        if not failover.still_coming(data):
            break
    return data


def auto_pick(client, item, wait, engine_ok=True):
    """(source fields, resolved result or None) for an Auto item; NoSource when no row can be downloaded."""
    sources = wait_sources(client, item, wait)
    try:
        state = torrents.from_payload(client.torrents(item["kind"], int(item["tmdb_id"]), item.get("season"),
                                                      item.get("episode")), item.get("season") is not None)
    except Exception:  # noqa: BLE001 - no torrent list: hosters only
        state = {"state": "unavailable", "rows": []}
    order = rules.auto_order(sources, state, item.get("starred"), item.get("muted"), engine_ok, item.get("rank"))
    if item.get("prefer"):
        order = rules.same_choice(order, item["prefer"])
    for candidate in order:
        if candidate.get("torrent"):
            return source_fields(candidate), None
        try:
            result = client.resolve(candidate.get("ref"))
        except Exception:  # noqa: BLE001
            continue
        if rules.downloadable(result)[0]:
            return source_fields(candidate), result
    raise NoSource()


def error_text(code, lang, needs=0, free=None) -> str:
    """A failure as the viewer reads it (047 texts)."""
    from . import i18n
    if code == "space" and free is not None:
        return i18n.t("download.err.space", lang) % (rules.gb(needs), rules.gb(free))
    if code == "space":
        return i18n.t("download.err.space_short", lang)
    key = "download.err.%s" % code
    text = i18n.t(key, lang)
    return text if text != key else i18n.t("download.err.other", lang)


def saved_tracks(paths) -> list:
    """Subtitles saved with a download as the player's track rows ("Arabic (saved)"); ids start "saved:"."""
    out = []
    for path in paths or []:
        if not isinstance(path, str) or not path.endswith(".vtt"):
            continue
        lang = path[:-4].rsplit(".", 1)[-1].rstrip("0123456789").upper()[:2] or "AR"
        out.append({"id": "saved:" + path, "lang": lang, "release": "(saved)", "label": "(saved)",
                    "source": "saved"})
    return out


def with_local(target, row):
    """The player target plays the downloaded copy first (the rest of the target stays for the online
    fallback). Unchanged when nothing of this film/episode can play from the disk yet."""
    path, _ = rules.local_play(row)
    if not path:
        return target
    return dict(target, local_path=path, local_subtitles=list(row.get("subtitles") or []),
                local_section=row.get("section"), local_torrent=local_torrent(row))


def local_torrent(row) -> bool:
    """FIX33: a download of a torrent release (the Torrents group): played from the disk it starts its subtitles
    like a torrent stream (spec 081 §2)."""
    return isinstance(row, dict) and (row.get("source_kind") == "torrent" or row.get("section") == torrents.GROUP)


def play_target(row, start_at=0) -> dict:
    """A Downloads row as the player's target."""
    target = {"kind": row.get("kind"), "id": row.get("tmdb_id"), "title": row.get("title"),
              "season": row.get("season"), "episode": row.get("episode"), "start_at": start_at or 0,
              "episodes": [], "seasons": [], "has_next": False}
    return with_local(target, row)
