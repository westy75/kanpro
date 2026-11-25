"""Opaque standalone seasons/episodes browser; playback is returned to the title screen.

FIX9: each episode row has a Watched toggle (accounts) and under it a Download button; green = Download season."""
import os
from enigma import ePoint, eTimer
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Screens.Screen import Screen
from . import downloadskey
from . import async_call, downloadmanager, downloadrules, episodeslayout, episodesnav, i18n, layout, plans, skins, \
    theme

ICONS = os.path.join(os.path.dirname(__file__), "icons")
REFRESH_MS = 2000                 # a running download's % on its button


class Episodes(Screen):
    def __init__(self, session, lang, client, posters, item, detail, source=None):
        self.skin = skins.episodes(lang)
        Screen.__init__(self, session)
        self.lang, self.rtl, self.client, self.posters, self.source = lang, lang == "ar", client, posters, source
        self.item, self.detail, self.seasons, self.episodes = item, detail, list(detail.get("seasons") or []), []
        self.season_index, self.offset, self.generation, self.shown_season = 0, 0, 0, None
        self.can_mark = bool(source and source.signed_in())        # /api/progress/watched is account-only
        self.manager = downloadmanager.manager()
        self.nav = episodesnav.Nav(len(self.seasons), 0, self.rtl, watched=self.can_mark,
                                   downloads=plans.downloads_allowed())
        for name in ("backpill", "back", "title", "meta", "seasonframe", "seasonselect", "seasonselected", "epframe",
                     "season_download", "epbtnframe"):
            self[name] = Label("")
        self["seasonarrow"], self["backicon"] = Pixmap(), Pixmap()
        self["back"].setText("Back" if lang != "ar" else "رجوع")
        self["title"].setText(item.get("title") or "")
        season_key = "episodes.season.one" if len(self.seasons) == 1 else "episodes.season.many"
        self["meta"].setText("%s   %s" % (item.get("year") or "", i18n.t(season_key, lang) % len(self.seasons)))
        for i in range(8):
            self["seasonbox%d" % i], self["season%d" % i], self["seasoncount%d" % i] = Label(""), Label(""), Label("")
        for i in range(4):
            self["epborder%d" % i], self["epcard%d" % i] = Label(""), Label("")
            self["still%d" % i], self["watched%d" % i] = Pixmap(), Pixmap()
            self["watchbtn%d" % i], self["downloadbtn%d" % i] = Label(""), Label("")
            self["ondisk%d" % i] = Label("")
            for kind in ("eptitle", "epdate", "epoverview"): self["%s%d" % (kind, i)] = Label("")
        self.timer = eTimer()
        self.timer.callback.append(self.refresh_downloads)
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
            "cancel": self.close, "ok": self.ok, "up": lambda: self.vertical(-1),
            "down": lambda: self.vertical(1), "left": lambda: self.horizontal("left"),
            "right": lambda: self.horizontal("right"), "green": self.download_season}, -1)
        downloadskey.bind(self, lang, client, source, posters, used=("green",))
        self.onLayoutFinish.append(self.start)
        self.onClose.append(self.timer.stop)

    def start(self):
        if self["backicon"].instance:
            self["backicon"].instance.setPixmapFromFile(os.path.join(
                ICONS, "pill_back_r_surface.png" if self.rtl else "pill_back_surface.png"))
        if self["seasonarrow"].instance:
            self["seasonarrow"].instance.setPixmapFromFile(os.path.join(ICONS, "selected.png"))
        for i in range(4):
            if self["watched%d" % i].instance:
                self["watched%d" % i].instance.setPixmapFromFile(os.path.join(ICONS, "watched.png"))
        self.draw_seasons(); self.load_season(0)
        self.timer.start(REFRESH_MS, False)

    def draw_seasons(self):
        for i in range(8):
            season = self.seasons[i] if i < len(self.seasons) else None
            for kind in ("seasonbox", "season", "seasoncount"):
                self["%s%d" % (kind, i)].show() if season else self["%s%d" % (kind, i)].hide()
            self["season%d" % i].setText((season.get("label") or "Season %s" % season.get("number")) if season else "")
            count = season.get("episode_count") if season else None
            key = "episodes.episode.one" if count == 1 else "episodes.episode.many"
            self["seasoncount%d" % i].setText((i18n.t(key, self.lang) % count) if count is not None else "")

    def load_season(self, index, enter=False):
        """Show season `index` in the right column; `enter` moves the focus to its first episode."""
        if not self.seasons: return
        self.season_index, self.generation = index, self.generation + 1
        generation, number = self.generation, self.seasons[index].get("number")
        async_call.run(self.client.season, int(self.item.get("id")), int(number),
                       on_done=lambda rows: self.loaded(rows, generation, enter),
                       on_error=lambda e: self.loaded([], generation, enter))

    def loaded(self, rows, generation, enter=False):
        if generation != self.generation: return
        focus = self.nav.index if self.nav.area == "seasons" else self.season_index
        self.episodes, self.offset = list(rows), 0
        self.shown_season = self.seasons[self.season_index].get("number")
        self.nav = episodesnav.Nav(len(self.seasons), len(rows), self.rtl,
                                   [ep.get("released", True) for ep in rows], self.season_index, self.can_mark,
                                   downloads=plans.downloads_allowed())
        if enter and rows:
            self.nav.area, self.nav.index = "episodes", 0
        else:
            self.nav.index = focus
        self.draw_episodes(); self.draw_focus()

    def draw_episodes(self):
        season = self.shown_season if self.shown_season is not None else 0
        views = [episodeslayout.episode_view(ep, season) for ep in self.episodes]
        for slot in range(4):
            index, view = self.offset + slot, None
            if index < len(views): view = views[index]
            for kind in ("epborder", "epcard", "eptitle", "epdate", "epoverview", "watched"):
                self["%s%d" % (kind, slot)].show() if view else self["%s%d" % (kind, slot)].hide()
            if not view:
                self["still%d" % slot].hide(); self["watchbtn%d" % slot].hide(); self["downloadbtn%d" % slot].hide()
                continue
            self["eptitle%d" % slot].setText(view["label"])
            self["epdate%d" % slot].setText(view["air_date"])
            self["epoverview%d" % slot].setText(view["overview"])
            self["watched%d" % slot].show() if view["watched"] else self["watched%d" % slot].hide()
            if self["epborder%d" % slot].instance:
                self["epborder%d" % slot].instance.setBackgroundColor(
                    theme.rgb(theme.YELLOW if view["watched"] else theme.BORDER))
                self["epborder%d" % slot].instance.invalidate()
            self.load_still(slot, view)
        self.draw_buttons()

    def draw_buttons(self):
        """The Watched toggle (✓ = watched only) and the Download button with its state, green when downloaded."""
        season = self.shown_season if self.shown_season is not None else 0
        items = self.manager.items()
        for slot in range(4):
            index = self.offset + slot
            watch, download = self["watchbtn%d" % slot], self["downloadbtn%d" % slot]
            disk = self["ondisk%d" % slot]
            if index >= len(self.episodes) or not self.episodes[index].get("released", True):
                watch.hide(); download.hide(); disk.hide()
                continue
            view = episodeslayout.episode_view(self.episodes[index], season)
            if self.can_mark:
                watch.setText(i18n.t("episode.watched" if view["watched"] else "episode.mark", self.lang))
                if watch.instance:
                    watch.instance.setForegroundColor(theme.rgb(theme.YELLOW if view["watched"] else theme.TEXT))
                    watch.instance.invalidate()
                watch.show()
            else:
                watch.hide()
            row = downloadrules.find(items, downloadrules.item_key(self.item.get("kind"), self.item.get("id"),
                                                                   season, view["number"]))
            on_disk = episodeslayout.on_disk(row)                            # FIX30: plays from the disk
            disk.setText(i18n.t("episode.on_disk", self.lang) if on_disk else "")
            disk.show() if on_disk else disk.hide()
            if not plans.downloads_allowed():     # 084: a free plan without downloads
                download.hide()
                continue
            download.setText(downloadrules.state_label(row, i18n.t, self.lang))
            if download.instance:
                done = downloadrules.row_state(row) == "downloaded"
                download.instance.setForegroundColor(theme.rgb(theme.GREEN if done else theme.TEXT))
                download.instance.invalidate()
            download.show()
        if not plans.downloads_allowed():
            self["season_download"].setText(i18n.t("plans.get", self.lang))
        elif self.shown_season is not None and self.episodes:
            done, total = downloadrules.season_done(self.episodes, items, self.item.get("kind"), self.item.get("id"),
                                                    season)
            self["season_download"].setText(i18n.t("download.season_hint", self.lang) % (done, total))
        else:
            self["season_download"].setText("")

    def refresh_downloads(self):
        if any(row.get("state") in downloadrules.ACTIVE for row in self.manager.items()):
            self.draw_buttons()

    def load_still(self, slot, view):
        widget, url = self["still%d" % slot], view.get("still")
        widget.hide(); widget.kanasa_url = url
        def done(path):
            if path and widget.instance and getattr(widget, "kanasa_url", None) == url:
                widget.instance.setPixmapFromFile(path); widget.show()
        async_call.run(self.posters.get, url or "", (270, 152), theme.RADIUS_CARD, None,
                       view["released"], view["percent"], on_done=done, on_error=lambda e: None)

    def vertical(self, direction):
        self.nav.vertical(direction)
        if self.nav.area == "episodes":
            offset = layout.keep_visible(self.offset, self.nav.index, len(self.episodes), 4)
            if offset != self.offset:
                self.offset = offset; self.draw_episodes()
        self.draw_focus()

    def horizontal(self, key):
        self.nav.horizontal(key)
        self.draw_focus()

    def ok(self):
        action = self.nav.ok()
        if not action: return
        if action[0] == "season":       # only OK changes the right column (owner 2026-11-17)
            self.load_season(action[1], enter=True); self.draw_focus(); return
        ep = self.episodes[action[1]]
        if self.shown_season is None: return
        season = int(self.shown_season)
        if action[0] == "watched":
            self.toggle_watched(ep, season)
        elif action[0] == "download":
            self.download(ep, season)
        else:
            self.close(("play", season, int(ep.get("number")), list(self.episodes)))

    # --- Watched (one episode: POST /api/progress/watched, like the website's episode check) ------
    def toggle_watched(self, ep, season):
        watched = not episodeslayout.episode_view(ep, season)["watched"]
        async_call.run(self.client.episode_watched, int(self.item.get("id")), season, int(ep.get("number")), watched,
                       on_done=lambda value: self.watched_done(ep, value), on_error=lambda e: None)

    def watched_done(self, ep, value):
        progress = dict(ep.get("progress") or {}, watched=bool(value))
        if not value:
            progress.update(percent=0)
        ep["progress"] = progress
        self.draw_episodes(); self.draw_focus()

    # --- Download ------------------------------------------------------------------------------
    def download(self, ep, season):
        if not plans.downloads_allowed():
            return
        row = self.manager.find(self.item.get("kind"), self.item.get("id"), season, ep.get("number"))
        state = downloadrules.row_state(row)
        if state == "failed-retry":
            self.manager.retry(row["id"]); self.draw_buttons()
        elif state == "download":
            from .downloadpage import DownloadPage
            self.session.openWithCallback(lambda *a: self.draw_buttons(), DownloadPage, self.lang, self.client,
                                          self.source, self.posters, self.item, season, int(ep.get("number")))
        else:                           # queued / NN% / downloaded: the Downloads page, on this item
            from .downloads import Downloads
            self.session.openWithCallback(lambda *a: self.draw_buttons(), Downloads, self.lang, self.client,
                                          self.source, self.posters, row["id"])

    def download_season(self):
        if self.shown_season is None or not self.episodes or self.source is None: return
        if not plans.downloads_allowed():
            plans_hint(self.session, self.lang)
            return
        from .downloadpage import SeasonDownload
        self.season_flow = SeasonDownload(self, self.lang, self.client, self.source, self.posters, self.item,
                                          int(self.shown_season), self.episodes, on_done=self.draw_buttons)
        self.season_flow.offer()

    def draw_focus(self):
        # crash 2026-11-21 19:36 (KeyError 'epcard-4'): Right from the seasons put the focus on an episode above
        # the scrolled window; only vertical() scrolled. Every focus change now scrolls the focus into view.
        if self.nav.area == "episodes" and self.episodes:
            offset = layout.keep_visible(self.offset, self.nav.index, len(self.episodes), 4)
            if offset != self.offset:
                self.offset = offset; self.draw_episodes()
        self["seasonframe"].hide(); self["epframe"].hide(); self["epbtnframe"].hide()
        self["seasonselect"].hide(); self["seasonselected"].hide(); self["seasonarrow"].hide()
        if self.seasons and self["seasonselect"].instance:
            x, y, _, _ = episodeslayout.season_card(self.season_index, self.rtl)
            for name in ("seasonselect", "seasonselected"):
                self[name].instance.move(ePoint(x - 3 if name == "seasonselect" else x,
                                                y - 3 if name == "seasonselect" else y)); self[name].show()
            # the app's Episodes screen marks the selected season by its fill + ring only ("N episodes" stays)
        for slot in range(4):
            if self["epcard%d" % slot].instance:
                self["epcard%d" % slot].instance.setBackgroundColor(theme.rgb(theme.SURFACE))
                self["epcard%d" % slot].instance.invalidate()   # the box repaints a colour only on invalidate
        if self.nav.area == "seasons" and self["seasonframe"].instance:
            x, y, _, _ = episodeslayout.season_card(self.nav.index, self.rtl)
            self["seasonframe"].instance.move(ePoint(x - 3, y - 3)); self["seasonframe"].show()
        elif self.nav.area == "episodes" and self.nav.part != "card" and self["epbtnframe"].instance:
            x, y, _, _ = episodeslayout.button_box(self.nav.index - self.offset, self.nav.part, self.rtl)
            self["epbtnframe"].instance.move(ePoint(x - theme.FRAME, y - theme.FRAME)); self["epbtnframe"].show()
        elif self.nav.area == "episodes" and self["epframe"].instance:
            slot = self.nav.index - self.offset
            x, y, _, _ = episodeslayout.episode_card(slot, self.rtl)
            self["epframe"].instance.move(ePoint(x - 3, y - 3)); self["epframe"].show()
            if self["epcard%d" % slot].instance:
                self["epcard%d" % slot].instance.setBackgroundColor(theme.rgb("#0f2230"))
                self["epcard%d" % slot].instance.invalidate()   # the box repaints a colour only on invalidate


def plans_hint(session, lang):
    """084 §5: a free plan without downloads: say where a plan comes from (activation is on the website)."""
    from Screens.MessageBox import MessageBox
    session.open(MessageBox, i18n.t("plans.downloads", lang) + "\n" + i18n.t("plans.get", lang),
                 MessageBox.TYPE_INFO, timeout=0)
