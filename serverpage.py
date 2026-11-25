"""Settings → Servers (066): Auto switch, the group rows (▲ ▼ ★ ⊘ inside their band), Reset to default.
Every change is kept at once and written to the account (signed in) or the box (a visitor). Thin: the rules
are in serversettings / failover."""
import os

from enigma import ePoint
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Screens.Screen import Screen

from . import async_call, i18n, layout, serversettings, skins, theme

ICONS = os.path.join(os.path.dirname(__file__), "icons")
GLYPHS = ("▲", "▼", theme.GLYPHS["star"], "")          # the 4th chip shows the eye_off icon


def group_title(group_id, lang) -> str:
    key = "servers.group.%s" % group_id
    text = i18n.t(key, lang)
    return group_id if text == key else text


class ServerSettings(Screen):
    def __init__(self, session, lang, source):
        self.skin = skins.server_settings(lang)
        Screen.__init__(self, session)
        self.lang, self.rtl, self.source = lang, lang == "ar", source
        self.index, self.column, self.offset, self.entries = 0, 0, 0, []
        self["title"] = Label(i18n.t("servers.title", lang))
        self["help"], self["rowframe"] = Label(""), Label("")
        for i in range(skins.SERVER_ROWS):
            for kind in ("srow", "skey", "sval"):
                self["%s%d" % (kind, i)] = Label("")
            for j in range(4):
                self["chip%d_%d" % (i, j)] = Label(GLYPHS[j])
            self["chipicon%d" % i] = Pixmap()
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions"], {
            "ok": self.ok, "cancel": self.close, "up": lambda: self.move(-1), "down": lambda: self.move(1),
            "left": lambda: self.side(-1), "right": lambda: self.side(1)}, -1)
        self.onLayoutFinish.append(self.start)

    def start(self):
        for i in range(skins.SERVER_ROWS):
            if self["chipicon%d" % i].instance:
                self["chipicon%d" % i].instance.setPixmapFromFile(os.path.join(ICONS, "eye_off.png"))
        if self.source.signed_in():           # the account may have changed on the site or the app
            async_call.run(self.source.load_server_groups, on_done=lambda _: self.draw(),
                           on_error=lambda error: self.draw())
        self.draw()

    def state(self):
        order = self.source.server_group_order()
        return (order[0] if order else None, self.source.server_group_rank(), self.source.server_group_muted())

    def draw(self):
        star, rank, muted = self.state()
        rows = serversettings.rows(star, rank, muted)
        self.entries = [("auto", None)] + [("group", row) for row in rows] + [("reset", None)]
        self.index = min(self.index, len(self.entries) - 1)
        self.offset = layout.keep_visible(self.offset, self.index, len(self.entries), skins.SERVER_ROWS)
        for slot in range(skins.SERVER_ROWS):
            index = self.offset + slot
            item = self.entries[index] if index < len(self.entries) else None
            for kind in ("srow", "skey", "sval"):
                self["%s%d" % (kind, slot)].show() if item else self["%s%d" % (kind, slot)].hide()
            group = item[1] if item and item[0] == "group" else None
            for j in range(4):
                chip = self["chip%d_%d" % (slot, j)]
                chip.show() if group else chip.hide()
            self["chipicon%d" % slot].show() if group else self["chipicon%d" % slot].hide()
            if not item:
                continue
            if item[0] == "auto":
                self["skey%d" % slot].setText(i18n.t("servers.auto", self.lang))
                self["sval%d" % slot].setText(i18n.t("settings.on" if self.source.server_autoplay()
                                                     else "settings.off", self.lang))
            elif item[0] == "reset":
                self["skey%d" % slot].setText(i18n.t("servers.reset", self.lang))
                self["sval%d" % slot].setText("")
            else:
                name = group_title(group["id"], self.lang)
                self["skey%d" % slot].setText(name + ("  " + theme.GLYPHS["star"] if group["starred"] else ""))
                self["sval%d" % slot].setText("")
                self.paint_chips(slot, index, group)
            if self["skey%d" % slot].instance:
                self["skey%d" % slot].instance.setForegroundColor(theme.rgb(
                    theme.DIM if group and group["muted"] else theme.TEXT))
                self["skey%d" % slot].instance.invalidate()
        self.draw_focus()

    def paint_chips(self, slot, index, group):
        enabled = (group["up"], group["down"], True, True)
        lit = (False, False, group["starred"], group["muted"])
        for j in range(4):
            chip = self["chip%d_%d" % (slot, j)]
            if not chip.instance:
                continue
            focused = index == self.index and j == self.column
            fill = theme.ACCENT if focused else theme.SELECTED if lit[j] else theme.CHIP
            chip.instance.setBackgroundColor(theme.rgb(fill))
            chip.instance.setForegroundColor(theme.rgb(theme.TEXT if enabled[j] else theme.BORDER))
            chip.instance.invalidate()           # the box repaints a colour only on invalidate

    def draw_focus(self):
        frame = self["rowframe"]
        if frame.instance:
            frame.instance.move(ePoint(layout.x(skins.SERVER_ROW_X, skins.SERVER_ROW_W, self.rtl) - theme.FRAME,
                                       skins.SERVER_ROW_Y0 + (self.index - self.offset) * skins.SERVER_ROW_STEP
                                       - theme.FRAME))
        kind = self.entries[self.index][0] if self.entries else "auto"
        if kind == "auto":
            text = i18n.t("servers.auto_help", self.lang)
        elif kind == "reset":
            text = i18n.t("servers.confirm", self.lang)
        else:
            text = i18n.t(("servers.move_up", "servers.move_down", "servers.star", "servers.mute")[self.column],
                          self.lang)
        self["help"].setText(text)

    def move(self, step):
        self.index = max(0, min(len(self.entries) - 1, self.index + step))
        self.draw()

    def side(self, direction):
        if self.entries and self.entries[self.index][0] == "group":
            self.column = max(0, min(3, self.column + layout.step(direction, self.rtl)))
            self.draw()

    def ok(self):
        if not self.entries:
            return
        kind, group = self.entries[self.index]
        star, rank, muted = self.state()
        if kind == "auto":
            self.change({"server_autoplay": not self.source.server_autoplay()})
        elif kind == "reset":
            from .choice import Choice
            rows = [(i18n.t("servers.confirm_yes", self.lang), "yes"), (i18n.t("servers.confirm_no", self.lang), "no")]
            self.session.openWithCallback(self.reset_answer, Choice, self.lang, i18n.t("servers.confirm", self.lang),
                                          rows)
        elif self.column in (0, 1):
            new_rank = serversettings.move(group["id"], -1 if self.column == 0 else 1, star, rank, muted)
            if new_rank is not None:
                self.change({"server_group_rank": new_rank})
                self.index += -1 if self.column == 0 else 1      # the focus follows the moved row
        else:
            order, new_muted = serversettings.toggle(group["id"], "star" if self.column == 2 else "mute",
                                                     star, muted)
            self.change({"server_group_order": order, "server_group_muted": new_muted})
            ids = [row["id"] for row in serversettings.rows(order[0] if order else None, rank, new_muted)]
            self.index = 1 + ids.index(group["id"])               # the row may change band

    def reset_answer(self, picked):
        if picked and picked[1] == "yes":
            self.index, self.column = 0, 0
            self.change(serversettings.reset())

    def change(self, fields):
        if self.source.set_server_settings(fields):
            async_call.run(self.source.save_server_settings, on_done=self.saved, on_error=lambda e: self.saved(False))
        self.draw()

    def saved(self, ok):
        if not ok:
            self["help"].setText(i18n.t("servers.save_failed", self.lang))
        self.draw()
