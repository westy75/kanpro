"""Aspect ratio in the player, like the Android player's ASPECT chip (no Enigma2 imports).

Modes cycle Fit -> Fill -> Stretch -> 16:9 -> 4:3 -> Fit; one device-wide choice (prefs "aspect").

On the box (e2iplayer's own way for exteplayer3, iptvextmovieplayer.py getE2VideoOptions/applyVideoOptions/__onClose):
the decoder's scaling policy is written straight to /proc/stb/video/policy (4:3 pictures) and policy2 (16:9 / wide
pictures), only ever a value listed in that file's *_choices, and put back when the player closes. Enigma2's own
config.av.policy_43/policy_169 are never touched, so a crash cannot save our value into the box settings (AVSwitch
writes its own values again at the next start). 16:9 / 4:3 also squeeze the picture into a centred box through the
decoder window /proc/stb/vmpeg/0/dst_* (hex, 720x576 for the whole screen), reset to full screen on close.
Every file is optional: a box without one is skipped silently."""
import os

from . import i18n

MODES = ("fit", "fill", "stretch", "16_9", "4_3")
FORCED = {"16_9": 16 / 9, "4_3": 4 / 3}

VIDEO, DECODER = "/proc/stb/video/", "/proc/stb/vmpeg/0/"
POLICIES = ((VIDEO + "policy", VIDEO + "policy_choices"), (VIDEO + "policy2", VIDEO + "policy2_choices"))
TV_ASPECT = VIDEO + "aspect"
DST = tuple(DECODER + "dst_" + name for name in ("left", "top", "width", "height"))
DST_APPLY = DECODER + "dst_apply"
DST_W, DST_H = 720, 576                  # the decoder window's coordinate space = the whole screen

# best first; a value is written only when the box lists it
CHAINS = {
    "fit": ("pillarbox", "letterbox"),               # keep the shape, black bars
    "fill": ("zoom", "panscan"),                     # keep the shape, crop to fill
    "stretch": ("bestfit", "scale", "nonlinear"),    # fill the screen, distorted ("Just scale" in AVSwitch)
}


def log(message):
    print("[Kanasa] %s" % message)


def clean(value) -> str:
    return value if isinstance(value, str) and value in MODES else "fit"


def next_mode(value) -> str:
    return MODES[(MODES.index(clean(value)) + 1) % len(MODES)]


def label(mode, lang) -> str:
    return i18n.t("player.aspect." + clean(mode), lang)


def toast(mode, lang) -> str:
    return i18n.t("player.aspect.toast", lang) % label(mode, lang)


def forced_frame(host_w, host_h, ratio, screen_ratio=None):
    """The largest centred (left, top, width, height) box of `ratio` inside the host (the Android player's
    forcedFrame). screen_ratio: the host's real shape when its pixels are not square (the decoder's 720x576 is the
    whole TV, 16:9 or 4:3); default = square pixels."""
    if host_w <= 0 or host_h <= 0 or not ratio:
        return 0, 0, max(0, int(host_w)), max(0, int(host_h))
    shape = screen_ratio or host_w / host_h
    if ratio < shape:                    # narrower than the screen: bars left and right
        width = int(round(host_w * ratio / shape))
        return (host_w - width) // 2, 0, width, host_h
    height = int(round(host_h * shape / ratio))
    return 0, (host_h - height) // 2, host_w, height


def pick_policy(mode, choices):
    """The value for this mode from a *_choices line, or None (no listed value fits: nothing is written)."""
    listed = str(choices or "").split()
    chain = CHAINS.get("stretch" if clean(mode) in FORCED else clean(mode), ())
    return next((value for value in chain if value in listed), None)


def tv_ratio(value) -> float:
    """/proc/stb/video/aspect: "4:3", "16:9", "16:10"; "any"/"auto"/missing = a 16:9 TV."""
    try:
        width, height = (float(part) for part in str(value or "").strip().split(":"))
        if width > 0 and height > 0:
            return width / height
    except ValueError:
        pass
    return 16 / 9


class ProcFs:
    """The real /proc (root: tests). read -> stripped text or None; write -> True, never creates a file."""
    def __init__(self, root=""):
        self.root = root

    def read(self, path):
        try:
            with open(self.root + path) as handle:
                return handle.read().strip()
        except (OSError, ValueError):
            return None

    def write(self, path, value):
        target = self.root + path
        if not os.path.exists(target):
            return False
        try:
            with open(target, "w") as handle:
                handle.write(str(value))
            return True
        except OSError:
            return False


class Control:
    """One player's aspect state: save() the box's policies once, apply() a mode per play, restore() on close
    (idempotent: every exit road may call it). Never raises."""
    def __init__(self, fs):
        self.fs, self.saved, self.changed = fs, None, False

    def _read(self, path):
        try:
            value = self.fs.read(path)
        except Exception:  # noqa: BLE001 - a proc file that errors is a missing one
            return None
        return value.strip() if isinstance(value, str) else None

    def _write(self, path, value):
        try:
            return bool(self.fs.write(path, value))
        except Exception as error:  # noqa: BLE001 - never crash the player over a picture setting
            log("aspect: %s not written: %r" % (path, error))
            return False

    def save(self):
        if self.saved is None:
            self.saved = {path: self._read(path) for path, _ in POLICIES}
            window = [self._read(path) for path in DST]       # box 11-23: "00000000" x4 = the driver's full screen
            self.saved_window = window if all(window) else None

    def _full(self):
        """The box's own window (exactly what was there before the player), else the whole 720x576 screen."""
        window = getattr(self, "saved_window", None)
        try:
            if window:
                return [int(value, 16) for value in window]
        except ValueError:
            pass
        return 0, 0, DST_W, DST_H

    def _window(self, frame):
        wrote = False
        for path, value in zip(DST, frame):
            wrote = self._write(path, "%x" % value) or wrote
        if wrote:
            self._write(DST_APPLY, "1")
        return wrote

    def apply(self, mode):
        self.save()
        mode = clean(mode)
        for path, choices in POLICIES:
            if self.saved.get(path) is None:
                continue                                 # this box has no such file
            value = pick_policy(mode, self._read(choices))
            if value and self._write(path, value):
                self.changed = True
                self.touched = getattr(self, "touched", set()) | {path}
        frame = forced_frame(DST_W, DST_H, FORCED[mode], tv_ratio(self._read(TV_ASPECT))) if mode in FORCED \
            else self._full()
        if self._window(frame):
            self.changed = True
        log("aspect: %s" % mode)

    def restore(self):
        if not self.changed:
            return
        self.changed = False
        for path, value in (self.saved or {}).items():
            if value and path in getattr(self, "touched", ()):
                self._write(path, value)     # only what the player changed (policy2 here: never written)
        self._window(self._full())
        log("aspect: box picture restored")
