"""The Downloads page: what is on the box's disk or on its way; OK plays from the disk, red deletes, green retries."""
import os
from enigma import eTimer
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Screens.Screen import Screen

from . import async_call, downloadmanager, downloadpick, downloadrules as rules, i18n, playerguard, skins, theme
from .rowlist import RowList

REFRESH_MS = 2000
ACCOUNT_WAIT_MS = 4000          # FIX33: a downloaded copy never waits longer for the account's resume point
POSTER = (404, 520)


def row_label(row, lang) -> str:
    """Title  S01·E04  ·  state  ·  size (numbers stay left-to-right inside Arabic)."""
    se = "  S%02d·E%02d" % (row["season"], row["episode"]) if row.get("season") is not None else ""
    parts = ["%s%s" % (row.get("title") or "", se), rules.state_label(row, i18n.t, lang)]
    if row.get("size"):
        parts.append(rules.gb(row["size"]))
    return "   ·   ".join(parts)


def detail_text(row, lang) -> str:
    lines = [row.get("label") or row.get("source") or ""]
    if row.get("quality") or row.get("quality_text"):
        lines.append(row.get("quality") or row.get("quality_text"))
    if row.get("state") in ("failed", "waiting") and row.get("error"):
        lines.append(downloadpick.error_text(row["error"], lang))
    return "\n".join(line for line in lines if line)


class Downloads(Screen):
    def __init__(self, session, lang, client, source, posters=None, focus_key=None):
        self.skin = skins.downloads(lang)
        Screen.__init__(self, session)
        self.lang, self.client, self.source, self.posters = lang, client, source, posters
        self.manager = downloadmanager.manager()
        self.rows, self.focus_key, self.poster_url = [], focus_key, None
        self["title"] = Label(i18n.t("downloads.title", lang))
        self["space"], self["status"], self["detail"] = Label(""), Label(""), Label("")
        self["how"] = Label(i18n.t("downloads.how", lang))
        self.empty_shown = False
        self["hint"] = Label(i18n.t("downloads.hint", lang))
        self["poster"] = Pixmap()
        self["list"] = RowList([], 1240 - 24, 64, theme.font("bold", 26, lang), lang == "ar")
        self.timer = eTimer()
        self.timer.callback.append(self.draw)
        self.pending_play = None
        self.wait_timer = eTimer()
        self.wait_timer.callback.append(self.account_waited)
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
            "cancel": self.close, "ok": self.play, "red": self.delete, "green": self.retry,
            "up": lambda: self.move(-1), "down": lambda: self.move(1)}, -1)
        self.onLayoutFinish.append(self.start)
        self.onClose.append(self.timer.stop)
        self.onClose.append(self.wait_timer.stop)

    def start(self):
        self.draw()
        self.timer.start(REFRESH_MS, False)

    def move(self, step):
        self["list"].up() if step < 0 else self["list"].down()
        self.draw_selected()

    def draw(self):
        selected = self.selected()
        key = self.focus_key or (selected or {}).get("id")
        self.rows = list(reversed(self.manager.items()))          # newest first
        self["list"].setList([row_label(row, self.lang) for row in self.rows])
        index = next((i for i, row in enumerate(self.rows) if key in (row.get("id"), row.get("key"))), 0)
        self.focus_key = None
        if self.rows and self["list"].instance:
            self["list"].moveToIndex(index)
        space = self.manager.space()
        if space is None:
            self["space"].setText(i18n.t("download.err.no_disk", self.lang))
        else:
            self["space"].setText(i18n.t("downloads.space", self.lang) % (rules.gb(space[0]), rules.gb(space[1])))
        if not self.rows:
            self["status"].setText(i18n.t("downloads.empty", self.lang))
            self["how"].show()
            self.empty_shown = True
        else:
            self["how"].hide()
            if self.empty_shown:                 # FIX30: a download appeared: the empty line goes
                self["status"].setText("")
                self.empty_shown = False
        self.draw_selected()

    def draw_selected(self):
        row = self.selected()
        self["detail"].setText(detail_text(row, self.lang) if row else "")
        url = (row or {}).get("poster")
        if url == self.poster_url:
            return
        self.poster_url = url
        self["poster"].hide()
        if not url or self.posters is None:
            return

        def done(path):
            if path and self.poster_url == url and self["poster"].instance:
                self["poster"].instance.setPixmapFromFile(path)
                self["poster"].show()
        async_call.run(self.posters.get, url, POSTER, theme.RADIUS_CARD, on_done=done, on_error=lambda e: None)

    def selected(self):
        index = self["list"].getSelectedIndex()
        return self.rows[index] if self.rows and index is not None and index < len(self.rows) else None

    # --- actions -------------------------------------------------------------------------------
    def play(self):
        row = self.selected()
        if not row or playerguard.busy():
            return                               # FIX33: a second OK while a Player is open opens nothing
        if row.get("state") == "failed":
            self.retry()
            return
        checked = row
        part = row.get("part")
        if row.get("state") in ("downloading", "waiting") and part and part.lower().endswith(".mp4.part"):
            try:
                with open(part, "rb") as handle:
                    prefix = handle.read(min(os.path.getsize(part), 32 * 1024 ** 2))
                checked = dict(row, contiguous=os.path.getsize(part),
                               moov_at_start=rules.mp4_prefix_playable(prefix, os.path.getsize(part)))
            except OSError:
                checked = row
        path, reason = rules.local_play(checked)
        if not path:
            if reason == "wait":
                from .choice import Choice
                rows = [(i18n.t("download.play_online", self.lang), "online"),
                        (i18n.t("download.cancel", self.lang), "cancel")]
                self.session.openWithCallback(lambda answer: self.play_online(row, answer), Choice, self.lang,
                                              i18n.t("download.wait_online", self.lang), rows)
            else:
                self["status"].setText(i18n.t("download.wait_done", self.lang))
            return
        item = {"kind": row.get("kind"), "id": row.get("tmdb_id")}
        saved = self.source.resume(item, None, row.get("season"), row.get("episode"), [])
        from . import playback
        start_at = playback.resume_offer(saved) or 0
        if self.source.signed_in():
            # the account's place for this film/episode (the website and the app share it); offline: the box's.
            # FIX33: one fetch per OK (a second OK meanwhile does nothing), and with no network the play never
            # waits for the request's timeout: after ACCOUNT_WAIT_MS the box's own point plays.
            if self.pending_play is not None:
                return
            token = self.pending_play = (row, start_at)
            async_call.run(self._account_resume, row,
                           on_done=lambda at: self.play_pending(token, at or start_at),
                           on_error=lambda error: self.play_pending(token, start_at))
            self.wait_timer.start(ACCOUNT_WAIT_MS, True)
        else:
            self.start_player(row, start_at)

    def play_pending(self, token, start_at):
        """The account's answer (or its failure) for the OK that is still waiting; a late one does nothing."""
        if self.pending_play is not token:
            return
        self.pending_play = None
        self.wait_timer.stop()
        self.start_player(token[0], start_at)

    def account_waited(self):
        """ACCOUNT_WAIT_MS without the account's answer (no network): the box's own point plays now."""
        token = self.pending_play
        if token is not None:
            self.play_pending(token, token[1])

    def play_online(self, row, answer):
        if not answer or answer[1] != "online":
            return
        target = downloadpick.play_target(dict(row, state="downloaded"), 0)
        target.pop("local_path", None)
        playerguard.open_player(self.session, None, self.lang, self.client, self.source, target)

    def _account_resume(self, row):
        from . import playback
        item = {"kind": row.get("kind"), "id": row.get("tmdb_id")}
        detail = self.client.title(row.get("kind"), int(row.get("tmdb_id")))
        episodes = self.client.season(int(row["tmdb_id"]), int(row["season"])) if row.get("season") is not None else []
        return playback.resume_offer(self.source.resume(item, detail, row.get("season"), row.get("episode"), episodes))

    def start_player(self, row, start_at):
        playerguard.open_player(self.session, None, self.lang, self.client, self.source,
                                downloadpick.play_target(row, start_at))

    def delete(self):
        row = self.selected()
        if not row:
            return
        from .choice import Choice
        rows = [(i18n.t("downloads.delete", self.lang), "yes"), (i18n.t("download.cancel", self.lang), "no")]
        self.session.openWithCallback(lambda answer: self.deleted(answer, row["id"]), Choice, self.lang,
                                      i18n.t("downloads.delete_ask", self.lang), rows)

    def deleted(self, answer, item_id):
        if answer and answer[1] == "yes":
            self.manager.delete(item_id)
            self.draw()

    def retry(self):
        row = self.selected()
        if row and row.get("state") == "failed":
            self.manager.retry(row["id"])
            self.draw()
