"""FIX33: one Player at a time (no Enigma2 imports at module level).

Box 2026-11-20 18:25: a second OK on the Downloads page opened a SECOND Player over the first; its exteplayer3
launch reaped the first one's run and the first Player failed over to the online servers. Every screen that opens
the Player goes through open_player(): while a Player is open (or opening) another OK opens nothing."""

_OPEN = []                     # the one Player screen that is open, or nothing


def log(message):
    print("[Kanasa] %s" % message)


def current():
    """The open Player, or None. A Player that has finished (Screen.doClose cleared it, or it says gone())
    no longer counts, even when its onClose never reached release()."""
    if not _OPEN:
        return None
    player = _OPEN[0]
    gone = getattr(player, "gone", None)
    try:
        finished = bool(gone()) if callable(gone) else False
    except Exception:  # noqa: BLE001 - a half torn-down screen is not open any more
        finished = True
    if finished:
        del _OPEN[:]
        return None
    return player


def busy() -> bool:
    return current() is not None


def claim(player) -> None:
    """Player.__init__: this screen is THE open Player."""
    _OPEN[:] = [player]


def release(player) -> None:
    """Player's onClose: free again (never a newer Player's claim)."""
    if _OPEN and _OPEN[0] is player:
        del _OPEN[:]


def open_player(session, callback, *args):
    """Open the Player screen with args (with a callback: openWithCallback), unless a Player is already open: then
    nothing opens and None is returned (the caller's OK did nothing)."""
    if busy():
        log("a Player is already open: this OK opens no second one")
        return None
    from .player import Player
    if callback is not None:
        return session.openWithCallback(callback, Player, *args)
    return session.open(Player, *args)
