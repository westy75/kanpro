"""Settings: account (QR sign-in / sign-out), language, poster cache, torrent engine, downloads, version."""
import time

from enigma import ePoint
from Components.ActionMap import ActionMap
from Components.Label import Label
from Screens.Screen import Screen

from . import downloadskey
from . import __version__, account as account_mod, analytics, api, async_call, bufferrules, downloadmanager, downloadrules, i18n, layout, prefs as prefs_mod, profiles, skins, theme, torrengine

ROWS = ("account", "lang", "livetv", "mainmenu", "buffer", "servers", "cache", "torrent", "downloads", "folder", "parallel", "dlkey", "share", "logs",
        "version")
VISIBLE = skins.SETTINGS_ROWS           # rows on screen; the list scrolls past them


def rows_for(state, signed_in) -> tuple:
    """084: a Profiles row under the account while profiles are on for this account (hidden otherwise)."""
    if signed_in and profiles.settings_visible(state):
        return ROWS[:1] + ("profiles",) + ROWS[1:]
    return ROWS


class Settings(Screen):
    """Closes with "restart" when the language or the account changed (Home reopens with the new state)."""

    def __init__(self, session, lang, client, account_file, prefs, posters, source=None):
        self.skin = skins.settings(lang)
        Screen.__init__(self, session)
        self.lang, self.client, self.account_file, self.prefs, self.posters, self.source = \
            lang, client, account_file, prefs, posters, source
        self.changed, self.cache_mb, self.index, self.offset = False, None, 0, 0
        self.engine_dir, self.engine_mb = None, None
        self.downloads_space = None          # (used, free) on the download disk; False = no disk
        self.rows = rows_for(profiles.last_state(), account_file.load() is not None)
        self["title"] = Label(i18n.t("settings.title", lang))
        self["rowframe"], self["hint"] = Label(""), Label("")
        for index in range(VISIBLE):
            self["row%d" % index], self["val%d" % index], self["key%d" % index] = Label(""), Label(""), Label("")
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions"], {
            "ok": self.ok, "cancel": self.leave,
            "up": lambda: self.move(-1), "down": lambda: self.move(1)}, -1)
        downloadskey.bind(self, lang, client, source, posters)
        self.onLayoutFinish.append(self.draw)
        self.onLayoutFinish.append(self.measure_cache)
        self.onLayoutFinish.append(self.measure_engine)
        self.onLayoutFinish.append(self.measure_downloads)

    def move(self, step):
        self.index = max(0, min(len(self.rows) - 1, self.index + step))
        self.draw()

    def measure_cache(self):
        async_call.run(self.posters.size_bytes, on_done=self.measured, on_error=lambda error: None)

    def measured(self, size):
        self.cache_mb = int(round(size / (1024.0 * 1024.0)))
        self.draw()

    def measure_engine(self):
        def measure():
            directory = torrengine.installed_dir()
            return directory, torrengine.size_bytes(directory) if directory else 0
        async_call.run(measure, on_done=self.engine_measured, on_error=lambda error: None)

    def engine_measured(self, found):
        self.engine_dir, size = found
        self.engine_mb = int(round(size / (1024.0 * 1024.0))) if self.engine_dir else 0
        self.draw()

    def measure_downloads(self):
        async_call.run(downloadmanager.manager().space, on_done=self.downloads_measured, on_error=lambda e: None)

    def downloads_measured(self, space):
        self.downloads_space = space if space else False
        self.draw()

    def downloads_value(self):
        if self.downloads_space is None:
            return i18n.t("home.loading", self.lang)
        if self.downloads_space is False:
            return i18n.t("settings.downloads_none", self.lang)
        return i18n.t("settings.downloads_value", self.lang) % downloadrules.gb(self.downloads_space[1])

    def engine_value(self):
        if self.engine_mb is None:
            return i18n.t("home.loading", self.lang)
        if not self.engine_dir:
            return i18n.t("settings.engine_none", self.lang)
        return i18n.t("settings.engine_installed", self.lang) % self.engine_mb

    def row_values(self, account) -> dict:
        names = {"auto": i18n.t("settings.lang_auto", self.lang), "ar": "العربية", "en": "English"}
        badge = account_mod.profile_badge(account, int(time.time()))
        name = ((account["name"] or "Kanasa") + ("  " + theme.GLYPHS["star"] + " Pro" if badge["state"] == "pro"
                                                  else "")) if account else "—"
        return {"account": name, "lang": names[self.prefs.lang()],
                "livetv": i18n.t("settings.on" if self.prefs.stop_live_tv() else "settings.off", self.lang),
                "mainmenu": self.main_menu_value(),
                "buffer": bufferrules.choice_text(self.prefs.buffer_minutes(), self.lang),
                "cache": "%d MB" % self.cache_mb if self.cache_mb is not None else i18n.t("home.loading", self.lang),
                "torrent": self.engine_value(), "downloads": self.downloads_value(),
                "servers": i18n.t("settings.servers_auto", self.lang) % i18n.t(
                    "settings.on" if self.source.server_autoplay() else "settings.off", self.lang)
                if self.source is not None else "",
                "folder": self.folder_value(), "parallel": str(self.prefs.simultaneous_downloads()),
                "dlkey": i18n.t("settings.key." + self.prefs.downloads_button(), self.lang),
                "share": i18n.t("settings.on" if analytics.sharing() else "settings.off", self.lang),
                "logs": getattr(self, "logs_value", ""),
                "version": __version__, "profiles": self.profile_value()}

    def main_menu_value(self):
        return i18n.t("settings.on" if self.prefs.main_menu() else "settings.off", self.lang)

    def profile_value(self):
        current = profiles.current_profile(profiles.last_state())
        return current["name"] if current else ""

    def folder_value(self):
        choice = self.prefs.download_folder()
        if choice == "auto":
            return i18n.t("settings.folder_auto", self.lang)
        return choice

    def draw(self):
        account = self.account_file.load()
        values = self.row_values(account)
        self.offset = layout.keep_visible(self.offset, self.index, len(self.rows), VISIBLE)
        for slot in range(VISIBLE):
            index = self.offset + slot
            name = self.rows[index] if index < len(self.rows) else None
            for kind in ("row", "key", "val"):
                self["%s%d" % (kind, slot)].show() if name else self["%s%d" % (kind, slot)].hide()
            if name:
                self["key%d" % slot].setText(i18n.t("settings.k." + name, self.lang))
                self["val%d" % slot].setText(values.get(name, ""))
        row = self.rows[self.index]
        hint = "settings.hint.sign_out" if row == "account" and account else \
            "settings.hint.torrent_none" if row == "torrent" and not self.engine_dir else "settings.hint." + row
        if row == "account" and account:
            self["hint"].setText(i18n.t("settings.signed_in_as", self.lang) % (account["name"] or "Kanasa"))
        elif row == "downloads":
            folder, space = downloadmanager.manager().directory, self.downloads_space
            used = downloadrules.gb(space[0]) if space else "…"
            self["hint"].setText(i18n.t(hint, self.lang) % (used, folder) if folder else
                                 i18n.t("download.err.no_disk", self.lang))
        elif row == "folder":
            folder = downloadmanager.manager().directory
            self["hint"].setText(i18n.t(hint, self.lang) % folder if folder else
                                 i18n.t("download.err.no_disk", self.lang))
        else:
            self["hint"].setText(i18n.t(hint, self.lang))
        frame = self["rowframe"]
        if frame.instance:
            frame.instance.move(ePoint(layout.x(skins.ROW_X, skins.ROW_W, self.lang == "ar") - theme.FRAME,
                                       skins.ROW_Y0 + (self.index - self.offset) * skins.ROW_STEP - theme.FRAME))

    def ok(self):
        row = self.rows[self.index]
        if row == "account":
            self.account()
        elif row == "profiles":
            from .profilescreens import ProfilesPage
            self.session.openWithCallback(self.profiles_closed, ProfilesPage, self.lang, self.client,
                                          profiles.last_state())
        elif row == "lang":
            self.prefs.set_lang(prefs_mod.next_lang(self.prefs.lang()))
            self.changed = True
            self.draw()
        elif row == "livetv":          # takes effect the next time KanasaStream opens
            self.prefs.set_stop_live_tv(not self.prefs.stop_live_tv())
            self.draw()
        elif row == "mainmenu":        # owner 11-23: the image's main menu shows it from its next build
            self.prefs.set_main_menu(not self.prefs.main_menu())
            self.draw()
        elif row == "buffer":
            self.prefs.set_buffer_minutes(bufferrules.next_choice(self.prefs.buffer_minutes()))
            self.draw()
        elif row == "cache":
            self.cache_mb = None
            self.draw()
            async_call.run(self.posters.clear, on_done=lambda _: self.measure_cache(),
                           on_error=lambda error: self.measure_cache())
        elif row == "torrent" and self.engine_dir:
            from .choice import Choice
            rows = [(i18n.t("settings.engine_remove", self.lang), "remove"),
                    (i18n.t("update.later", self.lang), "keep")]
            self.session.openWithCallback(self.remove_answer, Choice, self.lang,
                                          i18n.t("settings.engine_remove_ask", self.lang), rows)
        elif row == "torrent":
            from .engineflow import EngineInstallFlow
            self.engine_flow = EngineInstallFlow(self, on_installed=lambda: self.measure_engine())
            self.engine_flow.offer(force=True)
        elif row == "downloads" and self.source is not None:
            from .downloads import Downloads
            self.session.openWithCallback(lambda *a: self.measure_downloads(), Downloads, self.lang, self.client,
                                          self.source, self.posters)
        elif row == "servers" and self.source is not None:
            from .serverpage import ServerSettings
            self.session.openWithCallback(lambda *a: self.draw(), ServerSettings, self.lang, self.source)
        elif row == "folder":
            self.choose_folder()
        elif row == "dlkey":             # red → green → yellow → blue → off; applies at once on every screen
            self.prefs.set_downloads_button(prefs_mod.next_downloads_key(self.prefs.downloads_button()))
            self.draw()
        elif row == "parallel":          # 1 → 2 → 3 → 4 → 1; more workers start at once
            self.prefs.set_simultaneous_downloads(self.prefs.simultaneous_downloads() % 4 + 1)
            downloadmanager.manager().start()
            self.draw()
        elif row == "share":             # 083: per box, default on; off clears the queue and sends nothing
            analytics.set_sharing(not analytics.sharing())
            self.draw()
        elif row == "logs" and not getattr(self, "logs_busy", False):
            self.logs_busy, self.logs_value = True, i18n.t("analytics.logs_sending", self.lang)
            self.draw()
            account = self.account_file.load()
            async_call.run(analytics.send_log, "user", self.lang, account and account["bearer"],
                           on_done=self.logs_sent, on_error=lambda error: self.logs_sent({"error": "network"}))
        elif row == "version":
            from .updateflow import UpdateFlow
            self.update_flow = UpdateFlow(self)
            self.update_flow.check(force=True)

    def profiles_closed(self, answer=None):
        if answer is not None:
            profiles.remember(profiles.parse(answer))
        self.rows = rows_for(profiles.last_state(), self.account_file.load() is not None)
        self.index = min(self.index, len(self.rows) - 1)
        self.draw()

    def logs_sent(self, answer):
        self.logs_busy = False
        ref = answer.get("ref") if isinstance(answer, dict) else None
        self.logs_value = ref or ""
        from Screens.MessageBox import MessageBox
        text = i18n.t("analytics.logs_sent", self.lang) % ref if ref else i18n.t("analytics.logs_later", self.lang)
        self.session.open(MessageBox, text, MessageBox.TYPE_INFO if ref else MessageBox.TYPE_ERROR, timeout=0)
        self.draw()

    def choose_folder(self):
        """Automatic (Enigma2's movie disk, /media/hdd, then the most free space) or one detected disk."""
        def show(disks):
            from .choice import Choice
            rows = [(i18n.t("settings.folder_auto", self.lang), "auto")]
            for path, free in downloadrules.folder_choices(disks):
                rows.append((i18n.t("settings.folder_row", self.lang) % (path, downloadrules.gb(free)), path))
            self.session.openWithCallback(self.folder_chosen, Choice, self.lang,
                                          i18n.t("settings.k.folder", self.lang), rows)
        async_call.run(torrengine.disks, on_done=show, on_error=lambda error: show([]))

    def folder_chosen(self, picked):
        if not picked:
            return
        manager = downloadmanager.manager()
        if manager.current is not None:          # a running download keeps its disk
            self["hint"].setText(i18n.t("settings.folder_busy", self.lang))
            return
        self.prefs.set_download_folder(picked[1])
        manager.change_folder()
        self.draw()
        self.measure_downloads()

    def remove_answer(self, picked):
        if not picked or picked[1] != "remove" or not self.engine_dir:
            return
        torrengine.shutdown()             # our own engine must not run from a deleted file
        directory, self.engine_mb = self.engine_dir, None
        self.draw()
        async_call.run(torrengine.remove, directory, on_done=lambda _: self.measure_engine(),
                       on_error=lambda error: self.measure_engine())

    def account(self):
        account = self.account_file.load()
        if account is None:
            from .signin import SignIn
            self.session.openWithCallback(self.signed_in, SignIn, self.lang, self.client, self.account_file)
            return
        # forget the token here first; the website is told in the background (no network = still signed out here)
        self.account_file.clear()
        self.changed = True
        profiles.remember(None)              # 084: no profile UI for a visitor
        profiles.forget_pick()
        self.rows = rows_for(None, False)
        self.index = 0
        dead = api.Client(lang=self.lang, bearer=account["bearer"])
        async_call.run(dead.logout, on_done=lambda _: None, on_error=lambda error: None)
        self.draw()

    def signed_in(self, name=None):
        if name is not None:
            self.changed = True
        self.draw()

    def leave(self):
        self.close("restart" if self.changed else None)
