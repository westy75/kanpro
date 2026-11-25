"""Settings → Downloads button: the chosen colour key opens Downloads from any Kanasa screen that does not
use that colour itself. The choice is read at the key press, so a change applies to screens already open."""
from Components.ActionMap import ActionMap

from . import prefs as prefs_mod


def bind(screen, lang, client, source, posters, used=()):
    """Adds screen["downloadskey"]; `used` = the colours the screen's own keys already take."""
    def pressed(colour):
        if prefs_mod.downloads_key(prefs_mod.Prefs().downloads_button(), used) != colour or source is None:
            return
        from .downloads import Downloads
        screen.session.open(Downloads, lang, client, source, posters)
    keys = {colour: (lambda colour=colour: pressed(colour)) for colour in prefs_mod.DOWNLOAD_KEYS[:4]
            if colour not in used}
    screen["downloadskey"] = ActionMap(["ColorActions"], keys, -1)
