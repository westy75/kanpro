"""084 profiles on the box: the GET /api/profiles body, the picker rules, the PIN pad and the error texts
(no Enigma2 imports). Contract: specs/084-profiles-plans/contracts/084-profiles-plans.md.

Enigma2 is a TV: "Who's watching?" on EVERY plugin open when the account has 2+ profiles or any PIN (§6),
with the last-used profile pre-focused; a PIN profile asks its PIN every time."""
from . import layout

PALETTE = ("#e5484d", "#f76b15", "#e2a336", "#30a46c", "#12a594", "#3e63dd", "#8e4ec6", "#d6409f")
DARK_TEXT, LIGHT_TEXT = "#1d1400", "#ffffff"
PIN_LEN = 4
MAX_SHOWN = 5                      # 5 seats while the free != pro switch is OFF; the picker shows 5 avatars
NAME_MAX = 20

# the server's error codes (§3/§4) -> i18n keys; 423 "locked" and the body's pin_locked are the same state
ERROR_KEYS = {"wrong_pin": "profiles.err.wrong_pin", "locked": "profiles.err.pin_locked",
              "pin_locked": "profiles.err.pin_locked", "seats_full": "profiles.err.seats_full",
              "not_found": "profiles.err.not_found", "main_only": "profiles.err.main_only",
              "invalid_name": "profiles.err.invalid_name", "invalid_color": "profiles.err.invalid_color",
              "invalid_pin": "profiles.err.invalid_pin", "invalid_request": "profiles.err.invalid_pin",
              "is_main": "profiles.err.is_main", "wrong_password": "profiles.err.wrong_password",
              "rate_limited": "profiles.err.rate_limited", "network": "profiles.err.network"}

_last_state = {"enabled": False, "profiles": [], "current": None, "seats": 0, "can_manage": False}
_picked_for = [None]               # the bearer whose profile was picked in THIS plugin run


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def color(index) -> str:
    index = _int(index)
    return PALETTE[index] if index is not None and 0 <= index < len(PALETTE) else PALETTE[0]


def text_color(index) -> str:
    return DARK_TEXT if _int(index) == 2 else LIGHT_TEXT


def initial(name) -> str:
    for char in (name or "").strip() if isinstance(name, str) else "":
        if char.isalnum():
            return char.upper()
    return "?"


def _profile(raw):
    if not isinstance(raw, dict) or _int(raw.get("id")) is None:
        return None
    name = raw.get("name") if isinstance(raw.get("name"), str) else ""
    return {"id": raw["id"], "name": name, "color": _int(raw.get("color")) or 0,
            "has_pin": raw.get("has_pin") is True, "is_main": raw.get("is_main") is True,
            "locked": raw.get("locked") is True, "pin_locked": raw.get("pin_locked") is True}


def parse(body) -> dict:
    """GET /api/profiles (or a select / management answer). Anything but enabled:true = profiles off."""
    if not isinstance(body, dict) or body.get("enabled") is not True:
        return {"enabled": False, "profiles": [], "current": None, "seats": 0, "can_manage": False}
    rows = body.get("profiles") if isinstance(body.get("profiles"), list) else []
    found = [p for p in (_profile(raw) for raw in rows) if p is not None]
    current = _int(body.get("current"))
    return {"enabled": True, "profiles": found,
            "current": current if any(p["id"] == current for p in found) else None,
            "seats": _int(body.get("seats")) or 0, "can_manage": body.get("can_manage") is True}


def remember(state) -> dict:
    """The newest parsed state, for screens that open later (Settings shows its Profiles row from it)."""
    fresh = dict(state) if isinstance(state, dict) else parse(None)
    _last_state.clear()
    _last_state.update(fresh)
    return dict(fresh)


def last_state() -> dict:
    return dict(_last_state)


def needs_picker(state) -> bool:
    """2+ profiles, or any PIN. One profile without a PIN: never (the server picks it by itself)."""
    if not isinstance(state, dict) or not state.get("enabled"):
        return False
    found = state.get("profiles") or []
    return len(found) >= 2 or any(p.get("has_pin") for p in found)


def settings_visible(state) -> bool:
    return isinstance(state, dict) and state.get("enabled") is True


def current_profile(state):
    for p in (state or {}).get("profiles") or []:
        if p["id"] == (state or {}).get("current"):
            return p
    return None


def prefocus(state, remembered=None) -> int:
    """The avatar under the focus when the picker opens: `current`, else the profile last used on this box,
    else the main profile."""
    found = (state or {}).get("profiles") or []
    for wanted in ((state or {}).get("current"), remembered):
        for index, p in enumerate(found):
            if wanted is not None and p["id"] == wanted:
                return index
    for index, p in enumerate(found):
        if p["is_main"]:
            return index
    return 0


def badge(state):
    """The top-bar avatar of the current profile, or None (profiles off / nothing picked yet)."""
    if not settings_visible(state):
        return None
    p = current_profile(state)
    if p is None:
        return None
    return {"initial": initial(p["name"]), "color": color(p["color"]), "text": text_color(p["color"]),
            "name": p["name"]}


def mark_picked(bearer) -> None:
    _picked_for[0] = bearer


def picked(bearer) -> bool:
    """A Home reopened inside the same run (language / account change) does not ask again."""
    return bool(bearer) and _picked_for[0] == bearer


def forget_pick() -> None:
    _picked_for[0] = None


# --- the picker row ---------------------------------------------------------------------------------------
AVATAR, AVATAR_GAP, AVATAR_Y = 220, 72, 400


def avatar_boxes(count: int, rtl: bool) -> list:
    """(x, y, w, h) of each avatar in reading order, centred; Arabic reads right to left."""
    count = max(0, min(count, MAX_SHOWN))
    total = count * AVATAR + max(0, count - 1) * AVATAR_GAP
    left = (layout.W - total) // 2
    return [(layout.x(left + i * (AVATAR + AVATAR_GAP), AVATAR, rtl), AVATAR_Y, AVATAR, AVATAR)
            for i in range(count)]


def move(index: int, step: int, count: int) -> int:
    return max(0, min(count - 1, index + step)) if count else 0


# --- PIN pad --------------------------------------------------------------------------------------------
PAD_KEYS = ("1", "2", "3", "4", "5", "6", "7", "8", "9", "back", "0", "ok")
PAD_COLS, PAD_ROWS = 3, 4


def pad_move(index: int, d_row: int, d_col: int, rtl: bool) -> int:
    """The focusable 3x4 digit grid; LEFT/RIGHT are physical (the grid is mirrored in Arabic)."""
    row, col = divmod(index, PAD_COLS)
    row = max(0, min(PAD_ROWS - 1, row + d_row))
    col = max(0, min(PAD_COLS - 1, col + layout.step(d_col, rtl)))
    return row * PAD_COLS + col


class PinEntry:
    def __init__(self):
        self.digits, self.disabled = "", False

    def add(self, digit) -> None:
        if not self.disabled and isinstance(digit, str) and len(digit) == 1 and digit.isdigit() \
                and len(self.digits) < PIN_LEN:
            self.digits += digit

    def back(self) -> None:
        self.digits = self.digits[:-1]

    def clear(self) -> None:
        self.digits = ""

    def value(self) -> str:
        return self.digits

    def complete(self) -> bool:
        return len(self.digits) == PIN_LEN

    def masked(self) -> str:
        return " ".join("●" if i < len(self.digits) else "○" for i in range(PIN_LEN))


def error_code(error) -> str:
    reason = getattr(error, "reason", None)
    if isinstance(reason, str) and reason in ERROR_KEYS:
        return reason
    code = getattr(error, "code", None)
    return code if isinstance(code, str) else ""


def error_key(error) -> str:
    return ERROR_KEYS.get(error_code(error), "profiles.err.generic")


def pin_outcome(error):
    """What the PIN pad does with a failed submit: clear the digits, disable the pad, or keep them."""
    code = error_code(error)
    if code == "wrong_pin":
        return "clear", error_key(error)
    if code in ("locked", "pin_locked"):
        return "disable", error_key(error)
    return "keep", error_key(error)


# --- Settings -> Profiles --------------------------------------------------------------------------------
def actions(state, profile) -> list:
    """What this session may do to `profile` (§4): rename/colour = itself or the main holder; its own PIN =
    itself; another's PIN = the main holder (needs the account password); delete = the main holder, never main."""
    current = (state or {}).get("current")
    manage = (state or {}).get("can_manage") is True
    own = profile["id"] == current
    out = []
    if own or manage:
        out += ["rename", "color"]
    if own:
        out += ["pin_change", "pin_remove"] if profile["has_pin"] else ["pin_set"]
    elif manage and profile["has_pin"]:
        out.append("pin_other")
    if manage and not own and not profile["is_main"]:
        out.append("delete")
    return out


def can_add(state) -> bool:
    found = (state or {}).get("profiles") or []
    return (state or {}).get("can_manage") is True and len(found) < min((state or {}).get("seats") or 0, MAX_SHOWN)


def clean_name(text):
    name = text.strip() if isinstance(text, str) else ""
    return name if 1 <= len(name) <= NAME_MAX else None
