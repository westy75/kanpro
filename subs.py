"""Pure subtitle parsing, selection and panel shaping (no Enigma2 imports)."""
from bisect import bisect_right
from collections import namedtuple
import html
import re
import textwrap

from . import playerlayout


Cue = namedtuple("Cue", "start end text")
DEFAULT_PREFS = {"enabled": True, "languages": ["AR", "EN"], "size": "m"}
SIZES = ("s", "m", "l", "xl")                      # = the site's subtitle_prefs sizes
# the site/app look (accounts/subtitle_prefs.py DEFAULTS); the account keeps them, a visitor's box too
STYLE_DEFAULTS = {"color": "#ffffff", "background": "outline", "box_opacity": 60, "position": 8, "font": "regular"}
COLORS = ("#ffffff", "#ffeb3b", "#00e5ff", "#76ff03", "#ff8a80")
BACKGROUNDS = ("outline", "box", "none")
OPACITY_STEP = 10                                   # the website's Box opacity stepper: 0-100 by 10
POSITION_MAX, POSITION_STEP = 30, 1                 # % of the picture height above the bottom (site: 0-30 by 1)
FONTS = ("regular", "bold")                         # the fonts on the box (theme.FONT_FILES); box-only choice
# FIX19b (owner 2026-11-18: "even the large one is still small"): the website draws the text at the picture
# height x 4 / 5 / 6.5 / 8 % (subtitles.js SIZES), so on the 1080-line OSD the same names are these px.
SIZE_PX = {"s": 43, "m": 54, "l": 70, "xl": 86}
# characters per line in the 1600 px line labels at ~0.6 em a character
WRAP = {"s": 62, "m": 49, "l": 38, "xl": 31}
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
STATUSES = ("loading", "ok", "none", "unavailable", "key_needed")
_TIME = re.compile(r"^(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,](\d{3})$")
_TAG = re.compile(r"<[^>]*>")
_SPACE = re.compile(r"[ \t]+")
_SOURCE = {"sub-scene": "sub-scene", "subdl": "SubDL", "SubDL": "SubDL"}


def _seconds(value):
    match = _TIME.match(value.strip())
    if not match:
        return None
    hours, minutes, seconds, millis = match.groups()
    return (int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000.0)


def plain_text(value: str) -> str:
    """WebVTT cue markup to safe Label text, preserving intentional line breaks."""
    lines = []
    for line in str(value or "").splitlines():
        clean = html.unescape(_TAG.sub("", line))
        for marker in ("\u200e", "\u200f", "\u202a", "\u202b", "\u202c", "\u202d", "\u202e"):
            clean = clean.replace(marker, "")
        lines.append(_SPACE.sub(" ", clean).strip())
    return "\n".join(line for line in lines if line).strip()


def parse_vtt(data) -> list:
    """Parse useful cues from UTF-8 WebVTT. Bad blocks are ignored, never fatal."""
    if isinstance(data, bytes):
        data = data.decode("utf-8-sig", "replace")
    text = str(data or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n[ \t]*\n", text)
    cues = []
    for block in blocks:
        lines = block.split("\n")
        timing = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            continue
        left, right = lines[timing].split("-->", 1)
        start = _seconds(left)
        end = _seconds(right.strip().split()[0] if right.strip() else "")
        body = plain_text("\n".join(lines[timing + 1:]))
        if start is not None and end is not None and end > start and body:
            cues.append(Cue(start, end, body))
    return sorted(cues, key=lambda cue: (cue.start, cue.end))


def apply_offset(cues: list, seconds: float) -> list:
    """Return cues shifted later by seconds (positive means subtitles appear later)."""
    shift = float(seconds or 0)
    return [Cue(max(0.0, cue.start + shift), max(0.0, cue.end + shift), cue.text)
            for cue in cues if cue.end + shift > 0]


def cue_at(cues: list, seconds: float):
    """Text active at seconds, using a binary search and supporting overlapping cues."""
    if not cues:
        return None
    starts = [cue.start for cue in cues]
    upto = bisect_right(starts, float(seconds))
    active = [cue.text for cue in cues[:upto] if cue.end > float(seconds)]
    return "\n".join(active) if active else None


def wrap_text(value: str, width: int = 42, lines: int = 2) -> list:
    """Wrap without bidi manipulation; Enigma2's Label remains responsible for RTL."""
    chunks = []
    for source in plain_text(value).splitlines():
        chunks.extend(textwrap.wrap(source, width=width, break_long_words=True, break_on_hyphens=False,
                                    replace_whitespace=False) or [""])
    if len(chunks) > lines:
        chunks = chunks[:lines]
        chunks[-1] = (chunks[-1][:-1] + "…") if len(chunks[-1]) >= width else chunks[-1] + "…"
    return chunks + [""] * (lines - len(chunks))


def content_key(target: dict):
    try:
        ident = int(target.get("id"))
        if ident <= 0:
            return None
        if target.get("kind") == "movie":
            return "movie/%d" % ident
        season, episode = int(target.get("season")), int(target.get("episode"))
        if season > 0 and episode > 0:
            return "tv/%d/s/%d/e/%d" % (ident, season, episode)
    except (TypeError, ValueError):
        pass
    return None


def clean_prefs(value) -> dict:
    raw = value if isinstance(value, dict) else {}
    languages = []
    source_languages = raw.get("languages") if isinstance(raw.get("languages"), list) else DEFAULT_PREFS["languages"]
    for code in source_languages:
        code = str(code).upper()
        if re.match(r"^[A-Z]{2}$", code) and code not in languages:
            languages.append(code)
    out = {"enabled": raw.get("enabled") if isinstance(raw.get("enabled"), bool) else True,
           "languages": languages[:5],
           "size": raw.get("size") if raw.get("size") in SIZES else "m"}
    out.update(clean_style(raw))                     # the look, only the fields that were set
    return out


def clean_style(value) -> dict:
    """The look fields of subtitle prefs (site field names), each checked like the site's validate()."""
    raw = value if isinstance(value, dict) else {}
    out = {}
    if isinstance(raw.get("color"), str) and _HEX.match(raw["color"]):
        out["color"] = raw["color"].lower()
    if raw.get("background") in BACKGROUNDS:
        out["background"] = raw["background"]
    for name, high in (("box_opacity", 100), ("position", POSITION_MAX)):
        number = raw.get(name)
        if isinstance(number, int) and not isinstance(number, bool) and 0 <= number <= high:
            out[name] = number
    if raw.get("font") in FONTS:
        out["font"] = raw["font"]
    return out


def look(prefs) -> dict:
    """Every look field with the defaults filled in."""
    return dict(STYLE_DEFAULTS, **clean_style(prefs))


def _step(values, current, direction):
    index = values.index(current) if current in values else -1
    if index < 0:
        return values[0]
    return values[max(0, min(len(values) - 1, index + (1 if direction > 0 else -1)))]


def adjust(prefs, name, direction) -> dict:
    """A −/+ step of one look field: the prefs with that one field moved (clamped like the website)."""
    out = dict(prefs or {})
    style = look(out)
    if name == "size":
        out["size"] = _step(SIZES, out.get("size", "m"), direction)
    elif name == "color":
        index = COLORS.index(style["color"]) if style["color"] in COLORS else -1
        out["color"] = COLORS[(index + (1 if direction > 0 else -1)) % len(COLORS)]
    elif name == "background":
        out["background"] = BACKGROUNDS[(BACKGROUNDS.index(style["background"]) + (1 if direction > 0 else -1))
                                        % len(BACKGROUNDS)]
    elif name == "box_opacity":
        out["box_opacity"] = max(0, min(100, style["box_opacity"] + OPACITY_STEP * (1 if direction > 0 else -1)))
    elif name == "position":
        out["position"] = max(0, min(POSITION_MAX, style["position"] + POSITION_STEP * (1 if direction > 0 else -1)))
    elif name == "font":
        out["font"] = FONTS[(FONTS.index(style["font"]) + 1) % len(FONTS)]
    return out


def set_style(prefs, name, value) -> dict:
    """A pill of the panel's Settings cards: the prefs with that one field set (ignored when invalid)."""
    out = dict(prefs or {})
    if name == "size":
        if value in SIZES:
            out["size"] = value
    else:
        out.update(clean_style({name: value}))
    return out


def account_fields(prefs) -> dict:
    """What PUT /api/subtitles/prefs takes. The font and the size stay on the box: sizes render differently
    per platform (FIX19), and the PUT is partial, so leaving size out keeps the account's own value for the
    website and the app."""
    out = {"enabled": prefs.get("enabled", True), "languages": prefs.get("languages") or []}
    out.update({k: v for k, v in clean_style(prefs).items() if k != "font"})
    return out


def device_prefs(account, box) -> dict:
    """The account's synced prefs drawn with this box's own size (FIX19: never the account's size; "m" when
    the box has none) and its own font (the account has no font field for the box)."""
    prefs, box = clean_prefs(account), clean_prefs(box)
    prefs["size"] = box["size"]
    if box.get("font") and "font" not in prefs:
        prefs["font"] = box["font"]
    return prefs


def render_style(prefs) -> dict:
    """How the player draws the two subtitle lines on the 1920x1080 OSD: font face and px, wrap width,
    text colour, outline or a box (Enigma2 colour "#aarrggbb", aa = transparency) and the two line tops."""
    style = look(prefs)
    size = (prefs or {}).get("size") if (prefs or {}).get("size") in SIZES else "m"
    px = SIZE_PX[size]
    line_h = px + 18
    bottom = 1080 - int(round(1080 * style["position"] / 100.0)) - 4
    second = max(0, bottom - line_h)
    alpha = max(0, min(255, int(round(255 * (1 - style["box_opacity"] / 100.0)))))
    return {"face": "KanasaBold" if style["font"] == "bold" else "KanasaRegular", "px": px, "line_h": line_h,
            "wrap": WRAP[size], "color": style["color"], "outline": style["background"] == "outline",
            "box": "#%02x000000" % alpha if style["background"] == "box" else None,
            "tops": (second - line_h - 2, second)}


CONTROLS_GAP = 8
CONTROLS_TOP = playerlayout.CONTROLS_TOP
CONTROLS_BOTTOM = CONTROLS_TOP - CONTROLS_GAP        # last subtitle pixel before the controls bar


def above_controls(style) -> tuple:
    """The two line tops while the controls bar shows: the second line ends at CONTROLS_BOTTOM."""
    line_h = style["line_h"]
    return (CONTROLS_BOTTOM - 2 * line_h - 2, CONTROLS_BOTTOM - line_h)


def clean_choice(value, key=None):
    if not isinstance(value, dict) or (key is not None and value.get("key") not in (None, key)):
        return None
    ident, lang = value.get("id"), str(value.get("lang") or "").upper()
    offset = value.get("offset_ms", 0)
    if not isinstance(ident, str) or not ident or not isinstance(offset, int) or isinstance(offset, bool):
        return None
    result = {"key": key or value.get("key"), "id": ident, "lang": lang,
              "offset_ms": max(-600000, min(600000, offset))}
    if ident == "embedded" and isinstance(value.get("track"), str):
        result["track"] = value["track"][:80]
    return result


def track_source(track: dict) -> str:
    source = track.get("source") or "SubDL"
    return _SOURCE.get(source, str(source))


def effective_languages(languages, ui_lang) -> list:
    """The viewer's list, or the website's rule when it is empty: the interface language first, then Arabic
    or English (an empty list must never mean "no subtitles at all")."""
    if languages:
        return [str(code).upper() for code in languages]
    out = []
    for code in ((ui_lang or "").upper(), "AR", "EN"):
        if code and code not in out:
            out.append(code)
    return out


def pick_track(tracks: list, preferred: list, choice=None):
    wanted = clean_choice(choice)
    if wanted:
        found = next((track for track in tracks if track.get("id") == wanted["id"]), None)
        if found:
            return found, wanted["offset_ms"]
    for code in [str(item).upper() for item in preferred or []]:
        found = next((track for track in tracks if str(track.get("lang") or "").upper() == code), None)
        if found:
            return found, 0
    return None, 0


# --- FIX27: which subtitle a play starts with (website subtitles.js mount(): isOn / kanasa:source-group) -------
# The Arabic-subs server group plays hard-subbed videos: every play from it starts OFF, the viewer may turn
# subtitles on for that play only, and that is never written to the saved preference (owner 2026-11-11).
MUTED_GROUP = "arabic_subs"
_ISO3 = {"ara": "AR", "eng": "EN", "fre": "FR", "fra": "FR", "ger": "DE", "deu": "DE", "spa": "ES", "ita": "IT",
         "tur": "TR", "por": "PT", "rus": "RU", "dut": "NL", "nld": "NL", "per": "FA", "fas": "FA", "hin": "HI",
         # in-file tracks name every ISO 639-2 language (exteplayer3 s_l, box 2026-11-19)
         "kor": "KO", "cze": "CS", "ces": "CS", "dan": "DA", "gre": "EL", "ell": "EL", "fin": "FI", "heb": "HE",
         "hrv": "HR", "hun": "HU", "ind": "ID", "jpn": "JA", "may": "MS", "msa": "MS", "nor": "NO", "nob": "NO",
         "pol": "PL", "rum": "RO", "ron": "RO", "slo": "SK", "slk": "SK", "swe": "SV", "tha": "TH", "chi": "ZH",
         "zho": "ZH", "ukr": "UK", "vie": "VI", "urd": "UR", "ben": "BN", "bul": "BG", "srp": "SR", "kur": "KU",
         "tam": "TA", "tel": "TE"}


def muted_group(section) -> bool:
    return section == MUTED_GROUP


def embedded_code(lang) -> str:
    """An in-file track's language ("eng" or "en") as the site's two-letter code, "" when unknown."""
    text = str(lang or "").strip().lower()
    if len(text) == 2 and text.isalpha():
        return text.upper()
    return _ISO3.get(text, "")


def start_rule(section, torrent, enabled, tracks, embedded, preferred, saved=None) -> dict:
    """What a new play's subtitles start as: action "off" (the Arabic-subs group), "track" (a site/external
    file), "embedded" (an in-file track) or None (nothing starts). `automatic` = turned on by this rule alone
    (a torrent while the preference is off): the saved preference is never rewritten for it.
    - Arabic-subs group: off, whatever the preference says.
    - The preference on: the saved choice for this video, else the first preferred language (today's rule).
    - A torrent with the preference off: on when a site/external track is in a preferred language (owner).
    - Spec 081 §2 (FIX32): a torrent with no such site/external track starts with the in-file TEXT track
      (exteplayer3 s_l rows; subtitlelayout.in_video_rows already drops PGS/VobSub) of the first preferred
      language that has one. Never in the Arabic-subs group (above)."""
    out = {"action": None, "row": None, "offset_ms": 0, "automatic": False}
    if muted_group(section):
        out["action"] = "off"
        return out
    tracks, embedded = list(tracks or []), list(embedded or [])
    codes = [str(code).upper() for code in preferred or []]
    wanted = clean_choice(saved)
    if enabled and wanted and wanted["id"] == "embedded":
        row = next((r for r in embedded if r.get("label") == wanted.get("track")), None)
        if row is not None:
            out.update(action="embedded", row=row)
            return out
    if not enabled and not torrent:
        return out
    track, offset_ms = pick_track(tracks, codes, saved)
    if track is not None and not enabled and str(track.get("lang") or "").upper() not in codes:
        track, offset_ms = pick_track(tracks, codes, None)   # an automatic start only in a preferred language
    if track is not None:
        out.update(action="track", row=track, offset_ms=offset_ms, automatic=not enabled)
        return out
    if torrent:
        for code in codes:
            row = next((r for r in embedded if embedded_code(r.get("lang")) == code), None)
            if row is not None:
                out.update(action="embedded", row=row, automatic=not enabled)
                return out
    return out


def group_change(old_section, new_section, active) -> str:
    """A different server now plays (a failover or a hand pick): "keep" what shows, "off" (into the
    Arabic-subs group: like every play from it, it starts off), or "reset" (turn off and apply start_rule
    again: leaving that group, or nothing shows yet)."""
    if old_section == new_section:
        return "keep"
    if muted_group(new_section):
        return "off"
    if muted_group(old_section) or not active:
        return "reset"
    return "keep"


def format_offset(seconds: float) -> str:
    value = round(float(seconds or 0) * 2) / 2.0
    return "%+.1f s" % value if value else "0.0 s"


def panel_status(value) -> str:
    return value if value in STATUSES else "unavailable"


def torrent_release(candidate):
    row = candidate.get("torrent") if isinstance(candidate, dict) else None
    if not isinstance(row, dict):
        return None
    value = row.get("filename") or row.get("name")
    return value.strip()[:300] if isinstance(value, str) and value.strip() else None


# Explicit aliases make the pure API self-describing to callers and tests.
parse_webvtt = parse_vtt
offset_cues = apply_offset
wrap_lines = wrap_text
