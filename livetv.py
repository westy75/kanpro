"""Live TV while KanasaStream is open (no Enigma2 imports).

With the setting on (prefs.stop_live_tv, default on) the plugin remembers the channel that was playing
when it opened, stops it, and plays it again only when the plugin closes for good: not on the "restart"
re-open after a language/account change, and never after the player while the plugin is still open.
"""


def remember(enabled: bool, current):
    """The service to stop now and restore at the end, or None (setting off, or nothing was playing)."""
    return current if enabled and current else None


def restore_on_close(answer, saved) -> bool:
    """Play `saved` again when Home closed for good."""
    return saved is not None and answer != "restart"


# True while the plugin holds live TV stopped: the player then never restarts it on exit
holding = False


def player_restores(old_service) -> bool:
    """After the player: play the service it found only when the plugin is not holding live TV off."""
    return old_service is not None and not holding
