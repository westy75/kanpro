"""085 FR-031: the host worker. Runs the signed bundle's `e2iweb.device` in its own plain python3 process, never in
Enigma2's interpreter, and serves hostproto JSON lines on stdin/stdout.

Started by hostclient.py as
    /usr/bin/env MALLOC_ARENA_MAX=2 /usr/bin/python3 -I -u <plugin>/hostworker/main.py --bundle <root>/<v>
-I: no PYTHONPATH, no user site and no script directory on sys.path; the only extra entry is the bundle dir.
Plain python3 does not see /usr/lib/enigma2/python, so the bundle's shim Components/Plugins never meet Enigma2's.

Rules:
- the protocol owns the real stdout; fd 1 and sys.stdout point at stderr, so a print() in a host file can never
  corrupt a protocol line;
- 3 job threads (2 cores), one lock per site (hosts are not thread-safe): two jobs of one site queue;
- every op has a deadline from the moment its line is read; at the deadline the worker answers "timeout" itself and
  drops the late result. A thread still running 30 s past its deadline is stuck (it may hold a site lock
  forever): the worker exits, and the plugin starts a fresh one on the next call;
- `setup` runs inline on the reader (lines after it wait for it), every other op needs a finished setup.
"""
import argparse
import importlib.util
import inspect
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
THREADS = 3
STUCK_S = 30.0
SITE_OPS = ("sources", "page_sources")


def _load_proto():
    path = os.path.join(os.path.dirname(HERE), "hostproto.py")
    spec = importlib.util.spec_from_file_location("kanasa_hostproto", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proto = _load_proto()


def log(message):
    sys.stderr.write("[Kanasa] hostworker: %s\n" % message)
    sys.stderr.flush()


def _proc_status(path="/proc/self/status"):
    out = {}
    try:
        with open(path, encoding="ascii", errors="replace") as handle:
            for line in handle:
                key, _, value = line.partition(":")
                if key in ("VmRSS", "VmHWM", "VmPeak", "VmSize", "Threads"):
                    out[key] = int(value.split()[0])
    except (OSError, ValueError, IndexError):
        pass
    return out


def _accepts(function, name) -> bool:
    try:
        return name in inspect.signature(function).parameters
    except (TypeError, ValueError):
        return False


class Worker:
    def __init__(self, bundle_dir, out, state_cap_bytes=None, clock=time.monotonic, exit_fn=os._exit):
        self.bundle_dir = bundle_dir
        self.out = out
        self.state_cap_bytes = state_cap_bytes
        self.clock, self.exit_fn = clock, exit_fn
        self.started = clock()
        self.device = None
        self.setup_result = None
        self.write_lock = threading.Lock()
        self.jobs_lock = threading.Lock()
        self.jobs = {}                          # id -> [deadline, answered, started_at]
        self.site_locks = {}
        self.pool = ThreadPoolExecutor(max_workers=THREADS, thread_name_prefix="job")
        self.stop = threading.Event()
        self.done_count = 0

    # --- output --------------------------------------------------------------------------------------------
    def write(self, answer) -> None:
        try:
            line = proto.encode(answer)
        except (TypeError, ValueError):
            line = proto.encode(proto.error(answer.get("id"), "bad_result"))
        with self.write_lock:
            try:
                self.out.write(line)
                self.out.flush()
            except (OSError, ValueError):
                self.stop.set()                 # the plugin is gone

    def answer_once(self, call_id, answer) -> bool:
        with self.jobs_lock:
            job = self.jobs.get(call_id)
            if job is None or job[1]:
                return False
            job[1] = True
        self.write(answer)
        return True

    # --- ops -------------------------------------------------------------------------------------------------
    def op_setup(self, args):
        try:
            import sqlite3  # noqa: F401 - cache.py, catalog/store.py need it (python3-sqlite3 on openATV)
        except ImportError:
            return proto.error(None, "no_sqlite3")
        began = self.clock()
        if self.device is None:
            if self.bundle_dir not in sys.path:
                sys.path.insert(0, self.bundle_dir)
            import e2iweb.device as device  # noqa: E402 - the bundle, from --bundle
            self.device = device
        imported = self.clock()
        site_domains = args.get("site_domains") if isinstance(args.get("site_domains"), dict) else {}
        cap = args.get("state_cap_bytes", self.state_cap_bytes)
        extra = {}
        if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0 and _accepts(self.device.setup,
                                                                                      "state_cap_bytes"):
            extra["state_cap_bytes"] = cap
        platform = args.get("platform")
        if isinstance(platform, str) and platform and _accepts(self.device.setup, "platform"):
            extra["platform"] = platform        # T104: "enigma2" = the box's 25 s sources deadline (bundle 30+)
        result = self.device.setup(site_domains, **extra)
        result = dict(result) if isinstance(result, dict) else {"result": result}
        result["worker"] = {"import_s": round(imported - began, 3), "setup_s": round(self.clock() - imported, 3),
                            "cap_applied": _accepts(self.device.setup, "state_cap_bytes") and bool(cap)}
        self.setup_result = result
        return result

    def op_stats(self, args):
        status = _proc_status()
        version = None
        try:
            with open(os.path.join(self.bundle_dir, "e2iweb", "device", "BUNDLE_VERSION"), encoding="ascii") as f:
                version = f.read().strip()
        except OSError:
            pass
        with self.jobs_lock:
            busy = sum(1 for job in self.jobs.values() if not job[1])
        return {"rss_kb": status.get("VmRSS"), "hwm_kb": status.get("VmHWM"), "vm_kb": status.get("VmSize"),
                "vmpeak_kb": status.get("VmPeak"), "threads": status.get("Threads"),
                "uptime_s": round(self.clock() - self.started, 3), "bundle": version, "pid": os.getpid(),
                "python": "%d.%d.%d" % sys.version_info[:3], "setup": self.setup_result is not None,
                "busy": busy, "done": self.done_count}

    def _device_call(self, op, args):
        device = self.device
        if op == "sources":
            return device.sources(args.get("job"))
        if op == "page_sources":
            if not hasattr(device, "page_sources"):
                return proto.error(None, "update_required")
            return device.page_sources(args.get("job"))
        if op == "resolve":
            client = args.get("client")
            if isinstance(client, dict) and _accepts(device.resolve, "client"):
                return device.resolve(args.get("ref"), client=client)
            return device.resolve(args.get("ref"))
        if op == "hls_media":
            if not hasattr(device, "hls_media"):
                return proto.error(None, "update_required")
            return device.hls_media(args.get("stream_url"), args.get("headers") or {})
        if op == "torrents":
            return device.torrents(args.get("job"))
        if op in ("subscene", "subscene_file"):  # 086: sub-scene Arabic from the box's own connection (bundle 32+)
            if not hasattr(device, op):
                return proto.error(None, "update_required")
            if op == "subscene":
                return device.subscene(args.get("job"))
            return device.subscene_file(args.get("sub_id"), args.get("season"), args.get("episode"))
        return proto.error(None, "bad_op")

    def _site_lock(self, op, args):
        if op not in SITE_OPS:
            return None
        job = args.get("job")
        site = job.get("site") if isinstance(job, dict) else None
        if not isinstance(site, str) or not site:
            return None
        with self.jobs_lock:
            return self.site_locks.setdefault(site, threading.Lock())

    def _run_job(self, call_id, op, args, deadline):
        lock = self._site_lock(op, args)
        if lock is not None and not lock.acquire(timeout=max(0.0, deadline - self.clock())):
            self.answer_once(call_id, proto.error(call_id, "timeout"))
            return
        try:
            with self.jobs_lock:
                job = self.jobs.get(call_id)
                if job is not None:
                    job[2] = self.clock()
            try:
                result = self._device_call(op, args)
                answer = result if self._is_error(result) else proto.ok(call_id, result)
            except Exception as error:  # noqa: BLE001 - only the type name: messages can carry URLs
                answer = proto.error(call_id, "exception:" + type(error).__name__)
            answer["id"] = call_id
            self.answer_once(call_id, answer)
        finally:
            if lock is not None:
                lock.release()
            with self.jobs_lock:
                self.jobs.pop(call_id, None)
                self.done_count += 1

    @staticmethod
    def _is_error(result):
        return isinstance(result, dict) and result.get("ok") is False and set(result) == {"id", "ok", "error"}

    # --- dispatch ------------------------------------------------------------------------------------------
    def handle(self, obj) -> None:
        if obj is proto.LineBuffer.GARBAGE:
            self.write(proto.error(None, "bad_request"))
            return
        call_id, op, args, deadline_s = proto.parse_request(obj)
        if op is None:
            self.write(proto.error(call_id, "bad_request"))
            return
        if op in ("setup", "stats"):
            try:
                result = self.op_setup(args) if op == "setup" else self.op_stats(args)
                answer = result if self._is_error(result) else proto.ok(call_id, result)
            except Exception as error:  # noqa: BLE001
                log("%s failed: %s" % (op, type(error).__name__))
                answer = proto.error(call_id, "exception:" + type(error).__name__)
            answer["id"] = call_id
            self.write(answer)
            return
        if self.setup_result is None:
            self.write(proto.error(call_id, "not_setup"))
            return
        with self.jobs_lock:
            if call_id in self.jobs:
                duplicate = True
            else:
                duplicate = False
                self.jobs[call_id] = [self.clock() + deadline_s, False, None]
        if duplicate:
            self.write(proto.error(call_id, "duplicate_id"))
            return
        self.pool.submit(self._run_job, call_id, op, args, self.clock() + deadline_s)

    def watchdog_tick(self) -> None:
        now = self.clock()
        expired, stuck = [], False
        with self.jobs_lock:
            for call_id, job in self.jobs.items():
                if now >= job[0] and not job[1]:
                    expired.append(call_id)
                if now >= job[0] + STUCK_S:
                    stuck = True
        for call_id in expired:
            self.answer_once(call_id, proto.error(call_id, "timeout"))
        if stuck:
            log("a job is stuck %.0f s past its deadline: exiting" % STUCK_S)
            self.exit_fn(3)

    def _watchdog(self):
        while not self.stop.wait(0.2):
            self.watchdog_tick()

    def serve(self, fd=0) -> None:
        threading.Thread(target=self._watchdog, name="watchdog", daemon=True).start()
        buffer = proto.LineBuffer()
        while not self.stop.is_set():
            try:
                chunk = os.read(fd, 65536)
            except InterruptedError:
                continue
            except OSError:
                break
            if not chunk:
                break                            # the plugin closed our stdin: stop
            for obj in buffer.feed(chunk):
                self.handle(obj)
        self.stop.set()


def _limit_address_space(megabytes) -> None:
    if not megabytes:
        return
    try:
        import resource
        limit = int(megabytes) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ImportError, ValueError, OSError) as error:
        log("RLIMIT_AS not set: %s" % type(error).__name__)


def _install_netpin(mode) -> None:
    """netpin.py: short per-address connects, failed addresses last, IPv4 on a box without global IPv6."""
    try:
        spec = importlib.util.spec_from_file_location("kanasa_netpin", os.path.join(HERE, "netpin.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        family = module.install(mode)
        log("connect guard on, family %s" % family)
    except Exception as error:  # noqa: BLE001 - without it the worker still runs (urllib3 defaults)
        log("connect guard not installed: %s" % type(error).__name__)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--rlimit-as-mb", type=int, default=0)
    parser.add_argument("--state-cap-bytes", type=int, default=0)
    parser.add_argument("--nice", type=int, default=10)
    parser.add_argument("--family", choices=("auto", "4", "6", "any", "off"), default="auto")
    args = parser.parse_args(argv)
    if args.nice:
        try:
            os.nice(args.nice)
        except OSError:
            pass
    _limit_address_space(args.rlimit_as_mb)
    if args.family != "off":
        _install_netpin(args.family)
    out = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)                               # print() in bundle code goes to stderr, never into the protocol
    sys.stdout = sys.stderr
    worker = Worker(os.path.abspath(args.bundle), out, args.state_cap_bytes or None)
    worker.serve(0)
    worker.pool.shutdown(wait=False, cancel_futures=True)
    os._exit(0)                                 # job threads stuck in a host must not keep the process alive


if __name__ == "__main__":
    sys.exit(main())
