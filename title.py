"""Exact TV-app title page: hero actions, cast and recommendations."""
import os
from enigma import ePoint, eSize
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Screens.Screen import Screen
from . import downloadskey
from . import async_call, downloadmanager, downloadrules, i18n, layout, mylist, playback, plans, playflow, skins, \
    theme, \
    titleinfo, titlelayout

LOGO = os.path.join(os.path.dirname(__file__), "logo.png")


class Title(Screen):
    def __init__(self, session, lang, client, source, posters, item):
        self.skin = skins.title(lang)
        Screen.__init__(self, session)
        self.lang, self.rtl, self.client, self.source = lang, lang == "ar", client, source
        self.posters, self.item = posters, item
        self.detail, self.failed = None, False
        self.button_icon_state = {}
        # the player round trip (resume question, saved server, Up next) is shared with Home's Continue row
        self.flow = playflow.PlayFlow(session, lang, client, source, posters, item, None, self.flow_closed)
        self.cast_offset, self.rec_offset = 0, 0
        self.nav = titlelayout.Nav(item.get("kind") != "movie", 0, 0,
                                   downloads=item.get("kind") == "movie" and plans.downloads_allowed())
        self["backdrop"], self["logo"] = Pixmap(), Pixmap()
        self["backicon"] = Pixmap()
        for name in ("shade", "header", "backpill", "back", "backframe", "title", "meta", "overview", "chipframe",
                     "btnframe", "casttitle", "castframe", "recbar", "rectitle", "recframe"):
            self[name] = Label("")
        self["back"].setText("Back" if lang != "ar" else "رجوع")
        self["title"].setText(layout.card_title(item, skins.TITLE_CHARS))
        for i in range(3):
            self["chip%d" % i], self["btnbase%d" % i], self["btn%d" % i] = Label(""), Label(""), Label("")
            self["btnicon%d" % i] = Pixmap()
        for i in range(11):
            self["cphoto%d" % i] = Pixmap()
            self["cname%d" % i], self["cchar%d" % i] = Label(""), Label("")
        for i in range(titlelayout.RECOMMENDATION_SLOTS):
            self["rposter%d" % i] = Pixmap()
            for kind in ("rtitle", "rrate", "rkind", "ryear"): self["%s%d" % (kind, i)] = Label("")
        self["casttitle"].setText("Top cast" if lang != "ar" else "طاقم التمثيل")
        self["rectitle"].setText("More like this" if lang != "ar" else "المزيد مثل هذا")
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
            "cancel": self.close, "yellow": self.toggle_list, "ok": self.ok,
            "left": lambda: self.horizontal(-1), "right": lambda: self.horizontal(1),
            "up": lambda: self.vertical(-1), "down": lambda: self.vertical(1)}, -1)
        downloadskey.bind(self, lang, client, source, posters, used=("yellow",))
        self.onLayoutFinish.append(self.finish_layout)

    def finish_layout(self):
        if self["logo"].instance: self["logo"].instance.setPixmapFromFile(LOGO)
        if self["backicon"].instance:
            asset = "pill_back_r_surface.png" if self.rtl else "pill_back_surface.png"
            self["backicon"].instance.setPixmapFromFile(os.path.join(os.path.dirname(LOGO), "icons", asset))
        from . import localhosts
        localhosts.warm()                     # 085 T104: the host worker's setup is done before Play
        self.load()

    def load(self):
        self.failed = False; self["meta"].setText(i18n.t("home.loading", self.lang))
        async_call.run(self.client.title, self.item.get("kind"), int(self.item.get("id")),
                       on_done=self.loaded, on_error=self.load_failed)

    def load_failed(self, error):
        self.failed = True; self["meta"].setText(i18n.t("home.error", self.lang))

    def loaded(self, detail):
        self.detail = detail
        self.item.update({k: v for k, v in detail["item"].items() if v is not None})
        self["title"].setText(layout.card_title(self.item, skins.TITLE_CHARS))
        self["meta"].setText(titleinfo.app_meta(detail, self.lang))
        self["overview"].setText(layout.card_title({"title": (detail.get("overview") or "").replace("\n", " ")}, 112))
        refs = [ref for ref in detail.get("genre_refs") or [] if isinstance(ref.get("id"), int)
                and ref.get("kind") in ("movie", "tv")][:titlelayout.GENRE_SLOTS]
        detail["genre_refs"] = refs      # the chips ARE the refs: OK on chip i opens refs[i]
        genres = [ref.get("name") or "" for ref in refs] or list(detail.get("genres") or [])[:3]
        chip_left = skins.CHIP_X
        for i in range(3):
            self["chip%d" % i].setText(genres[i] if i < len(genres) else "")
            if i < len(genres) and self["chip%d" % i].instance:
                width = max(72, min(230, 28 + len(genres[i]) * 10))
                self["chip%d" % i].instance.move(ePoint(layout.x(chip_left, width, self.rtl), skins.CHIP_Y))
                self["chip%d" % i].instance.resize(eSize(width, skins.CHIP_H))
                chip_left += width + skins.CHIP_GAP
            self["chip%d" % i].show() if i < len(genres) else self["chip%d" % i].hide()
        cast, recs = detail.get("cast") or [], detail.get("recommendations") or []
        focusable = [index for index, member in enumerate(cast) if isinstance(member.get("id"), int)]
        self.nav = titlelayout.Nav(bool(detail.get("seasons")), len(cast), len(recs), len(refs), focusable,
                                   downloads=not detail.get("seasons") and plans.downloads_allowed())
        self.draw_cast(cast); self.draw_recommendations(recs); self.draw_buttons(); self.draw_focus()
        if detail.get("backdrop"):
            async_call.run(self.posters.get, detail["backdrop"], (1920, 1080), 0,
                           "right" if self.rtl else "left", on_done=self.backdrop_ready, on_error=lambda e: None)

    def backdrop_ready(self, path):
        if path and self["backdrop"].instance: self["backdrop"].instance.setPixmapFromFile(path)

    def draw_cast(self, cast):
        for i in range(11):
            index = self.cast_offset + i
            member = cast[index] if index < len(cast) else None
            self["cname%d" % i].setText((member.get("name") or "") if member else "")
            self["cchar%d" % i].setText(layout.card_title({"title": member.get("character") or ""}, 20) if member else "")
            photo = self["cphoto%d" % i]
            if not member: photo.hide(); continue
            self.load_image(photo, member.get("photo"), (112, 112), 56)

    def draw_recommendations(self, recs):
        for i in range(titlelayout.RECOMMENDATION_SLOTS):
            index = self.rec_offset + i
            rec = recs[index] if index < len(recs) else None
            if not rec:
                self["rposter%d" % i].hide()
                for kind in ("rtitle", "rrate", "rkind", "ryear"): self["%s%d" % (kind, i)].setText("")
                self["rrate%d" % i].hide(); self["rkind%d" % i].hide(); self["ryear%d" % i].hide()
                continue
            self["rtitle%d" % i].setText(layout.card_title(rec))
            # a badge Label with a background shows an empty box when it has no text: hide it instead
            for kind, text in (("rrate", "★ %s" % rec["rating"] if rec.get("rating") else ""),
                               ("rkind", i18n.t("card.series", self.lang) if rec.get("kind") != "movie" else ""),
                               ("ryear", str(rec["year"]) if rec.get("year") else "")):
                self["%s%d" % (kind, i)].setText(text)
                self["%s%d" % (kind, i)].show() if text else self["%s%d" % (kind, i)].hide()
            self.load_image(self["rposter%d" % i], rec.get("poster"), (235, 300), theme.RADIUS_CARD)

    def load_image(self, widget, url, size, rounded):
        # slots are reused while a row scrolls: like Home, never refetch or flash the image already shown
        if getattr(widget, "kanasa_url", None) == url and getattr(widget, "kanasa_shown", False):
            widget.show(); return
        widget.hide(); widget.kanasa_url, widget.kanasa_shown = url, False
        if not url: return
        def done(path):
            if path and widget.instance and getattr(widget, "kanasa_url", None) == url:
                widget.instance.setPixmapFromFile(path); widget.kanasa_shown = True; widget.show()
        async_call.run(self.posters.get, url or "", size, rounded, on_done=done, on_error=lambda e: None)

    def horizontal(self, direction):
        self.nav.move_horizontal(direction, self.rtl)
        self.scroll_rows()
        self.draw_buttons(); self.draw_focus()

    def vertical(self, direction):
        self.nav.move_vertical(direction); self.scroll_rows(); self.draw_buttons(); self.draw_focus()

    def scroll_rows(self):
        """The focus moves card by card; a row scrolls only when the focus reaches its visible edge."""
        if self.nav.area == "cast":
            cast = (self.detail or {}).get("cast") or []
            offset = titlelayout.keep_visible(self.cast_offset, self.nav.index, len(cast), titlelayout.CAST_SLOTS)
            if offset != self.cast_offset:
                self.cast_offset = offset; self.draw_cast(cast)
        elif self.nav.area == "recommendations":
            recs = (self.detail or {}).get("recommendations") or []
            offset = titlelayout.keep_visible(self.rec_offset, self.nav.index, len(recs),
                                              titlelayout.RECOMMENDATION_SLOTS)
            if offset != self.rec_offset:
                self.rec_offset = offset; self.draw_recommendations(recs)

    def ok(self):
        if self.failed: self.load(); return
        action = self.nav.ok()
        if action == "play": self.open_default()
        elif action == "back": self.close()
        elif action == "episodes": self.open_episodes()
        elif action == "mylist": self.toggle_list()
        elif action == "download": self.open_download()
        elif isinstance(action, tuple) and action[0] == "genre": self.open_genre(action[1])
        elif isinstance(action, tuple) and action[0] == "person": self.open_person(action[1])
        elif isinstance(action, tuple): self.open_recommendation(action[1])

    def draw_focus(self):
        for name in ("backframe", "chipframe", "btnframe", "castframe", "recframe"): self[name].hide()
        if self.nav.area == "back": self["backframe"].show()
        elif self.nav.area == "genres" and self["chipframe"].instance:
            chip = self["chip%d" % self.nav.index]
            if chip.instance:
                pos, size = chip.instance.position(), chip.instance.size()
                self["chipframe"].instance.move(ePoint(pos.x() - 3, pos.y() - 3))
                self["chipframe"].instance.resize(eSize(size.width() + 6, size.height() + 6)); self["chipframe"].show()
        elif self.nav.area == "cast" and self["castframe"].instance:
            x, y, _, _ = titlelayout.cast_box(self.nav.index - self.cast_offset, self.rtl)
            self["castframe"].instance.move(ePoint(x - 4, y - 4)); self["castframe"].show()
        elif self.nav.area == "recommendations" and self["recframe"].instance:
            x, y, _, _ = titlelayout.recommendation_box(self.nav.index - self.rec_offset, self.rtl)
            self["recframe"].instance.move(ePoint(x - 4, y - 4)); self["recframe"].show()

    def resume_label(self):
        if not self.detail: return None
        if self.detail.get("seasons"):
            _, saved = self.source.title_resume(self.item, self.detail)
        else:
            saved = self.source.resume(self.item, self.detail, None, None, [])
        offer = playback.resume_offer(saved)
        return i18n.t("title.resume", self.lang) % playback.clock(offer) if offer else None

    def series_primary(self):
        """The account's watched bit wins over a stale position (website rule)."""
        if not self.detail or not self.detail.get("seasons"):
            return None, None
        return playback.series_primary(self.detail.get("seasons") or [], self.item.get("progress") or {})

    def play_label(self):
        resume = self.resume_label()
        if resume:
            return resume
        mode, coords = self.series_primary()
        if mode == "next" and coords:
            return i18n.t("title.play_episode", self.lang) % coords
        if mode == "rewatch":
            return i18n.t("title.rewatch", self.lang)
        return i18n.t("title.play", self.lang)

    def draw_buttons(self):
        resume = self.resume_label()
        labels = {"play": resume or self.play_label(),
                  "episodes": i18n.t("title.episodes", self.lang),
                  "download": downloadrules.state_label(downloadmanager.manager().find(
                      self.item.get("kind"), self.item.get("id")), i18n.t, self.lang),
                  "mylist": ""}
        boxes = titlelayout.button_boxes(self.nav.buttons, bool(resume), self.rtl)
        active_list = self.source.in_list(self.item, self.detail)
        labels["mylist"] = i18n.t("title.remove" if active_list else "title.add", self.lang)
        for i in range(3):
            name = self.nav.buttons[i] if i < len(self.nav.buttons) else None
            label = labels[name] if name else ""
            self["btn%d" % i].setText(((label + "     ") if self.rtl else ("     " + label)) if name else "")
            for kind in ("btnbase", "btn", "btnicon"):
                self["%s%d" % (kind, i)].show() if name else self["%s%d" % (kind, i)].hide()
            if not name: continue
            x, y, width, height = boxes[i]
            for kind in ("btnbase", "btn"):
                widget = self["%s%d" % (kind, i)]
                widget.instance.move(ePoint(x - 2 if kind == "btnbase" else x, y - 2 if kind == "btnbase" else y))
                widget.instance.resize(eSize(width + 4 if kind == "btnbase" else width,
                                             height + 4 if kind == "btnbase" else height))
            focused = self.nav.area == "buttons" and self.nav.index == i
            # focus = the app's accent ring (tvAccentFocus); Play fills with the accent, the others light up
            fill = theme.ACCENT if name == "play" and focused else ("#2c2e3d" if focused else theme.SURFACE)
            outline = theme.ACCENT if focused or (name == "mylist" and active_list) else "#c8ccd6"
            self["btn%d" % i].instance.setBackgroundColor(theme.rgb(fill))
            self["btn%d" % i].instance.setForegroundColor(
                theme.rgb(theme.ACCENT if name == "mylist" and active_list else theme.TEXT))
            self["btn%d" % i].instance.invalidate()
            self["btnbase%d" % i].instance.setBackgroundColor(theme.rgb(outline))
            self["btnbase%d" % i].instance.invalidate()   # the box repaints a colour only on invalidate
            downloaded = name == "download" and labels["download"] == i18n.t("download.state.downloaded", self.lang)
            icon_name = {"play": "triangle", "episodes": "list", "mylist": "check" if active_list else "plus",
                         "download": "check" if downloaded else "next"}[name]
            icon = self["btnicon%d" % i]
            icon.instance.move(ePoint(x + (width - 52 if self.rtl else 16), y + (height - 36) // 2))
            asset = "pill_%s_%s.png" % (
                icon_name, "accent" if fill == theme.ACCENT else ("focus" if focused else "surface"))
            if self.button_icon_state.get(i) != asset:
                icon.instance.setPixmapFromFile(os.path.join(os.path.dirname(LOGO), "icons", asset))
                self.button_icon_state[i] = asset

    def toggle_list(self):
        """Visitor (or no named lists): My List on/off. Signed in with named lists: a popup like the app's,
        the built-in My List first, then each list with a check; OK toggles one and the popup stays."""
        if self.source.signed_in() and self.detail is None: return
        if not self.source.signed_in():
            async_call.run(self.source.toggle, self.item, self.detail, on_done=lambda active: self.draw_buttons(),
                           on_error=lambda e: None)
            return
        async_call.run(self._named_list_data, on_done=self.show_named_lists,
                       on_error=lambda e: self.show_named_lists(([], {})))   # lists unreachable: the plain toggle

    def _named_list_data(self):
        return self.client.library_lists(), self.client.library_title_state(
            self.item.get("kind"), int(self.item.get("id")))

    def show_named_lists(self, payload, reopen=False):
        lists, state = payload
        later = ((state or {}).get("lists") or {}).get("later")
        if isinstance(later, bool) and self.detail is not None:
            self.detail["in_list"] = later
        self.draw_buttons()
        if not lists and not reopen:          # no named lists: the one-press toggle, as before
            async_call.run(self.source.toggle, self.item, self.detail, on_done=lambda active: self.draw_buttons(),
                           on_error=lambda e: None)
            return
        if not lists:
            return
        from .choice import Choice
        self.session.openWithCallback(self.named_list_picked, Choice, self.lang, i18n.t("mylist.choose", self.lang),
                                      mylist.list_choices(lists, state, self.lang))

    def named_list_picked(self, picked):
        if not picked: self.draw_buttons(); return
        list_id, active = picked[1]
        if list_id == "included":             # My List follows the named lists (server rule): nothing to do
            async_call.run(self._named_list_data, on_done=lambda payload: self.show_named_lists(payload, True),
                           on_error=lambda e: None)
            return
        if list_id == "new":
            from Screens.VirtualKeyBoard import VirtualKeyBoard
            self.session.openWithCallback(self.new_list_named, VirtualKeyBoard,
                                          title=i18n.t("mylist.new_prompt", self.lang))
            return
        async_call.run(self._toggle_named, list_id, not active,
                       on_done=lambda payload: self.show_named_lists(payload, True), on_error=lambda e: None)

    def new_list_named(self, name=None):
        name = " ".join(str(name or "").split())
        if not name:
            return
        async_call.run(self._create_and_add_list, name,
                       on_done=lambda payload: self.show_named_lists(payload, True), on_error=lambda e: None)

    def _create_and_add_list(self, name):
        row = self.client.library_list_create(name)
        if isinstance(row.get("id"), int):
            self.client.library_list_set(row["id"], self.item.get("kind"), int(self.item.get("id")), True)
        return self._named_list_data()

    def _toggle_named(self, list_id, on):
        if list_id == "later":
            self.source.toggle(self.item, self.detail)
        else:
            self.client.library_list_set(list_id, self.item.get("kind"), int(self.item.get("id")), on)
        return self._named_list_data()

    def open_download(self):
        """A film's Download: the download page (pick a server or Auto); a running or finished download
        opens the Downloads page on it, a failed one is tried again."""
        if not plans.downloads_allowed():           # 084: never reached (no button), kept as the guard
            from .episodes import plans_hint
            plans_hint(self.session, self.lang)
            return
        row = downloadmanager.manager().find(self.item.get("kind"), self.item.get("id"))
        state = downloadrules.row_state(row)
        if state == "failed-retry":
            downloadmanager.manager().retry(row["id"]); self.draw_buttons(); return
        if state == "download":
            from .downloadpage import DownloadPage
            self.session.openWithCallback(lambda *a: self.draw_buttons(), DownloadPage, self.lang, self.client,
                                          self.source, self.posters, self.item)
            return
        from .downloads import Downloads
        self.session.openWithCallback(lambda *a: self.draw_buttons(), Downloads, self.lang, self.client, self.source,
                                      self.posters, row["id"])

    def open_recommendation(self, index):
        recs = (self.detail or {}).get("recommendations") or []
        if index < len(recs): self.session.open(Title, self.lang, self.client, self.source, self.posters, recs[index])

    def open_person(self, index):
        cast = (self.detail or {}).get("cast") or []
        if index >= len(cast) or not isinstance(cast[index].get("id"), int): return
        from .browse import Browse
        person = cast[index]
        self.session.open(Browse, self.lang, self.client, self.source, self.posters,
                          lambda page: self.client.person(person["id"], page), person.get("name") or "",
                          header=True)

    def open_genre(self, index):
        refs = (self.detail or {}).get("genre_refs") or []
        if index >= len(refs): return
        ref = refs[index]
        from .browse import Browse
        self.session.open(Browse, self.lang, self.client, self.source, self.posters,
                          lambda page: self.client.genre(ref["kind"], ref["id"], page), ref.get("name") or "")

    def open_episodes(self):
        from .episodes import Episodes
        self.session.openWithCallback(self.episodes_closed, Episodes, self.lang, self.client, self.posters,
                                      self.item, self.detail, self.source)

    def episodes_closed(self, answer=None):
        if isinstance(answer, tuple) and answer[0] == "play": self.open_player(answer[1], answer[2], answer[3])

    def open_default(self):
        if not (self.detail or {}).get("seasons"):
            self.open_player(None, None, [])
            return
        mode, primary_coords = self.series_primary()
        if mode in ("next", "rewatch") and primary_coords:
            async_call.run(self.client.season, int(self.item.get("id")), primary_coords[0],
                           on_done=lambda rows: self.open_player(primary_coords[0], primary_coords[1], list(rows),
                                                                 False),
                           on_error=lambda error: self.open_episodes())
            return
        coords, _ = self.source.title_resume(self.item, self.detail)
        if not coords or not self.resume_label():
            self.open_episodes()
            return
        async_call.run(self.client.season, int(self.item.get("id")), coords[0],
                       on_done=lambda rows: self.open_player(coords[0], coords[1], list(rows)),
                       on_error=lambda error: self.open_episodes())

    def open_player(self, season, episode, episodes, ask_resume=True, fetched=False):
        self.flow.detail = self.detail
        self.flow.open_player(season, episode, episodes, ask_resume, fetched)

    def flow_closed(self, answer=None):
        self.draw_buttons()
