"""085 T104: the box's local hosts switch. No Enigma2 imports at module level (tests use fakes).

- boot(client), once per plugin run (Home): fetch + verify + install the signed bundle off the GUI thread
  (bundlestore), then point the worker (hostclient.CLIENT) at the installed one. A bundle whose manifest lists no
  enigma2 host is not used (Phase 0: nothing to run).
- warm(): a title page opened - start the worker now, so its setup is done before the viewer presses play
  (box cold start 2.2-4.5 s, research §(e)).
- header_value(): "enigma2;bundle=<N>" only while the RUNNING worker answered setup for enigma2 from the verified
  bundle N; else None (no header: the server answers exactly as today - fail closed).
- resolve_blocking(): a device resolve from a download thread (the worker lives on the reactor).
"""
from . import bundlestore, hostclient


def log(message):
    print("[Kanasa] localhosts: %s" % message)


def platform_hosts(manifest) -> list:
    platforms = manifest.get("platforms") if isinstance(manifest, dict) else None
    mine = platforms.get(bundlestore.PLATFORM) if isinstance(platforms, dict) else None
    hosts = mine.get("hosts") if isinstance(mine, dict) else None
    return [h for h in hosts if isinstance(h, str) and h] if isinstance(hosts, list) else []


def configure(root, worker=None) -> bool:
    """Point the worker at the bundle installed under root (verified when it was installed). False: none to use."""
    worker = worker or hostclient.CLIENT
    found = bundlestore.current(root)
    manifest = bundlestore.installed_manifest(root) if found else None
    if found is None or not platform_hosts(manifest):
        log("no bundle with enigma2 hosts installed: device hosts off")
        return False
    domains = manifest.get("site_domains") if isinstance(manifest.get("site_domains"), dict) else {}
    worker.configure(found[1], {k: v for k, v in domains.items() if isinstance(k, str) and isinstance(v, str)},
                     bundlestore.state_cap_bytes(root), found[0])
    log("bundle %d: %s" % (found[0], ", ".join(platform_hosts(manifest))))
    return True


def update(client, root) -> dict:
    """Off the GUI thread: one bundle check (never raises)."""
    return bundlestore.update(root, client.bundle_manifest, client.bundle_zip)


_booted = {"done": False}


def boot(client, root=None, run=None, worker=None) -> None:
    """Once per plugin run. run(fn, *args, on_done, on_error) = async_call.run (tests: a direct call)."""
    if _booted["done"]:
        return
    _booted["done"] = True
    if run is None:
        from . import async_call
        run = async_call.run
    try:
        root = root or bundlestore.default_root()
    except Exception as error:  # noqa: BLE001 - no disk probe: flash
        log("root: %r" % (error,))
        root = bundlestore.FLASH_ROOT
    try:
        from . import sqliteinstall       # the worker's setup answers no_sqlite3 without it (T016)
        sqliteinstall.ensure()
    except Exception:  # noqa: BLE001 - no Enigma2 (tests) or no opkg
        pass

    def done(answer):
        if isinstance(answer, dict):
            log("bundle check: %s %s %s" % (answer.get("state"), answer.get("version"), answer.get("code") or ""))
        configure(root, worker)
    run(update, client, root, on_done=done, on_error=lambda error: done(None))


def reset() -> None:
    _booted["done"] = False


def warm(worker=None) -> bool:
    """A title page opened: start the worker if a bundle is configured (reactor thread)."""
    worker = worker or hostclient.CLIENT
    try:
        return worker.start() if worker.available() else False
    except Exception as error:  # noqa: BLE001 - a title page never fails for this
        log("warm: %r" % (error,))
        return False


def header_value(worker=None):
    version = (worker or hostclient.CLIENT).header_bundle()
    if isinstance(version, int) and not isinstance(version, bool) and 0 < version < 10 ** 6:
        return "enigma2;bundle=%d" % version
    return None


def resolve_blocking(ref, gui_call=None, worker=None):
    """A device resolve from a download thread: (ok, value) of the worker's answer. gui_call(fn) runs fn on the
    reactor and waits for the Deferred it returns (downloadmanager._gui_call)."""
    from . import devicejobs
    return call_blocking("resolve", {"ref": ref, "client": dict(devicejobs.CLIENT_CAPS)}, gui_call, worker)


def call_blocking(op, args, gui_call=None, worker=None):
    """Any worker call from a download thread (a resume searches the site again): (ok, value). The worker starts
    if the plugin closed it."""
    worker = worker or hostclient.CLIENT

    def on_gui():
        from twisted.internet import defer
        waiting = defer.Deferred()
        worker.call(op, args, lambda ok, value: waiting.callback((ok, value)))
        return waiting
    if gui_call is None:
        from .downloadmanager import _gui_call as gui_call
    return gui_call(on_gui)
