"""Measured player side-panel geometry (no Enigma2 imports)."""
from . import layout

PANEL = (576, 0, 1344, 1080)
GROUP_X, GROUP_Y, GROUP_W, GROUP_H, GROUP_STEP = 620, 150, 420, 90, 108
SERVER_X, SERVER_Y, SERVER_W, SERVER_H, SERVER_STEP = 1068, 150, 808, 158, 168   # 5 Android-style cards end at y 980
SERVER_SLOTS, GROUP_SLOTS = 5, 8
# FIX29 (box 11-20): a long torrent release name wrapped to a clipped 2nd line over the meta line. The card's label
# line is ONE line (25 px in 720 px, 656 px beside the red play mark): ~14 px a character in a release name.
SERVER_LABEL_CHARS = 46
EP_SEASON_X, EP_SEASON_Y, EP_SEASON_W, EP_SEASON_H, EP_SEASON_STEP = 620, 150, 300, 90, 108
PANEL_EP_X, PANEL_EP_Y, PANEL_EP_W, PANEL_EP_H, PANEL_EP_STEP = 948, 150, 794, 282, 290

# the download page (full screen): the Servers panel's two columns, pills left, multi-line cards right
DL_PILL_X, DL_PILL_Y, DL_PILL_W, DL_PILL_H, DL_PILL_STEP, DL_PILL_SLOTS = 96, 240, 460, 84, 96, 7
DL_CARD_X, DL_CARD_Y, DL_CARD_W, DL_CARD_H, DL_CARD_STEP, DL_CARD_SLOTS = 600, 240, 1224, 168, 180, 4


def one_line(text, limit=SERVER_LABEL_CHARS) -> str:
    """text on one line: newlines become spaces, and a longer text ends in an ellipsis."""
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[:max(1, limit - 1)].rstrip(" .-_·") + "…"


def download_pill(index: int, rtl: bool):
    return layout.x(DL_PILL_X, DL_PILL_W, rtl), DL_PILL_Y + index * DL_PILL_STEP, DL_PILL_W, DL_PILL_H


def download_card(index: int, rtl: bool):
    return layout.x(DL_CARD_X, DL_CARD_W, rtl), DL_CARD_Y + index * DL_CARD_STEP, DL_CARD_W, DL_CARD_H


def server_group(index: int, rtl: bool):
    return layout.x(GROUP_X, GROUP_W, rtl), GROUP_Y + index * GROUP_STEP, GROUP_W, GROUP_H


def server_card(index: int, rtl: bool):
    return layout.x(SERVER_X, SERVER_W, rtl), SERVER_Y + index * SERVER_STEP, SERVER_W, SERVER_H


def episode_season(index: int, rtl: bool):
    return layout.x(EP_SEASON_X, EP_SEASON_W, rtl), EP_SEASON_Y + index * EP_SEASON_STEP, EP_SEASON_W, EP_SEASON_H


def panel_episode(index: int, rtl: bool):
    return layout.x(PANEL_EP_X, PANEL_EP_W, rtl), PANEL_EP_Y + index * PANEL_EP_STEP, PANEL_EP_W, PANEL_EP_H


# FIX26: the Servers panel's top row, reachable with the arrows; its buttons in LTR skin coordinates
TOP_BUTTONS = {"star": (1236, 54, 244, 54), "mute": (1490, 54, 264, 54), "close": (1788, 44, 88, 88)}


def top_button(name: str, rtl: bool):
    left, top, width, height = TOP_BUTTONS[name]
    return layout.x(left, width, rtl), top, width, height


# FIX26: the right column is a list of lines of different heights (the Torrents group's "Sort & filter"
# header, its open drop-down lines, then the server cards); lines below an open drop-down shift down.
COLUMN_BOTTOM = 1000                    # the last card ends above the hint line (y 1016)
HEADER_H, OPTION_H, LINE_GAP = 64, 52, 8
OPTION_SLOTS = 10                       # torrents.SORTS + torrents.FILTERS


def line_height(kind: str) -> int:
    return HEADER_H if kind == "header" else OPTION_H if kind in ("sort", "filter") else SERVER_H


def line_step(kind: str) -> int:
    return SERVER_STEP if kind == "server" else line_height(kind) + LINE_GAP


def column_layout(kinds, offset: int, rtl: bool) -> list:
    """(line index, (x, y, w, h)) of the lines drawn from `offset` down; the first one always shows."""
    boxes, top = [], SERVER_Y
    for index in range(max(0, offset), len(kinds)):
        height = line_height(kinds[index])
        if boxes and top + height > COLUMN_BOTTOM:
            break
        boxes.append((index, (layout.x(SERVER_X, SERVER_W, rtl), top, SERVER_W, height)))
        top += line_step(kinds[index])
    return boxes


def column_offset(kinds, offset: int, index: int) -> int:
    """The first drawn line such that line `index` shows: the list scrolls only when the focus would leave."""
    offset = max(0, min(offset, max(0, len(kinds) - 1)))
    if index < offset:
        offset = max(0, index)
    while offset < index and index not in [i for i, _ in column_layout(kinds, offset, False)]:
        offset += 1
    last = len(kinds) - 1
    while offset > 0 and {index, last} <= {i for i, _ in column_layout(kinds, offset - 1, False)}:
        offset -= 1                     # a shorter list (a filter) fills the column from the top again
    return offset
