"""Person and genre results in the approved Home poster-card look."""
from enigma import ePoint, eSize
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Screens.Screen import Screen
from . import downloadskey
from . import async_call, browselayout, i18n, layout, skins, theme


class Browse(Screen):
    def __init__(self, session, lang, client, source, posters, loader, title, header=False):
        self.skin = skins.browse(lang); Screen.__init__(self, session)
        self.lang, self.rtl, self.client, self.source, self.posters = lang, lang == "ar", client, source, posters
        self.loader, self.page, self.loading, self.next_page = loader, 1, False, None
        self.grid, self.in_back, self.name = browselayout.Grid(header=header), True, title or ""
        self.photo_ok = False
        self["back"], self["title"], self["status"], self["focus"] = Label(""), Label(title or ""), Label(""), Label("")
        self["backframe"] = Label("")
        self["pphoto"] = Pixmap()
        for key in ("pname", "pborn", "pbio", "pfilm"): self[key] = Label("")
        self["back"].setText("← Back" if not self.rtl else "رجوع →")
        for row in range(layout.ROWS):
            for col in range(layout.COLS):
                slot = "%dc%d" % (row, col); self["r" + slot] = Pixmap()
                for kind in "tsbwp": self[kind + slot] = Label("")
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions"], {
            "cancel": self.close, "ok": self.ok, "up": lambda: self.move(-1, 0),
            "down": lambda: self.move(1, 0), "left": lambda: self.move(0, layout.step(-1, self.rtl)),
            "right": lambda: self.move(0, layout.step(1, self.rtl))}, -1)
        downloadskey.bind(self, lang, client, source, posters)
        self.onLayoutFinish.append(self.load)

    def load(self):
        if self.loading or self.page is None: return
        self.loading = True; page = self.page; self["status"].setText(i18n.t("home.loading", self.lang))
        async_call.run(self.loader, page, on_done=self.loaded, on_error=lambda error: self.failed())

    def loaded(self, result):
        self.loading = False; self.name = result.get("title") or self.name
        if result.get("person"): self.show_person(result["person"])
        first = not self.grid.items
        self.grid.append(result.get("items") or []); self.next_page = result.get("next_page")
        if first and self.grid.items: self.in_back = False      # the focus starts on the first poster
        self.page = self.next_page; self["status"].setText("" if self.grid.items else i18n.t("home.empty", self.lang)); self.draw()

    def show_person(self, person):
        self["pname"].setText(person.get("name") or self.name)
        born = browselayout.born(person)
        self["pborn"].setText(i18n.t("browse.born", self.lang) % born if born else "")
        self["pbio"].setText(browselayout.clip(person.get("biography"), 560))
        self["pfilm"].setText(i18n.t("browse.filmography", self.lang))
        url = person.get("photo")
        def done(path):
            if path and self["pphoto"].instance:
                self["pphoto"].instance.setPixmapFromFile(path); self.photo_ok = True; self.draw()
        if url: async_call.run(self.posters.get, url, (240, 360), theme.RADIUS_CARD, on_done=done, on_error=lambda e: None)

    def failed(self): self.loading = False; self["status"].setText(i18n.t("home.error", self.lang))

    def move(self, drow, dcol):
        if self.in_back:
            if drow > 0 and self.grid.items: self.in_back = False
        elif not self.grid.move(drow, dcol): self.in_back = True
        if self.grid.items and self.grid.index >= len(self.grid.items) - 3 and self.page: self.load()
        self.draw()

    def ok(self):
        if self.in_back: self.close(); return
        item = self.grid.focused()
        if item:
            from .title import Title
            self.session.open(Title, self.lang, self.client, self.source, self.posters, item)

    def draw(self):
        self["backframe"].show() if self.in_back else self["backframe"].hide()
        header = self.grid.header_visible()
        for key in ("pname", "pborn", "pbio", "pfilm"):
            self[key].show() if header and self[key].getText() else self[key].hide()
        self["pphoto"].show() if header and self.photo_ok else self["pphoto"].hide()
        self["title"].setText("" if header else self.name)
        for row, items in enumerate(self.grid.window()):
            for col, item in enumerate(items): self.draw_card("%dc%d" % (row, col), item)
        if self.in_back or not self.grid.focused() or not self["focus"].instance: self["focus"].hide()
        else:
            row, col = self.grid.slot(); x = layout.x(layout.LEFT + col * layout.PITCH, layout.POSTER[0], self.rtl)
            self["focus"].instance.move(ePoint(x - theme.FRAME, (152, 614)[row] - theme.FRAME)); self["focus"].show()

    def draw_card(self, slot, item):
        poster = self["r" + slot]
        if not item:
            poster.hide()
            for kind in "tsbwp": self[kind + slot].hide()
            return
        for kind in "ts": self[kind + slot].show()
        self["t" + slot].setText(layout.card_title(item)); self["s" + slot].setText(layout.card_sub(item, self.lang))
        badges = layout.card_badges(item)
        self["b" + slot].setText(i18n.t("card.series", self.lang)); self["b" + slot].show() if badges["series"] else self["b" + slot].hide()
        self["w" + slot].setText(i18n.t("card.watching", self.lang)); self["w" + slot].show() if badges["watching"] else self["w" + slot].hide()
        width = layout.bar_fill(badges["percent"], 100, layout.POSTER[0])
        if width and self["p" + slot].instance: self["p" + slot].instance.resize(eSize(width, 6)); self["p" + slot].show()
        else: self["p" + slot].hide()
        url = item.get("poster"); poster.hide(); poster.kanasa_url = url
        def done(path):
            if path and poster.instance and poster.kanasa_url == url: poster.instance.setPixmapFromFile(path); poster.show()
        async_call.run(self.posters.get, url or "", layout.POSTER, theme.RADIUS_CARD, on_done=done, on_error=lambda e: None)
