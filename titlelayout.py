"""Measured title-page geometry and focus model (no Enigma2 imports)."""
from . import layout

CAST_SLOTS, RECOMMENDATION_SLOTS = 11, 7
CAST_SIZE, CAST_X, CAST_Y, CAST_STEP = 112, 96, 500, 160
REC_SIZE, REC_X, REC_Y, REC_STEP = (235, 300), 96, 742, 249
GENRE_SLOTS = 3
BUTTON_WIDTHS = {"play": 150, "resume": 230, "episodes": 190, "mylist": 165, "download": 270}   # room for the 36 px icon
BUTTON_GAP = 16


def cast_box(index: int, rtl: bool):
    return layout.x(CAST_X + index * CAST_STEP, CAST_SIZE, rtl), CAST_Y, CAST_SIZE, CAST_SIZE


def recommendation_box(index: int, rtl: bool):
    return layout.x(REC_X + index * REC_STEP, REC_SIZE[0], rtl), REC_Y, REC_SIZE[0], REC_SIZE[1]


def button_boxes(names: list, resume=False, rtl=False) -> list:
    """Content-sized action pills in reading order."""
    left, boxes = 96, []
    for name in names:
        key = "resume" if name == "play" and resume else name
        width = BUTTON_WIDTHS[key]
        boxes.append((layout.x(left, width, rtl), 354, width, 59))
        left += width + BUTTON_GAP
    return boxes


keep_visible = layout.keep_visible


class Nav:
    """Buttons → cast → recommendations; horizontal movement follows reading direction."""
    def __init__(self, series: bool, cast_count: int, recommendation_count: int, genre_count=0,
                 cast_focusable=None, downloads=False):
        self.buttons = ["play", "episodes", "mylist"] if series else ["play", "mylist"]
        if downloads and not series:          # FIX9: a film's Download (an episode's is on its row)
            self.buttons.append("download")
        self.cast_focusable = list(range(cast_count)) if cast_focusable is None else list(cast_focusable)
        self.counts = {"back": 1, "genres": min(GENRE_SLOTS, genre_count), "buttons": len(self.buttons),
                       "cast": len(self.cast_focusable),
                       "recommendations": recommendation_count}
        self.area, self.index = "buttons", 0
        self.last = {}                   # the focus each row had when the viewer left it (like the app)

    def _areas(self):
        return [name for name in ("back", "genres", "buttons", "cast", "recommendations") if self.counts[name]]

    def move_horizontal(self, direction: int, rtl: bool):
        count = self.counts[self.area]
        pos = max(0, min(count - 1, self._position() + layout.step(direction, rtl)))
        self.index = self.cast_focusable[pos] if self.area == "cast" else pos

    def _position(self):
        return self.cast_focusable.index(self.index) if self.area == "cast" else self.index

    def move_vertical(self, direction: int):
        """Back → genres → buttons → cast → more like this (DOWN), the reverse on UP."""
        areas = self._areas()
        pos = max(0, min(len(areas) - 1, areas.index(self.area) + direction))
        if areas[pos] == self.area:
            return
        self.last[self.area] = self.index
        self.area = areas[pos]
        self.index = self.last.get(self.area, self.cast_focusable[0] if self.area == "cast" else 0)

    def ok(self):
        if self.area == "buttons":
            return self.buttons[self.index]
        if self.area == "back": return "back"
        if self.area == "genres": return "genre", self.index
        if self.area == "cast": return "person", self.index
        if self.area == "recommendations":
            return "recommendation", self.index
        return None
