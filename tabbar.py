"""The tab bar's focus: LEFT/RIGHT only highlight, OK loads (owner 2026-11-16) (no Enigma2 imports)."""
from . import tabs

PAGES = ("search", "profile", "settings", "downloads")   # open their own screen; the rows keep the loaded tab


class TabBar:
    def __init__(self):
        self.cursor = self.loaded = 0

    def move(self, d: int) -> None:
        self.cursor = (self.cursor + d) % len(tabs.TABS)

    def ok(self):
        tab_id = tabs.TABS[self.cursor][0]
        if tab_id in PAGES:
            return ("open", tab_id)
        self.loaded = self.cursor
        return ("load", tab_id)

    def leave(self) -> None:
        self.cursor = self.loaded
