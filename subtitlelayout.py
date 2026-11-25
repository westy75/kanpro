"""The Subtitles panel as the website draws it (subtitles.js subtitleGroups + renderSettingsGroup, FIX20):
groups on the left, a group's tracks or the Settings cards on the right, their geometry and the remote's
focus. No Enigma2 imports.

An item of the right column is a dict: kind "row" (Off, a track or Add language: OK picks it), "pills"
(a Settings card or language row: OK applies the focused one), "stepper" (− value +), "text", "note" or
"heading" (FIX30: a language name over its From the web tracks; never focused).
Every focusable thing carries its own `action`, so the screen only forwards it to the player:
("select", row) | ("set", name, value) | ("step", name, ±1) | ("shift", seconds) | None (disabled).
"""
import re

from . import i18n, layout, panellayout, subs

# the website's language order after the viewer's own (subtitles.js LANGUAGE_CODES)
LANGUAGE_CODES = ("AR", "EN", "FR", "TR", "ES", "DE", "IT", "PT", "RU", "FA", "UR", "ID", "MS", "NL", "PL", "RO",
                  "EL", "HE", "HI", "JA", "KO", "ZH", "SV", "DA", "NO", "FI", "CS", "HU", "BG", "SR", "HR", "VI",
                  "TH", "BN")
NOTES = ("loading", "none", "unavailable", "key_needed")     # the Settings note under Off (website notes)
GROUP_SLOTS = panellayout.GROUP_SLOTS

# right column: the Servers panel's card column, cards stacked with their own heights and scrolled
MAIN_X, MAIN_W, MAIN_TOP, MAIN_BOTTOM, MAIN_GAP = 1068, 808, 150, 1060, 12
ITEM_SLOTS, PILL_SLOTS = 9, 5
ROW_H = {1: 90, 2: 116}                     # a row of one / two text lines
CARD_H, NOTE_H, HEADING_H = 128, 48, 44
PAD = 28                                    # text and pills start this far inside a card
TITLE_DY, TITLE_H = 12, 40                  # a card's title
PILL_DY, PILL_H, PILL_GAP = 62, 50, 12      # its pills
SUB_DY, SUB_H = 54, 62                      # a text card's two lines
PLAY_W = 64                                 # the ▶ in front of the playing row's text
TRACK_WRAP = 44                             # characters a line of a release name (28 px bold, 680 px)
DOT, DOT_GAP, DOT_W = 22, 12, 34            # dot, real gap, and horizontal room in a colour pill
DOT_SPACES = "     "                        # matching room in the centred pill text


def edit_languages(languages, action, code) -> list:
    """The website's editLanguages rule: valid unique codes, ordered, and at most five."""
    out = []
    for item in languages or []:
        item = str(item).upper()
        if item in LANGUAGE_CODES and item not in out and len(out) < 5:
            out.append(item)
    code = str(code or "").upper()
    at = out.index(code) if code in out else -1
    if action == "add" and at < 0 and len(out) < 5 and code in LANGUAGE_CODES:
        out.append(code)
    elif action == "remove" and at >= 0:
        out.pop(at)
    elif action == "up" and at > 0:
        out[at - 1], out[at] = out[at], out[at - 1]
    elif action == "down" and 0 <= at < len(out) - 1:
        out[at], out[at + 1] = out[at + 1], out[at]
    return out


def available_languages(languages) -> list:
    chosen = edit_languages(languages, "", "")
    return [code for code in LANGUAGE_CODES if code not in chosen]


def lang_name(code, lang) -> str:
    key = "subtitles.lang." + str(code).lower()
    text = i18n.t(key, lang)
    return code if text == key else text


def in_video_rows(tracks, lang) -> list:
    """Our own exteplayer3's s_l list as the panel's in-video rows (Settings → Player = Kanasa exteplayer3).
    Only text tracks (S_TEXT/...): a bitmap track (PGS, VobSub) has no text for our lines. `index` is the
    track id that `s<id>` takes; a language that appears twice is numbered so every row reads differently."""
    rows, seen = [], {}
    for track in tracks or []:
        if not isinstance(track, dict) or not isinstance(track.get("id"), int) \
                or not str(track.get("codec") or "").upper().startswith("S_TEXT"):
            continue
        code = subs.embedded_code(track.get("lang"))
        name = lang_name(code, lang) if code else (str(track.get("lang") or "").strip() or
                                                   i18n.t("subtitles.embedded", lang))
        seen[name] = seen.get(name, 0) + 1
        label = name if seen[name] == 1 else "%s (%d)" % (name, seen[name])
        rows.append({"index": track["id"], "label": label[:80], "lang": code or str(track.get("lang") or ""),
                     "ext": True})
    return rows


# --- groups -----------------------------------------------------------------------------------------------
def web_order(tracks, languages) -> list:
    """The languages of the site/SubDL/subscene tracks in the panel's order: the viewer's languages first in
    their order, then the website's order, then the rest by code."""
    counts = {}
    for track in tracks or []:
        code = str(track.get("lang") or "").upper()
        if code:
            counts[code] = counts.get(code, 0) + 1
    order = []
    for code in [str(c).upper() for c in languages or []] + list(LANGUAGE_CODES) + sorted(counts):
        if code not in order and counts.get(code):
            order.append(code)
    return order


def _web_tracks(tracks) -> list:
    return [t for t in tracks or [] if isinstance(t, dict) and str(t.get("lang") or "").strip()]


def groups(tracks, embedded, languages, lang) -> list:
    """FIX30 (owner 2026-11-20: three parts, never one group per language): Settings, "In the video" (the
    file's own tracks, when any) and "From the web" (every site/SubDL/subscene track, when any)."""
    out = [{"id": "settings", "label": i18n.t("subtitles.settings", lang), "count": None}]
    if embedded:
        out.append({"id": "embedded", "label": i18n.t("subtitles.in_video", lang), "count": len(embedded)})
    web = _web_tracks(tracks)
    if web:
        out.append({"id": "web", "label": i18n.t("subtitles.from_web", lang), "count": len(web)})
    return out


def is_off(state) -> bool:
    return not state.get("active") and state.get("embedded_active") is None


def playing_group(state) -> str:
    """The group holding the subtitle that shows (website groupOf): "web", "embedded", else Settings."""
    active = state.get("active")
    if active:
        if any(t.get("id") == active for t in _web_tracks(state.get("tracks"))):
            return "web"
    elif state.get("embedded_active") is not None:
        return "embedded"
    return "settings"


# --- the right column ---------------------------------------------------------------------------------------
# FIX29 (box 11-20): a site title with CJK / emoji / rare symbols drew as "[]  -  + ()" - the box fonts have no
# glyphs for them. Only these blocks are kept: Latin (+ Latin-1, Extended-A/B, Additional), Greek, Cyrillic, Arabic
# (+ Supplement, Presentation Forms) and the common punctuation.
_FONT_RANGES = ((0x20, 0x7E), (0xA0, 0x24F), (0x370, 0x3FF), (0x400, 0x4FF), (0x600, 0x6FF), (0x750, 0x77F),
                (0x1E00, 0x1EFF), (0x2010, 0x2027), (0x2030, 0x203A), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))
_EMPTY_BRACKETS = re.compile(r"[(\[{<][^\w]*[)\]}>]")


def _font_char(char) -> bool:
    point = ord(char)
    return any(low <= point <= high for low, high in _FONT_RANGES)


def readable(text, fallback="") -> str:
    """Text the box fonts can draw: other characters go, then brackets with nothing readable inside, then the
    separators left at the ends or in a row. Nothing readable left (no letter or digit): the fallback."""
    value = "".join(char if _font_char(char) else " " for char in str(text or ""))
    previous = None
    while previous != value:
        previous, value = value, _EMPTY_BRACKETS.sub(" ", value)
    out = []
    for token in value.split():
        if re.search(r"\w", token):
            out.append(token)
        elif out and re.search(r"\w", out[-1]):
            out.append(token)                # one separator between two readable parts
    while out and not re.search(r"\w", out[-1]):
        out.pop()
    return " ".join(out) if out else fallback


def _release(track, lang="en") -> str:
    text = str(track.get("release") or track.get("label") or "")
    code = str(track.get("lang") or "").upper()
    text = text[len(code) + 3:] if code and text.upper().startswith(code + " · ") else text
    return readable(text, lang_name(code, lang) if code else "")


def _row(ident, text, current, row, wrap=True) -> dict:
    lines = [line for line in subs.wrap_text(text, TRACK_WRAP, 2) if line] if wrap else [text]
    return {"id": ident, "kind": "row", "text": "\n".join(lines) or text, "lines": max(1, len(lines)),
            "current": current, "action": ("select", row)}


def _track_row(track, state, lang) -> dict:
    code = str(track.get("lang") or "").upper()
    row = {"kind": "track", "id": track.get("id"), "track": track, "lang": code,
           "release": track.get("release") or track.get("label") or "", "source": subs.track_source(track)}
    loading = bool(track.get("id")) and track.get("id") == state.get("pending")
    text = (i18n.t("subtitles.loading", lang) + "  " if loading else "") + _release(track, lang)
    item = _row("track:%s" % track.get("id"), text,
                bool(track.get("id")) and track.get("id") == state.get("active"), row)
    item["loading"] = loading
    return item


def track_items(group_id, state, lang="en") -> list:
    """The In the video / From the web rows; the playing one is current. From the web: the tracks under a small
    non-focusable heading per language ("heading" items), languages in web_order. FIX26: the site subtitle that
    is loading (state["pending"]) says so at once, so a slow download never looks like a dead OK."""
    if group_id == "embedded":
        return [_row("embedded:%s" % row.get("index"), row.get("label") or "", row.get("index") ==
                     state.get("embedded_active"), dict(row, kind="embedded"))
                for row in state.get("embedded") or []]
    if group_id != "web":
        return []
    tracks = _web_tracks(state.get("tracks"))
    items = []
    for code in web_order(tracks, (state.get("prefs") or {}).get("languages")):
        items.append({"id": "heading:" + code, "kind": "heading", "text": lang_name(code, lang)})
        items += [_track_row(track, state, lang) for track in tracks
                  if str(track.get("lang") or "").upper() == code]
    return items


def blocked(state, todo) -> bool:
    """FIX26: while a site subtitle loads, OK on any subtitle row is ignored (settings still work)."""
    return bool((state or {}).get("pending")) and bool(todo) and todo[0] == "select"


FAILURE_NOTICES = ("failed", "limit")


def notice(state, lang):
    """FIX26: the panel's short toast: loading, or that the subtitle could not be loaded ("limit" = the
    server's daily subtitle limit, FIX31); "" = none."""
    value = (state or {}).get("notice")
    key = {"loading": "subtitles.loading_toast", "failed": "subtitles.load_failed",
           "limit": "subtitles.daily_limit"}.get(value)
    return i18n.t(key, lang) if key else ""


def failure_code(error) -> str:
    """A failed subtitle download as text for the log: ApiError's code (+ the server's reason), else the type."""
    code = getattr(error, "code", None)
    if code is None:
        return "empty file" if error is None else type(error).__name__
    reason = getattr(error, "reason", None)
    return "%s (%s)" % (code, reason) if reason else str(code)


def failure_notice(error) -> str:
    """FIX31: which note a failed subtitle download shows. The server's 503 {"error": "daily_limit"} is the
    daily limit; 422 no_episode_file, 404, 502, any other 503, a network error, an empty file = "failed"."""
    if getattr(error, "code", None) == "http_503" and getattr(error, "reason", None) == "daily_limit":
        return "limit"
    return "failed"


def next_same_language(tracks, track):
    """The next track after `track` in the list with the same language (another id), or None."""
    rows = [row for row in tracks or [] if isinstance(row, dict)]
    try:
        following = rows[rows.index(track) + 1:]
    except ValueError:
        return None
    lang = str((track or {}).get("lang") or "").upper()
    return next((row for row in following if row.get("id") != track.get("id")
                 and str(row.get("lang") or "").upper() == lang), None)


def _pills(ident, title, choices, current) -> dict:
    return {"id": ident, "kind": "pills", "title": title,
            "pills": [{"text": text, "selected": value == current, "action": ("set", ident, value)}
                      for value, text in choices]}


def _stepper(ident, title, value, high) -> dict:
    return {"id": ident, "kind": "stepper", "title": title,
            "pills": [{"text": "−", "disabled": value <= 0, "action": ("step", ident, -1) if value > 0 else None},
                      {"text": "%d%%" % value, "static": True},
                      {"text": "+", "disabled": value >= high,
                       "action": ("step", ident, 1) if value < high else None}]}


def settings_items(state, lang) -> list:
    """The Settings cards top to bottom, as the website: Off, the status note, Timing (only while a
    downloaded subtitle shows), My languages, Font (where the website has Arabic font), Size, Colour,
    Background, Box opacity, Height above the bottom, and the footer while off."""
    t = lambda key: i18n.t(key, lang)
    prefs = state.get("prefs") or {}
    style = subs.look(prefs)
    off = is_off(state)
    items = [_row("off", t("subtitles.off"), off, {"kind": "off"}, wrap=False)]
    status = state.get("status")
    if status in NOTES:
        items.append({"id": "note", "kind": "note", "text": t("subtitles.status." + status)})
    if state.get("active"):
        offset = float(state.get("offset") or 0)
        items.append({"id": "timing", "kind": "pills", "title": t("subtitles.timing"), "pills": [
            {"text": t("subtitles.earlier"), "action": ("shift", -0.5)},
            {"text": subs.format_offset(offset), "action": ("shift", -offset) if offset else None},
            {"text": t("subtitles.later"), "action": ("shift", 0.5)}]})
    languages = edit_languages(prefs.get("languages") or [], "", "")
    chosen = [lang_name(code, lang) for code in languages]
    items.append({"id": "languages", "kind": "text", "title": t("subtitles.my_languages"),
                  "text": " · ".join(chosen) if chosen else t("subtitles.languages_default")})
    for index, code in enumerate(languages):
        items.append({"id": "language:" + code, "kind": "pills", "title": lang_name(code, lang), "pills": [
            {"text": t("subtitles.languages_up"), "disabled": index == 0,
             "action": None if index == 0 else ("language", "up", code)},
            {"text": t("subtitles.languages_down"), "disabled": index == len(languages) - 1,
             "action": None if index == len(languages) - 1 else ("language", "down", code)},
            {"text": t("subtitles.languages_remove"), "action": ("language", "remove", code)}]})
    if len(languages) < 5:
        add = _row("add_language", t("subtitles.add_language"), False, {}, wrap=False)
        add["action"] = ("add_language",)
        items.append(add)
    else:
        items.append({"id": "add_language", "kind": "text", "title": t("subtitles.languages_full"), "text": ""})
    items.append(_pills("font", t("subtitles.font"), [(f, t("subtitles.font." + f)) for f in subs.FONTS],
                        style["font"]))
    items.append(_pills("size", t("subtitles.size"), [(s, t("subtitles.size." + s)) for s in subs.SIZES],
                        state.get("size") or prefs.get("size") or "m"))
    colours = _pills("color", t("subtitles.color"), [(c, t("subtitles.color." + c.lstrip("#"))) for c in subs.COLORS],
                     style["color"])
    for pill, colour in zip(colours["pills"], subs.COLORS):
        pill["dot"] = colour                 # a dot of the colour and its name, never the hex
    items.append(colours)
    items.append(_pills("background", t("subtitles.background"),
                        [(b, t("subtitles.background." + b)) for b in subs.BACKGROUNDS], style["background"]))
    items.append(_stepper("box_opacity", t("subtitles.box_opacity"), style["box_opacity"], 100))
    items.append(_stepper("position", t("subtitles.position"), style["position"], subs.POSITION_MAX))
    if off:
        items.append({"id": "footer", "kind": "note", "text": t("subtitles.off_note")})
    return items


def language_hint(state, lang) -> list:
    """082: no subtitle language chosen yet: a note and an Add language row on top of every group."""
    if (state.get("prefs") or {}).get("languages"):
        return []
    add = _row("hint_add", i18n.t("subtitles.add_language", lang), False, {}, wrap=False)
    add["action"] = ("add_language",)
    return [{"id": "hint", "kind": "note", "text": i18n.t("subtitles.languages_hint", lang)}, add]


def items_for(group_id, state, lang) -> list:
    items = settings_items(state, lang) if group_id == "settings" else track_items(group_id, state, lang)
    return language_hint(state, lang) + items


def stops(item) -> list:
    """The focusable pill indices of an item ([0] for a row; [] = never focused)."""
    if item["kind"] == "row":
        return [0]
    if item["kind"] in ("pills", "stepper"):
        return [i for i, pill in enumerate(item["pills"]) if not pill.get("static")]
    return []


def home_pill(item) -> int:
    """Where the focus lands on a card: its selected pill, else its first."""
    found = stops(item)
    chosen = next((i for i, pill in enumerate(item.get("pills") or []) if pill.get("selected")), None)
    return chosen if chosen in found else (found[0] if found else 0)


def action(item, pill):
    if item["kind"] == "row":
        return item["action"]
    pills = item.get("pills") or []
    return pills[pill].get("action") if 0 <= pill < len(pills) else None


def start_focus(items) -> int:
    """The playing row of the group, else its first focusable item (Settings: Off)."""
    current = next((i for i, item in enumerate(items) if item.get("current") and item["kind"] == "row"), None)
    if current is not None:
        return current
    return next((i for i, item in enumerate(items) if stops(item)), 0)


# --- geometry ------------------------------------------------------------------------------------------------
def height(item) -> int:
    if item["kind"] == "row":
        return ROW_H[2 if item.get("lines", 1) > 1 else 1]
    if item["kind"] == "heading":
        return HEADING_H
    return NOTE_H if item["kind"] == "note" else CARD_H


def _span(items, first, last) -> int:
    return sum(height(item) for item in items[first:last + 1]) + MAIN_GAP * (last - first)


def scroll(items, offset, focus) -> int:
    """First item drawn: the focused card stays whole on screen; the notes before the first focusable item
    and after the last one come into view with it (the website scrolls them along)."""
    if not items:
        return 0
    focusable = [i for i, item in enumerate(items) if stops(item)]
    focus = max(0, min(len(items) - 1, focus))
    first = 0 if not focusable or focus <= focusable[0] else focus
    while 0 < first and items[first - 1]["kind"] == "heading":
        first -= 1                            # FIX30: a language heading stays with its first row
    last = len(items) - 1 if not focusable or focus >= focusable[-1] else focus
    room = MAIN_BOTTOM - MAIN_TOP
    offset = max(0, min(offset, first))
    while offset < first and _span(items, offset, last) > room:
        offset += 1
    while offset > 0 and _span(items, offset - 1, len(items) - 1) <= room:
        offset -= 1                           # no empty space at the bottom while there is more above
    return offset


def placed(items, offset) -> list:
    """(item index, top, height) of the items drawn from `offset`, as many as fit (ITEM_SLOTS at most)."""
    out, top = [], MAIN_TOP
    for index in range(offset, len(items)):
        h = height(items[index])
        if top + h > MAIN_BOTTOM or len(out) == ITEM_SLOTS:
            break
        out.append((index, top, h))
        top += h + MAIN_GAP
    return out


def pill_width(pill) -> int:
    return max(PILL_H + 14, 32 + 14 * len(pill["text"]) + (DOT_W if pill.get("dot") else 0))


def pill_boxes(item, top, rtl) -> list:
    """(x, y, w, h) of each pill of a card at `top`, in reading order (mirrored in Arabic)."""
    out, left = [], MAIN_X + PAD
    for pill in item.get("pills") or []:
        width = pill_width(pill)
        out.append((layout.x(left, width, rtl), top + PILL_DY, width, PILL_H))
        left += width + PILL_GAP
    return out


def dot_box(box, rtl):
    """The colour dot inside a pill box: at the reading start of the pill."""
    x, y, w, h = box
    return (x + w - 16 - DOT if rtl else x + 16), y + (h - DOT) // 2, DOT, DOT


def pill_text(pill) -> str:
    return DOT_SPACES + pill["text"] if pill.get("dot") else pill["text"]


def main_box(top, h, rtl):
    return layout.x(MAIN_X, MAIN_W, rtl), top, MAIN_W, h


def text_box(item, top, h, rtl):
    """Where an item's main text goes: a row's text (after the ▶ when it plays), a card's title."""
    if item["kind"] == "row":
        indent = PLAY_W if item.get("current") else 0
        return layout.x(MAIN_X + PAD + indent, MAIN_W - 2 * PAD - indent, rtl), top + 8, \
            MAIN_W - 2 * PAD - indent, h - 16
    if item["kind"] == "note":
        return layout.x(MAIN_X, MAIN_W, rtl), top, MAIN_W, h
    if item["kind"] == "heading":            # FIX30: plain small text where a card's text starts
        return layout.x(MAIN_X + PAD, MAIN_W - 2 * PAD, rtl), top, MAIN_W - 2 * PAD, h
    return layout.x(MAIN_X + PAD, MAIN_W - 2 * PAD, rtl), top + TITLE_DY, MAIN_W - 2 * PAD, TITLE_H


def sub_box(top, rtl):
    return layout.x(MAIN_X + PAD, MAIN_W - 2 * PAD, rtl), top + SUB_DY, MAIN_W - 2 * PAD, SUB_H


def play_icon(top, h, rtl):
    """The 68 px ▶ of the playing row, in front of its text."""
    x, _, w, _ = main_box(top, h, rtl)
    return (x + w - 82 if rtl else x + 14), top + (h - 68) // 2


# --- focus ---------------------------------------------------------------------------------------------------
class Nav:
    """Two columns like panelnav.Nav (owner 2026-11-17: UP/DOWN on the groups only move the focus, OK opens
    the focused group and enters it, RIGHT enters the shown one), plus the pills of a Settings card:
    LEFT/RIGHT walk the pills (mirrored in Arabic) and leaving the first one goes back to the groups."""

    def __init__(self, group_count, selected, items, rtl, focus=None):
        self.rtl, self.count = rtl, group_count
        self.group = self.selected = selected
        self.area, self.item, self.pill, self.items_ = "main", 0, 0, []
        self.set_items(items, focus if focus is not None else start_focus(items), enter=True)

    def set_items(self, items, focus=None, pill=None, enter=False):
        """The right column now holds `items`; the focus goes to `focus` (else stays, else the first stop)."""
        self.items_ = list(items)
        if not self.items_:
            self.item, self.pill, self.area = 0, 0, "nav"
            return
        target = self.item if focus is None else focus
        if not (0 <= target < len(self.items_) and stops(self.items_[target])):
            target = start_focus(self.items_)
        self.item, found = target, stops(self.items_[target])
        self.pill = pill if pill in found else home_pill(self.items_[target])
        if enter and found:
            self.area = "main"

    def focused(self):
        return self.items_[self.item] if self.area == "main" and self.items_ else None

    def vertical(self, direction):
        if self.area == "nav":
            self.group = max(0, min(self.count - 1, self.group + direction))
            return
        index = self.item + direction
        while 0 <= index < len(self.items_) and not stops(self.items_[index]):
            index += direction
        if 0 <= index < len(self.items_):
            self.item, self.pill = index, home_pill(self.items_[index])

    def horizontal(self, key):
        step = layout.step(1 if key == "right" else -1, self.rtl)
        if self.area == "nav":
            if step > 0 and any(stops(item) for item in self.items_):
                self.area = "main"            # the shown group's column, not the focused one
            return
        found = stops(self.items_[self.item])
        position = found.index(self.pill) if self.pill in found else 0
        if step < 0 and position == 0:
            self.area, self.group = "nav", self.selected      # back to the groups, on the shown one
        elif 0 <= position + step < len(found):
            self.pill = found[position + step]

    def activate_group(self):
        """OK on a group: it becomes the shown one; the caller refills the column (set_items(enter=True))."""
        if self.area != "nav":
            return False
        self.selected = self.group
        return True
