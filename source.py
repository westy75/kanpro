"""One switch between the account and the visitor data on the box (no Enigma2 imports).

Signed in: My List and resume points are the account's (shared with the website and the app).
A 401 means the token was revoked: sign out here, keep the visitor data, carry on as a visitor.
"""
import time

from . import account as account_mod, api, downloadmanager, failover, mylist, playback, plans, prefs as prefs_mod

MIN_SAVE_S = 30


def offline_error(error) -> bool:
    """FIX33: the account could not be reached (no network, a timeout, the server down) - not a refusal."""
    code = getattr(error, "code", "")
    return isinstance(code, str) and (code == "network" or code.startswith("http_5"))


class Source:
    def __init__(self, client, store, account_file, prefs=None):
        self.client, self.store, self.account_file = client, store, account_file
        self.prefs = prefs or prefs_mod.Prefs()
        self._group_order = self.prefs.server_group_order() if not self.signed_in() else []
        self._group_muted = self.prefs.server_group_muted() if not self.signed_in() else []
        self._group_rank = self.prefs.server_group_rank() if not self.signed_in() else []
        self._server_autoplay = self.prefs.server_autoplay() if not self.signed_in() else True
        # points this box sent to the account: a title page fetched right after the player closed can
        # still carry the older server value (the save is async), so these win for this session
        self.sent = {}

    def signed_in(self) -> bool:
        return bool(self.client.bearer)

    def on_unauthorized(self) -> None:
        self.account_file.clear()
        self.client.bearer = None

    def _account(self, call, *args):
        """(True, result) from the account, or (False, None) once the token turned out dead."""
        try:
            return True, call(*args)
        except api.ApiError as error:
            if error.code != "unauthorized":
                raise
            self.on_unauthorized()
            return False, None

    # --- server groups -------------------------------------------------------------------------
    def server_group_order(self) -> list:
        return list(self._group_order)

    def server_group_muted(self) -> list:
        return list(self._group_muted)

    def server_group_rank(self) -> list:
        return list(self._group_rank)

    def server_autoplay(self) -> bool:
        return self._server_autoplay

    def load_server_groups(self) -> bool:
        if not self.signed_in():
            return False
        ok, data = self._account(self.client.server_group_prefs)
        if ok:
            self._group_order = prefs_mod.clean_groups(data.get("server_group_order"))
            self._group_muted = [group for group in prefs_mod.clean_groups(data.get("server_group_muted"))
                                 if group not in self._group_order]
            self._group_rank = prefs_mod.clean_groups(data.get("server_group_rank"))
            self._server_autoplay = data.get("server_autoplay") if isinstance(data.get("server_autoplay"), bool) else True
        return ok

    def toggle_server_group(self, group_id, kind) -> bool:
        if kind == "star":
            order, muted = failover.group_preferences(self._group_order, self._group_muted, star=group_id)
        else:
            order, muted = failover.group_preferences(self._group_order, self._group_muted, mute=group_id)
        self._group_order, self._group_muted = prefs_mod.clean_groups(order), prefs_mod.clean_groups(muted)
        if not self.signed_in():
            self.prefs.set_server_groups(self._group_order, self._group_muted)
            return False
        return True                         # caller saves off the Enigma2 GUI thread

    def save_server_groups(self) -> bool:
        if not self.signed_in():
            self.prefs.set_server_groups(self._group_order, self._group_muted)
            return True
        ok, data = self._account(self.client.server_group_prefs_set, self._group_order, self._group_muted)
        if ok:
            self._group_order = prefs_mod.clean_groups(data.get("server_group_order"))
            self._group_muted = prefs_mod.clean_groups(data.get("server_group_muted"))
            return True
        # The token was revoked: the choices now belong to this signed-out box.
        self.prefs.set_server_groups(self._group_order, self._group_muted)
        return False

    def rank_sources(self, data):
        """A sources answer with its groups in this viewer's effective order (066); a free plan keeps only its
        allowed groups (084 §5)."""
        order = self._group_order
        data = plans.filter_sources(data)
        return failover.ranked(data, order[0] if order else None, self._group_rank, self._group_muted)

    def set_server_settings(self, fields) -> bool:
        """Settings → Servers changed something (any subset of the four 066 fields). Kept here at once;
        True when the account must be told (the caller saves off the GUI thread)."""
        if "server_group_order" in fields:
            self._group_order = prefs_mod.clean_groups(fields["server_group_order"])[:1]
        if "server_group_muted" in fields:
            self._group_muted = [g for g in prefs_mod.clean_groups(fields["server_group_muted"])
                                 if g not in self._group_order]
        if "server_group_rank" in fields:
            self._group_rank = prefs_mod.clean_groups(fields["server_group_rank"])
        if "server_autoplay" in fields:
            self._server_autoplay = bool(fields["server_autoplay"])
        if not self.signed_in():
            self._store_server_settings()
            return False
        return True

    def _store_server_settings(self):
        self.prefs.set_server_groups(self._group_order, self._group_muted)
        self.prefs.set_server_settings(self._group_rank, self._server_autoplay)

    def save_server_settings(self) -> bool:
        """Write all four fields to the account (signed in) or the box (a visitor / a revoked token)."""
        if not self.signed_in():
            self._store_server_settings()
            return True
        ok, data = self._account(self.client.server_settings_set, {
            "server_group_order": self._group_order, "server_group_muted": self._group_muted,
            "server_group_rank": self._group_rank, "server_autoplay": self._server_autoplay})
        if ok:
            self._group_order = prefs_mod.clean_groups(data.get("server_group_order"))
            self._group_muted = prefs_mod.clean_groups(data.get("server_group_muted"))
            self._group_rank = prefs_mod.clean_groups(data.get("server_group_rank"))
            autoplay = data.get("server_autoplay")
            self._server_autoplay = autoplay if isinstance(autoplay, bool) else True
            return True
        self._store_server_settings()
        return False

    # --- My List -------------------------------------------------------------------------------
    def my_list_rows(self) -> list:
        """Signed in: "All" then each named list (like the app); a visitor: the box's list. Both end with the
        Downloads row (FIX9), always there since FIX30."""
        rows = None
        if self.signed_in():
            ok, items = self._account(self.client.library)
            if ok:
                lists, grouped = [], {}
                fetch = getattr(self.client, "library_lists", None)
                if fetch is not None:
                    try:
                        lists = fetch()
                        grouped = {row["id"]: self.client.library_list(row["id"]) for row in lists}
                    except api.ApiError:
                        lists, grouped = [], {}       # the named lists are extra: All still shows
                rows = mylist.group_rows(items, lists, grouped, self.client.lang)
        if rows is None:
            rows = mylist.load(self.client, self.store)
        return rows + [mylist.download_row(downloadmanager.manager().items(), self.client.lang)]

    def in_list(self, item, detail) -> bool:
        marked = (detail or {}).get("in_list")
        if self.signed_in() and marked is not None:
            return bool(marked)
        return self.store.in_list(mylist.key(item))

    def toggle(self, item, detail) -> bool:
        if self.signed_in() and (detail or {}).get("in_list") is not None:
            ok, active = self._account(self.client.library_set, item.get("kind"), int(item.get("id")),
                                       not detail["in_list"])
            if ok:
                detail["in_list"] = active
                return active
        return self.store.toggle_list(mylist.key(item))

    # --- resume --------------------------------------------------------------------------------
    def resume(self, item, detail, season, episode, episodes):
        key = playback.resume_key(item.get("kind"), item.get("id"), season, episode)
        if self.signed_in():
            if key in self.sent:
                return dict(self.sent[key])
            kept_of = getattr(self.store, "pending_point", None)
            kept = kept_of(key) if kept_of is not None else None
            if isinstance(kept, dict) and kept.get("t") is not None:
                # FIX33: a point saved while the network was down is newer than anything the account has
                return {"t": kept.get("t"), "h": kept.get("h"), "d": kept.get("d")}
            saved = None
            if season is None:
                saved = (detail or {}).get("progress")
            else:
                for ep in episodes or []:
                    got = ep.get("progress") if isinstance(ep, dict) and ep.get("number") == episode else None
                    if isinstance(got, dict) and got.get("position") is not None:
                        saved = {"t": got.get("position"), "h": None, "d": got.get("duration")}
                        if got.get("watched") is True:
                            saved["watched"] = True
                # a season row carries no server; the title's own progress names the server for its
                # current episode (the website's progress.server): resume tries that server first
                title_point = (detail or {}).get("progress") or {}
                progress = item.get("progress") if isinstance(item.get("progress"), dict) else {}
                if (progress.get("season"), progress.get("episode")) == (season, episode) and title_point.get("h"):
                    saved = dict(saved or {"t": title_point.get("t"), "d": title_point.get("d")},
                                 h=title_point["h"])
            if saved:
                return saved
        return self.store.resume(key)        # visitor, or a point kept here while the length was unknown

    def title_resume(self, item, detail):
        """((season, episode), saved) for the title button, using the chooser's exact resume sources."""
        progress = item.get("progress") if isinstance(item.get("progress"), dict) else {}
        season, episode = progress.get("season"), progress.get("episode")
        if isinstance(season, int) and isinstance(episode, int):
            if progress.get("watched") is True:
                return None, None
            saved = (detail or {}).get("progress") if self.signed_in() else None
            saved = saved if playback.resume_offer(saved) is not None else \
                self.resume(item, detail, season, episode, [])
            if playback.resume_offer(saved) is not None:
                return (season, episode), saved
        entries = dict(self.store.load().get("resume") or {})
        entries.update(self.sent)
        point = playback.latest_series_resume(entries, item.get("id"))
        return ((point[0], point[1]), point[2]) if point else (None, None)

    def save_progress(self, item, season, episode, at, length, hoster, source=None) -> None:
        """source: a torrent's resume-source object (torrents.source_object); hoster is then its key."""
        key = playback.resume_key(item.get("kind"), item.get("id"), season, episode)
        if self.signed_in() and length and length > 0:
            if at is None or at < MIN_SAVE_S:
                return
            point = round(min(at, length), 1)
            self.sent[key] = {"t": point, "h": hoster or None, "d": length}     # before the call: it is async
            try:
                ok, _ = self._account(self.client.progress_save, item.get("kind"), int(item.get("id")), season,
                                      episode, point, length, hoster or "", source)
            except api.ApiError as error:
                if not offline_error(error):
                    raise
                # FIX33: no network (a download played offline): the point stays on the box and goes to the
                # account with the next save that gets through (flush_pending)
                self.store.set_pending(key, {"t": point, "d": length, "h": hoster or None,
                                             "kind": item.get("kind"), "id": item.get("id"), "season": season,
                                             "episode": episode, "source": source, "at": int(time.time())})
                return
            if ok:
                self.store.clear_resume(key)  # a box point from before is now stale
                if hasattr(self.store, "clear_pending"):
                    self.store.clear_pending(key)
                    self.flush_pending()
                return
        if playback.finished(at or 0, length):
            self.store.clear_resume(key)
        elif at and at >= MIN_SAVE_S:
            self.store.set_resume(key, round(at, 1), hoster, length and round(length))

    def flush_pending(self) -> int:
        """FIX33: send the points kept while the network was down (off the GUI thread). Stops at the first
        network failure (still offline); a point the server refuses is dropped; a dead token turns them into
        the box's visitor points. Returns how many were sent."""
        sent = 0
        if not hasattr(self.store, "pending"):
            return sent
        for key, value in self.store.pending().items():
            if not self.signed_in():
                self.store.set_resume(key, value.get("t"), value.get("h"), value.get("d"))
                self.store.clear_pending(key, value)
                continue
            try:
                ok, _ = self._account(self.client.progress_save, value.get("kind"), int(value.get("id")),
                                      value.get("season"), value.get("episode"), value.get("t"), value.get("d"),
                                      value.get("h") or "", value.get("source"))
            except api.ApiError as error:
                if offline_error(error):
                    break                        # still offline: everything stays for the next try
                self.store.clear_pending(key, value)
                continue
            except (TypeError, ValueError):
                self.store.clear_pending(key, value)     # a broken entry never blocks the others
                continue
            if ok:
                self.store.clear_pending(key, value)
                self.store.clear_resume(key)
                sent += 1
            else:                                # the token died meanwhile: a visitor point
                self.store.set_resume(key, value.get("t"), value.get("h"), value.get("d"))
                self.store.clear_pending(key, value)
        return sent

    def save_site_progress(self, site_id, series_site, at, length, hoster) -> None:
        """FIX28: a site title's point goes to the account by its handle (the website shows it in Continue
        watching). Visitors keep none: a handle has no box-side resume key in this version."""
        if not self.signed_in() or not length or length <= 0 or at is None or at < MIN_SAVE_S:
            return
        self._account(self.client.site_progress_save, site_id, series_site, round(min(at, length), 1), length,
                      hoster or "")

    def clear_progress(self, item, season, episode) -> None:
        """Visitor only: the account marks >= 90 % as watched by itself."""
        self.store.clear_resume(playback.resume_key(item.get("kind"), item.get("id"), season, episode))

    # --- the one-time import -------------------------------------------------------------------
    def run_import(self, now: int) -> bool:
        if not self.signed_in():
            return False
        sent = self.store.load()
        payload = account_mod.import_payload(sent, now)
        if payload is None:
            return False
        try:
            ok, _ = self._account(self.client.account_import, payload)
        except api.ApiError:
            return False                      # kept on the box, tried again at the next start
        if not ok:
            return False
        self.store.forget(list(sent["list"]), dict(sent["resume"]))   # what arrived meanwhile stays
        return True
