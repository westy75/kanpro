"""Screen geometry, Arabic mirroring and card texts (no Enigma2 imports)."""
from . import tabs

W, H = 1920, 1080
COLS, ROWS = 7, 2
POSTER = (220, 330)
PITCH, LEFT = 248, 96
ROW_Y = (128, 600)            # row label tops; the poster starts 48 px lower
TAB_Y, TAB_H = 34, 60
TAB_W, TAB_GAP = 118, 6       # the category links: 24 px text (owner 2026-11-16: smaller)
TAB_WIDE = {}                  # a text tab wider than TAB_W (FIX31: Downloads became an icon button)
ICON, ICON_GAP = 68, 12       # the round icon buttons on the far side (tabs.ICONS), focus ring baked in
ICON_Y = TAB_Y + (TAB_H - ICON) // 2


def x(left: int, width: int, rtl: bool) -> int:
    return W - left - width if rtl else left


def step(d: int, rtl: bool) -> int:
    """LEFT/RIGHT as the viewer means them: in Arabic RIGHT goes back, LEFT goes on."""
    return -d if rtl else d


def back_direction(rtl: bool) -> str:
    """Physical direction that goes back in the current reading direction."""
    return "right" if step(1, rtl) < 0 else "left"


def keep_visible(offset: int, index: int, count: int, slots: int) -> int:
    """First visible slot of a scrolling list: the focus moves card by card and the list scrolls only
    when the focus would leave the visible window (like the Home rows)."""
    if index < offset:
        return max(0, index)
    if index >= offset + slots:
        return index - slots + 1
    return max(0, min(offset, max(0, count - slots)))


def row_label_y(row: int) -> int:
    return ROW_Y[row]


def row_label_box(row: int) -> tuple:
    """(x, y, w, h) of a Home row title (LTR)."""
    return (116, ROW_Y[row], 1600, 44)


# FIX29: the Home status line ("Loading…", errors, list hints) sits in the strip between the tab bar (its focus
# frame ends at 98) and the first row title (128) - it was drawn over the "Continue watching" title.
HOME_STATUS = (96, 99, 1728, 28)


def card_box(row: int, col: int, rtl: bool) -> dict:
    left = LEFT + col * PITCH
    return {"x": x(left, POSTER[0], rtl), "y": ROW_Y[row] + 48, "w": POSTER[0], "h": POSTER[1]}


def card_text_y(row: int):
    top = ROW_Y[row] + 48 + POSTER[1]
    return top + 8, top + 42


def tab_boxes(rtl: bool) -> list:
    """(x, width) per tabs.TABS entry: text links after the logo, the icon buttons on the far side."""
    icons = [tab_id for tab_id, _ in tabs.TABS if tab_id in tabs.ICONS]
    icon_left = {tab_id: W - 60 - (len(icons) - i) * ICON - (len(icons) - 1 - i) * ICON_GAP
                 for i, tab_id in enumerate(icons)}
    boxes, left = [], 150
    for tab_id, _ in tabs.TABS:
        if tab_id in tabs.ICONS:
            boxes.append((x(icon_left[tab_id], ICON, rtl), ICON))
        else:
            width = TAB_WIDE.get(tab_id, TAB_W)
            boxes.append((x(left, width, rtl), width))
            left += width + TAB_GAP
    return boxes


def card_title(item: dict, limit: int = 16) -> str:
    title = item.get("title") or ""
    return title if len(title) <= limit else title[:limit - 1] + "…"


def clock(seconds) -> str:
    total = max(0, int(seconds or 0))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return "%d:%02d:%02d" % (hours, minutes, secs) if hours else "%d:%02d" % (minutes, secs)


def _progress(item: dict):
    progress = item.get("progress")
    return progress if isinstance(progress, dict) else None


def card_sub(item: dict, lang: str) -> str:
    progress = _progress(item)
    if progress and progress.get("position"):
        when = "%s %s" % ("من" if lang == "ar" else "from", clock(progress["position"]))
        if progress.get("season") is not None and progress.get("episode") is not None:
            return "S%02d·E%02d · %s" % (progress["season"], progress["episode"], when)
        return when
    return str(item["year"]) if item.get("year") else ""


def card_badges(item: dict) -> dict:
    progress = _progress(item)
    account = item.get("account") if isinstance(item.get("account"), dict) else {}
    percent = progress.get("percent") if progress else None
    if isinstance(percent, (int, float)) and not isinstance(percent, bool) and progress.get("duration"):
        percent = max(0, min(100, int(percent)))
    else:
        percent = 0
    return {"series": item.get("kind") == "tv" or item.get("category") == "series",
            "watching": bool(account.get("watching")) or progress is not None, "percent": percent}


def bar_fill(at, length, width: int) -> int:
    if not at or not length or length <= 0:
        return 0
    return int(width * min(1.0, max(0.0, at / float(length))))


def text_lines(text: str, per_line: int) -> int:
    """Lines a wrapped Label needs (about per_line characters a line): a popup sizes its body by it."""
    return sum(max(1, -(-len(line) // per_line)) for line in (text or "").splitlines())
