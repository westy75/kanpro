"""Two-column Episodes focus (no Enigma2 imports).

Like the player panels (panelnav): UP/DOWN in the seasons column only move the focus; the episodes column
changes only when OK picks a season (`selected`). RIGHT enters the episodes of the selected season.

FIX9: an episode row has the card, then (reading direction) its Watched toggle and, under it, its Download
button. RIGHT from the card enters the buttons, LEFT goes back to the card; UP/DOWN walk Watched -> Download ->
the next episode's Watched. `watched=False` (a visitor: the account route marks episodes) leaves only Download.
"""
from . import layout


class Nav:
    def __init__(self, seasons: int, episodes: int, rtl: bool, released=None, selected=0, watched=True,
                 downloads=True):
        self.rtl, self.counts = rtl, {"seasons": seasons, "episodes": episodes}
        self.released = list(released or [True] * episodes)
        self.selected = selected
        self.area, self.index = "seasons", selected
        self.part = "card"                     # in the episodes area: card | watched | download
        # 084: a free plan without downloads has no Download button to focus
        self.parts = (("watched",) if watched else ()) + (("download",) if downloads else ())

    def vertical(self, direction: int):
        count = self.counts[self.area]
        if not count:
            return
        if self.area != "episodes" or self.part == "card":
            self.index = max(0, min(count - 1, self.index + direction))
            return
        # the buttons: one column of (Watched, Download) pairs, row after row
        slots = [(i, part) for i in range(count) for part in self.parts]
        pos = max(0, min(len(slots) - 1, slots.index((self.index, self.part)) + direction))
        self.index, self.part = slots[pos]

    def horizontal(self, key: str):
        step = layout.step(1 if key == "right" else -1, self.rtl)
        if self.area == "episodes" and self.part != "card":
            if step < 0:
                self.part = "card"
        elif self.area == "episodes" and step > 0:
            if self.parts:
                self.part = self.parts[0]
        elif self.area == "episodes" and step < 0 and self.counts["seasons"]:
            self.area, self.index, self.part = "seasons", self.selected, "card"
        elif self.area == "seasons" and step > 0 and self.counts["episodes"]:
            self.area, self.index, self.part = "episodes", 0, "card"

    def ok(self):
        if not self.counts[self.area]:
            return None
        if self.area == "episodes" and not self.released[self.index]:
            return None
        if self.area == "seasons":
            return ("season", self.index)
        return ("episode" if self.part == "card" else self.part, self.index)
