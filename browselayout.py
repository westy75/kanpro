"""Pure focus/window rules for person and genre poster grids."""
from . import layout

COLS, VISIBLE_ROWS = layout.COLS, 2


class Grid:
    def __init__(self, items=None, header=False):
        self.items, self.index, self.first_row, self.header = list(items or []), 0, 0, header

    def header_visible(self):
        """A person page shows the actor header above the first row, which then sits in the lower slot row
        (like the app, where the header scrolls away once the focus leaves the first row)."""
        return self.header and self.index // COLS == 0

    def focused(self):
        return self.items[self.index] if self.items else None

    def move(self, drow, dcol):
        if not self.items: return False
        row, col = divmod(self.index, COLS)
        if drow < 0 and row == 0: return False
        target_row = max(0, min((len(self.items) - 1) // COLS, row + drow))
        target = min(len(self.items) - 1, target_row * COLS + max(0, min(COLS - 1, col + dcol)))
        self.index = target
        row = self.index // COLS
        if row < self.first_row: self.first_row = row
        elif row >= self.first_row + VISIBLE_ROWS: self.first_row = row - VISIBLE_ROWS + 1
        return True

    def slot(self):
        if self.header_visible(): return 1, self.index % COLS
        return self.index // COLS - self.first_row, self.index % COLS

    def window(self):
        start = self.first_row * COLS
        if self.header_visible():
            chunk = self.items[:COLS]
            return [[None] * COLS, chunk + [None] * (COLS - len(chunk))]
        rows = []
        for row in range(VISIBLE_ROWS):
            chunk = self.items[start + row * COLS:start + (row + 1) * COLS]
            rows.append(chunk + [None] * (COLS - len(chunk)))
        return rows

    def append(self, items):
        known = {(item.get("kind"), item.get("id")) for item in self.items}
        self.items.extend(item for item in items if (item.get("kind"), item.get("id")) not in known)


def clip(text, limit):
    """Cut long text at a word boundary with an ellipsis (a box Label clips without one)."""
    text = " ".join((text or "").split())
    if len(text) <= limit: return text
    cut = text[:limit - 1]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut) + "…"


def born(person):
    return " · ".join(value for value in (person.get("birthday"), person.get("place_of_birth")) if value)
