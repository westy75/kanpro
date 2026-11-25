"""The download page (app 064): the Servers panel's rows for one film or episode; the viewer picks one, or Auto.
Also the season download (Auto / Manual). Thin: the rules are in downloadrules / downloadpick."""
from enigma import ePoint, eTimer
from Components.ActionMap import ActionMap
from Components.Label import Label
from Screens.Screen import Screen

from . import downloadskey
from . import async_call, downloadmanager, downloadpick, downloadrules as rules, failover, i18n, layout, \
    panellayout as P, panelnav, skins, theme, torrengine, torrents
from . import devicejobs, hostclient, localhosts

POLL_MS, MAX_POLLS = 3000, 30


def episode_badge(season, episode) -> str:
    return "S%02d · E%02d" % (int(season), int(episode)) if season is not None else ""


class DownloadPage(Screen):
    """manual: None for one film/episode; else {"index", "count", "prev"} in a Manual season run, and the page
    closes with ("queued", fields) | ("skip",) | ("rest",) | ("stop",)."""

    def __init__(self, session, lang, client, source, posters, item, season=None, episode=None, manual=None):
        self.skin = skins.download_page(lang)
        Screen.__init__(self, session)
        self.lang, self.client, self.source, self.posters = lang, client, source, posters
        self.item, self.season, self.episode, self.manual = item, season, episode, manual
        self.manager = downloadmanager.manager()
        self.sources, self.torrent_state, self.polls = {}, torrents.loading(), 0
        self.torrent_sort, self.torrent_filter = source.prefs.torrent_view()
        self.rows, self.busy, self.refused, self.downloading_cid, self.closed = [], False, set(), None, False
        # 085 T108: the same device rows as the player (decided once, like the player's first poll)
        self.local_header, self.device_jobs, self.server_data = localhosts.header_value(), None, None
        self.engine = torrengine.installed_dir() is not None
        self["title"] = Label(item.get("title") or "")
        badge = episode_badge(season, episode)
        if manual:
            badge = i18n.t("download.episode_of", lang) % (manual["index"] + 1, manual["count"], badge)
        self["subtitle"] = Label(badge or i18n.t("download.title", lang))
        self["status"] = Label(i18n.t("download.checking", lang))
        self["hint"] = Label(i18n.t("download.page_hint", lang))
        self.rtl, self.pills, self.nav = lang == "ar", [], panelnav.Nav(0, 0, lang == "ar")
        self.pill_offset = self.card_offset = 0
        for name in ("navframe", "groupselect", "mainframe"):
            self[name] = Label("")
        for i in range(P.DL_PILL_SLOTS):
            for kind in ("pillbox", "pill", "pillcount"):
                self["%s%d" % (kind, i)] = Label("")
        for i in range(P.DL_CARD_SLOTS):
            for kind in ("card", "line1_", "note", "line2_", "line3_", "line4_"):
                self["%s%d" % (kind, i)] = Label("")
        self.poll_timer = eTimer()
        self.poll_timer.callback.append(self.poll)
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
            "cancel": self.leave, "ok": self.pick, "up": lambda: self.move(-1), "down": lambda: self.move(1),
            "left": lambda: self.side("left"), "right": lambda: self.side("right"),
            "red": self.cycle_torrent_sort, "green": self.cycle_torrent_filter}, -1)
        downloadskey.bind(self, lang, client, source, posters, used=("red", "green"))
        self.onLayoutFinish.append(self.start)
        self.onClose.append(self.stopped)

    def stopped(self):
        self.closed = True
        self.poll_timer.stop()
        if self.device_jobs is not None:
            self.device_jobs.cancel()

    def start(self):
        if not self.manager.directory:
            self["status"].setText(i18n.t("download.err.no_disk", self.lang))
        self.draw()
        t = self.item
        async_call.run(self.client.torrents, t.get("kind"), int(t.get("id")), self.season, self.episode,
                       on_done=lambda data: self.torrents_loaded(torrents.from_payload(data, self.season is not None)),
                       on_error=lambda error: self.torrents_loaded(
                           torrents.from_error(getattr(error, "code", "network"))))
        self.poll()

    # --- the same lists the player's Servers panel shows ---------------------------------------
    def poll(self):
        if self.closed:
            return
        self.polls += 1
        t = self.item
        async_call.run(self.client.sources, t.get("kind"), int(t.get("id")), self.season, self.episode,
                       self.local_header, on_done=self.sources_loaded, on_error=lambda error: self.sources_loaded(None))

    def sources_loaded(self, data):
        if self.closed:
            return
        if data is not None:
            self.server_data = data
            self.sources = self.source.rank_sources(self.device_merge(data))
        if (data is None or failover.still_coming(data)) and self.polls < MAX_POLLS:
            self.poll_timer.start(POLL_MS, True)
        self.draw()

    def device_merge(self, data):
        if not self.local_header or (self.device_jobs is None and not devicejobs.local_answer(data)):
            return data
        if self.device_jobs is None:
            self.device_jobs = devicejobs.Runner(hostclient.CLIENT.call, on_change=self.device_changed,
                                                 report=self.device_report)
        self.device_jobs.start(data)
        return self.device_jobs.merge(data)

    def device_changed(self):
        if self.closed or self.server_data is None or self.device_jobs is None:
            return
        self.sources = self.source.rank_sources(self.device_jobs.merge(self.server_data))
        self.draw()

    def device_report(self, body):
        if self.source.signed_in():
            async_call.run(self.client.device_match, body, on_done=lambda answer: None, on_error=lambda error: None)

    def torrents_loaded(self, state):
        if not self.closed:
            self.torrent_state = state
            self.draw()

    def draw(self):
        order, muted = self.source.server_group_order(), self.source.server_group_muted()
        page = rules.page_rows(self.sources, self.torrent_state, order[0] if order else None, muted, self.engine,
                               self.refused, self.lang, rank=self.source.server_group_rank(),
                               torrent_view=(self.torrent_sort, self.torrent_filter))
        old = [pill["id"] for pill in self.pills]
        selected_id = old[self.nav.selected] if old and self.nav.selected < len(old) else None
        focused_id = old[self.nav.positions["nav"]] if old and self.nav.positions["nav"] < len(old) else None
        self.pills = rules.page_columns(page, self.lang, self.manual)
        ids = [pill["id"] for pill in self.pills]
        groups = [i for i, pill in enumerate(self.pills) if pill["action"] is None]
        if selected_id in ids:
            self.nav.selected = ids.index(selected_id)
        elif groups:
            self.nav.selected = groups[0]          # the first group's servers show from the start
        else:
            self.nav.selected = 0
        self.nav.counts["nav"] = len(self.pills)
        self.nav.positions["nav"] = ids.index(focused_id) if focused_id in ids else 0
        if not old:
            self.nav.area = "nav"                  # the focus starts on Auto (or Skip in a Manual run)
        self.nav.set_main(len(self.cards()), enter=False)
        if self.nav.area == "main" and not self.nav.counts["main"]:
            self.nav.area = "nav"
        self.paint()
        coming = failover.still_coming(self.sources) or not self.sources or \
            (self.torrent_state or {}).get("state") == "loading"
        choosable = any(r.get("enabled") for pill in self.pills for r in pill["rows"])
        if not self.manager.directory:
            return
        if coming:
            text = i18n.t("download.checking", self.lang)
        elif not choosable:
            text = i18n.t("download.none_episode" if self.season is not None else "download.none", self.lang)
        else:
            text = i18n.t("download.pick_manual" if self.manual else "download.pick", self.lang)
        if not self.busy:
            self["status"].setText(text)
        self.draw_hint()

    def torrent_selected(self) -> bool:
        return bool(self.pills) and self.nav.selected < len(self.pills) and \
            self.pills[self.nav.selected]["title"] == i18n.t("torrents.group", self.lang)

    def draw_hint(self):
        if self.torrent_selected():
            text = i18n.t("torrents.controls", self.lang) % (
                i18n.t("torrents.sort." + self.torrent_sort, self.lang),
                i18n.t("torrents.filter." + self.torrent_filter, self.lang))
        else:
            text = i18n.t("download.page_hint", self.lang)
        self["hint"].setText(text)

    def cycle_torrent_sort(self):
        if self.torrent_selected():
            self.torrent_sort = torrents.next_sort(self.torrent_sort)
            self.source.prefs.set_torrent_view(self.torrent_sort, self.torrent_filter)
            self.draw()

    def cycle_torrent_filter(self):
        if self.torrent_selected():
            empty = self.torrent_filter != "all" and not torrents.display_rows(
                (self.torrent_state or {}).get("rows") or [], self.torrent_sort, self.torrent_filter)
            self.torrent_filter = torrents.next_filter(self.torrent_filter, empty)
            self.source.prefs.set_torrent_view(self.torrent_sort, self.torrent_filter)
            self.draw()

    def cards(self):
        if not self.pills or self.nav.selected >= len(self.pills):
            return []
        return self.pills[self.nav.selected]["rows"]

    def paint(self):
        self.pill_offset = layout.keep_visible(self.pill_offset, self.nav.positions["nav"], len(self.pills),
                                               P.DL_PILL_SLOTS)
        for slot in range(P.DL_PILL_SLOTS):
            index = self.pill_offset + slot
            pill = self.pills[index] if index < len(self.pills) else None
            for kind in ("pillbox", "pill", "pillcount"):
                widget = self["%s%d" % (kind, slot)]
                widget.show() if pill else widget.hide()
                if not pill:
                    continue
                if kind == "pill":
                    widget.setText(pill["title"])
                elif kind == "pillcount":
                    widget.setText(str(pill["count"]))
                if widget.instance:
                    widget.instance.setBackgroundColor(theme.rgb(
                        theme.SELECTED if index == self.nav.selected and pill["action"] is None else theme.SURFACE))
                    widget.instance.invalidate()   # the box repaints a colour only on invalidate
        rows = self.cards()
        self.card_offset = layout.keep_visible(self.card_offset, self.nav.positions["main"], len(rows),
                                               P.DL_CARD_SLOTS)
        for slot in range(P.DL_CARD_SLOTS):
            index = self.card_offset + slot
            view = rules.card_view(rows[index], self.lang, self.downloading_cid) if index < len(rows) else None
            for kind in ("card", "line1_", "note", "line2_", "line3_", "line4_"):
                widget = self["%s%d" % (kind, slot)]
                widget.show() if view else widget.hide()
                if view and kind != "card":
                    widget.setText(view[kind.rstrip("_")])
                if view and kind in ("line1_", "line2_") and widget.instance:
                    widget.instance.setForegroundColor(theme.rgb(theme.TEXT if view["enabled"] else theme.DIM))
                    widget.instance.invalidate()
        self.draw_focus()

    def draw_focus(self):
        for name in ("navframe", "groupselect", "mainframe"):
            self[name].hide()
        pill_slot = self.nav.selected - self.pill_offset
        if self.pills and self.pills[self.nav.selected]["action"] is None and 0 <= pill_slot < P.DL_PILL_SLOTS \
                and self["groupselect"].instance and not (self.nav.area == "nav" and
                                                          self.nav.positions["nav"] == self.nav.selected):
            x, y, _, _ = P.download_pill(pill_slot, self.rtl)
            self["groupselect"].instance.move(ePoint(x - 3, y - 3))
            self["groupselect"].show()
        if self.nav.area == "nav" and self.pills and self["navframe"].instance:
            x, y, _, _ = P.download_pill(self.nav.positions["nav"] - self.pill_offset, self.rtl)
            self["navframe"].instance.move(ePoint(x - 3, y - 3))
            self["navframe"].show()
        elif self.nav.area == "main" and self.cards() and self["mainframe"].instance:
            x, y, _, _ = P.download_card(self.nav.positions["main"] - self.card_offset, self.rtl)
            self["mainframe"].instance.move(ePoint(x - 3, y - 3))
            self["mainframe"].show()

    def move(self, step):
        self.nav.vertical(step)
        self.paint()

    def side(self, key):
        self.nav.horizontal(key)
        self.paint()

    # --- choosing ------------------------------------------------------------------------------
    def leave(self):
        self.close(("stop",) if self.manual else None)

    def pick(self):
        if self.busy or not self.pills:
            return
        if self.nav.area == "nav":
            pill = self.pills[self.nav.positions["nav"]]
            if pill["action"] is None:              # OK on a group: its servers fill the right column
                self.nav.activate_nav()
                self.card_offset = 0
                self.nav.set_main(len(self.cards()), enter=True)
                self.paint()
                return
            value = pill["action"]
        else:
            rows = self.cards()
            if self.nav.positions["main"] >= len(rows):
                return
            value = rows[self.nav.positions["main"]]
        if value in ("skip", "stop", "rest"):
            self.close((value,))
            return
        if isinstance(value, dict) and not value.get("enabled"):
            return                                  # greyed: its reason is on the row
        if not self.manager.directory:
            self["status"].setText(i18n.t("download.err.no_disk", self.lang))
            return
        old = self.manager.find(self.item.get("kind"), self.item.get("id"), self.season, self.episode)
        if old is not None and old.get("state") != "failed" and not self.manual:
            from .choice import Choice
            name = old.get("label") or old.get("source") or i18n.t("download.auto", self.lang).split(" ·")[0]
            rows = [(i18n.t("download.replace_yes", self.lang), "yes"), (i18n.t("download.cancel", self.lang), "no")]
            self.session.openWithCallback(lambda answer: answer and answer[1] == "yes" and self.chosen(value),
                                          Choice, self.lang, i18n.t("download.replace", self.lang) % name, rows)
            return
        self.chosen(value)

    def chosen(self, value):
        fields = downloadpick.item_fields(self.item, self.season, self.episode, self.source)
        if value == "auto":
            self.queue(dict(fields, **downloadpick.auto_fields()), None, None)
            return
        candidate = value["candidate"]
        if candidate.get("torrent"):
            self.queue(dict(fields, **downloadpick.source_fields(candidate)), None, candidate)
            return
        self.busy = True
        self["status"].setText(i18n.t("download.preparing", self.lang))
        if devicejobs.is_device(candidate):     # 085 T108: resolved by the box's worker, downloaded direct
            cached = self.device_jobs.fresh_result(candidate.get("cid")) if self.device_jobs is not None else None
            if cached is not None:
                self.resolved(cached, fields, candidate)
                return

            def answered(ok, value):
                result = devicejobs.play_result(value) if ok else None
                if result is None:
                    self.resolve_failed(candidate)
                else:
                    self.resolved(result, fields, candidate)
            hostclient.CLIENT.call("resolve", {"ref": candidate.get("ref"), "client": dict(devicejobs.CLIENT_CAPS)},
                                   answered)
            return
        async_call.run(self.client.resolve, candidate.get("ref"),
                       on_done=lambda result: self.resolved(result, fields, candidate),
                       on_error=lambda error: self.resolve_failed(candidate))

    def resolve_failed(self, candidate):
        self.busy = False
        if self.closed:
            return
        self["status"].setText(i18n.t("download.err.source_gone", self.lang))

    def resolved(self, result, fields, candidate):
        self.busy = False
        if self.closed:
            return
        allowed, reason = rules.downloadable(result)
        if not allowed:
            self.refused.add(candidate.get("cid"))     # greyed from now on, with the reason
            self.draw()
            self["status"].setText(downloadpick.error_text(reason, self.lang))
            return
        self.queue(dict(fields, **downloadpick.source_fields(candidate)), result, candidate)

    def queue(self, fields, prepared, candidate):
        try:
            self.manager.enqueue(fields, prepared)
        except downloadmanager.DownloadError as error:
            space = self.manager.space()
            self["status"].setText(downloadpick.error_text(error.code, self.lang, fields.get("size") or 0,
                                                           space[1] if space else None))
            return
        if self.manual:
            self.close(("queued", fields))
            return
        self.downloading_cid = (candidate or {}).get("cid")
        self.draw()
        self["status"].setText(i18n.t("download.started", self.lang))
        from .choice import Choice
        rows = [(i18n.t("download.go", self.lang), "go"), (i18n.t("download.stay", self.lang), "stay")]
        self.session.openWithCallback(self.after_queue, Choice, self.lang, i18n.t("download.started", self.lang), rows)

    def after_queue(self, answer):
        if answer and answer[1] == "go":
            from .downloads import Downloads
            self.session.open(Downloads, self.lang, self.client, self.source, self.posters)


class SeasonDownload:
    """Download season (app 064): the count, then Auto (each episode picks its server when its turn comes) or
    Manual (the download page for each episode in order; Skip, Stop, Use the last server for the rest)."""

    def __init__(self, screen, lang, client, source, posters, item, season, episodes, on_done=None):
        self.screen, self.lang, self.client, self.source, self.posters = screen, lang, client, source, posters
        self.item, self.season, self.on_done = item, int(season), on_done
        self.manager = downloadmanager.manager()
        self.eligible = rules.season_eligible(episodes, self.manager.items(), item.get("kind"), item.get("id"),
                                              self.season)
        self.queued, self.position, self.prev = 0, 0, None

    def offer(self):
        from .choice import Choice
        from Screens.MessageBox import MessageBox
        session = self.screen.session
        if not self.manager.directory:
            session.open(MessageBox, i18n.t("download.err.no_disk", self.lang), MessageBox.TYPE_ERROR, timeout=8)
            return
        if not self.eligible:
            session.open(MessageBox, i18n.t("download.season_none", self.lang), MessageBox.TYPE_INFO, timeout=6)
            return
        rows = [(i18n.t("download.auto", self.lang), "auto"), (i18n.t("download.manual", self.lang), "manual"),
                (i18n.t("download.cancel", self.lang), "cancel")]
        count = len(self.eligible)
        title = i18n.t("download.season_count_one", self.lang) if count == 1 else \
            i18n.t("download.season_count", self.lang) % count
        session.openWithCallback(self.mode, Choice, self.lang, title, rows)

    def mode(self, answer):
        if answer and answer[1] == "auto":
            self.queue_rest(None, self.eligible)
            self.summary()
        elif answer and answer[1] == "manual":
            self.next_episode()

    def fields(self, ep):
        return downloadpick.item_fields(self.item, self.season, int(ep["number"]), self.source)

    def queue_rest(self, prefer, episodes):
        for ep in episodes:
            try:
                self.manager.enqueue(dict(self.fields(ep), **downloadpick.auto_fields(prefer)))
                self.queued += 1
            except downloadmanager.DownloadError:
                break                                  # no disk / no space: the summary shows what was queued

    def next_episode(self):
        if self.position >= len(self.eligible):
            self.summary()
            return
        ep = self.eligible[self.position]
        manual = {"index": self.position, "count": len(self.eligible), "prev": self.prev}
        self.screen.session.openWithCallback(self.picked, DownloadPage, self.lang, self.client, self.source,
                                             self.posters, self.item, self.season, int(ep["number"]), manual)

    def picked(self, answer=None):
        what = answer[0] if answer else "stop"
        if what == "queued":
            self.queued += 1
            self.prev = answer[1]
        elif what == "rest" and self.prev:
            self.queue_rest(self.prev, self.eligible[self.position:])
            self.summary()
            return
        elif what == "stop":
            self.summary()
            return
        self.position += 1
        self.next_episode()

    def summary(self):
        if self.on_done:
            self.on_done()
        if not self.queued:
            return
        from .choice import Choice
        rows = [(i18n.t("download.go", self.lang), "go"), (i18n.t("download.stay", self.lang), "stay")]
        title = i18n.t("download.season_queued_one", self.lang) if self.queued == 1 else \
            i18n.t("download.season_queued", self.lang) % self.queued
        self.screen.session.openWithCallback(self.after_summary, Choice, self.lang, title, rows)

    def after_summary(self, answer):
        if answer and answer[1] == "go":
            from .downloads import Downloads
            self.screen.session.open(Downloads, self.lang, self.client, self.source, self.posters)
