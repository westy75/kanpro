"""Our own exteplayer3 process (Settings → Player = "Kanasa exteplayer3", phase 1).

A thin owner of ONE /usr/bin/exteplayer3 run on an eConsoleAppContainer, started with an argument list (never a
shell string). Enigma2 is imported only by the default factories, so tests drive it with fakes.

Rules that bind (design 2026-11-19, docs/superpowers/specs/2026-11-19-enigma2-own-exteplayer3.md):
- two players at once are a known crash trigger: a new run starts only after the previous one EXITED, and only
  when no other exteplayer3 (ServiceApp's included) is alive - waited for BUSY_WAIT_MS at most, else refused;
- stop = `q`, wait QUIT_WAIT_MS for the exit, SIGTERM, then SIGKILL TERM_WAIT_MS later; `then(exited)` runs only
  from the exit (or after the SIGKILL was seen to land / gave up), never from a plain timer;
- a pid record lets the next Enigma2 reap a run orphaned by a GUI restart (exact argv check, never pkill).
"""
import json
import os
import signal
import time

from . import extcmd, extproto

PID_FILE = "/tmp/kanasa-exteplayer3.pid"
QUIT_WAIT_MS, TERM_WAIT_MS, KILL_WAIT_MS = 3000, 5000, 5000
BUSY_WAIT_MS, BUSY_STEP_MS, DEATH_STEP_MS, LAUNCH_DELAY_MS = 12000, 300, 250, 400
EXTRAPOLATE_S = 2.0
SEEK_REPLY_S = 3.0           # a seek whose PLAYBACK_SEEK_ABS never comes stops hiding J lines after this          # the subtitle clock runs on between two J answers, never further than this

_LIVE = set()                # ExtPlayers with a run or a stop in flight: kept alive whatever the Screen does


def log(message):
    print("[Kanasa] ext: %s" % message)


def _enigma_container():
    from enigma import eConsoleAppContainer
    return eConsoleAppContainer()


def _enigma_timer():
    from enigma import eTimer
    return eTimer()


# --- /proc ------------------------------------------------------------------------------------------------
def cmdline(pid, proc_root="/proc"):
    try:
        with open(os.path.join(proc_root, str(pid), "cmdline"), "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def is_exteplayer3(argv) -> bool:
    return bool(argv) and os.path.basename(argv[0]) == "exteplayer3"


def alive(pid, proc_root="/proc") -> bool:
    """A process that exists and is not a zombie (a SIGKILLed child waits as Z until it is reaped)."""
    try:
        with open(os.path.join(proc_root, str(pid), "stat"), "rb") as handle:
            stat = handle.read().decode("ascii", "replace")
    except OSError:
        return False
    state = stat.rsplit(")", 1)[-1].split()
    return bool(state) and state[0] not in ("Z", "X")


def player_pids(proc_root="/proc") -> list:
    """Every exteplayer3 alive on the box (ours, ServiceApp's, a leftover)."""
    try:
        names = os.listdir(proc_root)
    except OSError:
        return []
    return sorted(int(name) for name in names
                  if name.isdigit() and is_exteplayer3(cmdline(name, proc_root)) and alive(name, proc_root))


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
        pid, argv = value.get("pid"), value.get("argv")
        if isinstance(pid, int) and pid > 0 and isinstance(argv, list):
            return pid, argv
    except (OSError, ValueError, AttributeError):
        pass
    return None


def clear_record(pid=None, path=PID_FILE):
    """Remove the record - never a newer run's record from an old run's exit."""
    record = read_record(path)
    if pid is None or record is None or record[0] == pid:
        try:
            os.unlink(path)
        except OSError:
            pass


def live_pids(record=None) -> set:
    """FIX33: the pids of the runs (playing or being stopped) that a live ExtPlayer of THIS Enigma2 owns (with
    `record`: only the players that keep that pid record - the ones a reap of it could hit)."""
    pids = set()
    for player in list(_LIVE):
        if record is not None and getattr(player, "record", record) != record:
            continue
        for run in (getattr(player, "run", None), getattr(player, "stopping", None)):
            pid = getattr(run, "pid", None)
            if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
                pids.add(pid)
    return pids


def reap_orphan(path=PID_FILE, proc_root="/proc", kill=os.kill, sleep=time.sleep, clock=time.monotonic,
                owned=None) -> bool:
    """Stop the exteplayer3 a killed Enigma2 left behind, if and only if it is exactly the one we started.

    FIX33 (box 2026-11-20 18:25:13): a second Player's launch reaped the FIRST Player's live run, whose ExtPlayer
    then reported "exited by itself" and the first Player failed over. A recorded pid that a live ExtPlayer of this
    Enigma2 owns (`owned(pid)`, default: live_pids) is never signalled here: its owner stops it, and the
    caller waits for it like for any other running exteplayer3 (or refuses to start)."""
    record = read_record(path)
    if record is None:
        clear_record(None, path)
        return False
    pid, argv = record
    is_owned = owned if owned is not None else (lambda value: value in live_pids(path))
    if is_owned(pid):
        log("exteplayer3 pid %s belongs to a live player here: not reaped" % pid)
        return False
    if not alive(pid, proc_root) or cmdline(pid, proc_root) != argv:
        clear_record(pid, path)
        return False
    for sig, wait in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 1.0)):
        try:
            kill(pid, sig)
        except OSError:
            break
        deadline = clock() + wait
        while alive(pid, proc_root) and clock() < deadline:
            sleep(0.1)
        if not alive(pid, proc_root):
            break
    clear_record(pid, path)
    log("stopped orphaned exteplayer3 (pid %s)" % pid)
    return True


# --- one shot timers ------------------------------------------------------------------------------------------
class _OneShot:
    def __init__(self, factory):
        self.factory, self.timer = factory, None

    def start(self, ms, fn):
        self.stop()
        timer = self.factory()
        timer.callback.append(fn)
        timer.start(int(ms), True)
        self.timer = timer

    def stop(self):
        timer, self.timer = self.timer, None
        if timer is not None:
            timer.stop()


class _Run:
    def __init__(self, argv, container):
        self.argv, self.container, self.pid = list(argv), container, None
        self.parser = extproto.Parser()


# --- the player process -----------------------------------------------------------------------------------------
class ExtPlayer:
    """on_event(Event) gets every line of the CURRENT run; on_exit(code) gets an exit nobody asked for."""

    def __init__(self, on_event, on_exit, container_factory=None, timer_factory=None, clock=time.monotonic,
                 kill=os.kill, proc_root="/proc", record=PID_FILE, sleep=time.sleep):
        self.on_event, self.on_exit = on_event, on_exit
        self.container_factory = container_factory or _enigma_container
        timers = timer_factory or _enigma_timer
        self.clock, self.kill, self.proc_root, self.record, self.sleep = clock, kill, proc_root, record, sleep
        self.run, self.stopping, self.waiters, self.serial = None, None, [], 0
        self.t_launch, self.t_quit, self.t_term, self.t_death = (_OneShot(timers) for _ in range(4))
        self.death_deadline = 0.0
        self.last_url, self.last_subs, self.last_audio = None, [], []
        self.reset()

    def reset(self):
        """Per-run state, all from the JSON lines (Enigma2's service APIs see nothing in this mode)."""
        self.ms, self.lms, self.latched, self.length = None, None, 0.0, None
        self.playing, self.paused, self.video, self.version, self.errors = False, False, None, None, []
        self.subs, self.audio, self.sub_current, self.audio_current = [], [], None, None
        self.seek_sent = None                    # when a gf went out: J lines until its reply are the old place
        # FIX31: the raw last J ms (never the clock's run-on, never a pause's latch) and whether two J lines
        # with ms > 0 have risen: only that is a playing film (a dead torrent sends v_c and nothing more)
        self.j_ms, self.moving = None, False

    # --- state ----------------------------------------------------------------------------------------------
    def busy(self) -> bool:
        """A run or a stop is in flight (a launch must wait for its exit)."""
        return self.run is not None or self.stopping is not None

    def running(self) -> bool:
        return self.run is not None

    def position(self):
        """Seconds: the last J, run on by the wall clock while playing (at most EXTRAPOLATE_S), like e2iplayer's
        subtitle clock (XMP:1094-1112). None before the first J."""
        if self.ms is None:
            return None
        at = self.ms / 1000.0
        if self.run is not None and self.playing and not self.paused:
            at += max(0.0, min(self.clock() - self.latched, EXTRAPOLATE_S))
        return at

    def duration(self):
        return float(self.length) if isinstance(self.length, int) and self.length > 0 else None

    def buffered(self):
        """Seconds: J.lms = how far exteplayer3 has loaded (e2iplayer's bufferingCBar, XMP:1338-1342, 1756-1757),
        for the grey buffer bar. None before a J with lms, and after a seek until the first J of the new place."""
        return self.lms / 1000.0 if self.lms is not None else None

    def picture(self) -> bool:
        """FIX31 (box 2026-11-20): the film really plays = two J lines with ms > 0 and rising. v_c alone is the
        container's header: a dead torrent (205 B/s) sent it and then nothing, which looked like "playing"."""
        return self.moving

    def raw_position(self):
        """Seconds of the last J line as exteplayer3 sent it (no wall-clock run-on): the stall clock reads this,
        because the run-on moves between two frozen J lines. None before the first J."""
        return self.j_ms / 1000.0 if self.j_ms is not None else None

    def seeking(self) -> bool:
        """A gf is out and its answer not yet in (SEEK_REPLY_S at most): its re-buffer is not a stall."""
        return self.seek_sent is not None and self.clock() - self.seek_sent < SEEK_REPLY_S

    def width(self) -> int:
        return (self.video or {}).get("width") or 0

    def height(self) -> int:
        return (self.video or {}).get("height") or 0

    # --- commands --------------------------------------------------------------------------------------------
    def send(self, lines) -> bool:
        if self.run is None:
            return False
        self._write(self.run, lines)
        return True

    def poll(self):
        """l + j once PLAYBACK_PLAY said the film plays (e2iplayer starts its 1 s poll there too)."""
        if self.run is not None and self.playing:
            self._write(self.run, extcmd.poll())

    def seek(self, seconds):
        """Absolute (gf, measured: PLAYBACK_SEEK_ABS answers it). The position stays honest: J lines that come
        before that answer are dropped (they still name the old place), the first one after it is the new
        place. `c` goes first, so a paused film plays again."""
        if self.send(extcmd.seek_abs(seconds)):
            self.seek_sent, self.paused = self.clock(), False
            self.lms = None                          # the old place's loaded end says nothing about the new one

    def set_pause(self, paused):
        if self.send(extcmd.pause() if paused else extcmd.resume()):
            at = self.position()
            if at is not None:                       # the clock holds where the pause began, restarts from there
                self.ms = int(at * 1000)
            self.paused, self.latched = bool(paused), self.clock()

    def subtitle(self, track_id):
        return self.send(extcmd.sub(track_id) if track_id is not None else extcmd.sub_off())

    def audio_track(self, track_id):
        """The Audio panel's pick: c + a<id> + ac (the a_l id; a_s answers, ac makes a_c report the new one)."""
        return self.send(extcmd.audio(track_id))

    def audio_index_for(self, url, track_id):
        """-t for a new run of the SAME url (whose a_l list we know): the INDEX of track_id in that list."""
        if url != self.last_url or track_id is None:
            return None
        return extproto.audio_index(self.last_audio, track_id)

    def sub_index_for(self, url, track_id):
        """-9 for a new run of the SAME url while that in-video track shows (failover retry, quality restart)."""
        if url != self.last_url or track_id is None:
            return None
        return extproto.sub_index(self.last_subs, track_id)

    # --- launch -----------------------------------------------------------------------------------------------
    def launch(self, argv, done):
        """Start argv once nothing else plays: our previous run is stopped (and has EXITED), our recorded orphan
        reaped, a foreign exteplayer3 waited for. done(ok, reason) - not called for a launch replaced by a newer
        one or cancelled by stop()."""
        self.serial += 1
        serial, deadline = self.serial, self.clock() + BUSY_WAIT_MS / 1000.0
        self.t_launch.stop()
        self.reset()                             # nothing of the previous run may look like this one's picture
        _LIVE.add(self)
        self._stop(lambda exited: self._when_free(argv, done, serial, deadline, exited))

    def _when_free(self, argv, done, serial, deadline, exited):
        if serial != self.serial:
            return
        if not exited:
            done(False, "the previous exteplayer3 did not exit")
            return
        # yield to the GUI loop after nav.stopService before the decoder is taken (ServiceFlow's delay)
        self.t_launch.start(LAUNCH_DELAY_MS, lambda: self._try_start(argv, done, serial, deadline))

    def _try_start(self, argv, done, serial, deadline):
        if serial != self.serial:
            return
        if self.busy():                              # defensive: a launch only starts from an idle player
            done(False, "a run is still in flight")
            return
        try:
            reap_orphan(self.record, self.proc_root, self.kill, self.sleep, self.clock)
        except Exception as error:  # noqa: BLE001 - a failed reap is checked by the scan below
            log("reap failed: %r" % (error,))
        others = player_pids(self.proc_root)
        if others:
            if self.clock() < deadline:
                self.t_launch.start(BUSY_STEP_MS, lambda: self._try_start(argv, done, serial, deadline))
            else:
                log("another exteplayer3 still runs (pid %s): not starting a second one" % others)
                done(False, "another exteplayer3 is running")
            return
        container = self.container_factory()
        run = _Run(argv, container)
        container.appClosed.append(lambda code: self._closed(run, code))
        container.stderrAvail.append(lambda data: self._data(run, data))
        container.dataAvail.append(lambda data: None)          # stdout: drained, dropped
        try:
            code = container.execute(argv[0], *argv)          # path, then argv[0..]: no shell
        except Exception as error:  # noqa: BLE001 - the binary is missing / the image API differs
            code = repr(error)
        if code:
            log("exteplayer3 did not start (%s)" % (code,))
            done(False, "exteplayer3 did not start")
            return
        try:
            run.pid = container.getPID()
        except Exception:  # noqa: BLE001 - older images: kill() still works
            run.pid = None
        write_record(run.pid, argv, self.record)
        self.reset()
        self.run = run
        _LIVE.add(self)                          # FIX33: a running player is live (live_pids: never reaped)
        self.last_url = argv[-1]
        log("exteplayer3 started (pid %s)" % run.pid)
        done(True, None)

    # --- stop -------------------------------------------------------------------------------------------------
    def stop(self, then=None):
        """Stop the run (and cancel a launch that waits). then(exited) runs once the process has gone."""
        self.serial += 1
        self.t_launch.stop()
        self.reset()
        self._stop(then)

    def _stop(self, then):
        if then is not None:
            self.waiters.append(then)
        if self.run is not None:
            run, self.run = self.run, None
            self.stopping = run
            self._write(run, extcmd.quit())
            self.t_quit.start(QUIT_WAIT_MS, lambda: self._term(run))
            return
        if self.stopping is None:
            self._release(True)

    def _term(self, run):
        if self.stopping is not run:
            return
        log("exteplayer3 ignored q for %d s: SIGTERM" % (QUIT_WAIT_MS // 1000))
        self._signal(run, signal.SIGTERM)
        self.t_term.start(TERM_WAIT_MS, lambda: self._kill(run))

    def _kill(self, run):
        if self.stopping is not run:
            return
        log("exteplayer3 ignored SIGTERM: SIGKILL")
        try:
            run.container.kill()
        except Exception:  # noqa: BLE001
            pass
        self._signal(run, signal.SIGKILL)
        self.death_deadline = self.clock() + KILL_WAIT_MS / 1000.0
        self.t_death.start(DEATH_STEP_MS, lambda: self._await_death(run))

    def _await_death(self, run):
        if self.stopping is not run:
            return
        if run.pid is None or not alive(run.pid, self.proc_root):
            self._stopped(run, True)
        elif self.clock() < self.death_deadline:
            self.t_death.start(DEATH_STEP_MS, lambda: self._await_death(run))
        else:
            log("exteplayer3 pid %s survived SIGKILL" % run.pid)
            self._stopped(run, False)

    def _signal(self, run, sig):
        if run.pid:
            try:
                self.kill(run.pid, sig)
            except OSError:
                pass

    def _stopped(self, run, exited):
        for timer in (self.t_quit, self.t_term, self.t_death):
            timer.stop()
        if exited:
            clear_record(run.pid, self.record)
        self.stopping = None
        log("exteplayer3 stopped (pid %s, exited=%s)" % (run.pid, exited))
        self._release(exited)

    def _release(self, exited):
        waiters, self.waiters = self.waiters, []
        if not self.busy() and not waiters:
            _LIVE.discard(self)
        for then in waiters:
            then(exited)
        if not self.busy() and not self.waiters:
            _LIVE.discard(self)

    # --- the process' callbacks -------------------------------------------------------------------------------
    def _closed(self, run, code):
        if run is self.stopping:
            self._stopped(run, True)
            return
        if run is not self.run:
            return
        self.run = None
        clear_record(run.pid, self.record)
        log("exteplayer3 exited by itself (%s)" % (code,))
        _LIVE.discard(self)
        self.on_exit(code)

    def _data(self, run, chunk):
        if run is not self.run:
            return                                   # a run being stopped: its last lines are not the new film
        for event in run.parser.feed(chunk):
            if run is not self.run:
                return
            self._apply(event)
            self.on_event(event)

    def _apply(self, event):
        kind, data = event
        if kind == "position":
            if not self.playing:
                # box 11-24: the first J after a launch can be the previous run's (95443 s, 1822 s, 180 s before
                # the real 0.9 s); before PLAYBACK_PLAY it is never this run's place. After it, a stale one is
                # followed by a LOWER real one, so it never makes the rising pair that is a picture (FIX31).
                return
            if self.seek_sent is not None and self.clock() - self.seek_sent < SEEK_REPLY_S:
                return
            self.ms, self.latched = data["ms"], self.clock()
            if self.j_ms is not None and self.j_ms > 0 and data["ms"] > self.j_ms:
                self.moving = True
            self.j_ms = data["ms"]
            if data["lms"] is not None:
                self.lms = data["lms"]
        elif kind in ("seek", "seek_abs"):
            self.seek_sent = None
        elif kind == "length":
            self.length = data["length"]
        elif kind == "play":
            self.playing = data["sts"] == 0
        elif kind == "stop":
            self.playing = False
        elif kind == "status":
            self.paused = data["paused"]
        elif kind == "video":
            self.video = data
        elif kind == "sub_list":
            self.subs = self.last_subs = data
        elif kind == "audio_list":
            self.audio = self.last_audio = data
        elif kind == "sub_current":
            self.sub_current = data
        elif kind == "audio_current":
            self.audio_current = data
        elif kind == "audio_selected":
            if data["sts"] == 0:                     # a2 -> {"a_s":{"id":2,"sts":0}} (box run3): a_c may not follow
                row = next((r for r in self.audio if r.get("id") == data["id"]), None)
                self.audio_current = dict(row) if row else {"id": data["id"], "codec": "", "lang": ""}
        elif kind == "version":
            self.version = data["version"]
        elif kind == "error":
            self.errors.append(data)

    def _write(self, run, lines):
        data = extcmd.encode(lines)
        try:
            run.container.write(data, len(data))
            return
        except Exception:  # noqa: BLE001 - the two write() signatures of Enigma2 images (as e2iplayer)
            pass
        try:
            run.container.write(data)
        except Exception as error:  # noqa: BLE001
            log("write %r failed: %r" % (lines, error))


def installed(path=extproto.EXE) -> bool:
    return os.access(path, os.X_OK)
