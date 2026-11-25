"""The on-box torrent engine (TorrServer): install, start/stop, its local HTTP API. Thin; the rules are in
torrentrules.py. Enigma2 is imported only inside Process (tests import this module without it)."""
import hashlib
import json
import os
import shlex
import shutil
import signal
import time

from . import torrentrules as rules


# Enigma2 can be killed without delivering the container's close callback.  Keep the PID of the
# TorrServer we launched so the next Enigma2 can distinguish that orphan from somebody else's
# TorrServer listening on the same port.  Never use pkill: this plugin owns only this PID.
PID_FILE = "/tmp/kanasa-torrserver.pid"


class EngineError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def log(message):
    print("[Kanasa] torrent: %s" % message)


# --- the engine's local API ----------------------------------------------------------------------------
def _requests_post(url, body, timeout):
    import requests  # on the box; imported late so tests never need it
    response = requests.post(url, data=json.dumps(body), headers={"Content-Type": "application/json"},
                             timeout=timeout)
    return response.status_code, response.content


def _requests_get(url, timeout):
    import requests  # on the box; imported late so tests never need it
    response = requests.get(url, timeout=timeout)
    return response.status_code, response.content


class EngineClient:
    """Only ever http://127.0.0.1:<port>. The hash is the only torrent input (no magnet trackers)."""

    def __init__(self, port=rules.PORT, post=None, get=None):
        self.base = "http://127.0.0.1:%d" % port
        self.port = port
        self.post = post or _requests_post
        self.get = get or _requests_get
        self.buffer_minutes = 4

    def _control(self, path, body, code, timeout=10.0):
        try:
            status, raw = self.post(self.base + path, body, timeout)
        except Exception:  # noqa: BLE001 - every transport failure is one reason
            status, raw = None, b""
        if status != 200:
            raise EngineError(code)
        try:
            return json.loads(raw.decode("utf-8")) if raw else None
        except (ValueError, UnicodeDecodeError):
            raise EngineError(code) from None

    def echo(self) -> bool:
        try:
            status, raw = self.get(self.base + "/echo", 2.0)
        except Exception:  # noqa: BLE001
            return False
        return status == 200 and rules.is_echo(raw)

    def wait_ready(self, seconds=rules.START_S, sleep=time.sleep, clock=time.monotonic) -> bool:
        end = clock() + seconds
        while clock() < end:
            if self.echo():
                return True
            sleep(0.5)
        return self.echo()

    def apply_settings(self, report=True) -> bool:
        """RAM cache 128 MB, preload 32 MB (+ the app's other pins). Best effort: False = defaults stay."""
        try:
            current = self._control("/settings", {"action": "get"}, "settings")
            self._control("/settings", {"action": "set",
                                         "sets": rules.settings_patch(current, self.buffer_minutes)}, "settings")
            return True
        except EngineError:
            if report:
                log("settings not applied")
            return False

    def wait_operational(self, seconds=rules.START_S, sleep=time.sleep, clock=time.monotonic) -> bool:
        """Wait until both the lightweight echo and the settings API answer.

        TorrServer can accept /echo before its control API is ready after process startup.  Add must
        never race that interval, particularly on the first try after Enigma2 starts.
        """
        end = clock() + seconds
        while True:
            if self.echo() and self.apply_settings(report=False):
                return True
            if clock() >= end:
                log("engine did not become ready (echo/settings)")
                return False
            sleep(0.5)

    def add(self, info_hash: str) -> None:
        self._control("/torrents", {"action": "add", "link": "magnet:?xt=urn:btih:" + info_hash,
                                    "save_to_db": False}, "add")

    def status(self, info_hash: str) -> dict:
        return rules.parse_status(self._control("/torrents", {"action": "get", "hash": info_hash}, "status"))

    def drop(self, info_hash: str) -> bool:
        try:
            self._control("/torrents", {"action": "drop", "hash": info_hash}, "drop", 5.0)
            return True
        except EngineError:
            return False

    def open_file(self, row, progress=None, cancelled=lambda: False, sleep=time.sleep, clock=time.monotonic):
        """Add the torrent, wait for its file list (METADATA_S at most), choose the file. Runs off the GUI
        thread; `progress` (a dict) gets the peer count for the card. -> (stream url, file entry)."""
        if not self.wait_operational(sleep=sleep, clock=clock):
            raise EngineError("not_ready")
        self.add(row["hash"])
        end = clock() + rules.METADATA_S
        while True:
            if cancelled():
                raise EngineError("cancelled")
            state = self.status(row["hash"])
            if progress is not None:
                progress.update(peers=state["peers"], speed=state["speed"])
            if state["files"]:
                entry = rules.match_file(state["files"], row.get("filename"), row.get("episode"))
                if entry is None:
                    raise EngineError("no_video")
                return rules.stream_url(row["hash"], entry, self.port), entry
            if clock() >= end:
                raise EngineError("no_peers")
            sleep(1.0)


# --- install ------------------------------------------------------------------------------------------
def _media_names():
    try:
        return os.listdir("/media")
    except OSError:
        return []


def _free(path):
    try:
        stat = os.statvfs(path)
        return stat.f_bavail * stat.f_frsize
    except OSError:
        return 0


def writable(path) -> bool:
    """A real write test: create and delete a probe file (os.access lies on read-only remounts and shares)."""
    probe = os.path.join(path, ".kanasa-probe-%d" % os.getpid())
    try:
        with open(probe, "wb") as handle:
            handle.write(b"k")
        os.remove(probe)
        return True
    except OSError:
        try:
            os.remove(probe)
        except OSError:
            pass
        return False


def disks() -> list:
    from . import downloadrules
    try:
        with open("/proc/mounts", encoding="utf-8") as handle:
            mounts = downloadrules.mount_rows(handle.read())
    except OSError:
        mounts = []
    out = []
    for row in mounts:
        mounted = os.path.ismount(row["path"])
        out.append({"path": row["path"], "mounted": mounted, "writable": mounted and writable(row["path"]),
                    "free": _free(row["path"]), "device": row["device"], "fs": row["fs"],
                    "noexec": bool(row.get("noexec"))})
    return out


def movie_paths() -> list:
    """Enigma2's movie locations (config.usage.default_path, config.movielist.videodirs), read at runtime."""
    from . import downloadrules
    try:
        from Components.config import config
    except ImportError:
        return []
    default_path = videodirs = None
    try:
        default_path = config.usage.default_path.value
    except Exception:  # noqa: BLE001 - an image without the setting
        pass
    try:
        videodirs = config.movielist.videodirs.value
    except Exception:  # noqa: BLE001
        pass
    return downloadrules.movie_paths(default_path, videodirs)


def install_dir():
    """Where a new engine goes, or None (no disk or flash with 300 MB free)."""
    return rules.choose_install_dir(disks(), _free("/"))


def _mount_paths():
    """Every real disk/share path in /proc/mounts (no write probe: this runs on every open)."""
    from . import downloadrules
    try:
        with open("/proc/mounts", encoding="utf-8") as handle:
            return [row["path"] for row in downloadrules.mount_rows(handle.read())]
    except OSError:
        return []


def installed_dir():
    """The directory of an installed engine, or None. Looks everywhere install_dir() can put it
    (any mounted disk, e.g. /media/sda1 or /media/mmc), not only /media/hdd and /media/usb*:
    a box whose disk has another name was asked to install again on every open."""
    roots = rules.install_roots(_media_names()) + _mount_paths()
    seen = set()
    for directory in [os.path.join(root, rules.DIR_NAME) for root in roots] + [rules.ROOT_DIR]:
        if directory in seen:
            continue
        seen.add(directory)
        if os.access(rules.binary_path(directory), os.X_OK):
            return directory
    return None


def size_bytes(directory) -> int:
    total = 0
    for parent, _, names in os.walk(directory or ""):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(parent, name))
            except OSError:
                pass
    return total


def remove(directory) -> None:
    """Delete an engine directory (only ever one named kanasa-torrent)."""
    if directory and os.path.basename(directory.rstrip("/")) == rules.DIR_NAME:
        shutil.rmtree(directory, ignore_errors=True)


def _requests_fetch(url, timeout):
    import requests  # on the box; imported late so tests never need it
    response = requests.get(url, stream=True, timeout=timeout)
    if response.status_code != 200:
        response.close()
        raise EngineError("download")
    return response.iter_content(256 * 1024)


def download(directory, asset, progress=None, fetch=None, cancelled=lambda: False) -> str:
    """Stream the pinned asset into directory, check size + sha256, make it executable. Off the GUI thread;
    progress["percent"] is updated for the screen. -> the binary's path."""
    size, sha256 = rules.ASSETS[asset]
    os.makedirs(directory, exist_ok=True)
    part, final = rules.binary_path(directory) + ".part", rules.binary_path(directory)
    digest, done = hashlib.sha256(), 0
    try:
        with open(part, "wb") as handle:
            for chunk in (fetch or _requests_fetch)(rules.asset_url(asset), 30.0):
                if cancelled():
                    raise EngineError("cancelled")
                if not chunk:
                    continue
                handle.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if done > size:
                    raise EngineError("invalid")
                if progress is not None:
                    progress["percent"] = int(done * 100 / size)
        if done != size or digest.hexdigest() != sha256:
            raise EngineError("invalid")
        os.chmod(part, 0o755)
        os.replace(part, final)
        if not os.access(final, os.X_OK):
            # a disk that cannot run programs (noexec, some shares/filesystems): never report "installed"
            _unlink(final)
            raise EngineError("noexec")
        return final
    except EngineError:
        _unlink(part)
        raise
    except Exception:  # noqa: BLE001 - network or disk: one reason
        _unlink(part)
        raise EngineError("download") from None


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _pid_from_file(path=None):
    path = PID_FILE if path is None else path
    try:
        with open(path, encoding="ascii") as handle:
            value = int(handle.read().strip())
        return value if value > 0 else None
    except (OSError, ValueError):
        return None


def _write_pid(pid, path=None):
    path = PID_FILE if path is None else path
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return
    try:
        with open(path, "w", encoding="ascii") as handle:
            handle.write("%d\n" % pid)
    except OSError:
        pass


def _clear_pid(pid=None, path=None):
    """Remove our record, but never erase a newer process's record from an old close callback."""
    path = PID_FILE if path is None else path
    recorded = _pid_from_file(path)
    if pid is None or recorded == pid:
        _unlink(path)


def _alive(pid, kill=os.kill):
    try:
        kill(pid, 0)
        return True
    except OSError:
        return False


def _is_our_command(pid, directory, proc_root="/proc"):
    """Exact argv check before signalling a persisted PID (PID reuse must be harmless)."""
    if not directory:
        return False
    try:
        with open(os.path.join(proc_root, str(pid), "cmdline"), "rb") as handle:
            argv = [part.decode("utf-8", "replace") for part in handle.read().split(b"\0") if part]
    except OSError:
        return False
    return argv == shlex.split(rules.command(directory))


def reap_orphan(directory, sleep=time.sleep, clock=time.monotonic, kill=os.kill, proc_root="/proc") -> bool:
    """Stop the still-live TorrServer from a killed Enigma2, if and only if it is ours.

    An old process can answer /echo while its control API no longer works.  The pid file alone is
    deliberately insufficient because PIDs are reusable; the process argv must exactly be our
    pinned command before it receives TERM (and, after two seconds, KILL).
    """
    pid = _pid_from_file()
    if not pid:
        _clear_pid()
        return False
    if not _alive(pid, kill) or not _is_our_command(pid, directory, proc_root):
        _clear_pid(pid)
        return False
    try:
        kill(pid, signal.SIGTERM)
    except OSError:
        _clear_pid(pid)
        return False
    deadline = clock() + 2.0
    while _alive(pid, kill) and clock() < deadline:
        sleep(min(0.1, max(0.0, deadline - clock())))
    if _alive(pid, kill):
        try:
            kill(pid, signal.SIGKILL)
        except OSError:
            pass
    _clear_pid(pid)
    log("stopped orphaned engine (pid %s)" % pid)
    return True


def machine() -> str:
    try:
        return os.uname().machine
    except (AttributeError, OSError):
        return ""


# --- the process --------------------------------------------------------------------------------------
class Process:
    """The engine this plugin started. One per Enigma2 process; stopped when the plugin closes."""

    def __init__(self):
        self.container, self.pid = None, None

    def running(self) -> bool:
        return self.container is not None

    def start(self, directory) -> bool:
        from enigma import eConsoleAppContainer
        self.stop()
        reap_orphan(directory)
        container = eConsoleAppContainer()
        container.appClosed.append(self._closed)
        if container.execute(rules.command(directory)):
            return False
        self.container = container
        try:
            self.pid = container.getPID()
        except Exception:  # noqa: BLE001 - older images: kill() still works
            self.pid = None
        _write_pid(self.pid)
        log("engine started (pid %s)" % self.pid)
        return True

    def _closed(self, code):
        log("engine exited (%s)" % code)
        _clear_pid(self.pid)
        self.container, self.pid = None, None

    def stop(self) -> None:
        """Kill only our own PID (never pkill: another TorrServer on the box is not ours)."""
        container, pid = self.container, self.pid
        self.container, self.pid = None, None
        if container is None:
            return
        try:
            container.appClosed.remove(self._closed)
        except Exception:  # noqa: BLE001
            pass
        try:
            container.kill()
            _clear_pid(pid)
        except Exception:  # noqa: BLE001
            if pid:
                try:
                    os.kill(pid, 15)
                    _clear_pid(pid)
                except OSError:
                    pass
        log("engine stopped (pid %s)" % pid)


ENGINE = Process()


def shutdown() -> None:
    ENGINE.stop()
