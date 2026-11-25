"""A small chooser in the Kanasa look (resume / start over); closes with (label, value) or None."""
from Components.ActionMap import ActionMap
from Components.Label import Label
from Screens.Screen import Screen

from . import layout, skins, theme
from .rowlist import RowList


class Choice(Screen):
    def __init__(self, session, lang, title, rows, body=""):
        self.skin = skins.choice(lang, len(rows), layout.text_lines(body, 46) if body else 0)
        Screen.__init__(self, session)
        self.rows = list(rows)
        self["card"], self["title"], self["body"] = Label(""), Label(title or ""), Label(body or "")
        self["list"] = RowList([label for label, _ in self.rows], 800 - 12, 72, theme.font("bold", 30, lang),
                               lang == "ar")
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions"], {
            "ok": self.pick, "cancel": lambda: self.close(None),
            "up": lambda: self["list"].up(), "down": lambda: self["list"].down()}, -1)

    def pick(self):
        index = self["list"].getSelectedIndex()
        self.close(self.rows[index] if self.rows and index is not None and index < len(self.rows) else None)
