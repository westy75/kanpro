"""Player control geometry and directionality (no Enigma2 imports)."""
from . import layout

TOP_X, TOP_W, TOP_Y, TOP_H = 48, 348, 56, 80
ELAPSED_X, ELAPSED_W = 48, 120
TOTAL_X, TOTAL_W = 1754, 116
TRACK_X, TRACK_W, TRACK_Y = 176, 1570, 893
BUTTON_X, BUTTON_Y, BUTTON_W, BUTTON_H = 48, 962, 132, 70
CONTROLS_TOP = 810                              # top of the player's dimmed controls bar
BUTTON_WIDTHS = {"next": 132, "episodes": 188, "subtitles": 188, "audio": 150, "aspect": 230, "quality": 167,
                 "servers": 167}
BUTTON_POS = {"next": 48, "episodes": 720, "subtitles": 920, "audio": 1120, "aspect": 1282, "quality": 1524,
              "servers": 1703}


def box(left: int, width: int, rtl: bool) -> int:
    """Horizontal position of one of the measured LTR controls."""
    return layout.x(left, width, rtl)


def progress(at, length, rtl: bool):
    """Return the fill's left/width and the scrubber's left position."""
    width = layout.bar_fill(at, length, TRACK_W)
    track_x = layout.x(TRACK_X, TRACK_W, rtl)
    left = track_x if not rtl else track_x + TRACK_W - width
    return left, width, left + (width - 3 if not rtl else -3)


def button_x(name: str, rtl: bool) -> int:
    return box(BUTTON_POS[name], BUTTON_WIDTHS[name], rtl)


def visual_buttons(names: list, rtl: bool) -> list:
    """Button names in physical left-to-right order for remote navigation."""
    return list(reversed(names)) if rtl else list(names)


# FIX26: the no-servers card's button row (Try again / Report / Back), centred under its status line
CARD_BTN_Y, CARD_BTN_W, CARD_BTN_H, CARD_BTN_GAP, CARD_BTN_SLOTS = 540, 280, 72, 32, 3
CARD_X, CARD_W = 440, 1040


def card_button_x(slot: int, count: int) -> int:
    """Left edge of visual slot `slot` of `count` buttons (names are already in physical order)."""
    total = count * CARD_BTN_W + max(0, count - 1) * CARD_BTN_GAP
    return CARD_X + (CARD_W - total) // 2 + slot * (CARD_BTN_W + CARD_BTN_GAP)


# FIX32 (spec 081 §1): the searching card's info line + one Servers button in the middle. Under the server row
# (trying/torrent: the card grows to CARD_H_TALL), else in the row's place (looking/resuming: the card keeps
# its height and the hint line stays at 652).
CARD_Y, CARD_H, CARD_H_TALL = 330, 400, 530
CARD_INFO_X, CARD_INFO_W, CARD_INFO_H = 480, 960, 40


def card_pick_layout(row, pick) -> dict:
    """card_h (the card box's height), info_y and button_y of the searching card's Servers button."""
    if pick and row:
        return {"card_h": CARD_H_TALL, "info_y": 708, "button_y": 760}
    return {"card_h": CARD_H, "info_y": 494, "button_y": 546}


def card_pick_icon_x(rtl: bool) -> int:
    """The Servers icon inside the card's one button: at its start (right edge in Arabic), as on the bar."""
    x = card_button_x(0, 1)
    return x + CARD_BTN_W - 44 if rtl else x + 8
