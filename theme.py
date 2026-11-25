"""Kanasa TV-app look for the box: tokens and fonts (no Enigma2 imports).

Measured on the Vu+ 2026-11-16 (plan 5 probe): cornerRadius, backgroundGradient, itemCornerRadius and
alpha-blended PNGs work; borderWidth on a Label outlines the TEXT and "#00000000" draws black, so a frame is
an accent rounded Label behind a slightly smaller widget. The ⏭ glyph does not render; ✓ draws as √.
"""
BG, SURFACE, CHIP, ACCENT, TEXT, DIM, TRACK = ("#0b0c13", "#171821", "#272e47", "#ff5268", "#f4f7fd",
                                               "#9a9aab", "#2a2b33")
SELECTED, BORDER, EPISODE_FOCUS = "#3a1820", "#2a2f3d", "#4fc3f7"
GREEN = "#3ccf6e"                # FIX9: "Downloaded" (the app's green downloaded mark)
YELLOW = "#ffeb3b"               # watched episode control, like the TV app/site
WHITE = "#ffffff"
RADIUS_PILL, RADIUS_CARD, RADIUS_CHIP = 28, 12, 8
FRAME = 4                      # px of accent showing round a focused widget
FONT_FILES = {"KanasaBold": "/usr/share/fonts/Roboto-Bold.ttf",
              "KanasaRegular": "/usr/share/fonts/LiberationSans-Regular.ttf"}   # Arabic falls back fine
GLYPHS = {"check": "✓", "play": "▶", "star": "★", "back": "←", "next": "▶▶", "dot": "●", "list": "≡",
          "closed": "▼", "open": "▲"}      # FIX26 drop-down marks: WGL4 triangles (in Liberation Sans)


def font(weight: str, size: int, lang: str) -> str:
    return "%s;%d" % ("KanasaBold" if weight == "bold" else "KanasaRegular", size)


def rgb(value):
    """A '#rrggbb' / '#aarrggbb' colour as the box's gRGB (lazy import: this module stays pure).
    The enigma module has no parseColor on openATV — measured 2026-11-17, a crash on import."""
    from enigma import gRGB
    return gRGB(int(value.lstrip("#"), 16))
