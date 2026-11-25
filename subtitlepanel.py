"""Subtitles side panel, the website's (FIX20); rendering and network ownership stay in Player.

subtitlelayout decides the groups, the cards, where they go and where the focus is; this screen only
moves the slot widgets and forwards an OK to the player's callbacks (select / shift / resize).
"""
import os

from enigma import ePoint, eSize, eTimer
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Screens.Screen import Screen

from . import i18n, layout, panellayout, panelnav, skins, theme
from . import subtitlelayout as SL


ICONS = os.path.join(os.path.dirname(__file__), "icons")
ITEM_WIDGETS = ("card", "ctitle", "csub", "ring")


class SubtitlesPanel(Screen):
    def __init__(self, session, lang, state, select, shift, resize, edit_language):
        self.skin = skins.subtitles_panel(lang)
        Screen.__init__(self, session)
        self.lang, self.rtl, self.state = lang, lang == "ar", state
        self.select, self.shift, self.resize, self.edit_language = select, shift, resize, edit_language
        self.groups = self.build_groups()
        ids = [group["id"] for group in self.groups]
        playing = SL.playing_group(state)
        selected = ids.index(playing) if playing in ids else 0
        # focus starts on the playing subtitle's row, else Settings > Off
        self.nav = SL.Nav(len(self.groups), selected, SL.items_for(ids[selected], state, lang), self.rtl)
        self.offset, self.group_offset = 0, 0
        for name in ("dimleft", "panel", "ptitle", "navframe", "groupselect", "groupselected", "mainframe",
                     "currentring", "pillframe", "ptoast"):
            self[name] = Label("")
        for name in ("close", "groupplay", "playicon"):
            self[name] = Pixmap()
        self["ptitle"].setText(i18n.t("subtitles.title", lang))
        for i in range(SL.GROUP_SLOTS):
            for kind in ("groupbox", "group", "groupcount"):
                self["%s%d" % (kind, i)] = Label("")
        for i in range(SL.ITEM_SLOTS):
            for kind in ITEM_WIDGETS:
                self["%s%d" % (kind, i)] = Label("")
            for k in range(SL.PILL_SLOTS):
                self["pill%d_%d" % (i, k)] = Label("")
        for k in range(SL.PILL_SLOTS):
            self["dot%d" % k] = Label("")
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions"], {
            "cancel": lambda: self.close(None), "ok": self.ok,
            "up": lambda: self.vertical(-1), "down": lambda: self.vertical(1),
            "left": lambda: self.horizontal("left"), "right": lambda: self.horizontal("right")}, -1)
        self.onLayoutFinish.append(self.begin)
        self.toast_timer = eTimer(); self.toast_timer.callback.append(self.toast_done)

    def begin(self):
        for name, asset in (("close", "close_n.png"), ("groupplay", "selected.png"), ("playicon", "selected.png")):
            if self[name].instance:
                self[name].instance.setPixmapFromFile(os.path.join(ICONS, asset))
        self.draw()

    # --- model ------------------------------------------------------------------------------------------
    def build_groups(self):
        return SL.groups(self.state.get("tracks") or [], self.state.get("embedded") or [],
                         (self.state.get("prefs") or {}).get("languages") or [], self.lang)

    def shown_id(self):
        return self.groups[self.nav.selected]["id"] if self.groups else "settings"

    def updated(self):
        """The player changed the state (tracks arrived, a pick, a setting): rebuild, keeping the focus."""
        shown = self.shown_id()
        focused = self.groups[self.nav.group]["id"] if self.nav.group < len(self.groups) else None
        current = self.nav.items_[self.nav.item]["id"] if self.nav.items_ else None
        self.groups = self.build_groups()
        ids = [group["id"] for group in self.groups]
        gone = shown not in ids
        if gone:                                   # the shown group is gone: back to Settings > Off
            shown, current = "settings", None
        self.nav.count, self.nav.selected = len(self.groups), ids.index(shown)
        self.nav.group = ids.index(focused) if focused in ids else self.nav.selected
        items = SL.items_for(shown, self.state, self.lang)
        found = next((i for i, item in enumerate(items) if item["id"] == current), None)
        if gone:
            found, self.offset = SL.start_focus(items), 0
        self.nav.set_items(items, found, self.nav.pill)
        self.draw()

    # --- drawing ----------------------------------------------------------------------------------------
    def draw(self):
        self.draw_groups()
        self.draw_main()
        self.draw_toast()

    def draw_toast(self):
        """FIX26: a short toast while a site subtitle loads, or when it could not be loaded (3 s)."""
        text = SL.notice(self.state, self.lang)
        self["ptoast"].setText(text)
        self["ptoast"].show() if text else self["ptoast"].hide()
        if self.state.get("notice") in SL.FAILURE_NOTICES:
            self.toast_timer.start(3000, True)

    def toast_done(self):
        if self.state.get("notice") in SL.FAILURE_NOTICES:
            self.state["notice"] = None
            self.draw_toast()

    def paint(self, widget, fill=None, color=None):
        if widget.instance:
            if fill:
                widget.instance.setBackgroundColor(theme.rgb(fill))
            if color:
                widget.instance.setForegroundColor(theme.rgb(color))
            widget.instance.invalidate()           # the box repaints a colour only on invalidate

    def place(self, widget, box):
        if widget.instance:
            widget.instance.move(ePoint(box[0], box[1]))
            widget.instance.resize(eSize(box[2], box[3]))
        widget.show()

    def draw_groups(self):
        self.group_offset = layout.keep_visible(self.group_offset, self.nav.group, len(self.groups),
                                                SL.GROUP_SLOTS)
        playing = SL.playing_group(self.state)
        selected_slot = play_slot = None
        for i in range(SL.GROUP_SLOTS):
            index = self.group_offset + i
            group = self.groups[index] if index < len(self.groups) else None
            for kind in ("groupbox", "group", "groupcount"):
                self["%s%d" % (kind, i)].show() if group else self["%s%d" % (kind, i)].hide()
            if not group:
                continue
            if index == self.nav.selected:
                selected_slot = i
            if group["id"] == playing:
                play_slot = i
            self["group%d" % i].setText(group["label"])
            count = self["groupcount%d" % i]
            count.setText("" if group["count"] is None else str(group["count"]))
            if count.instance:                     # the ▶ sits where the count was: shift the count
                _, top, _, _ = panellayout.server_group(i, self.rtl)
                left = 928 - (64 if group["id"] == playing else 0)
                count.instance.move(ePoint(layout.x(left, 84, self.rtl), top))
            fill = theme.SELECTED if index == self.nav.selected else theme.SURFACE
            self.paint(self["group%d" % i], fill, theme.TEXT)
            self.paint(count, fill, theme.DIM)
        for name in ("groupselect", "groupselected", "groupplay", "navframe"):
            self[name].hide()
        # FIX26: the focused group always gets the white ring; on the selected one it replaces the accent ring
        rings = panelnav.left_rings(self.nav.area, self.nav.group, self.nav.selected)
        if selected_slot is not None and self["groupselect"].instance:
            x, y, _, _ = panellayout.server_group(selected_slot, self.rtl)
            self["groupselect"].instance.move(ePoint(x - 3, y - 3))
            self["groupselect"].show() if rings["accent"] else self["groupselect"].hide()
            self["groupselected"].instance.move(ePoint(x, y)); self["groupselected"].show()
        if play_slot is not None and self["groupplay"].instance:
            x, y, _, _ = panellayout.server_group(play_slot, self.rtl)
            self["groupplay"].instance.move(ePoint(x + (8 if self.rtl else 344), y + 11)); self["groupplay"].show()
        if rings["focus"] is not None and self["navframe"].instance:
            x, y, _, _ = panellayout.server_group(rings["focus"] - self.group_offset, self.rtl)
            self["navframe"].instance.move(ePoint(x - 3, y - 3)); self["navframe"].show()

    def draw_main(self):
        items = self.nav.items_
        self.offset = SL.scroll(items, self.offset, self.nav.item)
        for i in range(SL.ITEM_SLOTS):
            for kind in ITEM_WIDGETS:
                self["%s%d" % (kind, i)].hide()
            for k in range(SL.PILL_SLOTS):
                self["pill%d_%d" % (i, k)].hide()
        for name in ["dot%d" % k for k in range(SL.PILL_SLOTS)] + ["mainframe", "pillframe", "currentring",
                                                                    "playicon"]:
            self[name].hide()
        focused = self.nav.focused()
        for slot, (index, top, h) in enumerate(SL.placed(items, self.offset)):
            self.draw_item(slot, items[index], top, h, items[index] is focused)

    def draw_item(self, slot, item, top, h, focused):
        kind, title, sub = item["kind"], self["ctitle%d" % slot], self["csub%d" % slot]
        if kind in ("note", "heading"):            # plain dim text on the panel, no card (FIX30: a language)
            sub.setText(item["text"])
            self.place(sub, SL.text_box(item, top, h, self.rtl))
            self.paint(sub, theme.BG)
            return
        current = bool(item.get("current"))
        fill = theme.SELECTED if current else theme.SURFACE
        box = SL.main_box(top, h, self.rtl)
        self.place(self["card%d" % slot], box)
        self.paint(self["card%d" % slot], fill)
        title.setText(item["text"] if kind == "row" else item["title"])
        self.place(title, SL.text_box(item, top, h, self.rtl))
        self.paint(title, fill)
        if kind == "row":
            ring = (box[0] - 3, box[1] - 3, box[2] + 6, box[3] + 6)
            if current:
                x, y = SL.play_icon(top, h, self.rtl)
                if self["playicon"].instance:
                    self["playicon"].instance.move(ePoint(x, y))
                self["playicon"].show()
            if focused:                            # the white focus ring replaces the accent one
                self.place(self["mainframe"], ring)
            elif current:
                self.place(self["currentring"], ring)
            return
        if kind == "text":
            sub.setText(item["text"])
            self.place(sub, SL.sub_box(top, self.rtl))
            self.paint(sub, fill)
            return
        f = theme.FRAME
        for k, (pill, pbox) in enumerate(zip(item["pills"][:SL.PILL_SLOTS], SL.pill_boxes(item, top, self.rtl))):
            widget = self["pill%d_%d" % (slot, k)]
            widget.setText(SL.pill_text(pill))
            self.place(widget, pbox)
            self.paint(widget, theme.CHIP, theme.DIM if pill.get("disabled") else theme.ACCENT if pill.get("selected") else theme.TEXT)
            on_it = focused and k == self.nav.pill
            if on_it:                              # white ring on the focused pill, accent on the chosen one
                self.place(self["pillframe"], (pbox[0] - f, pbox[1] - f, pbox[2] + 2 * f, pbox[3] + 2 * f))
            elif pill.get("selected"):
                self.place(self["ring%d" % slot], (pbox[0] - 3, pbox[1] - 3, pbox[2] + 6, pbox[3] + 6))
            if pill.get("dot"):
                dot = self["dot%d" % k]
                self.place(dot, SL.dot_box(pbox, self.rtl))
                self.paint(dot, pill["dot"])

    # --- remote -----------------------------------------------------------------------------------------
    def vertical(self, direction):
        self.nav.vertical(direction)
        self.draw()

    def horizontal(self, key):
        self.nav.horizontal(key)
        self.draw()

    def ok(self):
        if self.nav.area == "nav":                 # only OK opens a group (owner 2026-11-17)
            if self.nav.activate_group():
                self.offset = 0
                items = SL.items_for(self.shown_id(), self.state, self.lang)
                self.nav.set_items(items, SL.start_focus(items), enter=True)
                self.draw()
            return
        item = self.nav.focused()
        todo = SL.action(item, self.nav.pill) if item else None
        if not todo or SL.blocked(self.state, todo):     # FIX26: one OK per load; the row says Loading…
            return
        if todo[0] == "select":
            self.select(todo[1])                   # the player picks it and calls updated()
        elif todo[0] == "shift":
            self.shift(todo[1])                    # the player moves the timing and calls updated()
        elif todo[0] == "language":
            self.edit_language(todo[1], todo[2])
            self.updated()
        elif todo[0] == "add_language":
            from .choice import Choice
            rows = [(SL.lang_name(code, self.lang), code)
                    for code in SL.available_languages((self.state.get("prefs") or {}).get("languages") or [])]
            if rows:
                self.session.openWithCallback(self.language_picked, Choice, self.lang,
                                              i18n.t("subtitles.add_language", self.lang), rows)
        else:                                      # the player changes, draws and saves the setting
            if todo[0] == "set":
                self.resize(todo[1], 0, todo[2])
            else:
                self.resize(todo[1], todo[2])
            self.updated()

    def language_picked(self, picked):
        if picked:
            self.edit_language("add", picked[1])
            self.updated()
