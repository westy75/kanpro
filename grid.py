"""Focus, scrolling and paging over rows of cards (no Enigma2 imports)."""


class Grid:
    def __init__(self, rows: list, cols: int, visible_rows: int):
        self.rows = [dict(row, items=list(row.get("items") or [])) for row in rows]
        self.cols, self.visible_rows = cols, visible_rows
        self.row = self.col = self.first_row = 0
        self.asked = set()

    def focused(self):
        if not self.rows:
            return None
        return self.rows[self.row]["items"][self.col]

    def move(self, d_row: int, d_col: int) -> bool:
        if not self.rows:
            return False
        if self.row + d_row < 0:
            return False
        self.row = min(len(self.rows) - 1, self.row + d_row)
        last = len(self.rows[self.row]["items"]) - 1
        self.col = max(0, min(last, self.col + d_col))
        if self.row < self.first_row:
            self.first_row = self.row
        elif self.row >= self.first_row + self.visible_rows:
            self.first_row = self.row - self.visible_rows + 1
        return True

    def _start(self, index: int) -> int:
        return max(0, self.col - self.cols + 1) if index == self.row else 0

    def window(self) -> list:
        out = []
        for slot in range(self.visible_rows):
            index = self.first_row + slot
            if index >= len(self.rows):
                out.append({"label": "", "items": [None] * self.cols})
                continue
            row, start = self.rows[index], self._start(index)
            items = row["items"][start:start + self.cols]
            out.append({"label": row.get("label") or "", "items": items + [None] * (self.cols - len(items))})
        return out

    def focus_slot(self):
        return self.row - self.first_row, self.col - self._start(self.row)

    def wants_more(self):
        if not self.rows:
            return None
        row = self.rows[self.row]
        page = row.get("next_page")
        key = (row.get("page_tab"), row.get("page_row"), page, row.get("id"))
        if not page or not key[0] or key in self.asked or self.col < len(row["items"]) - 3:
            return None
        self.asked.add(key)
        return key

    def append(self, row_id: str, items: list, next_page) -> None:
        for row in self.rows:
            if row.get("id") != row_id:
                continue
            known = {(item.get("kind"), item.get("id")) for item in row["items"]}
            fresh = [item for item in items if (item.get("kind"), item.get("id")) not in known]
            row["items"].extend(fresh)
            row["next_page"] = next_page if fresh else None
            return
