"""Every screen's skin XML, built from theme + layout (no Enigma2 imports; tested off the box).

A "frame" (focus, active tab, focused button/row) is an accent rounded Label one zPosition below the widget
it surrounds, theme.FRAME px larger on each side: the box draws borderWidth as a text outline.
"""
from . import episodeslayout, layout, panellayout, playerlayout, profiles, subtitlelayout, tabs, theme, titlelayout

T = theme


def _attrs(attrs: dict) -> str:
    return "".join(' %s="%s"' % (key, value) for key, value in attrs.items() if value is not None)


def _w(name, left, top, width, height, rtl=False, **attrs) -> str:
    return '<widget name="%s" position="%d,%d" size="%d,%d"%s />' % (
        name, layout.x(left, width, rtl), top, width, height, _attrs(attrs))


def _text(name, left, top, width, height, lang, size=28, weight="regular", color=None, bg=None,
          align=None, valign="center", radius=None, z=None, wrap=True, rtl=None) -> str:
    rtl = lang == "ar" if rtl is None else rtl
    return _w(name, left, top, width, height, rtl, font=T.font(weight, size, lang),
              foregroundColor=color or T.TEXT, backgroundColor=bg, transparent=None if bg else "1",
              halign=align or ("right" if rtl else "left"), valign=valign,
              cornerRadius=radius, zPosition=z, noWrap=None if wrap else "1")


def _box(name, left, top, width, height, lang, color, radius=None, z=None, gradient=None, rtl=None) -> str:
    rtl = lang == "ar" if rtl is None else rtl
    return _w(name, left, top, width, height, rtl, backgroundColor=None if gradient else color,
              backgroundGradient=gradient, cornerRadius=radius, zPosition=z)


def _screen(name: str, parts: list) -> str:
    return ('<screen name="%s" position="0,0" size="1920,1080" flags="wfNoBorder" backgroundColor="%s">%s</screen>'
            % (name, T.BG, "".join(parts)))


# --- Home / Search ------------------------------------------------------------------------------------
HOME_WIDGETS = {"logo", "status", "tabframe", "row0", "row1", "bar0", "bar1", "focus", "profileinitial",
                "prostar", "profileavatar"} | \
    {"tab%d" % i for i in range(len(tabs.TABS))} | \
    {"%s%dc%d" % (k, r, c) for k in "rtsbwp" for r in range(layout.ROWS) for c in range(layout.COLS)}


def home(lang: str) -> str:
    rtl = lang == "ar"
    f = T.FRAME
    parts = [_w("logo", 60, layout.TAB_Y, 64, 64, rtl, alphatest="blend", scale="1"),
             _w("tabframe", 0, layout.TAB_Y - f, layout.TAB_W + 2 * f, layout.TAB_H + 2 * f, False,
                backgroundColor=T.ACCENT, cornerRadius=T.RADIUS_PILL + f, zPosition="0")]
    for index, ((tab_id, _), (box_x, box_w)) in enumerate(zip(tabs.TABS, layout.tab_boxes(rtl))):
        if tab_id in tabs.ICONS:        # opaque PNG with the focus ring baked in (live TV shows through alpha)
            parts.append(_w("tab%d" % index, box_x, layout.ICON_Y, box_w, box_w, False, alphatest="off",
                            zPosition="1"))
            continue
        parts.append('<widget name="tab%d" position="%d,%d" size="%d,%d"%s />' % (
            index, box_x, layout.TAB_Y, box_w, layout.TAB_H, _attrs({
                "font": T.font("bold", 24, lang), "foregroundColor": T.DIM, "backgroundColor": T.BG,
                "halign": "center", "valign": "center", "cornerRadius": T.RADIUS_PILL, "zPosition": "1"})))
    # signed in (FIX11): the account's initial on the accent avatar; Pro: a gold star on its corner
    profile = [i for i, (tab_id, _) in enumerate(tabs.TABS) if tab_id == "profile"][0]
    box_x, box_w = layout.tab_boxes(rtl)[profile]
    # 084: the current profile's colour inside the avatar ring (hidden while profiles are off)
    inset = 8
    parts.append(_w("profileavatar", box_x + inset, layout.ICON_Y + inset, box_w - 2 * inset, box_w - 2 * inset,
                    False, backgroundColor=profiles.PALETTE[0], cornerRadius=(box_w - 2 * inset) // 2,
                    zPosition="2"))
    parts.append(_text("profileinitial", box_x, layout.ICON_Y, box_w, box_w, lang, size=30, weight="bold",
                       color=T.WHITE, align="center", z="3", wrap=False, rtl=False))
    parts.append(_w("prostar", box_x + box_w - 26, layout.ICON_Y - 6, 30, 30, False, alphatest="blend", scale="1",
                    zPosition="4"))
    parts.append(_text("status", *layout.HOME_STATUS, lang, size=22, color=T.ACCENT, z="3", wrap=False))
    for r in range(layout.ROWS):
        label_y = layout.row_label_y(r)
        parts.append(_box("bar%d" % r, 96, label_y + 6, 6, 30, lang, T.ACCENT))
        parts.append(_text("row%d" % r, *layout.row_label_box(r), lang, size=30, weight="bold"))
        title_y, sub_y = layout.card_text_y(r)
        for c in range(layout.COLS):
            box = layout.card_box(r, c, False)
            left, top, width, height = box["x"], box["y"], box["w"], box["h"]
            parts.append(_w("r%dc%d" % (r, c), left, top, width, height, rtl, alphatest="blend", scale="1",
                            zPosition="1"))
            parts.append(_w("b%dc%d" % (r, c), left + width - 92, top + 8, 84, 30, rtl,
                            font=T.font("bold", 18, lang), foregroundColor=T.TEXT, backgroundColor=T.SURFACE,
                            halign="center", valign="center", cornerRadius=T.RADIUS_CHIP, zPosition="2"))
            parts.append(_w("w%dc%d" % (r, c), left + width - 128, top + height - 42, 120, 30, rtl,
                            font=T.font("bold", 18, lang), foregroundColor=T.ACCENT, backgroundColor=T.BG,
                            halign="center", valign="center", cornerRadius=T.RADIUS_CHIP, zPosition="2"))
            parts.append(_w("p%dc%d" % (r, c), left, top + height - 6, width, 6, rtl, backgroundColor=T.ACCENT,
                            zPosition="2"))
            parts.append(_text("t%dc%d" % (r, c), left, title_y, width, 32, lang, size=24, weight="bold",
                               wrap=False))
            parts.append(_text("s%dc%d" % (r, c), left, sub_y, width, 28, lang, size=20, color=T.DIM, wrap=False))
    first = layout.card_box(0, 0, False)
    parts.append(_w("focus", first["x"] - f, first["y"] - f, first["w"] + 2 * f, first["h"] + 2 * f, rtl,
                    backgroundColor=T.ACCENT, cornerRadius=T.RADIUS_CARD + f, zPosition="0"))
    return _screen("KanasaHome", parts)


# --- Person / genre browse ---------------------------------------------------------------------------
BROWSE_WIDGETS = {"back", "backframe", "title", "status", "focus",
                  "pphoto", "pname", "pborn", "pbio", "pfilm"} | \
    {"%s%dc%d" % (kind, row, col) for kind in "rtsbwp" for row in range(layout.ROWS)
     for col in range(layout.COLS)}


def browse(lang: str) -> str:
    rtl = lang == "ar"
    parts = [
        _text("back", 72, 34, 150, 58, lang, size=22, weight="bold", bg=T.SURFACE,
              align="center", radius=T.RADIUS_PILL, z="1"),
        _w("backframe", 72 - T.FRAME, 34 - T.FRAME, 150 + 2 * T.FRAME, 58 + 2 * T.FRAME, rtl,
           backgroundColor=T.ACCENT, cornerRadius=T.RADIUS_PILL + T.FRAME, zPosition="0"),
        _text("title", 250, 34, 1450, 64, lang, size=42, weight="bold"),
        _text("status", 96, 112, 1728, 44, lang, size=26, color=T.ACCENT),
        _w("focus", 92, 148, layout.POSTER[0] + 8, layout.POSTER[1] + 8, rtl,
           backgroundColor=T.ACCENT, cornerRadius=T.RADIUS_CARD + T.FRAME, zPosition="0"),
        # person header (app TvPersonHeader): photo, name, born line, 4-5 line bio; posters below in row 2
        _w("pphoto", 96, 120, 240, 360, rtl, alphatest="blend", scale="1", zPosition="1"),
        _text("pname", 372, 120, 1452, 60, lang, size=44, weight="bold", wrap=False),
        _text("pborn", 372, 186, 1452, 36, lang, size=24, color=T.DIM, wrap=False),
        _text("pbio", 372, 236, 1452, 244, lang, size=24, valign="top"),
        _text("pfilm", 96, 548, 1000, 48, lang, size=30, weight="bold")]
    tops = (152, 614)
    for row, top in enumerate(tops):
        for col in range(layout.COLS):
            left = layout.LEFT + col * layout.PITCH
            slot = "%dc%d" % (row, col)
            parts += [_w("r" + slot, left, top, *layout.POSTER, rtl, alphatest="blend", scale="1", zPosition="1"),
                      _text("t" + slot, left, top + 338, layout.POSTER[0], 32, lang, size=22, weight="bold",
                            wrap=False),
                      _text("s" + slot, left, top + 372, layout.POSTER[0], 28, lang, size=18, color=T.DIM,
                            wrap=False),
                      _text("b" + slot, left + 128, top + 8, 84, 28, lang, size=16, weight="bold",
                            bg=T.SURFACE, align="center", radius=T.RADIUS_CHIP, z="2", wrap=False),
                      _text("w" + slot, left + 100, top + 292, 112, 28, lang, size=16, weight="bold",
                            color=T.ACCENT, bg=T.BG, align="center", radius=T.RADIUS_CHIP, z="2", wrap=False),
                      _box("p" + slot, left, top + 324, layout.POSTER[0], 6, lang, T.ACCENT, z="2")]
    return _screen("KanasaBrowse", parts)


# --- Title ---------------------------------------------------------------------------------------------
TITLE_WIDGETS = {"backdrop", "shade", "header", "backpill", "back", "backicon", "backframe", "logo", "title", "meta", "chip0", "chip1",
                 "chip2", "chipframe", "overview", "btnframe", "casttitle", "castframe", "recbar",
                 "rectitle", "recframe"} | {"%s%d" % (kind, i) for kind in ("btnbase", "btn", "btnicon")
                                             for i in range(3)} | {"%s%d" % (kind, i) for kind in ("cphoto", "cname", "cchar")
                                             for i in range(11)} | \
    {"%s%d" % (kind, i) for kind in ("rposter", "rtitle", "rrate", "rkind", "ryear")
     for i in range(titlelayout.RECOMMENDATION_SLOTS)}
BUTTON_Y, BUTTON_H, BUTTON_W, BUTTON_GAP = 354, 59, 230, 16
CHIP_X, CHIP_Y, CHIP_W, CHIP_H, CHIP_GAP = 96, 261, 230, 32, 12
TITLE_CHARS = 44


def title(lang: str) -> str:
    rtl = lang == "ar"
    f = T.FRAME
    # the fade is baked into the backdrop file (posters shade=): a transparent widget pixel shows live TV
    parts = [_box("shade", 0, 0, 1920, 1080, lang, T.BG, z="0"),
             _w("backdrop", 0, 0, 1920, 1080, False, alphatest="off", scale="1", zPosition="1"),
             _box("header", 0, 0, 1920, 112, lang, T.BG, z="2"),
             _box("backpill", 96, 26, 120, 52, lang, T.SURFACE, radius=T.RADIUS_PILL, z="3"),
             _text("back", 142, 26, 66, 52, lang, size=20, weight="bold", bg=T.SURFACE, z="4", wrap=False),
             _w("backframe", 93, 23, 126, 58, rtl, backgroundColor=T.ACCENT,
                cornerRadius=T.RADIUS_PILL + 3, zPosition="2"),
             _w("backicon", 106, 34, 36, 36, rtl, alphatest="off", scale="1", zPosition="4"),
             _w("logo", 220, 26, 52, 52, rtl, alphatest="off", scale="1", zPosition="3"),
             _text("title", 96, 154, 1100, 58, lang, size=46, weight="bold", z="3", wrap=False),
             _text("meta", 96, 214, 1100, 34, lang, size=20, weight="bold", color=T.TEXT, z="3")]
    parts.append(_w("chipframe", CHIP_X - 3, CHIP_Y - 3, CHIP_W + 6, CHIP_H + 6, rtl,
                    backgroundColor=T.ACCENT, cornerRadius=T.RADIUS_CHIP + 3, zPosition="2"))
    for index in range(3):             # widths follow the text at run time (title.size_chips)
        parts.append(_text("chip%d" % index, CHIP_X + index * (CHIP_W + CHIP_GAP), CHIP_Y, CHIP_W, CHIP_H, lang,
                           size=18, weight="bold", bg=T.CHIP, align="center", radius=T.RADIUS_CHIP, z="3", wrap=False))
    parts.append(_text("overview", 96, 306, 1100, 34, lang, size=20, valign="top", z="3", wrap=False))
    parts.append(_w("btnframe", 96 - f, BUTTON_Y - f, BUTTON_W + 2 * f, BUTTON_H + 2 * f, rtl,
                    backgroundColor=T.ACCENT, cornerRadius=T.RADIUS_PILL + f, zPosition="3"))
    for index in range(3):
        left = 96 + index * (BUTTON_W + BUTTON_GAP)
        parts += [_box("btnbase%d" % index, left - 2, BUTTON_Y - 2, BUTTON_W + 4, BUTTON_H + 4, lang,
                       "#c8ccd6", radius=T.RADIUS_PILL + 2, z="3"),
                  _text("btn%d" % index, left, BUTTON_Y, BUTTON_W, BUTTON_H, lang, size=20, weight="bold",
                        bg=T.SURFACE, align="center", radius=T.RADIUS_PILL, z="4", wrap=False),
                  _w("btnicon%d" % index, left + 16, BUTTON_Y + 11, 36, 36, rtl, alphatest="off", scale="1",
                     zPosition="5")]
    TL = titlelayout
    parts += [_text("casttitle", 96, 448, 700, 42, lang, size=28, weight="bold", z="3"),
              _w("castframe", TL.CAST_X - 4, TL.CAST_Y - 4, TL.CAST_SIZE + 8, TL.CAST_SIZE + 8, rtl,
                 backgroundColor=T.ACCENT, cornerRadius=TL.CAST_SIZE // 2 + 4, zPosition="2")]
    for i in range(TL.CAST_SLOTS):
        left = TL.CAST_X + i * TL.CAST_STEP
        pad = (TL.CAST_STEP - 8 - TL.CAST_SIZE) // 2           # the name may be wider than the photo
        parts += [_w("cphoto%d" % i, left, TL.CAST_Y, TL.CAST_SIZE, TL.CAST_SIZE, rtl, alphatest="blend",
                     scale="1", zPosition="3"),
                  _text("cname%d" % i, left - pad, 616, TL.CAST_STEP - 8, 42, lang, size=17, weight="bold",
                        align="center", valign="top", z="3"),
                  _text("cchar%d" % i, left - pad, 658, TL.CAST_STEP - 8, 24, lang, size=15, color=T.DIM,
                        align="center", z="3", wrap=False)]
    rw, rh = TL.REC_SIZE
    parts += [_box("recbar", 96, 706, 4, 24, lang, T.ACCENT, z="3"),
              _text("rectitle", 110, 696, 700, 42, lang, size=24, weight="bold", z="3"),
              _w("recframe", TL.REC_X - 4, TL.REC_Y - 4, rw + 8, rh + 8, rtl, backgroundColor=T.ACCENT,
                 cornerRadius=T.RADIUS_CARD + T.FRAME, zPosition="2")]
    for i in range(TL.RECOMMENDATION_SLOTS):
        left = TL.REC_X + i * TL.REC_STEP
        parts += [_w("rposter%d" % i, left, TL.REC_Y, rw, rh, rtl, alphatest="blend", scale="1", zPosition="3"),
                  _text("rtitle%d" % i, left, TL.REC_Y + rh + 4, rw, 28, lang, size=18, weight="bold", z="3",
                        wrap=False),
                  _text("rrate%d" % i, left + 8, TL.REC_Y + 8, 66, 24, lang, size=14, weight="bold", bg=T.SURFACE,
                        align="center", radius=T.RADIUS_CHIP, z="4", wrap=False),
                  _text("rkind%d" % i, left + rw - 74, TL.REC_Y + 8, 66, 24, lang, size=14, weight="bold",
                        bg=T.SURFACE, align="center", radius=T.RADIUS_CHIP, z="4", wrap=False),
                  # owner 2026-11-21: the year badge like the Home cards' (bottom corner of the poster)
                  _text("ryear%d" % i, left + 8, TL.REC_Y + rh - 32, 66, 24, lang, size=14, weight="bold",
                        bg=T.SURFACE, align="center", radius=T.RADIUS_CHIP, z="4", wrap=False)]
    return _screen("KanasaTitle", parts)


# --- Episodes ------------------------------------------------------------------------------------------
EP_BTN_X, EP_BTN_W, EP_BTN_H, EP_BTN_DY = (episodeslayout.BUTTON_X, episodeslayout.BUTTON_W,
                                          episodeslayout.BUTTON_H, episodeslayout.BUTTON_DY)
EPISODES_WIDGETS = {"backpill", "back", "backicon", "title", "meta", "seasonframe", "seasonselect", "seasonselected", "seasonarrow",
                    "epframe", "season_download", "epbtnframe"} | {"%s%d" % (kind, i) for kind in ("seasonbox", "season", "seasoncount") for i in range(8)} | \
    {"%s%d" % (kind, i) for kind in ("epborder", "epcard", "still", "eptitle", "epdate", "epoverview", "watched", "watchbtn", "downloadbtn",
                                      "ondisk")
     for i in range(4)}


def episodes(lang: str) -> str:
    rtl = lang == "ar"
    parts = [_box("backpill", 72, 44, 132, 54, lang, T.SURFACE, radius=T.RADIUS_PILL),
             _text("back", 118, 44, 76, 54, lang, size=22, weight="bold", bg=T.SURFACE, wrap=False),
             _w("backicon", 82, 53, 36, 36, rtl, alphatest="off", scale="1", zPosition="1"),
                                      _text("title", 72, 125, 1200, 50, lang, size=40, weight="bold"),
                                      _text("meta", 72, 177, 900, 34, lang, size=20, color=T.DIM),
                                      _text("season_download", 1010, 177, 740, 34, lang, size=20,
                                            weight="bold", color=T.ACCENT, align="right" if not rtl else "left"),
                                      _w("seasonframe", 69, 226, 546, 64, rtl, backgroundColor=T.ACCENT,
                                         cornerRadius=T.RADIUS_CARD + 3, zPosition="0"),
                                      _w("seasonselect", 69, 226, 546, 64, rtl, backgroundColor=T.ACCENT,
                                         cornerRadius=T.RADIUS_CARD + 3, zPosition="0"),
                                      _box("seasonselected", 72, 229, 540, 58, lang, T.SELECTED,
                                           radius=T.RADIUS_CARD, z="2"),
                                      _w("seasonarrow", 536, 224, 68, 68, rtl, alphatest="off", scale="1",
                                         zPosition="4"),
                                      _w("epframe", 643, 226, 1110, 186, rtl, backgroundColor=T.EPISODE_FOCUS,
                                         cornerRadius=T.RADIUS_CARD + 3, zPosition="0"),
                                      _w("epbtnframe", EP_BTN_X - T.FRAME, 229, EP_BTN_W + 2 * T.FRAME,
                                         EP_BTN_H + 2 * T.FRAME, rtl, backgroundColor=T.ACCENT,
                                         cornerRadius=T.RADIUS_PILL + T.FRAME, zPosition="2")]
    for i in range(8):
        top = 229 + i * 70
        parts += [_box("seasonbox%d" % i, 72, top, 540, 58, lang, T.SURFACE, radius=T.RADIUS_CARD, z="1"),
                  _text("season%d" % i, 90, top, 330, 58, lang, size=20, weight="bold", z="3", wrap=False),
                  _text("seasoncount%d" % i, 420, top, 174, 58, lang, size=15, color=T.DIM, z="3",
                        align="right" if not rtl else "left", wrap=False)]
    for i in range(4):
        top = 229 + i * 198
        parts += [_box("epborder%d" % i, 645, top - 1, 1106, 182, lang, T.BORDER, radius=T.RADIUS_CARD + 1, z="0"),
                  _box("epcard%d" % i, 646, top, 1104, 180, lang, T.SURFACE, radius=T.RADIUS_CARD, z="1"),
                  _w("still%d" % i, 660, top + 13, 270, 152, rtl, alphatest="blend", scale="1", zPosition="2"),
                  _text("eptitle%d" % i, 952, top + 13, 530, 42, lang, size=22, weight="bold", z="2", wrap=False),
                  _text("epdate%d" % i, 952, top + 55, 300, 28, lang, size=16, color=T.DIM, z="2", wrap=False),
                  # FIX30: a copy on the box's disk (green text, never the watched check)
                  _text("ondisk%d" % i, 1262, top + 55, 220, 28, lang, size=16, weight="bold", color=T.GREEN,
                        align="right" if not rtl else "left", z="2", wrap=False),
                  _text("epoverview%d" % i, 952, top + 84, 530, 72, lang, size=18, color=T.DIM, valign="top",
                        z="2"),
                  # FIX9: the Watched toggle and, under it, the Download button (episodeslayout.button_box)
                  _text("watchbtn%d" % i, EP_BTN_X, top + EP_BTN_DY[0], EP_BTN_W, EP_BTN_H, lang, size=18,
                        weight="bold", bg=T.CHIP, align="center", radius=T.RADIUS_PILL, z="3", wrap=False),
                  _text("downloadbtn%d" % i, EP_BTN_X, top + EP_BTN_DY[1], EP_BTN_W, EP_BTN_H, lang, size=18,
                        weight="bold", bg=T.CHIP, align="center", radius=T.RADIUS_PILL, z="3", wrap=False),
                  _w("watched%d" % i, 668, top + 20, 52, 52, False, alphatest="blend", scale="1", zPosition="4")]
    return _screen("KanasaEpisodes", parts)


# --- Player --------------------------------------------------------------------------------------------
PLAYER_CARD = ("card", "cardtitle", "cardstatus", "cardrow", "cardquality", "cardserver", "cardsecs", "cardhint",
               "cardbtnframe", "cardbtn0", "cardbtn1", "cardbtn2",     # FIX26: the no-servers card's buttons
               "cardinfo", "cardpickframe", "cardpick")                 # spec 081: "Pick your server" (+ cardpickicon)
PLAYER_RING = ("ringtop", "ringbottom", "ringleft", "ringright")       # the timeline's focus ring
PLAYER_WIDGETS = {"dimtop", "dimbottom", "topbox", "topframe", "top", "topicon", "badge", "elapsed", "total",
                  "track", "buffered", "fill", "head", "btnframe", "paused", "upnext", "toast", "subline1", "subline2",
                  "subbox1", "subbox2", "cardpickicon"} | set(PLAYER_CARD) | \
                 set(PLAYER_RING) | \
                 {"%s_%s" % (kind, name) for kind in ("btn", "btnicon")
                  for name in ("next", "episodes", "subtitles", "audio", "aspect", "quality", "servers")}
PLAYER_BUTTONS = ("next", "episodes", "subtitles", "audio", "aspect", "quality", "servers")
PLAYER_BTN_X, PLAYER_BTN_Y, PLAYER_BTN_W, PLAYER_BTN_H = (playerlayout.BUTTON_X, playerlayout.BUTTON_Y,
                                                            playerlayout.BUTTON_W, playerlayout.BUTTON_H)
PLAYER_BTN_POS = playerlayout.BUTTON_POS
TRACK_X, TRACK_W, TRACK_Y = playerlayout.TRACK_X, playerlayout.TRACK_W, playerlayout.TRACK_Y


def player(lang: str) -> str:
    rtl = lang == "ar"
    ring_y0, ring_y1 = TRACK_Y - 52, TRACK_Y + 52
    parts = [_box("dimtop", 0, 0, 1920, 138, lang, "#4d0b0d14", z="0", rtl=False),
             _box("dimbottom", 0, playerlayout.CONTROLS_TOP, 1920, 1080 - playerlayout.CONTROLS_TOP, lang,
                  "#4d0b0d14", z="0", rtl=False),
             # the search card (searchcard.py): opaque text on opaque boxes, above everything
             _box("card", playerlayout.CARD_X, playerlayout.CARD_Y, playerlayout.CARD_W, playerlayout.CARD_H, lang,
                  T.SURFACE, radius=T.RADIUS_CARD + 8, z="6", rtl=rtl),
             _text("cardtitle", 480, 360, 960, 56, lang, size=38, weight="bold", bg=T.SURFACE, align="center",
                   z="7", rtl=rtl, wrap=False),
             _text("cardstatus", 480, 424, 960, 44, lang, size=28, color=T.DIM, bg=T.SURFACE, align="center",
                   z="7", rtl=rtl, wrap=False),
             _box("cardrow", 480, 492, 960, 140, lang, T.BG, radius=T.RADIUS_CARD, z="7", rtl=rtl),
             _text("cardquality", 512, 510, 700, 50, lang, size=34, weight="bold", bg=T.BG, z="8", rtl=rtl,
                   wrap=False),
             _text("cardserver", 512, 566, 880, 44, lang, size=28, color=T.DIM, bg=T.BG, z="8", rtl=rtl,
                   wrap=False),
             _text("cardsecs", 1240, 510, 168, 50, lang, size=28, color=T.DIM, bg=T.BG,
                   align="left" if rtl else "right", z="8", rtl=rtl, wrap=False),
             _text("cardhint", 480, 652, 960, 50, lang, size=26, color=T.ACCENT, bg=T.SURFACE, align="center",
                   z="7", rtl=rtl, wrap=False),
             # FIX26: the no-servers card's real buttons (Try again / Report / Back); white ring = focus
             _w("cardbtnframe", playerlayout.card_button_x(0, 3) - 3, playerlayout.CARD_BTN_Y - 3,
                playerlayout.CARD_BTN_W + 6, playerlayout.CARD_BTN_H + 6, False, backgroundColor=T.WHITE,
                cornerRadius=T.RADIUS_PILL + 3, zPosition="7"),
             ] + [_text("cardbtn%d" % i, playerlayout.card_button_x(i, 3), playerlayout.CARD_BTN_Y,
                        playerlayout.CARD_BTN_W, playerlayout.CARD_BTN_H, lang, size=28, weight="bold", bg=T.CHIP,
                        align="center", radius=T.RADIUS_PILL, z="8", rtl=False, wrap=False)
                  for i in range(playerlayout.CARD_BTN_SLOTS)] + [
             # spec 081: the searching card's info line and its one focused Servers button (white ring = focus)
             _text("cardinfo", playerlayout.CARD_INFO_X, 494, playerlayout.CARD_INFO_W, playerlayout.CARD_INFO_H,
                   lang, size=24, color=T.DIM, bg=T.SURFACE, align="center", z="7", rtl=rtl, wrap=False),
             _w("cardpickframe", playerlayout.card_button_x(0, 1) - 3, 543, playerlayout.CARD_BTN_W + 6,
                playerlayout.CARD_BTN_H + 6, False, backgroundColor=T.WHITE, cornerRadius=T.RADIUS_PILL + 3,
                zPosition="7"),
             _text("cardpick", playerlayout.card_button_x(0, 1), 546, playerlayout.CARD_BTN_W, playerlayout.CARD_BTN_H,
                   lang, size=28, weight="bold", bg=T.ACCENT, align="center", radius=T.RADIUS_PILL, z="8",
                   rtl=False, wrap=False),             # focused like the title page's Play (owner 11-21: show the hover)
             _w("cardpickicon", playerlayout.card_pick_icon_x(rtl), 546 + (playerlayout.CARD_BTN_H - 36) // 2, 36, 36,
                False, alphatest="off", scale="1", zPosition="9"),
             _text("toast", 560, 56, 800, 72, lang, size=28, weight="bold", bg=T.SURFACE, align="center",
                   radius=T.RADIUS_PILL, z="6", rtl=rtl, wrap=False),
             _w("subbox1", 160, 884, 1600, 56, False, backgroundColor="#66000000", cornerRadius=str(T.RADIUS_CHIP),
                zPosition="4"),
             _w("subbox2", 160, 942, 1600, 56, False, backgroundColor="#66000000", cornerRadius=str(T.RADIUS_CHIP),
                zPosition="4"),
             _w("subline1", 160, 884, 1600, 56, False, font=T.font("regular", 38, lang),
                foregroundColor=T.WHITE, transparent="1", halign="center", valign="center", noWrap="1",
                borderWidth="4", borderColor="#ff000000", zPosition="5"),
             _w("subline2", 160, 942, 1600, 56, False, font=T.font("regular", 38, lang),
                foregroundColor=T.WHITE, transparent="1", halign="center", valign="center", noWrap="1",
                borderWidth="4", borderColor="#ff000000", zPosition="5"),
             _box("ringtop", 24, ring_y0, 1872, 3, lang, T.ACCENT, z="4", rtl=rtl),
             _box("ringbottom", 24, ring_y1 - 3, 1872, 3, lang, T.ACCENT, z="4", rtl=rtl),
             _box("ringleft", 24, ring_y0, 3, ring_y1 - ring_y0, lang, T.ACCENT, z="4", rtl=rtl),
             _box("ringright", 1893, ring_y0, 3, ring_y1 - ring_y0, lang, T.ACCENT, z="4", rtl=rtl),
             _w("topframe", 45, 53, 354, 86, rtl, backgroundColor=T.ACCENT, cornerRadius=T.RADIUS_PILL + 3,
                zPosition="1"),
             _box("topbox", 48, 56, 348, 80, lang, T.SURFACE, radius=T.RADIUS_PILL, z="2", rtl=rtl),
             _text("top", 112, 56, 150, 80, lang, size=28, weight="bold", bg=T.SURFACE, align="center",
                   z="3", rtl=rtl, wrap=False),
             _w("topicon", 64, 78, 36, 36, rtl, alphatest="off", scale="1", zPosition="4"),
             _text("badge", 270, 72, 98, 48, lang, size=24, weight="bold", bg="#2a2f3e", align="center",
                   radius=T.RADIUS_CHIP, z="5", rtl=rtl, wrap=False),
             _text("elapsed", 48, TRACK_Y - 26, 120, 52, lang, size=32, bg="#0b0d14", wrap=False,
                   align="right" if rtl else "left", rtl=rtl),
             _text("total", 1754, TRACK_Y - 26, 116, 52, lang, size=32, bg="#0b0d14",
                   align="left" if rtl else "right", rtl=rtl),
             _box("track", TRACK_X, TRACK_Y, TRACK_W, 4, lang, "#3a3f4c", z="1", rtl=rtl),
             _box("buffered", TRACK_X, TRACK_Y, TRACK_W, 4, lang, "#8a90a0", z="2", rtl=rtl),
             _box("fill", TRACK_X, TRACK_Y, TRACK_W, 4, lang, T.ACCENT, z="3", rtl=rtl),
             _box("head", TRACK_X, TRACK_Y - 41, 6, 86, lang, T.ACCENT, z="4", rtl=rtl),
             _w("btnframe", PLAYER_BTN_X - 3, PLAYER_BTN_Y - 3, PLAYER_BTN_W + 6,
                PLAYER_BTN_H + 6, rtl, backgroundColor=T.ACCENT, cornerRadius=T.RADIUS_PILL + 3,
                zPosition="1")] + [
             _text("btn_" + name, PLAYER_BTN_POS[name], PLAYER_BTN_Y,
                   playerlayout.BUTTON_WIDTHS[name], PLAYER_BTN_H, "en" if lang != "ar" else "ar", size=26,
                   weight="bold", bg="#1c1f2b", align="center", radius=T.RADIUS_PILL, z="2", rtl=rtl)
             for name in PLAYER_BUTTONS] + [
             _w("btnicon_" + name, PLAYER_BTN_POS[name] + 8, PLAYER_BTN_Y + 17, 36, 36, rtl,
                alphatest="off", scale="1", zPosition="3") for name in PLAYER_BUTTONS] + [
             _text("paused", 900, 480, 120, 120, lang, size=52, weight="bold", bg=T.SURFACE, align="center",
                   radius=60, z="3", rtl=rtl),
             _text("upnext", 1300, 820, 580, 110, lang, size=30, weight="bold", bg=T.ACCENT, align="center",
                   radius=T.RADIUS_PILL, rtl=rtl)]
    return ('<screen name="KanasaPlayer" position="0,0" size="1920,1080" flags="wfNoBorder" '
            'backgroundColor="#ff000000">%s</screen>' % "".join(parts))


# --- Settings ------------------------------------------------------------------------------------------
SETTINGS_ROWS = 8           # settings.ROWS
SETTINGS_WIDGETS = {"title", "rowframe", "hint"} | \
    {"%s%d" % (k, i) for k in ("row", "key", "val") for i in range(SETTINGS_ROWS)}
ROW_X, ROW_Y0, ROW_W, ROW_H, ROW_STEP = 96, 220, 1100, 80, 90


def settings(lang: str) -> str:
    rtl = lang == "ar"
    f = T.FRAME
    parts = [_text("title", 96, 90, 1400, 90, lang, size=64, weight="bold"),
             _w("rowframe", ROW_X - f, ROW_Y0 - f, ROW_W + 2 * f, ROW_H + 2 * f, rtl, backgroundColor=T.ACCENT,
                cornerRadius=T.RADIUS_PILL + f, zPosition="0")]
    for index in range(SETTINGS_ROWS):
        top = ROW_Y0 + index * ROW_STEP
        parts.append(_box("row%d" % index, ROW_X, top, ROW_W, ROW_H, lang, T.SURFACE, radius=T.RADIUS_PILL, z="1"))
        parts.append(_text("key%d" % index, ROW_X + 36, top, 760, ROW_H, lang, size=30, weight="bold", z="2"))
        parts.append(_text("val%d" % index, ROW_X + 796, top, 268, ROW_H, lang, size=30, color=T.DIM, z="2",
                           align="left" if rtl else "right"))
    parts.append(_text("hint", 96, ROW_Y0 + SETTINGS_ROWS * ROW_STEP + 10, 1400, 44, lang, size=26, color=T.DIM))
    return _screen("KanasaSettings", parts)


# --- Sign in -------------------------------------------------------------------------------------------
SIGNIN_WIDGETS = {"title", "steps", "qrcard", "qr", "code", "or_type", "status"}


def signin(lang: str) -> str:
    rtl = lang == "ar"
    return _screen("KanasaSignIn", [
        _text("title", 96, 90, 1700, 90, lang, size=64, weight="bold"),
        _text("steps", 96, 190, 1700, 110, lang, size=30, color=T.DIM, valign="top"),
        _box("qrcard", 140, 330, 400, 400, lang, T.WHITE, radius=24, z="0"),
        _w("qr", 160, 350, 360, 360, rtl, alphatest="off", scale="1", zPosition="1"),
        _text("code", 640, 400, 620, 120, lang, size=72, weight="bold", bg=T.SURFACE, align="center",
              radius=T.RADIUS_PILL),
        _text("or_type", 640, 560, 1180, 100, lang, size=30, color=T.DIM, valign="top"),
        _text("status", 96, 800, 1700, 80, lang, size=32, color=T.ACCENT)])


# --- Chooser (resume / servers) ------------------------------------------------------------------------
CHOICE_WIDGETS = {"card", "title", "list"}
CHOICE_BODY_WIDGETS = CHOICE_WIDGETS | {"body"}  # with notes lines (update popup)


def choice(lang: str, rows: int, body_lines: int = 0) -> str:
    # full screen on the page colour: the box hides the screen below a popup, so anything see-through shows TV
    list_height = min(max(rows, 1), 8) * 72
    body_height = min(max(body_lines, 0), 7) * 44
    body_gap = body_height + 20 if body_height else 0
    width, height = 800, list_height + 90 + body_gap
    left, top = (1920 - width) // 2, (1080 - height) // 2
    body = _text("body", left + 20, top + 80, width - 40, body_height, lang, size=28, bg=T.SURFACE,
                 valign="top", z="1") if body_height else ""
    return ('<screen name="KanasaChoice" position="0,0" size="1920,1080" flags="wfNoBorder" '
            'backgroundColor="%s">%s%s%s%s</screen>' % (T.BG,
                _box("card", left - 24, top - 24, width + 48, height + 48, lang, T.SURFACE, radius=T.RADIUS_CARD,
                     rtl=False),
                _text("title", left, top, width, 70, lang, size=34, weight="bold", bg=T.SURFACE, align="center",
                      z="1", rtl=False), body,
                _w("list", left, top + 90 + body_gap, width, list_height, False, font=T.font("bold", 30, lang),
                   itemHeight="72", itemCornerRadius=T.RADIUS_PILL, foregroundColor=T.TEXT,
                   backgroundColor=T.SURFACE, backgroundColorSelected=T.ACCENT, foregroundColorSelected=T.WHITE,
                   scrollbarMode="showOnDemand", zPosition="1")))


# --- Download page and Downloads (FIX9) ------------------------------------------------------------------
DOWNLOAD_PAGE_WIDGETS = {"title", "subtitle", "status", "hint", "navframe", "groupselect", "mainframe"} | \
    {"%s%d" % (kind, i) for kind in ("pillbox", "pill", "pillcount") for i in range(panellayout.DL_PILL_SLOTS)} | \
    {"%s%d" % (kind, i) for kind in ("card", "line1_", "note", "line2_", "line3_", "line4_")
     for i in range(panellayout.DL_CARD_SLOTS)}
DOWNLOADS_WIDGETS = {"title", "space", "status", "how", "list", "poster", "detail", "hint"}


def _list(name, left, top, width, height, lang, size=28) -> str:
    """A full-width list in the Choice look: accent fill on the focused row (visible focus on the TV)."""
    return _w(name, left, top, width, height, lang == "ar", font=T.font("bold", size, lang), itemHeight="64",
              itemCornerRadius=T.RADIUS_PILL, foregroundColor=T.TEXT, backgroundColor=T.BG,
              backgroundColorSelected=T.ACCENT, foregroundColorSelected=T.WHITE, scrollbarMode="showOnDemand",
              zPosition="1")


SERVER_ROWS, SERVER_ROW_X, SERVER_ROW_Y0, SERVER_ROW_W, SERVER_ROW_H, SERVER_ROW_STEP = 10, 96, 200, 1400, 66, 76
SERVER_CHIP_W, SERVER_CHIP_H, SERVER_CHIP_GAP = 72, 54, 12
SERVER_SETTINGS_WIDGETS = {"title", "help", "rowframe"} | \
    {"%s%d" % (kind, i) for kind in ("srow", "skey", "sval") for i in range(SERVER_ROWS)} | \
    {"chip%d_%d" % (i, j) for i in range(SERVER_ROWS) for j in range(4)} | \
    {"chipicon%d" % i for i in range(SERVER_ROWS)}


def server_chip_x(index: int) -> int:
    """Left edge of a group row's ▲ ▼ ★ ⊘ chip (LTR; layout.x mirrors it)."""
    return SERVER_ROW_X + SERVER_ROW_W - 24 - 4 * SERVER_CHIP_W - 3 * SERVER_CHIP_GAP + \
        index * (SERVER_CHIP_W + SERVER_CHIP_GAP)


def server_settings(lang: str) -> str:
    """Settings → Servers (066): Auto switch, group rows with ▲ ▼ ★ ⊘ chips, Reset."""
    rtl, f = lang == "ar", T.FRAME
    parts = [_text("title", 96, 70, 1400, 90, lang, size=64, weight="bold"),
             _w("rowframe", SERVER_ROW_X - f, SERVER_ROW_Y0 - f, SERVER_ROW_W + 2 * f, SERVER_ROW_H + 2 * f, rtl,
                backgroundColor=T.ACCENT, cornerRadius=T.RADIUS_PILL + f, zPosition="0"),
             _text("help", 96, SERVER_ROW_Y0 + SERVER_ROWS * SERVER_ROW_STEP + 10, 1600, 80, lang, size=26,
                   color=T.DIM, valign="top")]
    for i in range(SERVER_ROWS):
        top = SERVER_ROW_Y0 + i * SERVER_ROW_STEP
        parts += [_box("srow%d" % i, SERVER_ROW_X, top, SERVER_ROW_W, SERVER_ROW_H, lang, T.SURFACE,
                       radius=T.RADIUS_PILL, z="1"),
                  _text("skey%d" % i, SERVER_ROW_X + 36, top, 820, SERVER_ROW_H, lang, size=30, weight="bold",
                        z="2", wrap=False),
                  _text("sval%d" % i, SERVER_ROW_X + 880, top, 480, SERVER_ROW_H, lang, size=30, color=T.DIM,
                        align="left" if rtl else "right", z="2", wrap=False)]
        for j in range(4):
            parts.append(_text("chip%d_%d" % (i, j), server_chip_x(j), top + 6, SERVER_CHIP_W, SERVER_CHIP_H,
                               lang, size=30, weight="bold", bg=T.CHIP, align="center", radius=T.RADIUS_CHIP,
                               z="2", wrap=False))
        parts.append(_w("chipicon%d" % i, server_chip_x(3) + 18, top + 15, 36, 36, rtl, alphatest="blend",
                        scale="1", zPosition="4"))
    return _screen("KanasaServerSettings", parts)


def download_page(lang: str) -> str:
    """App 064 TV: pills (Auto + the server groups) on the left, the chosen group's cards on the right."""
    rtl, P = lang == "ar", panellayout
    parts = [_text("title", 96, 60, 1728, 70, lang, size=48, weight="bold", wrap=False),
             _text("subtitle", 96, 132, 1728, 44, lang, size=28, color=T.DIM, wrap=False),
             _text("status", 96, 182, 1728, 44, lang, size=26, color=T.ACCENT, wrap=False),
             _text("hint", 96, 1000, 1728, 40, lang, size=24, color=T.DIM, wrap=False),
             _w("navframe", P.DL_PILL_X - 3, P.DL_PILL_Y - 3, P.DL_PILL_W + 6, P.DL_PILL_H + 6, rtl,
                backgroundColor=T.WHITE, cornerRadius=T.RADIUS_CARD + 3, zPosition="1"),
             _w("groupselect", P.DL_PILL_X - 3, P.DL_PILL_Y - 3, P.DL_PILL_W + 6, P.DL_PILL_H + 6, rtl,
                backgroundColor=T.ACCENT, cornerRadius=T.RADIUS_CARD + 3, zPosition="1"),
             _w("mainframe", P.DL_CARD_X - 3, P.DL_CARD_Y - 3, P.DL_CARD_W + 6, P.DL_CARD_H + 6, rtl,
                backgroundColor=T.WHITE, cornerRadius=T.RADIUS_CARD + 3, zPosition="1")]
    for i in range(P.DL_PILL_SLOTS):
        top = P.DL_PILL_Y + i * P.DL_PILL_STEP
        parts += [_box("pillbox%d" % i, P.DL_PILL_X, top, P.DL_PILL_W, P.DL_PILL_H, lang, T.SURFACE,
                       radius=T.RADIUS_CARD, z="2"),
                  _text("pill%d" % i, P.DL_PILL_X + 28, top, P.DL_PILL_W - 150, P.DL_PILL_H, lang, size=28,
                        weight="bold", bg=T.SURFACE, z="3", wrap=False),
                  _text("pillcount%d" % i, P.DL_PILL_X + P.DL_PILL_W - 120, top, 96, P.DL_PILL_H, lang, size=26,
                        color=T.DIM, bg=T.SURFACE, align="right" if not rtl else "left", z="3", wrap=False)]
    left, width = P.DL_CARD_X + 32, P.DL_CARD_W - 64
    for i in range(P.DL_CARD_SLOTS):
        top = P.DL_CARD_Y + i * P.DL_CARD_STEP
        parts += [_box("card%d" % i, P.DL_CARD_X, top, P.DL_CARD_W, P.DL_CARD_H, lang, T.SURFACE,
                       radius=T.RADIUS_CARD, z="2"),
                  _text("line1_%d" % i, left, top + 8, width - 420, 42, lang, size=32, weight="bold", bg=T.SURFACE,
                        z="3", wrap=False),
                  _text("note%d" % i, left + width - 420, top + 8, 420, 42, lang, size=24, color=T.ACCENT,
                        bg=T.SURFACE, align="right" if not rtl else "left", z="3", wrap=False),
                  _text("line2_%d" % i, left, top + 52, width, 36, lang, size=26, bg=T.SURFACE, z="3", wrap=False),
                  _text("line3_%d" % i, left, top + 90, width, 34, lang, size=24, color=T.DIM, bg=T.SURFACE,
                        z="3", wrap=False),
                  _text("line4_%d" % i, left, top + 126, width, 34, lang, size=24, color=T.DIM, bg=T.SURFACE,
                        z="3", wrap=False)]
    return _screen("KanasaDownloadPage", parts)


SITE_TITLE_WIDGETS = {"title", "meta", "status", "overview", "list", "poster"}


def site_title(lang: str) -> str:
    """A Turkish-site title (018 §5.2): name, year · kind, overview, then Play or the seasons' episodes."""
    rtl = lang == "ar"
    return _screen("KanasaSiteTitle", [
        _text("title", 96, 60, 1240, 70, lang, size=48, weight="bold", wrap=False),
        _text("meta", 96, 132, 1240, 40, lang, size=26, color=T.DIM, wrap=False),
        _text("status", 96, 176, 1240, 40, lang, size=26, color=T.ACCENT, wrap=False),
        _text("overview", 96, 220, 1240, 130, lang, size=26, color=T.DIM, valign="top"),
        _list("list", 96, 370, 1240, 640, lang),
        _w("poster", 1420, 60, 404, 606, rtl, alphatest="blend", scale="1", zPosition="1")])


def downloads(lang: str) -> str:
    rtl = lang == "ar"
    return _screen("KanasaDownloads", [
        _text("title", 96, 60, 1200, 70, lang, size=48, weight="bold", wrap=False),
        _text("space", 96, 132, 1200, 44, lang, size=26, color=T.DIM, wrap=False),
        _text("status", 96, 182, 1200, 44, lang, size=26, color=T.ACCENT, wrap=False),
        _list("list", 96, 240, 1240, 704, lang),
        # FIX30: the empty page's one line on how to download (shown only while the list is empty)
        _text("how", 96, 240, 1240, 44, lang, size=26, color=T.TEXT, wrap=False, z="2"),
        _w("poster", 1420, 240, 404, 520, rtl, alphatest="off", scale="1", zPosition="1"),
        _text("detail", 1400, 780, 444, 160, lang, size=24, color=T.DIM, valign="top"),
        _text("hint", 96, 970, 1728, 44, lang, size=24, color=T.DIM, wrap=False)])


# --- Player side panels --------------------------------------------------------------------------------
PANEL_X, PANEL_W = 576, 1344
SERVERS_WIDGETS = {"dimleft", "panel", "ptitle", "close", "grouphint", "groupstar", "groupmute", "sortfilter",
                   "sortfilterbox", "topframe", "navframe", "mainframe", "groupselect",
                   "groupselected", "grouparrow", "serverplaying", "serverplayingbox", "serverplayicon"} | \
    {"%s%d" % (kind, i) for kind in ("groupbox", "group", "groupcount") for i in range(8)} | \
    {"groupmute%d" % i for i in range(8)} | \
    {"%s%d" % (kind, i) for kind in ("sfoptbox", "sfopt") for i in range(panellayout.OPTION_SLOTS)} | \
    {"%s%d" % (kind, i) for kind in ("server", "quality", "serverlabel", "servermeta", "servertracks", "checked")
     for i in range(panellayout.SERVER_SLOTS)}
PLAYER_EPISODES_WIDGETS = {"dimleft", "panel", "ptitle", "psub", "close", "navframe", "mainframe",
                           "seasonselect", "seasonselected", "seasonarrow"} | \
    {"%s%d" % (kind, i) for kind in ("seasonbox", "season") for i in range(7)} | \
    {"%s%d" % (kind, i) for kind in ("epcard", "still", "eptitle", "epdate", "epoverview", "now",
                                                    "watchedbox", "watched", "ondisk") for i in range(3)}
PANEL_WIDGETS = SERVERS_WIDGETS
SL = subtitlelayout
SUBTITLES_WIDGETS = {"dimleft", "panel", "ptitle", "close", "navframe", "groupselect", "groupselected", "ptoast",
                     "groupplay", "mainframe", "pillframe", "currentring", "playicon"} | \
    {"%s%d" % (kind, i) for kind in ("groupbox", "group", "groupcount") for i in range(SL.GROUP_SLOTS)} | \
    {"%s%d" % (kind, i) for kind in ("card", "ctitle", "csub", "ring") for i in range(SL.ITEM_SLOTS)} | \
    {"pill%d_%d" % (i, k) for i in range(SL.ITEM_SLOTS) for k in range(SL.PILL_SLOTS)} | \
    {"dot%d" % k for k in range(SL.PILL_SLOTS)}


def _panel_screen(name, parts):
    return ('<screen name="%s" position="0,0" size="1920,1080" flags="wfNoBorder" '
            'backgroundColor="#ff000000">%s</screen>' % (name, "".join(parts)))


def servers_panel(lang: str) -> str:
    rtl, SH, P = lang == "ar", panellayout.SERVER_H, panellayout
    parts = [_box("dimleft", 0, 0, PANEL_X, 1080, lang, "#66000000", z="0", rtl=False),
             _box("panel", PANEL_X, 0, PANEL_W, 1080, lang, T.BG, z="0"),
             _text("ptitle", 620, 60, 1000, 60, lang, size=44, weight="bold", bg=T.BG, z="2"),
             _text("groupstar", 1236, 54, 244, 54, lang, size=20, weight="bold", bg=T.SURFACE,
                   align="center", radius=T.RADIUS_CHIP, z="3", wrap=False),
             _text("groupmute", 1490, 54, 264, 54, lang, size=20, weight="bold", bg=T.SURFACE,
                   align="center", radius=T.RADIUS_CHIP, z="3", wrap=False),
             _w("close", 1788, 44, 88, 88, rtl, alphatest="off", scale="1", zPosition="3"),
             # FIX26: the white ring of the focused top-row button (X uses its own close_w.png)
             _w("topframe", 1233, 51, 250, 60, rtl, backgroundColor=T.WHITE, cornerRadius=T.RADIUS_CHIP + 3,
                zPosition="2"),
             _text("grouphint", 620, 1016, 1256, 34, lang, size=23, color=T.DIM, bg=T.BG,
                   align="center", z="3", wrap=False),
             _w("mainframe", 1065, 147, 814, SH + 6, rtl, backgroundColor=T.WHITE,
                cornerRadius=T.RADIUS_CARD + 3, zPosition="1"),
             _w("groupselect", 617, 147, 426, 96, rtl, backgroundColor=T.ACCENT,
                cornerRadius=T.RADIUS_CARD + 3, zPosition="1"),
             _w("navframe", 617, 147, 426, 96, rtl, backgroundColor=T.WHITE,     # FIX26: over the accent ring
                cornerRadius=T.RADIUS_CARD + 3, zPosition="1"),
             # FIX26: the Torrents column's "Sort & filter" drop-down: a header line, then one line per choice
             _box("sortfilterbox", 1068, 150, 808, P.HEADER_H, lang, T.SURFACE, radius=T.RADIUS_CARD, z="2"),
             _text("sortfilter", 1100, 150, 744, P.HEADER_H, lang, size=26, weight="bold", bg=T.SURFACE,
                   z="3", wrap=False),
             _box("groupselected", 620, 150, 420, 90, lang, T.SELECTED, radius=T.RADIUS_CARD, z="2"),
             _w("grouparrow", 964, 161, 68, 68, rtl, alphatest="off", scale="1", zPosition="5"),
             _w("serverplaying", 1065, 147, 814, SH + 6, rtl, backgroundColor=T.ACCENT,
                cornerRadius=T.RADIUS_CARD + 3, zPosition="1"),
             _box("serverplayingbox", 1068, 150, 808, SH, lang, T.SELECTED, radius=T.RADIUS_CARD, z="2"),
             _w("serverplayicon", 1082, 150 + (SH - 68) // 2, 68, 68, rtl, alphatest="off", scale="1",
                zPosition="5")]
    for i in range(panellayout.GROUP_SLOTS):
        top = 150 + i * 108
        parts += [_box("groupbox%d" % i, 620, top, 420, 90, lang, T.SURFACE, radius=T.RADIUS_CARD, z="1"),
                  _text("group%d" % i, 648, top, 280, 90, lang, size=30, weight="bold", bg=T.SURFACE,
                        z="3", wrap=False),
                  _text("groupcount%d" % i, 928, top, 84, 90, lang, size=28, color=T.DIM, bg=T.SURFACE,
                        align="right" if not rtl else "left", z="3", wrap=False),
                  _w("groupmute%d" % i, 884, top + 27, 36, 36, rtl, alphatest="blend", scale="1",
                     zPosition="5")]
    for i in range(P.OPTION_SLOTS):
        top = 222 + i * (P.OPTION_H + P.LINE_GAP)            # placeholders: draw_column() moves each one
        parts += [_box("sfoptbox%d" % i, 1068, top, 808, P.OPTION_H, lang, T.SURFACE, radius=T.RADIUS_CARD,
                       z="2"),
                  _text("sfopt%d" % i, 1100, top, 744, P.OPTION_H, lang, size=24, bg=T.SURFACE, z="3",
                        wrap=False)]
    for i in range(panellayout.SERVER_SLOTS):
        top = panellayout.SERVER_Y + i * panellayout.SERVER_STEP
        parts += [_box("server%d" % i, 1068, top, 808, SH, lang, T.SURFACE, radius=T.RADIUS_CARD, z="1"),
                  _text("quality%d" % i, 1100, top + 8, 720, 38, lang, size=32, weight="bold", bg=T.SURFACE,
                        z="3", wrap=False),
                  _text("serverlabel%d" % i, 1100, top + 46, 720, 40, lang, size=25, color=T.DIM, bg=T.SURFACE,
                        valign="top", z="3", wrap=False),   # FIX29: one line (playernav ellipsis), meta at +96
                  _text("servermeta%d" % i, 1100, top + 96, 744, 28, lang, size=21, color=T.DIM, bg=T.SURFACE,
                        z="3", wrap=False),
                  _text("servertracks%d" % i, 1100, top + 126, 744, 26, lang, size=20, color=T.DIM,
                        bg=T.SURFACE, z="3", wrap=False),
                  _text("checked%d" % i, 1600, top + 126, 244, 26, lang, size=20, color=T.DIM, bg=T.SURFACE,
                        align="right" if not rtl else "left", z="3", wrap=False)]
    return _panel_screen("KanasaServersPanel", parts)


def player_episodes_panel(lang: str) -> str:
    rtl = lang == "ar"
    parts = [_box("dimleft", 0, 0, PANEL_X, 1080, lang, "#66000000", z="0", rtl=False),
             _box("panel", PANEL_X, 0, PANEL_W, 1080, lang, T.BG, z="0"),
             _text("ptitle", 620, 48, 980, 58, lang, size=44, weight="bold", bg=T.BG, z="2"),
             _text("psub", 620, 104, 980, 34, lang, size=22, color=T.DIM, bg=T.BG, z="2"),
             _w("close", 1788, 44, 88, 88, rtl, alphatest="off", scale="1", zPosition="3"),
             _w("navframe", 617, 147, 306, 96, rtl, backgroundColor=T.WHITE,
                cornerRadius=T.RADIUS_CARD + 3, zPosition="1"),
             _w("mainframe", 945, 147, 800, 288, rtl, backgroundColor=T.WHITE,
                cornerRadius=T.RADIUS_CARD + 3, zPosition="1"),
             _w("seasonselect", 617, 147, 306, 96, rtl, backgroundColor=T.ACCENT,
                cornerRadius=T.RADIUS_CARD + 3, zPosition="1"),
             _box("seasonselected", 620, 150, 300, 90, lang, T.SELECTED, radius=T.RADIUS_CARD, z="2"),
             _w("seasonarrow", 844, 161, 68, 68, rtl, alphatest="off", scale="1", zPosition="5")]
    for i in range(7):
        top = 150 + i * 108
        parts += [_box("seasonbox%d" % i, 620, top, 300, 90, lang, T.SURFACE, radius=T.RADIUS_CARD, z="1"),
                  _text("season%d" % i, 648, top, 244, 90, lang, size=30, weight="bold", bg=T.SURFACE,
                        z="3", wrap=False)]
    for i in range(3):
        top = 150 + i * 290
        parts += [_box("epcard%d" % i, 948, top, 794, 282, lang, T.SURFACE, radius=T.RADIUS_CARD, z="2"),
                  _w("still%d" % i, 968, top + 58, 300, 169, rtl, alphatest="blend", scale="1", zPosition="3"),
                  _text("eptitle%d" % i, 1292, top + 18, 426, 72, lang, size=30, weight="bold", bg=T.SURFACE,
                        z="3"),
                  _text("now%d" % i, 1292, top + 92, 180, 38, lang, size=24, weight="bold", bg=T.ACCENT,
                        align="center", radius=T.RADIUS_CHIP, z="3", wrap=False),
                  # FIX31: a copy on the box's disk, as on the title page (green text, never the watched check)
                  _text("ondisk%d" % i, 1484, top + 92, 234, 38, lang, size=24, weight="bold", color=T.GREEN,
                        bg=T.SURFACE, z="3", wrap=False),
                  _text("epdate%d" % i, 1292, top + 134, 426, 34, lang, size=24, color=T.DIM, bg=T.SURFACE,
                        z="3", wrap=False),
                  _text("epoverview%d" % i, 1292, top + 174, 426, 96, lang, size=26, color=T.DIM,
                        bg=T.SURFACE, valign="top", z="3"),
                  _box("watchedbox%d" % i, 1765, top, 1, 1, lang, T.SURFACE, radius=0, z="2"),
                  _w("watched%d" % i, 976, top + 66, 52, 52, False, alphatest="blend", scale="1", zPosition="5")]
    return _panel_screen("KanasaPlayerEpisodesPanel", parts)


def subtitles_panel(lang: str) -> str:
    """FIX20: the website's subtitle panel. Groups like the Servers panel; the right column's cards, pills and
    dots are slots the screen moves and sizes per draw (subtitlelayout places them)."""
    rtl, f = lang == "ar", T.FRAME
    parts = [_box("dimleft", 0, 0, PANEL_X, 1080, lang, "#66000000", z="0", rtl=False),
             _box("panel", PANEL_X, 0, PANEL_W, 1080, lang, T.BG, z="0"),
             _text("ptitle", 620, 60, 1000, 60, lang, size=44, weight="bold", bg=T.BG, z="2"),
             _w("close", 1788, 44, 88, 88, rtl, alphatest="off", scale="1", zPosition="3"),
             # frames first: a later widget on the same zPosition draws over an earlier one
             _w("groupselect", 617, 147, 426, 96, rtl, backgroundColor=T.ACCENT,
                cornerRadius=T.RADIUS_CARD + 3, zPosition="1"),
             _w("navframe", 617, 147, 426, 96, rtl, backgroundColor=T.WHITE,     # FIX26: over the accent ring
                cornerRadius=T.RADIUS_CARD + 3, zPosition="1"),
             _box("groupselected", 620, 150, 420, 90, lang, T.SELECTED, radius=T.RADIUS_CARD, z="2"),
             _w("groupplay", 964, 161, 68, 68, rtl, alphatest="off", scale="1", zPosition="5"),
             _w("mainframe", SL.MAIN_X - 3, SL.MAIN_TOP - 3, SL.MAIN_W + 6, SL.ROW_H[1] + 6, rtl,
                backgroundColor=T.WHITE, cornerRadius=T.RADIUS_CARD + 3, zPosition="1"),
             _w("currentring", SL.MAIN_X - 3, SL.MAIN_TOP - 3, SL.MAIN_W + 6, SL.ROW_H[1] + 6, rtl,
                backgroundColor=T.ACCENT, cornerRadius=T.RADIUS_CARD + 3, zPosition="1"),
             _w("pillframe", SL.MAIN_X + SL.PAD - f, SL.MAIN_TOP + SL.PILL_DY - f, 120 + 2 * f, SL.PILL_H + 2 * f,
                rtl, backgroundColor=T.WHITE, cornerRadius=SL.PILL_H // 2 + f, zPosition="2"),
             _w("playicon", SL.MAIN_X + 14, SL.MAIN_TOP + 11, 68, 68, rtl, alphatest="off", scale="1",
                zPosition="5"),
             # FIX26: the short "Loading the subtitle…" / "Could not load" toast, opaque, over the column
             _text("ptoast", 1068, 1000, 808, 64, lang, size=26, weight="bold", bg=T.CHIP, align="center",
                   radius=T.RADIUS_PILL, z="6", wrap=False)]
    for i in range(SL.GROUP_SLOTS):
        top = 150 + i * 108
        parts += [_box("groupbox%d" % i, 620, top, 420, 90, lang, T.SURFACE, radius=T.RADIUS_CARD, z="1"),
                  _text("group%d" % i, 648, top, 280, 90, lang, size=30, weight="bold", bg=T.SURFACE,
                        z="3", wrap=False),
                  _text("groupcount%d" % i, 928, top, 84, 90, lang, size=28, color=T.DIM, bg=T.SURFACE,
                        align="right" if not rtl else "left", z="3", wrap=False)]
    for i in range(SL.ITEM_SLOTS):
        top = SL.MAIN_TOP + i * (SL.CARD_H // 2)          # placeholders: draw() moves every slot it shows
        parts += [_box("card%d" % i, SL.MAIN_X, top, SL.MAIN_W, SL.ROW_H[1], lang, T.SURFACE,
                       radius=T.RADIUS_CARD, z="1"),
                  _text("ctitle%d" % i, SL.MAIN_X + SL.PAD, top + 8, SL.MAIN_W - 2 * SL.PAD, 74, lang, size=28,
                        weight="bold", bg=T.SURFACE, z="3"),
                  _text("csub%d" % i, SL.MAIN_X + SL.PAD, top + SL.SUB_DY, SL.MAIN_W - 2 * SL.PAD, SL.SUB_H,
                        lang, size=24, color=T.DIM, bg=T.SURFACE, valign="top", z="3"),
                  _w("ring%d" % i, SL.MAIN_X + SL.PAD - 3, top - 3, 126, SL.PILL_H + 6, rtl,
                     backgroundColor=T.ACCENT, cornerRadius=SL.PILL_H // 2 + 3, zPosition="2")]
        for k in range(SL.PILL_SLOTS):
            parts.append(_text("pill%d_%d" % (i, k), SL.MAIN_X + SL.PAD + k * 132, top, 120, SL.PILL_H, lang,
                               size=26, weight="bold", bg=T.CHIP, align="center", radius=SL.PILL_H // 2,
                               z="3", wrap=False))
    for k in range(SL.PILL_SLOTS):
        parts.append(_w("dot%d" % k, SL.MAIN_X + SL.PAD + 16 + k * 132, SL.MAIN_TOP, SL.DOT, SL.DOT, rtl,
                        backgroundColor=T.WHITE, cornerRadius=SL.DOT // 2, zPosition="4"))
    return _panel_screen("KanasaSubtitlesPanel", parts)


def panel(lang: str) -> str:
    return servers_panel(lang)


# --- 084 profiles: "Who's watching?", the PIN pad, Settings -> Profiles, the colour swatches ---------------------
PICKER_WIDGETS = {"title", "hint", "status", "frame"} | \
    {"%s%d" % (kind, i) for kind in ("av", "nm", "lk") for i in range(profiles.MAX_SHOWN)}
PICKER_RING = 8                        # a thick accent ring: the focus must read from the sofa


def profile_picker(lang: str) -> str:
    """Full screen and opaque (live TV would show through a see-through page). The avatars are placed for five;
    the screen moves them to the real count at run time (profiles.avatar_boxes)."""
    rtl, r = lang == "ar", PICKER_RING
    boxes = profiles.avatar_boxes(profiles.MAX_SHOWN, False)
    parts = [_text("title", 96, 190, 1728, 110, lang, size=72, weight="bold", align="center"),
             _text("status", 96, 300, 1728, 56, lang, size=30, color=T.ACCENT, align="center"),
             _w("frame", boxes[0][0] - r, profiles.AVATAR_Y - r, profiles.AVATAR + 2 * r, profiles.AVATAR + 2 * r,
                False, backgroundColor=T.ACCENT, cornerRadius=T.RADIUS_CARD * 3 + r, zPosition="0")]
    for i, (left, top, width, height) in enumerate(boxes):
        parts += [_text("av%d" % i, left, top, width, height, lang, size=110, weight="bold", color=T.WHITE,
                        bg=profiles.PALETTE[i % len(profiles.PALETTE)], align="center", radius=T.RADIUS_CARD * 3,
                        z="1", wrap=False, rtl=False),
                  _text("nm%d" % i, left - 26, top + height + 24, width + 52, 50, lang, size=32, weight="bold",
                        align="center", wrap=False, rtl=False),
                  _text("lk%d" % i, left - 26, top + height + 78, width + 52, 40, lang, size=24, color=T.DIM,
                        align="center", wrap=False, rtl=False)]
    parts.append(_text("hint", 96, 960, 1728, 48, lang, size=26, color=T.DIM, align="center"))
    return _screen("KanasaProfilePicker", parts)


PAD_KEY_W, PAD_KEY_H, PAD_GAP, PAD_TOP = 150, 96, 22, 452
PAD_LEFT = (1920 - (profiles.PAD_COLS * PAD_KEY_W + (profiles.PAD_COLS - 1) * PAD_GAP)) // 2
PIN_WIDGETS = {"title", "avatar", "masked", "status", "hint", "padframe"} | \
    {"key%d" % i for i in range(len(profiles.PAD_KEYS))}


def pad_key_box(index: int, rtl: bool) -> tuple:
    row, col = divmod(index, profiles.PAD_COLS)
    left = PAD_LEFT + col * (PAD_KEY_W + PAD_GAP)
    return layout.x(left, PAD_KEY_W, rtl), PAD_TOP + row * (PAD_KEY_H + PAD_GAP), PAD_KEY_W, PAD_KEY_H


def pin_pad(lang: str) -> str:
    rtl, f = lang == "ar", T.FRAME
    parts = [_text("avatar", 900, 70, 120, 120, lang, size=64, weight="bold", color=T.WHITE,
                   bg=profiles.PALETTE[0], align="center", radius=T.RADIUS_CARD * 2, wrap=False, rtl=False),
             _text("title", 96, 206, 1728, 70, lang, size=44, weight="bold", align="center"),
             _text("masked", 96, 290, 1728, 90, lang, size=64, weight="bold", align="center", wrap=False),
             _text("status", 96, 384, 1728, 50, lang, size=28, color=T.ACCENT, align="center"),
             _w("padframe", PAD_LEFT - f, PAD_TOP - f, PAD_KEY_W + 2 * f, PAD_KEY_H + 2 * f, rtl,
                backgroundColor=T.ACCENT, cornerRadius=T.RADIUS_PILL + f, zPosition="0")]
    for index, key in enumerate(profiles.PAD_KEYS):
        x, y, w, h = pad_key_box(index, False)
        parts.append(_text("key%d" % index, x, y, w, h, lang, size=40, weight="bold", bg=T.SURFACE,
                           align="center", radius=T.RADIUS_PILL, z="1", wrap=False, rtl=rtl))
    parts.append(_text("hint", 96, 990, 1728, 44, lang, size=24, color=T.DIM, align="center"))
    return _screen("KanasaPinPad", parts)


PROFILES_PAGE_WIDGETS = {"title", "list", "hint", "status", "avatar", "avname"}


def profiles_page(lang: str) -> str:
    rtl = lang == "ar"
    return _screen("KanasaProfiles", [
        _text("title", 96, 90, 1400, 90, lang, size=64, weight="bold"),
        _list("list", 96, 220, 1100, 6 * 64, lang, size=30),
        _text("avatar", 1360, 250, 260, 260, lang, size=130, weight="bold", color=T.WHITE, bg=profiles.PALETTE[0],
              align="center", radius=T.RADIUS_CARD * 3, wrap=False, rtl=rtl),
        _text("avname", 1300, 530, 380, 56, lang, size=34, weight="bold", align="center", wrap=False, rtl=rtl),
        _text("status", 96, 660, 1728, 50, lang, size=28, color=T.ACCENT),
        _text("hint", 96, 980, 1728, 44, lang, size=26, color=T.DIM)])


RECENT_WIDGETS = {"title", "list", "status", "hint"}
RECENT_ROWS = 10


def recent_searches(lang: str) -> str:
    """Search history before the keyboard: New search…, the recent queries, Clear all (scrolls past 10)."""
    return _screen("KanasaRecentSearches", [
        _text("title", 96, 90, 1400, 90, lang, size=64, weight="bold"),
        _list("list", 96, 220, 1200, RECENT_ROWS * 64, lang, size=30),
        _text("status", 96, 900, 1728, 50, lang, size=28, color=T.ACCENT),
        _text("hint", 96, 980, 1728, 44, lang, size=26, color=T.DIM)])


SWATCH, SWATCH_GAP, SWATCH_Y = 140, 36, 470
SWATCH_LEFT = (1920 - (8 * SWATCH + 7 * SWATCH_GAP)) // 2
COLOUR_WIDGETS = {"title", "swframe", "hint"} | {"sw%d" % i for i in range(len(profiles.PALETTE))}


def swatch_box(index: int, rtl: bool) -> tuple:
    return layout.x(SWATCH_LEFT + index * (SWATCH + SWATCH_GAP), SWATCH, rtl), SWATCH_Y, SWATCH, SWATCH


def colour_pick(lang: str) -> str:
    rtl, r = lang == "ar", PICKER_RING
    parts = [_text("title", 96, 300, 1728, 90, lang, size=56, weight="bold", align="center"),
             _w("swframe", SWATCH_LEFT - r, SWATCH_Y - r, SWATCH + 2 * r, SWATCH + 2 * r, rtl,
                backgroundColor=T.ACCENT, cornerRadius=T.RADIUS_CARD * 2 + r, zPosition="0")]
    for index, colour in enumerate(profiles.PALETTE):
        x, y, w, h = swatch_box(index, False)
        parts.append(_text("sw%d" % index, x, y, w, h, lang, size=64, weight="bold",
                           color=profiles.text_color(index), bg=colour, align="center",
                           radius=T.RADIUS_CARD * 2, z="1", wrap=False, rtl=rtl))
    parts.append(_text("hint", 96, 980, 1728, 44, lang, size=26, color=T.DIM, align="center"))
    return _screen("KanasaColourPick", parts)
