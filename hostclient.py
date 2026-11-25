"""085 FR-031 / T015: the plugin's owner of the host worker (hostworker/main.py). Everything here runs on Enigma2's
reactor thread; the worker does the host work, so the UI never waits on a host. Enigma2 is imported only inside
the default factories (tests inject fakes).

- Started lazily on the first call, with eConsoleAppContainer (no shell), PID + argv recorded in PID_FILE; an orphan
  from a killed Enigma2 is reaped only when /proc/<pid>/cmdline is exactly our recorded argv (torrengine's rule).
- The first line sent is `setup` (site domains of the installed bundle, state cap); calls written after it are
  read by the worker only once setup is done. setup -> no_sqlite3: device hosts off for this plugin run.
- Deadlines: the worker answers "timeout" itself at the op's deadline; no answer at deadline + 5 s means it is
  stuck: SIGKILL, every pending call gets "timeout", the next call starts a fresh worker.
- Three deaths (crash or stuck kill) in 10 minutes: device hosts off for 15 minutes, one log line.
- Memory: `stats` after each job; RSS > 120 MB -> restart when idle, > 200 MB -> kill now.
- stop() on plugin close (frees the RAM for Enigma2); a new bundle -> restart when idle.
T104: the server list's device jobs and the player's device resolves call it; title pages pre-start it (warm)."""
import json
import os
import re
import signal
import time

from . import hostproto

PID_FILE = "/tmp/kanasa-hostworker.pid"
PYTHON = "/usr/bin/python3"
MAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hostworker", "main.py")
RLIMIT_AS_MB = 256          # T017: VmPeak 90 MB with 7 threads after a krmzi sources + 2 resolves on the box
RSS_RESTART_KB = 120 * 1024
RSS_KILL_KB = 200 * 1024
DEATHS_WINDOW_S = 600.0
DEATHS_LIMIT = 3
OFF_S = 900.0
TICK_MS = 1000
PLATFORM = "enigma2"        # device.setup(platform=...): the box's 25 s sources deadline (plan decision 10)


def log(message):
    print("[Kanasa] hostclient: %s" % message)


def _enigma_container():
    from enigma import eConsoleAppContainer
    return eConsoleAppContainer()


def _enigma_timer():
    from enigma import eTimer
    return eTimer()


# --- pid record (extplayer.py's shape: {"pid", "argv"}) -------------------------------------------------------
def _cmdline(pid, proc_root):
    try:
        with open(os.path.join(proc_root, str(pid), "cmdline"), "rb") as handle:
            return [part.decode("utf-8", "replace") for part in handle.read().split(b"\0") if part]
    except OSError:
        return None


def write_record(pid, argv, path=PID_FILE):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"pid": pid, "argv": list(argv)}, handle)
    except OSError:
        pass


def read_record(path=PID_FILE):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        if isinstance(value.get("pid"), int) and value["pid"] > 0 and isinstance(value.get("argv"), list):
            return value["pid"], value["argv"]
    except (OSError, ValueError, AttributeError):
        pass
    return None


def clear_record(pid=None, path=PID_FILE):
    record = read_record(path)
    if pid is None or record is None or record[0] == pid:
        try:
            os.unlink(path)
        except OSError:
            pass


def reap_orphan(path=PID_FILE, proc_root="/proc", kill=os.kill) -> bool:
    """SIGKILL the worker a killed Enigma2 left behind, only if its argv is exactly the recorded one."""
    record = read_record(path)
    if record is None:
        return False
    pid, argv = record
    if _cmdline(pid, proc_root) != argv:
        clear_record(pid, path)
        return False
    try:
        kill(pid, signal.SIGKILL)
    except OSError:
        pass
    clear_record(pid, path)
    log("stopped orphaned host worker (pid %s)" % pid)
    return True


class HostClient:
    def __init__(self, container_factory=None, timer_factory=None, clock=time.monotonic, pid_path=PID_FILE,
                 proc_root="/proc", kill=os.kill, python=PYTHON, main=MAIN):
        self.container_factory = container_factory or _enigma_container
        self.timer_factory = timer_factory or _enigma_timer
        self.clock, self.pid_path, self.proc_root, self.kill = clock, pid_path, proc_root, kill
        self.python, self.main = python, main
        self.bundle_dir, self.site_domains, self.state_cap, self.version = None, {}, None, None
        self.loaded_version = None         # T104: the bundle the RUNNING worker imported and set up for enigma2
        self.starting_version = None
        self.container, self.pid, self.timer = None, None, None
        self.buffer = hostproto.LineBuffer()
        self.next_id = 0
        self.pending = {}                  # id -> (callback, kill_at, op)
        self.internal = {}                 # id -> handler(ok, value) for setup / stats
        self.ready = False
        self.off_reason = None             # "no_sqlite3": off for this plugin run
        self.disabled_until = 0.0
        self.deaths = []
        self.restart_when_idle = False
        self.stderr_tail = []
        self.last_setup = None
        self.last_stats = None

    # --- configuration -----------------------------------------------------------------------------------------
    def configure(self, bundle_dir, site_domains=None, state_cap_bytes=None, version=None) -> None:
        """The installed bundle (bundlestore.current). A different bundle restarts a running worker when idle.
        version: the bundle's number (default: the <root>/<v> directory name)."""
        changed = bundle_dir != self.bundle_dir
        self.bundle_dir, self.site_domains, self.state_cap = bundle_dir, dict(site_domains or {}), state_cap_bytes
        if version is None and isinstance(bundle_dir, str) and re.fullmatch(r"[1-9]\d{0,8}",
                                                                           os.path.basename(bundle_dir)):
            version = int(os.path.basename(bundle_dir))
        self.version = version if isinstance(version, int) and not isinstance(version, bool) else None
        if changed and self.container is not None:
            self.restart_when_idle = True
            self._idle_check()

    def argv(self) -> list:
        """The worker's own argv (what /proc/<pid>/cmdline shows after /usr/bin/env execs it)."""
        return [self.python, "-I", "-u", self.main, "--bundle", self.bundle_dir, "--rlimit-as-mb", str(RLIMIT_AS_MB)]

    def available(self) -> bool:
        return self.bundle_dir is not None and self.off_reason is None and self.clock() >= self.disabled_until

    def running(self) -> bool:
        return self.container is not None

    def header_bundle(self):
        """T104: the bundle version for X-Kanasa-Local-Hosts, or None. Only a RUNNING worker that answered setup
        for enigma2 counts (a stopped, crashed, disabled or still-starting worker: no header, today's answer)."""
        if self.container is None or not self.ready or not self.available():
            return None
        setup = self.last_setup if isinstance(self.last_setup, dict) else {}
        if setup.get("platform") != PLATFORM:
            return None                    # a bundle without the platform argument: Android's 12 s deadline
        return self.loaded_version

    def subscene_ready(self) -> bool:
        """086: the running enigma2 worker's bundle has device.subscene (setup answered a `subscene` key)."""
        setup = self.last_setup if isinstance(self.last_setup, dict) else {}
        return self.header_bundle() is not None and isinstance(setup.get("subscene"), dict)

    # --- process -------------------------------------------------------------------------------------------------
    def start(self) -> bool:
        if self.container is not None:
            return True
        if not self.available():
            return False
        try:
            reap_orphan(self.pid_path, self.proc_root, self.kill)
        except Exception as error:  # noqa: BLE001
            log("reap failed: %r" % (error,))
        argv = self.argv()
        command = ["/usr/bin/env", "MALLOC_ARENA_MAX=2"] + argv
        container = self.container_factory()
        container.appClosed.append(lambda code, c=container: self._closed(c, code))
        container.dataAvail.append(lambda data, c=container: self._data(c, data))
        container.stderrAvail.append(self._stderr)
        try:
            code = container.execute(command[0], *command)
        except Exception as error:  # noqa: BLE001
            code = repr(error)
        if code:
            log("worker did not start (%s)" % (code,))
            return False
        self.container, self.buffer, self.ready = container, hostproto.LineBuffer(), False
        self.starting_version, self.loaded_version = self.version, None
        try:
            self.pid = container.getPID()
        except Exception:  # noqa: BLE001
            self.pid = None
        write_record(self.pid, argv, self.pid_path)
        log("worker started (pid %s, bundle %s)" % (self.pid, os.path.basename(self.bundle_dir or "")))
        args = {"site_domains": self.site_domains, "platform": PLATFORM}
        if self.state_cap:
            args["state_cap_bytes"] = self.state_cap
        self._send("setup", args, None, self._setup_done)
        return True

    def stop(self, reason="stopped") -> None:
        """Plugin close, idle restart, memory kill. Not a death."""
        container, pid = self.container, self.pid
        self._drop(container)
        self._fail_all(reason)
        if container is not None:
            self._kill(container, pid)
            log("worker stopped (%s, pid %s)" % (reason, pid))

    def _drop(self, container):
        self.container, self.pid, self.ready = None, None, False
        self.loaded_version = None
        self.restart_when_idle = False
        if self.timer is not None:
            self.timer.stop()

    def _kill(self, container, pid):
        try:
            container.kill()
        except Exception:  # noqa: BLE001
            if pid:
                try:
                    self.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        clear_record(pid, self.pid_path)

    def _closed(self, container, code):
        if container is not self.container:
            return                           # an old worker we already stopped
        pid = self.pid
        self._drop(container)
        clear_record(pid, self.pid_path)
        log("worker exited by itself (code %s); stderr: %s" % (code, " | ".join(self.stderr_tail[-3:])))
        self._fail_all("worker_died")
        self._death()

    def _death(self):
        now = self.clock()
        self.deaths = [t for t in self.deaths if now - t < DEATHS_WINDOW_S] + [now]
        if len(self.deaths) >= DEATHS_LIMIT:
            self.disabled_until = now + OFF_S
            self.deaths = []
            log("worker died %d times in %d min: device hosts off for %d min"
                % (DEATHS_LIMIT, DEATHS_WINDOW_S // 60, OFF_S // 60))

    # --- calls -----------------------------------------------------------------------------------------------------
    def call(self, op, args, callback) -> bool:
        """callback(ok, result_or_error_code), always later on the reactor or now for a refusal."""
        if op not in hostproto.OPS or op == "setup":
            callback(False, "bad_op")
            return False
        if not self.available():
            callback(False, self.off_reason or ("disabled" if self.bundle_dir else "no_bundle"))
            return False
        if not self.start():
            callback(False, "start_failed")
            return False
        self._send(op, args, callback, None)
        return True

    def _send(self, op, args, callback, internal):
        self.next_id += 1
        call_id = self.next_id
        deadline = hostproto.DEADLINES[op]
        if callback is not None:
            self.pending[call_id] = (callback, self.clock() + deadline + hostproto.KILL_GRACE_S, op)
        else:
            self.internal[call_id] = internal
            self.pending[call_id] = (None, self.clock() + deadline + hostproto.KILL_GRACE_S, op)
        self._write(hostproto.request(call_id, op, args, deadline))
        self._arm()
        return call_id

    def _write(self, data):
        container = self.container
        # eConsoleAppContainer.write takes a str (box 2026-11-23: bytes -> TypeError, every call failed). The
        # protocol line is pure ASCII (hostproto.encode), so decoding cannot change it.
        if isinstance(data, bytes):
            data = data.decode("ascii")
        try:
            container.write(data, len(data))
            return
        except Exception:  # noqa: BLE001 - the two write() signatures of Enigma2 images
            pass
        try:
            container.write(data)
        except Exception as error:  # noqa: BLE001
            log("write failed: %r" % (error,))

    def _arm(self):
        if self.timer is None:
            self.timer = self.timer_factory()
            self.timer.callback.append(self.tick)
        self.timer.start(TICK_MS, False)

    def tick(self):
        """Every second while calls are pending: a call past deadline + 5 s means the worker is stuck."""
        if not self.pending:
            if self.timer is not None:
                self.timer.stop()
            return
        now = self.clock()
        if any(kill_at <= now for _, kill_at, _ in self.pending.values()):
            container, pid = self.container, self.pid
            stuck = [op for _, kill_at, op in self.pending.values() if kill_at <= now]
            log("worker stuck on %s: killed" % ",".join(sorted(set(stuck))))
            self._drop(container)
            self._fail_all("timeout")
            if container is not None:
                self._kill(container, pid)
            self._death()

    def _fail_all(self, code):
        pending, internal = self.pending, self.internal
        self.pending, self.internal = {}, {}
        for call_id, (callback, _, _) in sorted(pending.items()):
            handler = callback or internal.get(call_id)
            if handler is not None and handler is not self._stats_done:
                self._safe(handler, False, code)

    @staticmethod
    def _safe(handler, ok, value):
        try:
            handler(ok, value)
        except Exception as error:  # noqa: BLE001 - a closed Screen's callback must not break the others
            log("callback failed: %r" % (error,))

    # --- answers ----------------------------------------------------------------------------------------------------
    def _stderr(self, data):
        if isinstance(data, bytes):
            data = data.decode("utf-8", "replace")
        for line in str(data).splitlines():
            if line.strip():
                self.stderr_tail = (self.stderr_tail + [line.strip()[:200]])[-20:]

    def _data(self, container, data):
        if container is not self.container:
            return
        for obj in self.buffer.feed(data):
            answer = hostproto.parse_answer(obj) if obj is not hostproto.LineBuffer.GARBAGE else None
            if answer is None or answer[0] not in self.pending:
                continue
            call_id, ok, value = answer
            callback, _, op = self.pending.pop(call_id)
            handler = callback or self.internal.pop(call_id, None)
            if handler is not None:
                self._safe(handler, ok, value)
            if self.container is not container:
                return                       # a handler stopped the worker
            if callback is not None and op != "stats":
                self._send("stats", {}, None, self._stats_done)
        self._idle_check()

    def _setup_done(self, ok, value):
        if ok:
            self.ready, self.last_setup = True, value
            self.loaded_version = self.starting_version
            return
        if value == "no_sqlite3":
            self.off_reason = "no_sqlite3"
            log("python3-sqlite3 missing: device hosts off")
            self.stop("no_sqlite3")
        else:
            log("setup failed: %s" % value)

    def _stats_done(self, ok, value):
        if not ok or not isinstance(value, dict):
            return
        self.last_stats = value
        rss = value.get("rss_kb")
        if not isinstance(rss, int):
            return
        if rss > RSS_KILL_KB:
            log("worker RSS %d MB: killed" % (rss // 1024))
            self.stop("memory")
        elif rss > RSS_RESTART_KB:
            log("worker RSS %d MB: restart when idle" % (rss // 1024))
            self.restart_when_idle = True

    def _idle_check(self):
        if self.restart_when_idle and self.container is not None \
                and all(callback is None and op == "stats" for callback, _, op in self.pending.values()):
            self.stop("restart_idle")


CLIENT = HostClient()


def shutdown() -> None:
    CLIENT.stop("plugin_closed")
