"""Semantic two-column player-panel focus, the app's TwoColumnPanelRules (no Enigma2 imports).

Owner 2026-11-17: UP/DOWN in the left column only move the focus; the right column changes to the
focused group/season only on OK. Until then it keeps showing the SELECTED one, and RIGHT enters it.

FIX26: an optional top row (the Servers panel's [★ Open first] [⊘ Not interested] [X]) sits above both
columns: UP from the first row of either column enters it, LEFT/RIGHT move inside it (mirrored in Arabic),
DOWN returns to the column it came from.
"""
from . import layout

TOP_ALL, TOP_CLOSE = ("star", "mute", "close"), ("close",)


def top_items(group) -> tuple:
    """The Servers panel's top row for the selected group: the device row has no star/mute (only X)."""
    return TOP_CLOSE if not group or group.get("id") == "device" else TOP_ALL


def left_rings(area: str, focused: int, selected: int) -> dict:
    """FIX26 item 6: which rings the left column draws. The focused group always gets the white focus ring
    (also on the red-filled selected one: white then replaces the selected one's accent ring); with the focus
    elsewhere only the selected group's accent ring shows."""
    on_nav = area == "nav"
    return {"focus": focused if on_nav else None, "accent": not (on_nav and focused == selected)}


def column_items(servers, torrent_group=False, open_=False, sorts=(), filters=()) -> list:
    """The right column's focusable lines, top to bottom: (kind, value). The Torrents group starts with the
    "Sort & filter" header; open, one line per sort then per filter choice follows; then the servers."""
    items = []
    if torrent_group:
        items.append(("header", None))
        if open_:
            items += [("sort", value) for value in sorts] + [("filter", value) for value in filters]
    return items + [("server", index) for index in range(len(servers or []))]


def server_at(items, index):
    """The server index of column line `index`, or None (a header/option line, or out of range)."""
    if 0 <= index < len(items) and items[index][0] == "server":
        return items[index][1]
    return None


def item_of_server(items, server) -> int:
    """The column line showing server number `server` (the first server line when it is gone)."""
    first = None
    for index, (kind, value) in enumerate(items):
        if kind == "server":
            if value == server:
                return index
            first = index if first is None else first
    return first if first is not None else 0


class Nav:
    def __init__(self, nav_count: int, main_count: int, rtl: bool, nav_index=0, main_index=0, top=()):
        self.rtl, self.counts = rtl, {"nav": nav_count, "main": main_count, "top": len(top)}
        self.positions = {"nav": nav_index, "main": main_index, "top": 0}
        self.selected = nav_index
        self.area = "main" if main_count else "nav"
        self.top, self.came_from = tuple(top), self.area

    def set_top(self, names):
        """The top row's buttons changed (another group was selected): keep the focus inside it."""
        self.top = tuple(names)
        self.counts["top"] = len(self.top)
        self.positions["top"] = max(0, min(self.positions["top"], len(self.top) - 1))
        if self.area == "top" and not self.top:
            self.area = self.came_from

    @property
    def top_name(self):
        return self.top[self.positions["top"]] if self.area == "top" and self.top else None

    @property
    def index(self):
        return self.positions[self.area]

    def vertical(self, direction: int):
        if self.area == "top":
            if direction > 0:
                self.area = self.came_from if self.counts[self.came_from] else "nav"
            return
        if direction < 0 and self.top and self.positions[self.area] == 0:
            self.came_from, self.area = self.area, "top"
            return
        count = self.counts[self.area]
        if count:
            self.positions[self.area] = max(0, min(count - 1, self.index + direction))

    def horizontal(self, key: str):
        step = layout.step(1 if key == "right" else -1, self.rtl)
        if self.area == "top":           # star, mute, X are drawn left to right (mirrored in Arabic)
            self.positions["top"] = max(0, min(len(self.top) - 1, self.positions["top"] + step))
            return
        if self.area == "main" and step < 0 and self.counts["nav"]:
            self.area = "nav"
            self.positions["nav"] = self.selected      # main -> nav lands on the selected row
        elif self.area == "nav" and step > 0 and self.counts["main"]:
            self.area = "main"                         # the selected group's list, not the focused one

    def activate_nav(self):
        """OK on a left-column row: it becomes the selected one; the caller refills the right column."""
        if self.area != "nav" or not self.counts["nav"]:
            return False
        self.selected = self.positions["nav"]
        self.positions["main"] = 0
        return True

    def set_main(self, count: int, enter: bool):
        """The right column now holds `count` rows; `enter` moves the focus to its first row."""
        self.counts["main"] = count
        self.positions["main"] = 0 if enter else min(self.positions["main"], max(0, count - 1))
        if enter and count:
            self.area = "main"
