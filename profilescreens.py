"""084 profile screens: "Who's watching?", the PIN pad, Settings -> Profiles and the colour swatches.

Thin Screens over profiles.py (the rules) and api.Client (the calls). Box lessons kept: full-screen opaque pages
(live TV shows through anything see-through), modals opened from an eTimer started in onLayoutFinish, only
bound methods in onLayoutFinish, RowList for lists, RT_HALIGN_RIGHT / mirrored rows in Arabic."""
from enigma import ePoint
from Components.ActionMap import ActionMap
from Components.Label import Label
from Screens.Screen import Screen

from . import async_call, i18n, layout, profiles, skins, theme
from .rowlist import RowList

GREY = "#3a3b46"
DIGITS = "1234567890"


def _paint(widget, background=None, foreground=None):
    if widget.instance is None:
        return
    if background is not None:
        widget.instance.setBackgroundColor(theme.rgb(background))
    if foreground is not None:
        widget.instance.setForegroundColor(theme.rgb(foreground))
    widget.instance.invalidate()


def _number_keys(handler):
    return {digit: (lambda digit=digit: handler(digit)) for digit in DIGITS}


class ProfilePicker(Screen):
    """Closes with the select answer (a GET /api/profiles body with `current`) or None (EXIT: on the plugin's
    opening picker the caller closes the plugin; from "Switch profile" it goes back, owner 11-23). The main
    holder gets an "Add profile" tile after the avatars, like the website picker (owner 11-23)."""

    def __init__(self, session, lang, client, state, remembered=None, prefs=None, closing=True):
        self.skin = skins.profile_picker(lang)
        Screen.__init__(self, session)
        self.lang, self.rtl, self.client, self.prefs = lang, lang == "ar", client, prefs
        self.state = state
        self.take(state)
        self.index = profiles.prefocus(dict(state, profiles=self.rows), remembered)
        self.busy, self.closed, self.as_main = False, False, None
        self["title"] = Label(i18n.t("profiles.title", lang))
        self["hint"] = Label(i18n.t("profiles.hint" if closing else "profiles.hint_back", lang))
        self["status"], self["frame"] = Label(""), Label("")
        for i in range(profiles.MAX_SHOWN):
            self["av%d" % i], self["nm%d" % i], self["lk%d" % i] = Label(""), Label(""), Label("")
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions"], {
            "ok": self.pick, "cancel": self.leave,
            "left": lambda: self.go(layout.step(-1, self.rtl)), "right": lambda: self.go(layout.step(1, self.rtl)),
            "up": lambda: None, "down": lambda: None}, -1)
        self.onLayoutFinish.append(self.draw)

    def take(self, state):
        self.state = state
        self.rows = list(state.get("profiles") or [])[:profiles.MAX_SHOWN]
        # a shared TV: the tile shows whatever profile the box last used; the main profile's pick (PIN) comes first
        self.can_add = profiles.can_add(dict(state, can_manage=True))     # keeps rows + the tile within MAX_SHOWN

    def count(self):
        return len(self.rows) + (1 if self.can_add else 0)

    def on_add(self):
        return self.can_add and self.index == len(self.rows)

    def go(self, step):
        self.index = profiles.move(self.index, step, self.count())
        self["status"].setText("")
        self.draw_focus()

    def draw(self):
        boxes = profiles.avatar_boxes(self.count(), self.rtl)
        for i in range(profiles.MAX_SHOWN):
            avatar, name, mark = self["av%d" % i], self["nm%d" % i], self["lk%d" % i]
            if i >= self.count():
                avatar.hide(); name.hide(); mark.hide()
                continue
            x, y, w, h = boxes[i]
            if i == len(self.rows):                           # the "Add profile" tile
                avatar.setText("+")
                name.setText(i18n.t("profiles.add_tile", self.lang))
                mark.setText("")
                _paint(avatar, GREY, theme.WHITE)
                _paint(name, foreground=theme.TEXT)
                for widget, top in ((avatar, y), (name, y + h + 24), (mark, y + h + 78)):
                    if widget.instance is not None:
                        widget.instance.move(ePoint(x if widget is avatar else x - 26, top))
                    widget.show()
                continue
            p = self.rows[i]
            avatar.setText(profiles.initial(p["name"]))
            name.setText(p["name"])
            if p["locked"]:
                mark.setText(i18n.t("profiles.upgrade", self.lang))
                _paint(avatar, GREY, theme.DIM)
            else:
                mark.setText(i18n.t("profiles.pin_mark", self.lang) + "  ••••" if p["has_pin"] else "")
                _paint(avatar, profiles.color(p["color"]), profiles.text_color(p["color"]))
            _paint(name, foreground=theme.DIM if p["locked"] else theme.TEXT)
            for widget, top in ((avatar, y), (name, y + h + 24), (mark, y + h + 78)):
                if widget.instance is not None:
                    widget.instance.move(ePoint(x if widget is avatar else x - 26, top))
                widget.show()
        self.draw_focus()

    def draw_focus(self):
        frame = self["frame"]
        if not self.count() or frame.instance is None:
            frame.hide()
            return
        x, y, _, _ = profiles.avatar_boxes(self.count(), self.rtl)[self.index]
        frame.instance.move(ePoint(x - skins.PICKER_RING, y - skins.PICKER_RING))
        frame.show()
        for i in range(self.count()):
            _paint(self["nm%d" % i], foreground=theme.WHITE if i == self.index else theme.DIM)

    def pick(self):
        if self.busy or not self.count():
            return
        if self.on_add():
            self.add()
            return
        p = self.rows[self.index]
        if p["locked"]:
            self["status"].setText(i18n.t("profiles.err.seats_full", self.lang))
            return
        if p["has_pin"]:                      # asked EVERY time, even for the current profile (§3, §6)
            self.session.openWithCallback(self.pin_done, PinPad, self.lang,
                                          i18n.t("profiles.pin_title", self.lang) % p["name"], p,
                                          lambda pin: self.client.select_profile(p["id"], pin),
                                          disabled=p["pin_locked"])
            return
        self.busy = True
        self["status"].setText(i18n.t("home.loading", self.lang))
        async_call.run(self.client.select_profile, p["id"], on_done=self.selected, on_error=self.failed)

    def pin_done(self, answer=None):
        if answer is not None:
            self.selected(answer)

    def selected(self, answer):
        self.busy = False
        state = profiles.parse(answer)
        if self.prefs is not None and state.get("current") is not None:
            try:
                self.prefs.set_last_profile(state["current"])
            except OSError:
                pass
        self.closed = True
        self.close(answer)

    def failed(self, error):
        self.busy = False
        self["status"].setText(i18n.t(profiles.error_key(error), self.lang))

    def leave(self):
        self.closed = True
        self.close(self.as_main)            # Add switched the box to the main profile: Home must follow, never guess

    # --- "Add profile" (main holder): the main profile's PIN first, as anyone can sit at the TV ---------------
    def add(self):
        """Adding is the main holder's power on the server (can_manage): pick the main profile first, with its
        PIN when it has one, even if it is already the server's profile."""
        main = next((p for p in self.rows if p["is_main"]), None)
        if main is None:
            return
        if main["has_pin"]:
            self.session.openWithCallback(self.add_unlocked, PinPad, self.lang,
                                          i18n.t("profiles.pin_title", self.lang) % main["name"], main,
                                          lambda pin: self.client.select_profile(main["id"], pin),
                                          disabled=main["pin_locked"])
            return
        if self.state.get("current") == main["id"]:
            self.add_unlocked(True)
            return
        self.busy = True
        self["status"].setText(i18n.t("home.loading", self.lang))
        async_call.run(self.client.select_profile, main["id"], on_done=self.add_unlocked, on_error=self.failed)

    def add_unlocked(self, answer=None):
        self.busy = False
        if answer is None:
            return
        if isinstance(answer, dict):
            self.as_main = answer
            self.state = dict(self.state, current=profiles.parse(answer).get("current"))
        # owner 11-23 (app + website the same): the tile opens Settings -> Profiles, where adding happens
        state = dict(self.state, can_manage=True)
        self.session.openWithCallback(self.page_closed, ProfilesPage, self.lang, self.client, state)

    def page_closed(self, answer=None):
        if answer is not None:
            self.added(answer)

    def added(self, answer):
        self.busy = False
        state = profiles.parse(answer)
        if not state["enabled"]:
            self.leave()
            return
        before = {p["id"] for p in self.rows}
        profiles.remember(state)
        self.take(state)
        fresh = [i for i, p in enumerate(self.rows) if p["id"] not in before]
        self.index = fresh[0] if fresh else min(self.index, self.count() - 1)
        self["status"].setText(i18n.t("profiles.saved", self.lang))
        self.draw()


class PinPad(Screen):
    """4 digits from the remote's number keys or the focusable 3x4 grid; OK on 4 digits submits.
    submit(pin) runs off the GUI thread; its answer closes the pad (None = cancelled). With submit=None the pad
    closes with the typed PIN itself (Settings: choose a new PIN)."""

    def __init__(self, session, lang, title, profile, submit=None, disabled=False):
        self.skin = skins.pin_pad(lang)
        Screen.__init__(self, session)
        self.lang, self.rtl, self.submit, self.profile = lang, lang == "ar", submit, profile or {}
        self.entry, self.index, self.busy, self.closed = profiles.PinEntry(), 0, False, False
        self.entry.disabled = bool(disabled)
        self["title"] = Label(title)
        self["avatar"] = Label(profiles.initial(self.profile.get("name")))
        self["masked"], self["status"], self["padframe"] = Label(""), Label(""), Label("")
        self["hint"] = Label(i18n.t("profiles.pin_hint", lang))
        for index, key in enumerate(profiles.PAD_KEYS):
            text = theme.GLYPHS["back"] if key == "back" else ("OK" if key == "ok" else key)
            self["key%d" % index] = Label(text)
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions"], {
            "ok": self.ok, "cancel": self.leave,
            "up": lambda: self.go(-1, 0), "down": lambda: self.go(1, 0),
            "left": lambda: self.go(0, -1), "right": lambda: self.go(0, 1)}, -1)
        self["digits"] = ActionMap(["NumberActions"], _number_keys(self.digit), -1)
        self.onLayoutFinish.append(self.draw)

    def draw(self):
        colour = self.profile.get("color")
        _paint(self["avatar"], profiles.color(colour), profiles.text_color(colour))
        self["masked"].setText(self.entry.masked())
        if self.entry.disabled:
            self["status"].setText(i18n.t("profiles.err.pin_locked", self.lang))
        for index in range(len(profiles.PAD_KEYS)):
            _paint(self["key%d" % index], foreground=theme.DIM if self.entry.disabled else theme.TEXT)
        self.draw_focus()

    def draw_focus(self):
        frame = self["padframe"]
        if frame.instance is None:
            return
        x, y, _, _ = skins.pad_key_box(self.index, self.rtl)
        frame.instance.move(ePoint(x - theme.FRAME, y - theme.FRAME))
        frame.show() if not self.entry.disabled else frame.hide()

    def go(self, d_row, d_col):
        self.index = profiles.pad_move(self.index, d_row, d_col, self.rtl)
        self.draw_focus()

    def digit(self, value):
        if self.busy or self.entry.disabled:
            return
        if not self.entry.value():
            self["status"].setText("")
        self.entry.add(value)
        self["masked"].setText(self.entry.masked())
        if self.entry.complete():
            self.send()                      # the 4th digit submits, like the app's PIN pad

    def ok(self):
        if self.busy or self.entry.disabled:
            return
        key = profiles.PAD_KEYS[self.index]
        if key == "back":
            self.entry.back()
            self["masked"].setText(self.entry.masked())
        elif key == "ok":
            if self.entry.complete():
                self.send()
        else:
            self.digit(key)

    def send(self):
        pin = self.entry.value()
        if self.submit is None:
            self.closed = True
            self.close(pin)
            return
        self.busy = True
        self["status"].setText(i18n.t("profiles.checking", self.lang))
        async_call.run(self.submit, pin, on_done=self.accepted, on_error=self.refused)

    def accepted(self, answer):
        self.busy, self.closed = False, True
        self.close(answer)

    def refused(self, error):
        self.busy = False
        action, key = profiles.pin_outcome(error)
        if action == "disable":
            self.entry.disabled = True
            self.entry.clear()
        elif action == "clear":
            self.entry.clear()
        self["masked"].setText(self.entry.masked())
        self.draw()
        self["status"].setText(i18n.t(key, self.lang))

    def leave(self):
        self.closed = True
        self.close(None)


class ColourPick(Screen):
    """The 8 palette swatches; closes with the colour index or None."""

    def __init__(self, session, lang, current=0, name=""):
        self.skin = skins.colour_pick(lang)
        Screen.__init__(self, session)
        self.lang, self.rtl = lang, lang == "ar"
        self.index = current if isinstance(current, int) and 0 <= current < len(profiles.PALETTE) else 0
        self["title"] = Label(i18n.t("profiles.color_title", lang))
        self["swframe"] = Label("")
        self["hint"] = Label(i18n.t("profiles.hint", lang))
        for index in range(len(profiles.PALETTE)):
            self["sw%d" % index] = Label(profiles.initial(name) if name else "")
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions"], {
            "ok": lambda: self.close(self.index), "cancel": lambda: self.close(None),
            "left": lambda: self.go(layout.step(-1, self.rtl)), "right": lambda: self.go(layout.step(1, self.rtl)),
            "up": lambda: None, "down": lambda: None}, -1)
        self.onLayoutFinish.append(self.draw)

    def go(self, step):
        self.index = profiles.move(self.index, step, len(profiles.PALETTE))
        self.draw()

    def draw(self):
        frame = self["swframe"]
        if frame.instance is not None:
            x, y, _, _ = skins.swatch_box(self.index, self.rtl)
            frame.instance.move(ePoint(x - skins.PICKER_RING, y - skins.PICKER_RING))


class ProfilesPage(Screen):
    """Settings -> Profiles: list, add (main holder), rename, colour, own PIN, delete (§4). Closes with the
    newest state (a GET /api/profiles body) when something changed, else None."""

    def __init__(self, session, lang, client, state):
        self.skin = skins.profiles_page(lang)
        Screen.__init__(self, session)
        self.lang, self.rtl, self.client = lang, lang == "ar", client
        self.state, self.answer, self.busy = state, None, False
        self["title"] = Label(i18n.t("profiles.page_title", lang))
        self["hint"] = Label(i18n.t("profiles.page_hint", lang))
        self["status"], self["avatar"], self["avname"] = Label(""), Label(""), Label("")
        self["list"] = RowList([], 1100 - 12, 64, theme.font("bold", 30, lang), self.rtl)
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions"], {
            "ok": self.ok, "cancel": lambda: self.close(self.answer),
            "up": self.up, "down": self.down}, -1)
        self.onLayoutFinish.append(self.fill)

    # --- the list ------------------------------------------------------------------------------------------
    def entries(self):
        rows = [("profile", p) for p in self.state.get("profiles") or []]
        if profiles.can_add(self.state):
            rows.append(("add", None))
        return rows

    def label(self, kind, p):
        if kind == "add":
            return i18n.t("profiles.add", self.lang)
        marks = [p["name"]]
        if p["is_main"]:
            marks.append(i18n.t("profiles.main", self.lang))
        if p["id"] == self.state.get("current"):
            marks.append(i18n.t("profiles.you", self.lang))
        if p["has_pin"]:
            marks.append(i18n.t("profiles.pin_mark", self.lang))
        return "   ·   ".join(marks)

    def fill(self):
        self.rows = self.entries()
        self["list"].setList([self.label(kind, p) for kind, p in self.rows])
        self.preview()

    def selected(self):
        index = self["list"].getSelectedIndex()
        return self.rows[index] if self.rows and index is not None and index < len(self.rows) else (None, None)

    def up(self):
        self["list"].up()
        self.preview()

    def down(self):
        self["list"].down()
        self.preview()

    def preview(self):
        kind, p = self.selected()
        if kind != "profile":
            self["avatar"].hide()
            self["avname"].setText("")
            return
        self["avatar"].setText(profiles.initial(p["name"]))
        _paint(self["avatar"], profiles.color(p["color"]), profiles.text_color(p["color"]))
        self["avatar"].show()
        self["avname"].setText(p["name"])

    # --- actions -------------------------------------------------------------------------------------------
    def ok(self):
        if self.busy:
            return
        kind, p = self.selected()
        if kind == "add":
            self.ask_name(None)
            return
        if kind != "profile":
            return
        allowed = profiles.actions(self.state, p)
        if not allowed:
            self["status"].setText(i18n.t("profiles.err.main_only", self.lang))
            return
        from .choice import Choice
        rows = [(i18n.t("profiles." + action, self.lang), action) for action in allowed]
        rows.append((i18n.t("profiles.cancel", self.lang), "cancel"))
        self.session.openWithCallback(lambda picked: self.action(picked, p), Choice, self.lang, p["name"], rows)

    def action(self, picked, p):
        if not picked or picked[1] == "cancel":
            return
        what = picked[1]
        if what == "rename":
            self.ask_name(p)
        elif what == "color":
            self.session.openWithCallback(lambda index=None: self.colour_chosen(p, index), ColourPick, self.lang,
                                          p["color"], p["name"])
        elif what in ("pin_set", "pin_change"):
            self.session.openWithCallback(lambda pin=None: self.pin_first(p, pin), PinPad, self.lang,
                                          i18n.t("profiles.pin_new", self.lang) % p["name"], p)
        elif what == "pin_remove":
            self.call(self.client.remove_profile_pin, p["id"])
        elif what == "pin_other":
            # Removing ANOTHER profile's PIN needs the account password (§4); typing a password with a remote's
            # on-screen keyboard is awkward and shows it on the TV: the website does it.
            from Screens.MessageBox import MessageBox
            self.session.open(MessageBox, i18n.t("profiles.pin_other_web", self.lang), MessageBox.TYPE_INFO,
                              timeout=0)
        elif what == "delete":
            from .choice import Choice
            rows = [(i18n.t("profiles.delete_yes", self.lang), "yes"), (i18n.t("profiles.cancel", self.lang), "no")]
            self.session.openWithCallback(
                lambda answer: answer and answer[1] == "yes" and self.call(self.client.delete_profile, p["id"]),
                Choice, self.lang, i18n.t("profiles.delete_ask", self.lang), rows)

    def ask_name(self, p):
        from Screens.VirtualKeyBoard import VirtualKeyBoard
        self.session.openWithCallback(lambda text=None: self.named(p, text), VirtualKeyBoard,
                                      title=i18n.t("profiles.name_prompt", self.lang), text=p["name"] if p else "")

    def named(self, p, text=None):
        if text is None:
            return
        name = profiles.clean_name(text)
        if name is None:
            self["status"].setText(i18n.t("profiles.err.invalid_name", self.lang))
            return
        if p is None:
            used = {q["color"] for q in self.state.get("profiles") or []}
            colour = next((i for i in range(len(profiles.PALETTE)) if i not in used), 0)
            self.call(self.client.create_profile, name, colour)
        elif name != p["name"]:
            self.call(self.client.update_profile, p["id"], name)

    def colour_chosen(self, p, index=None):
        if index is not None and index != p["color"]:
            self.call(lambda: self.client.update_profile(p["id"], color=index))

    def pin_first(self, p, pin=None):
        if pin is None:
            return
        self.session.openWithCallback(lambda again=None: self.pin_second(p, pin, again), PinPad, self.lang,
                                      i18n.t("profiles.pin_again", self.lang), p)

    def pin_second(self, p, pin, again=None):
        if again is None:
            return
        if again != pin:
            self["status"].setText(i18n.t("profiles.pin_mismatch", self.lang))
            return
        self.call(self.client.set_profile_pin, p["id"], pin)

    def call(self, fn, *args):
        self.busy = True
        self["status"].setText(i18n.t("home.loading", self.lang))
        async_call.run(fn, *args, on_done=self.changed, on_error=self.failed)
        return True

    def changed(self, answer):
        self.busy = False
        state = profiles.parse(answer)
        if not state["enabled"]:
            self.close(answer)               # profiles were switched off meanwhile: no profile UI any more
            return
        self.state, self.answer = state, answer
        profiles.remember(state)
        self["status"].setText(i18n.t("profiles.saved", self.lang))
        self.fill()

    def failed(self, error):
        self.busy = False
        self["status"].setText(i18n.t(profiles.error_key(error), self.lang))
