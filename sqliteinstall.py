"""085 FR-032 / T016: the host worker needs sqlite3 (cache.py, catalog/store.py), which openATV ships apart as
python3-sqlite3 (missing on the owner's reflashed box, 2026-11-23). New ipks Depend on it; a box that updates
through the plugin's own updater may not pull new Depends, so the plugin installs it itself, as playerinstall.py
does for exteplayer3: one background opkg run per plugin run, `opkg update` first. Without it the worker answers
setup with no_sqlite3 and device hosts stay off (hostclient). Phase 0: not called by any screen yet."""
import importlib.util

PACKAGE = "python3-sqlite3"
COMMAND = "opkg update >/dev/null 2>&1; opkg install " + PACKAGE

_run = {"state": None, "container": None, "listeners": []}     # state: None | installing | present | failed


def log(message):
    print("[Kanasa] sqliteinstall: %s" % message)


def _container():
    from enigma import eConsoleAppContainer
    return eConsoleAppContainer()


def installed() -> bool:
    """The worker is plain python3, the same interpreter and site-packages as Enigma2's: one lookup tells."""
    try:
        return importlib.util.find_spec("sqlite3") is not None and importlib.util.find_spec("_sqlite3") is not None
    except (ImportError, ValueError):
        return False


def reset() -> None:
    _run.update(state=None, container=None, listeners=[])


def state():
    return _run["state"]


def _finished(code):
    importlib.invalidate_caches()
    ok = installed()
    log("opkg finished code=%s, sqlite3 %s" % (code, "present" if ok else "still missing"))
    listeners = _run["listeners"]
    _run.update(state="present" if ok else "failed", container=None, listeners=[])
    for listener in listeners:
        try:
            listener(ok)
        except Exception as error:  # noqa: BLE001 - one listener must not stop the others
            log("listener failed: %r" % (error,))


def ensure(on_done=None) -> str:
    """'present', 'installing' (on_done(ok) when opkg ends) or 'failed'. Never raises."""
    if installed():
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
    except Exception as error:  # noqa: BLE001 - no enigma / no opkg
        log("cannot start opkg: %r" % (error,))
        _run.update(state="failed", container=None, listeners=[])
        return "failed"
    log("sqlite3 missing: installing %s" % PACKAGE)
    _run.update(state="installing", container=container)      # kept: a dropped container kills its child
    return "installing"
