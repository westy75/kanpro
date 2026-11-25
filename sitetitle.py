"""A Turkish-site title from search (018 §5): GET /api/catalog/site/{id}; a movie plays its own handle, a
series lists its seasons' episodes; each plays via /api/catalog/site/{id}/sources. FIX28: a signed-in viewer
resumes the account's point (018 6.2) and the player saves progress by handle; no My List toggle here."""
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Screens.Screen import Screen

from . import async_call, i18n, searchtext, skins, theme
from .rowlist import RowList


class SiteTitle(Screen):
    def __init__(self, session, lang, client, source, posters, item):
        self.skin = skins.site_title(lang)
        Screen.__init__(self, session)
        self.lang, self.client, self.source, self.posters, self.item = lang, client, source, posters, item
        self.data, self.rows = None, []
        self["title"] = Label(item.get("title") or "")
        self["meta"], self["overview"] = Label(""), Label("")
        self["status"] = Label(i18n.t("home.loading", lang))
        self["poster"] = Pixmap()
        self["list"] = RowList([], 1240 - 24, 64, theme.font("bold", 28, lang), lang == "ar")
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions"], {
            "cancel": self.close, "ok": self.play, "up": lambda: self.move(-1), "down": lambda: self.move(1)}, -1)
        self.onLayoutFinish.append(self.start)

    def start(self):
        from . import localhosts
        localhosts.warm()                     # 085 T104: the host worker's setup is done before Play
        self.load_poster(self.item.get("poster"))
        async_call.run(self.client.site_title, self.item.get("id"), on_done=self.loaded,
                       on_error=lambda error: self["status"].setText(i18n.t("site.loading_failed", self.lang)))

    def load_poster(self, url):
        def done(path):
            if path and self["poster"].instance:
                self["poster"].instance.setPixmapFromFile(path)
                self["poster"].show()
        if url and self.posters is not None:
            async_call.run(self.posters.get, url, (404, 606), theme.RADIUS_CARD, on_done=done,
                           on_error=lambda error: None)

    def loaded(self, data):
        self.data = data if isinstance(data, dict) else {}
        self["status"].setText("")
        self["title"].setText(self.data.get("title") or self.item.get("title") or "")
        kind = i18n.t("site.movie" if self.data.get("kind") == "movie" else "site.series", self.lang)
        self["meta"].setText("  ·  ".join(str(p) for p in (self.data.get("year"), kind) if p))
        self["overview"].setText(self.data.get("overview") or "")
        if self.data.get("poster") and self.data.get("poster") != self.item.get("poster"):
            self.load_poster(self.data.get("poster"))
        self.rows, focus = searchtext.site_rows(self.data, self.lang, self.item.get("id"))
        self["list"].setList([label for label, _ in self.rows])
        if self["list"].instance:
            self["list"].moveToIndex(focus)

    def move(self, step):
        """Season titles are not focus targets: skip them."""
        for _ in range(len(self.rows)):
            self["list"].up() if step < 0 else self["list"].down()
            index = self["list"].getSelectedIndex()
            if index is not None and index < len(self.rows) and self.rows[index][1]:
                break

    def play(self):
        index = self["list"].getSelectedIndex()
        if self.data is None or index is None or index >= len(self.rows) or not self.rows[index][1]:
            return
        from . import playerguard
        target = searchtext.site_target(self.data, self.rows[index][1], self.data.get("title"))
        playerguard.open_player(self.session, self.back_from_player, self.lang, self.client, self.source, target)

    def back_from_player(self, *args):
        """The saved point changed: reload so the next OK resumes it (the server keeps the title cached)."""
        async_call.run(self.client.site_title, self.item.get("id"), on_done=self.loaded, on_error=lambda e: None)
