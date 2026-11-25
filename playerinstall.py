"""0.6.10: exteplayer3 is the only player (FIX32), but ipks up to 0.6.9 did not Depend on it, so a fresh install had
none ("reinstall KanasaStream" on the owner's reflashed box, 11-23). The plugin installs it itself: one background
opkg run per plugin run, `opkg update` first (stale package lists point at files the feed no longer has)."""
from . import extplayer

COMMAND = "opkg update >/dev/null 2>&1; opkg install exteplayer3"

_run = {"state": None, "container": None, "listeners": []}     # state: None | installing | present | failed


def log(message):
    print("[Kanasa] playerinstall: %s" % message)


def _container():
    from enigma import eConsoleAppContainer
    return eConsoleAppContainer()


def reset() -> None:
    _run.update(state=None, container=None, listeners=[])


def state():
    return _run["state"]


def _finished(code):
    ok = extplayer.installed()
    log("opkg finished code=%s, exteplayer3 %s" % (code, "present" if ok else "still missing"))
    listeners = _run["listeners"]
    _run.update(state="present" if ok else "failed", container=None, listeners=[])
    for listener in listeners:
        try:
            listener(ok)
        except Exception as error:  # noqa: BLE001 - a closed Screen must not stop the others
            log("listener failed: %r" % (error,))


def ensure(on_done=None) -> str:
    """'present', 'installing' (on_done(ok) is called when opkg ends) or 'failed'. Never raises."""
    if extplayer.installed():
        _run["state"] = "present"
        return "present"
    if on_done is not None:
        _run["listeners"].append(on_done)
    if _run["state"] == "installing":
        return "installing"
    try:
        container = _container()
        container.appClosed.append(_finished)
        if container.execute(COMMAND):
            raise OSError("opkg did not start")
    except Exception as error:  # noqa: BLE001 - no enigma / no opkg: the missing card stays
        log("cannot start opkg: %r" % (error,))
        _run.update(state="failed", container=None, listeners=[])
        return "failed"
    log("exteplayer3 missing: installing it")
    _run.update(state="installing", container=container)      # kept: a dropped container kills its child
    return "installing"
