"""Home: tab bar, focused title info, rows of poster tiles that page; OK opens the title (a Continue watching
card plays at once, FIX19; INFO opens its title page)."""
import time

import os

from enigma import ePoint, eSize, eTimer
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Screens.Screen import Screen

from . import downloadskey
from . import account as account_mod, api, async_call, downloadmanager, grid, i18n, layout, mylist, plans, playflow, posters, \
    prefs as prefs_mod, profiles, searchtext, skins, source as source_mod, store as store_mod, tabbar, tabs, theme

COLS, ROWS = layout.COLS, layout.ROWS
LOGO = os.path.join(os.path.dirname(__file__), "logo.png")    # the TV app emblem
ICONS = os.path.join(os.path.dirname(__file__), "icons")     # tools/enigma2/make_icons.py: <tab>_<state>.png


def visitor_store():
    root, _ = posters.default_root()
    base = root.rsplit("/cache", 1)[0] if root.startswith("/media/hdd") else "/etc/enigma2"
    name = "visitor.json" if base.startswith("/media/hdd") else "kanasa_visitor.json"
    return store_mod.Store(base + "/" + name)


class Home(Screen):
    def __init__(self, session, lang):
        self.skin = skins.home(lang)
        Screen.__init__(self, session)
        self.lang, self.rtl = lang, lang == "ar"
        self.store = visitor_store()
        self.account_file, self.prefs = account_mod.Account(), prefs_mod.Prefs()
        signed = self.account_file.load()
        # a 401 on any call forgets the token: every screen carries on as a visitor
        self.client = api.Client(lang=lang, bearer=signed and signed["bearer"],
                                 on_unauthorized=self.account_file.clear, on_profile_required=self.profile_signal)
        self.source = source_mod.Source(self.client, self.store, self.account_file, self.prefs)
        downloadmanager.manager(self.client)     # FIX9: queued downloads continue (Range resume) with this client
        root, cap = posters.default_root()
        self.posters = posters.PosterCache(root, cap)
        self.grid = grid.Grid([], COLS, ROWS)
        self.bar, self.in_tabs, self.failed, self.generation = tabbar.TabBar(), False, False, 0
        self.update_checked = False
        self.continue_flow = None                # FIX19: a Continue watching card on its way to the player
        self["status"] = Label(i18n.t("home.loading", lang))
        self["logo"], self["tabframe"], self["focus"] = Pixmap(), Label(""), Label("")
        for index, (tab_id, key) in enumerate(tabs.TABS):
            self["tab%d" % index] = Pixmap() if tab_id in tabs.ICONS else Label(i18n.t(key, lang))
        self.icon_state = {}
        self["profileinitial"], self["prostar"], self["profileavatar"] = Label(""), Pixmap(), Label("")
        # 084: "Who's watching?" before any per-viewer row loads; every 409 profile_required comes back here
        self.profile_state = profiles.parse(None)
        self.begun, self.picker_open, self.picker_pending = False, False, False
        self.picker_timer = eTimer()
        self.picker_timer.callback.append(self.open_picker)
        self.badge = account_mod.profile_badge(self.account_file.load(), int(time.time()))
        for r in range(ROWS):
            self["row%d" % r], self["bar%d" % r] = Label(""), Label("")
            for c in range(COLS):
                slot = "%dc%d" % (r, c)
                self["r" + slot] = Pixmap()
                for kind in "tsbwp":
                    self[kind + slot] = Label("")
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "MenuActions", "ColorActions"], {
            "cancel": self.close, "ok": self.ok, "menu": self.open_settings, "yellow": self.edit_list,
            "up": lambda: self.move(-1, 0), "down": lambda: self.move(1, 0),
            "left": lambda: self.move(0, layout.step(-1, self.rtl)),
            "right": lambda: self.move(0, layout.step(1, self.rtl))}, -1)
        # INFO: the title page of the focused card (FIX19: OK on a Continue watching card plays instead)
        self["infokey"] = ActionMap(["InfobarEPGActions"], {"showEventInfo": self.info}, -1)
        downloadskey.bind(self, lang, self.client, self.source, self.posters, used=("yellow",))
        self.onLayoutFinish.append(self.show_logo)
        self.onLayoutFinish.append(self.start)        # bound methods only (openATV exec()s others)

    # --- loading -------------------------------------------------------------------------------
    def show_logo(self):
        if self["logo"].instance:
            self["logo"].instance.setPixmapFromFile(LOGO)
        self["profileinitial"].setText(self.badge["initial"])
        self["profileinitial"].show() if self.badge["state"] != "out" else self["profileinitial"].hide()
        if self.badge["state"] == "pro" and self["prostar"].instance:
            self["prostar"].instance.setPixmapFromFile(os.path.join(ICONS, "pro_star.png"))
            self["prostar"].show()
        else:
            self["prostar"].hide()
        self.draw_profile()
        self.draw()

    # --- 084 profiles + plans --------------------------------------------------------------------------
    def start(self):
        """Signed in: entitlement + profiles first (§6), then the picker when the rule says so, then the rows.
        A visitor (or profiles off) loads exactly as before."""
        if not self.source.signed_in():
            plans.reset()
            self.begin()
            return
        async_call.run(self.fetch_account, on_done=self.account_fetched,
                       on_error=lambda error: self.account_fetched((None, None)))

    def fetch_account(self):
        """Off the GUI thread. A failed call is None: entitlement fails open (pro), profiles = off for now
        (a later 409 still brings the picker)."""
        got = []
        for call in (self.client.entitlement, self.client.profiles):
            try:
                got.append(call())
            except api.ApiError:
                got.append(None)
        return tuple(got)

    def account_fetched(self, got):
        entitlement, body = got
        plans.set_state(entitlement)
        self.take_profiles(profiles.parse(body))
        if profiles.needs_picker(self.profile_state) and not profiles.picked(self.client.bearer):
            self.picker_timer.start(0, True)      # a modal from a timer, never from inside a callback chain
        else:
            self.begin()

    def begin(self):
        if self.begun:
            return
        self.begun = True
        self.load_server_groups()
        self.load()
        self.start_import()

    def take_profiles(self, state):
        self.profile_state = profiles.remember(state)
        self.draw_profile()

    def draw_profile(self):
        """The top-bar avatar: the current profile's initial on its colour while profiles are on."""
        avatar, letter = self["profileavatar"], self["profileinitial"]
        shown = profiles.badge(self.profile_state) if self.badge["state"] != "out" else None
        if shown is None:
            avatar.hide()
            letter.setText(self.badge["initial"])
            if letter.instance:
                letter.instance.setForegroundColor(_rgb(theme.WHITE))
                letter.instance.invalidate()
            return
        letter.setText(shown["initial"])
        if avatar.instance:
            avatar.instance.setBackgroundColor(_rgb(shown["color"]))
            avatar.instance.invalidate()
        if letter.instance:
            letter.instance.setForegroundColor(_rgb(shown["text"]))
            letter.instance.invalidate()
        avatar.show()
        letter.show()

    def profile_signal(self):
        """api.Client's 409 profile_required hook: any thread -> the GUI thread."""
        async_call.on_gui(self.profile_required)

    def profile_required(self):
        if self.picker_open or self.picker_pending:
            return
        self.picker_pending = True
        profiles.forget_pick()
        async_call.run(self.client.profiles, on_done=self.required_state, on_error=self.required_failed)

    def required_state(self, body):
        self.picker_pending = False
        self.take_profiles(profiles.parse(body))
        self.open_picker()

    def required_failed(self, error):
        self.picker_pending = False

    def open_picker(self):
        if self.picker_open or not self.profile_state.get("enabled") or not self.profile_state.get("profiles"):
            if not self.begun:
                self.begin()
            return
        self.picker_open = True
        from .profilescreens import ProfilePicker
        self.session.openWithCallback(self.profile_picked, ProfilePicker, self.lang, self.client,
                                      self.profile_state, self.prefs.last_profile(), self.prefs,
                                      closing=not self.begun)

    def profile_picked(self, answer=None):
        self.picker_open = False
        if answer is None:
            if not self.begun:
                self.close()                      # EXIT on the opening "Who's watching?" closes the plugin
            return                                # from "Switch profile": back where you were (owner 11-23)
        profiles.mark_picked(self.client.bearer)
        self.take_profiles(profiles.parse(answer))
        if not self.begun:
            self.begin()
            return
        self.source.sent = {}                     # resume points of the previous viewer
        self.load_server_groups()                 # rows, stars and resume points are per viewer
        self.load()

    def switch_profile(self):
        def fresh(body):
            state = profiles.parse(body)
            if state["enabled"]:
                self.take_profiles(state)
            self.open_picker()
        async_call.run(self.client.profiles, on_done=fresh, on_error=lambda error: self.open_picker())

    def start_import(self):
        """Visitor My List + resume points from before the sign-in go to the account once (kept on failure)."""
        if self.source.signed_in():
            async_call.run(self.source.run_import, int(time.time()), on_done=lambda done: None,
                           on_error=lambda error: None)
            # FIX33: resume points saved while the network was down (a download watched offline)
            async_call.run(self.source.flush_pending, on_done=lambda sent: None, on_error=lambda error: None)

    def load_server_groups(self):
        if self.source.signed_in():
            async_call.run(self.source.load_server_groups, on_done=lambda loaded: None,
                           on_error=lambda error: None)

    def load(self):
        tab_id = tabs.TABS[self.bar.loaded][0]
        load = tabs.loader(self.client, self.source, tab_id)
        if load is None:
            return
        self.generation += 1
        generation = self.generation
        self.failed = False
        self["status"].setText(i18n.t("home.loading", self.lang))
        async_call.run(load, on_done=lambda rows: self.loaded(rows, generation, tab_id),
                       on_error=lambda error: self.load_failed(generation))

    def load_failed(self, generation):
        if generation != self.generation:
            return
        self.failed = True
        self["status"].setText(i18n.t("home.error", self.lang))

    def loaded(self, rows, generation, tab_id):
        if generation != self.generation:     # a late answer for a tab the viewer already left
            return
        self.grid = grid.Grid(rows, COLS, ROWS)
        empty_key = "mylist.empty" if tab_id == "mylist" else "home.empty"
        # FIX30: My List always ends with its Downloads row; "empty" means no titles
        filled = mylist.has_titles(rows) if tab_id == "mylist" else bool(rows)
        self["status"].setText("" if filled else i18n.t(empty_key, self.lang))
        self.draw()
        if not self.update_checked:
            self.update_checked = True
            from .updateflow import UpdateFlow
            self.update_flow = UpdateFlow(self)
            self.update_flow.on_checked = self.prompt_engine
            self.update_flow.check()

    def crash_answer(self, picked):
        from . import analytics
        crash, analytics.pending_crash = analytics.pending_crash, None
        if picked and picked[1] == "send" and crash:
            signed = self.account_file.load()
            async_call.run(analytics.send_log, "crash", self.lang, signed and signed["bearer"], crash,
                           on_done=lambda answer: None, on_error=lambda error: None)
        self.prompt_engine()

    def prompt_engine(self, update_popup=False):
        """After the first rows and update check, offer the optional engine when its pure rule allows.
        083 first (never over an update popup): the one-time usage notice, then a crash report question."""
        from . import analytics
        if not update_popup and analytics.notice_due():
            from Screens.MessageBox import MessageBox
            analytics.notice_shown()
            self.session.openWithCallback(lambda *a: self.prompt_engine(), MessageBox,
                                          i18n.t("analytics.notice", self.lang), MessageBox.TYPE_INFO, timeout=0)
            return
        if not update_popup and analytics.pending_crash:
            from .choice import Choice
            rows = [(i18n.t("analytics.crash_send", self.lang), "send"),
                    (i18n.t("analytics.crash_skip", self.lang), "skip")]
            self.session.openWithCallback(self.crash_answer, Choice, self.lang,
                                          i18n.t("analytics.crash_title", self.lang), rows)
            return
        from .engineflow import EngineInstallFlow
        self.engine_flow = EngineInstallFlow(self)
        self.engine_flow.offer(update_popup=update_popup)

    def more(self):
        wanted = self.grid.wants_more()
        if wanted is None:
            return
        page_tab, page_row, page, row_id = wanted
        generation = self.generation
        async_call.run(self.client.row_page, page_tab, page_row, page,
                       on_done=lambda got: self.paged(got, row_id, generation), on_error=lambda error: None)

    def paged(self, got, row_id, generation):
        if generation == self.generation:
            self.grid.append(row_id, got["items"], got["next_page"])
            self.draw()

    # --- My List: rename / delete a named list (like the app) ---------------------------------------
    def focused_list(self):
        if self.in_tabs or not self.source.signed_in() or tabs.TABS[self.bar.loaded][0] != "mylist" \
                or not self.grid.rows:
            return None
        return mylist.named_list(self.grid.rows[self.grid.row])

    def edit_list(self):
        named = self.focused_list()
        if named is None:
            if prefs_mod.downloads_key(self.prefs.downloads_button()) == "yellow":
                from .downloads import Downloads     # yellow is also the Downloads key: off a named list
                self.session.open(Downloads, self.lang, self.client, self.source, self.posters)
            return
        from .choice import Choice
        rows = [(i18n.t("mylist.rename", self.lang), "rename"), (i18n.t("mylist.delete", self.lang), "delete"),
                (i18n.t("download.cancel", self.lang), "cancel")]
        self.session.openWithCallback(lambda picked: self.list_action(picked, named), Choice, self.lang, named[1],
                                      rows)

    def list_action(self, picked, named):
        if not picked or picked[1] == "cancel":
            return
        if picked[1] == "rename":
            from Screens.VirtualKeyBoard import VirtualKeyBoard
            self.session.openWithCallback(lambda text=None: self.list_renamed(named, text), VirtualKeyBoard,
                                          title=i18n.t("mylist.rename_prompt", self.lang), text=named[1])
            return
        from .choice import Choice
        rows = [(i18n.t("mylist.delete", self.lang), "yes"), (i18n.t("download.cancel", self.lang), "no")]
        self.session.openWithCallback(lambda answer: answer and answer[1] == "yes" and self.list_call(
            self.client.library_list_delete, named[0]), Choice, self.lang,
            i18n.t("mylist.delete_ask", self.lang) % named[1], rows)

    def list_renamed(self, named, text=None):
        name = mylist.clean_name(text)
        if name and name != named[1]:
            self.list_call(self.client.library_list_rename, named[0], name)

    def list_call(self, call, *args):
        def failed(error):
            self["status"].setText(i18n.t("mylist.failed", self.lang))
        async_call.run(call, *args, on_done=lambda result: self.load(), on_error=failed)

    def list_hint(self):
        hint = i18n.t("mylist.edit_hint", self.lang)
        if self.focused_list() is not None:
            self["status"].setText(hint)
        elif self["status"].getText() == hint:
            self["status"].setText("")

    # --- keys ----------------------------------------------------------------------------------
    def move(self, d_row, d_col):
        before = self.grid.window()
        if self.in_tabs:
            if d_row > 0 and self.grid.focused() is not None:
                self.in_tabs = False
                self.bar.leave()         # the rows belong to the loaded tab, not the highlighted one
            elif d_col:
                self.bar.move(d_col)     # only highlights: OK loads (owner 2026-11-16)
            self.draw(cards=False)
            return
        if not self.grid.move(d_row, d_col):
            self.in_tabs = True
        self.more()
        self.draw(cards=before != self.grid.window())
        self.list_hint()

    def ok(self):
        if self.in_tabs:
            action, tab_id = self.bar.ok()
            if tab_id == "search":
                from .search import Search
                self.session.open(Search, self.lang, self.client, self.source, self.posters)
            elif tab_id == "settings":
                self.open_settings()
            elif tab_id == "profile":
                self.open_profile()
            elif tab_id == "downloads":           # FIX30: the Downloads page from the top menu
                self.open_downloads()
            else:
                self.grid = grid.Grid([], COLS, ROWS)
                self.draw()
                self.load()
            return
        if self.failed:
            self.load()
            return
        item = self.grid.focused()
        if item is not None and not self.in_tabs:
            site_play = searchtext.site_continue_target(item, self.focused_row_id())
            if site_play is not None:                 # FIX28: a site episode in Continue watching plays
                from . import playerguard
                playerguard.open_player(self.session, self.back_from_title, self.lang, self.client, self.source,
                                        site_play)
                return
            if item.get("kind") == "site":            # 018: a Turkish-site title from search
                from .sitetitle import SiteTitle
                self.session.openWithCallback(self.back_from_title, SiteTitle, self.lang, self.client, self.source,
                                              self.posters, item)
                return
            if item.get("kind") == "download":        # My List's Downloads row (FIX9; FIX30: also its empty tile)
                self.open_downloads(item.get("id"))
                return
            if playflow.continue_action(item, self.focused_row_id()) == "play":
                self.play_continue(item)
                return
            self.open_title(item)

    def focused_row_id(self):
        return self.grid.rows[self.grid.row].get("id") if self.grid.rows else None

    def open_title(self, item):
        from .title import Title
        self.session.openWithCallback(self.back_from_title, Title, self.lang, self.client, self.source,
                                      self.posters, item)

    def info(self):
        item = None if self.in_tabs or self.failed else self.grid.focused()
        if item is not None and item.get("kind") in ("movie", "tv"):
            self.open_title(item)

    def play_continue(self, item):
        """FIX19: a Continue watching card plays like the title page's Resume (saved point, saved server first,
        the card's episode) without the title page or the resume question; the player's close comes back here
        and the row reloads."""
        if self.continue_flow is not None:
            return                               # the title is still loading: one OK is enough
        card = dict(item, progress=dict(item["progress"]))
        self.continue_flow = playflow.PlayFlow(self.session, self.lang, self.client, self.source, self.posters,
                                               card, None, self.continue_closed)
        self.continue_flow.on_started = self.continue_started
        self["status"].setText(i18n.t("home.loading", self.lang))
        self.continue_flow.play_continue(lambda: self.continue_fallback(item))

    def continue_started(self):
        self.continue_flow = None
        self["status"].setText("")

    def continue_fallback(self, item):
        self.continue_started()
        self.open_title(item)

    def continue_closed(self, answer=None):
        self.back_from_title()

    def open_downloads(self, focus=None):
        from .downloads import Downloads
        self.session.openWithCallback(self.back_from_title, Downloads, self.lang, self.client, self.source,
                                      self.posters, focus)

    def open_settings(self):
        from .settings import Settings
        self.session.openWithCallback(self.back_from_settings, Settings, self.lang, self.client, self.account_file,
                                      self.prefs, self.posters, self.source)

    def open_profile(self):
        """Signed out: the QR sign-in straight away. Signed in (owner 2026-11-23, the same on every device): the
        profile button is the current profile's avatar and offers Switch profile (profiles on) + Sign out;
        Settings has its own gear button."""
        if self.account_file.load() is not None:
            from .choice import Choice
            current = profiles.current_profile(self.profile_state)
            rows = [(i18n.t("profiles.switch", self.lang), "switch")] if profiles.settings_visible(self.profile_state) \
                else []
            rows.append((i18n.t("profile.sign_out", self.lang), "sign_out"))
            title = i18n.t("profiles.menu_title", self.lang) % current["name"] if current else \
                i18n.t("tab.profile", self.lang)
            self.session.openWithCallback(self.profile_menu, Choice, self.lang, title, rows)
            return
        from .signin import SignIn
        self.session.openWithCallback(self.back_from_sign_in, SignIn, self.lang, self.client, self.account_file)

    def profile_menu(self, picked=None):
        if picked and picked[1] == "switch":
            self.switch_profile()
        elif picked and picked[1] == "sign_out":
            self.sign_out()

    def sign_out(self):
        """Settings' account row does the same: forget the token here first, tell the website in the background
        (no network = still signed out here), then a fresh Home as a visitor."""
        account = self.account_file.load()
        if account is None:
            return
        self.account_file.clear()
        profiles.remember(None)
        profiles.forget_pick()
        dead = api.Client(lang=self.lang, bearer=account["bearer"])
        async_call.run(dead.logout, on_done=lambda _: None, on_error=lambda error: None)
        self.close("restart")

    def back_from_sign_in(self, name=None):
        if name:
            self.close("restart")        # like Settings: a fresh Home with the account everywhere

    def back_from_settings(self, answer=None):
        if answer == "restart":
            self.close("restart")        # plugin.main opens a fresh Home: new language, new account everywhere
            return
        if self.profile_state.get("enabled"):     # Settings -> Profiles may have renamed / recoloured this one
            self.take_profiles(profiles.last_state())

    def back_from_title(self, *args):
        # Progress saved by the player changes Continue watching on Home as well as My List.
        self.load()

    # --- drawing -------------------------------------------------------------------------------
    def draw(self, cards=True):
        self.draw_tabs()
        if cards:
            for r, row in enumerate(self.grid.window()):
                self["row%d" % r].setText(row["label"])
                self["bar%d" % r].show() if row["label"] else self["bar%d" % r].hide()
                for c, item in enumerate(row["items"]):
                    self.draw_card("%dc%d" % (r, c), item)
        slot = self.grid.focus_slot() if self.grid.focused() is not None else None
        if slot is None or self.in_tabs or not self["focus"].instance:
            self["focus"].hide()
        else:
            box = layout.card_box(slot[0], slot[1], self.rtl)
            self["focus"].instance.move(ePoint(box["x"] - theme.FRAME, box["y"] - theme.FRAME))
            self["focus"].show()

    def draw_tabs(self):
        boxes = layout.tab_boxes(self.rtl)
        for index, (tab_id, _) in enumerate(tabs.TABS):
            label = self["tab%d" % index]
            focused = self.in_tabs and index == self.bar.cursor
            if tab_id in tabs.ICONS:
                state = ("a" if index == self.bar.loaded else "n") + ("f" if focused else "")
                name = "%s_%s.png" % (tab_id, state)
                if tab_id == "profile" and self.badge["state"] != "out":
                    name = "avatar_%s.png" % ("f" if focused else "n")      # signed in: a different look
                if label.instance and self.icon_state.get(tab_id) != name:
                    label.instance.setPixmapFromFile(os.path.join(ICONS, name))
                    self.icon_state[tab_id] = name
                continue
            if label.instance:
                active = index == self.bar.loaded
                lit = active or focused
                label.instance.setForegroundColor(_rgb(theme.TEXT if lit else theme.DIM))
                label.instance.setBackgroundColor(_rgb(theme.SURFACE if active else theme.BG))
                label.instance.invalidate()
        frame = self["tabframe"]
        if frame.instance and self.in_tabs and tabs.TABS[self.bar.cursor][0] not in tabs.ICONS:
            box_x, box_w = boxes[self.bar.cursor]
            frame.instance.resize(eSize(box_w + 2 * theme.FRAME, layout.TAB_H + 2 * theme.FRAME))
            frame.instance.move(ePoint(box_x - theme.FRAME, layout.TAB_Y - theme.FRAME))
            frame.show()
        else:
            frame.hide()

    def draw_card(self, slot, item):
        poster, title, sub = self["r" + slot], self["t" + slot], self["s" + slot]
        series, watching, bar = self["b" + slot], self["w" + slot], self["p" + slot]
        if item is None:
            poster.hide()
            title.setText("")
            sub.setText("")
            for widget in (series, watching, bar):
                widget.hide()
            return
        self.show_poster(poster, item.get("poster"))
        title.setText(layout.card_title(item))
        sub.setText(layout.card_sub(item, self.lang))
        badges = layout.card_badges(item)
        series.setText(i18n.t("card.series", self.lang))
        series.show() if badges["series"] else series.hide()
        watching.setText(i18n.t("card.watching", self.lang))
        watching.show() if badges["watching"] else watching.hide()
        width = layout.bar_fill(badges["percent"], 100, layout.POSTER[0])
        if width and bar.instance:
            bar.instance.resize(eSize(width, 6))
            bar.show()
        else:
            bar.hide()

    def show_poster(self, tile, url):
        # tiles are reused while scrolling: hide until THIS url's image is in, so a tile never shows
        # the previous title's poster (no poster / failed fetch = blank tile)
        if getattr(tile, "kanasa_url", None) == url and getattr(tile, "kanasa_shown", False):
            tile.show()
            return
        if getattr(tile, "kanasa_url", None) == url:
            return                       # already fetching it
        tile.kanasa_url, tile.kanasa_shown = url, False
        tile.hide()

        def done(path):
            if path and tile.instance and getattr(tile, "kanasa_url", None) == url:
                tile.instance.setPixmapFromFile(path)
                tile.kanasa_shown = True
                tile.show()
        async_call.run(self.posters.get, url or "", layout.POSTER, theme.RADIUS_CARD, on_done=done,
                       on_error=lambda e: None)


def _rgb(value):
    from enigma import gRGB
    return gRGB(int(value.lstrip("#"), 16))
