"""Play with buffering (FIX12): the one TEMPORARY download that runs ahead of the player (no Enigma2 imports).

Like E2iPlayer's "play with buffering": the stream the player would play is downloaded into
<disk>/kanasa-downloads/.buffer/ and the player plays that growing file from the disk. Not a Download: it is not in
downloads.json or the Downloads page, does not count against Settings → Simultaneous downloads, runs one at a time
(starting a new one stops the old one), and queued downloads give way to it (bufferrules.hold_downloads). Its files
go when it stops, and any leftovers when the plugin starts. Source links are never logged.
"""
import os
import shutil
import threading
import time
import uuid

from . import bufferrules as rules, downloadrules, playback

SUBDIR = rules.SUBDIR
CHUNK = 256 * 1024
TIMEOUT = 20
WAITS_S = (2, 5, 10, 20)               # a broken connection: Range resume after these waits, then the job fails
SPACE_CHECK_BYTES = 16 * 1024 ** 2
MOOV_MAX = 64 * 1024 ** 2              # an MP4 whose index is not within this many first bytes is streamed
JUMP_BYTES = 16 * 1024 ** 2            # an MP4 resume this far past the bytes read: Range from the resume point

_LOCK = threading.Lock()
ACTIVE = None                          # the one running Job


def log(message):
    print("[Kanasa] buffer: %s" % message)


class Failed(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class Cancelled(Exception):
    pass


def _requests_http(url, headers, timeout):
    import requests  # on the box; imported late so tests never need it
    return requests.get(url, headers=headers, stream=True, timeout=timeout)


def directory_for(download_dir):
    return os.path.join(download_dir, SUBDIR) if download_dir else None


def cleanup(download_dir, keep=None) -> int:
    """Delete every leftover file in .buffer/ (the plugin starts; a GUI restart killed a job). Returns the count."""
    folder, removed = directory_for(download_dir), 0
    if not folder or not os.path.isdir(folder):
        return 0
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if keep and os.path.abspath(path) == os.path.abspath(keep):
            continue
        try:
            os.unlink(path)
            removed += 1
        except OSError:
            pass
    return removed


def holding() -> bool:
    """Downloads wait while this is True (the buffer is the highest priority)."""
    job = ACTIVE
    return bool(job) and job.holding()


def start(result, start_at, download_dir, minutes, **kwargs):
    """Stop any running buffer and start a new one for `result` (the player's resolved row)."""
    global ACTIVE
    job = Job(result, start_at, download_dir, minutes, **kwargs)
    with _LOCK:
        old, ACTIVE = ACTIVE, job
    if old is not None:
        old.stop()
    job.start()
    return job


def stop(job=None):
    """Stop `job` (or whichever runs) and delete its files."""
    global ACTIVE
    with _LOCK:
        if job is None or job is ACTIVE:
            job, ACTIVE = ACTIVE if job is None else job, None
    if job is not None:
        job.stop()


class Job:
    def __init__(self, result, start_at, download_dir, minutes, http=None, disk_usage=None, sleep=None, base="",
                 hls_height=None):
        self.result, self.start_at, self.minutes = dict(result or {}), float(start_at or 0), rules.clean(minutes)
        self.hls_height = hls_height                         # prefs.hls_height: a master is picked like the player
        self.folder = directory_for(download_dir)
        self.http = http or _requests_http
        self.disk_usage = disk_usage or (lambda path: shutil.disk_usage(path))
        self.sleep = sleep or time.sleep
        self.base = base
        self.fmt = rules.container(self.result)
        self.mode = rules.start_mode(self.fmt, self.start_at, self.minutes)
        ext = ".mp4" if self.fmt == "mp4" else ".mkv" if self.fmt == "mkv" else ".ts"
        self.path = os.path.join(self.folder, uuid.uuid4().hex[:12] + ext) if self.folder else None
        self.lock = threading.Lock()
        self.cancelled = threading.Event()
        self.thread = None
        self.state, self.error = "starting", None          # starting | running | done | failed | stopped
        self.start_s, self.end_s, self.base_s = 0.0, 0.0, 0.0
        self.duration, self.size, self.written = 0.0, 0, 0
        self.ready_path = None                              # set once the file exists with playable bytes
        self.play_at = self.start_at                        # the player's position (for the downloads' hold)
        self.duration_hint = 0.0                            # MKV/TS: the length the player reports
        self.index, self.region = None, 0

    # -- the player's view (GUI thread)
    @property
    def can_restart(self) -> bool:
        return self.fmt in rules.ONLINE_FORMATS

    def snapshot(self) -> dict:
        with self.lock:
            return {"state": self.state, "error": self.error, "path": self.ready_path, "fmt": self.fmt,
                    "start_s": self.start_s, "end_s": self.end_s, "base_s": self.base_s,
                    "duration": self.duration, "done": self.state == "done", "can_restart": self.can_restart}

    def holding(self) -> bool:
        with self.lock:
            running, end_s = self.state in ("starting", "running"), self.end_s
        return rules.hold_downloads(running, rules.ahead(end_s, self.play_at), self.minutes)

    # -- lifetime
    def start(self):
        self.thread = threading.Thread(target=self._main, name="KanasaBuffer", daemon=True)
        self.thread.start()

    def stop(self):
        """Cancel and delete the file at once (Linux frees the space when the thread lets go of it)."""
        self.cancelled.set()
        with self.lock:
            if self.state in ("starting", "running"):
                self.state = "stopped"
        self._delete()

    def _delete(self):
        try:
            if self.path:
                os.unlink(self.path)
        except OSError:
            pass

    def _set(self, **values):
        with self.lock:
            if self.state == "stopped":
                values.pop("state", None)            # a stopped job never comes back
            for key, value in values.items():
                setattr(self, key, value)

    def _check(self):
        if self.cancelled.is_set():
            raise Cancelled()

    def _main(self):
        try:
            if not self.path:
                raise Failed("no_disk")
            os.makedirs(self.folder, exist_ok=True)
            if self.fmt == "hls":
                self._hls()
            else:
                self._file()
            self._set(state="done")
            log("complete")
        except Cancelled:
            pass
        except Exception as error:  # noqa: BLE001 - the player falls back to streaming
            if self.cancelled.is_set():
                # FIX29 (box 01:00:36 "failed: FileNotFoundError"): stop() deletes the file at once, so the thread
                # can meet a missing file (or a closed response) before its next _check. That is the stop, not a fault.
                return
            code = error.code if isinstance(error, Failed) else "error"
            log("failed: %s" % (code if isinstance(error, Failed) else type(error).__name__))
            self._set(state="failed", error=code)
        finally:
            if self.cancelled.is_set():
                self._delete()

    # -- I/O helpers
    def _target(self):
        return playback.stream_target(self.result, self.base, False, True)[0]

    def _headers(self, start=0):
        url, headers = playback.stream_target(self.result, self.base, False, True)
        headers = dict(headers)
        if start:
            headers["Range"] = "bytes=%d-" % start
        return headers

    def _free_ok(self):
        try:
            free = self.disk_usage(self.folder).free
        except OSError:
            return False
        return free >= downloadrules.RESERVE_BYTES

    def _get(self, url, start=0):
        """A response (200/206), after a few short waits on network errors; Failed otherwise."""
        for attempt in range(len(WAITS_S) + 1):
            self._check()
            try:
                response = self.http(url, self._headers(start), TIMEOUT)
            except Exception:  # noqa: BLE001 - timeout, reset, DNS
                if attempt >= len(WAITS_S):
                    raise Failed("network") from None
                self.sleep(WAITS_S[attempt])
                continue
            if response.status_code in (200, 206):
                return response
            response.close()
            raise Failed("expired" if downloadrules.should_reresolve(response.status_code) else "http")
        raise Failed("network")

    def _text(self, url):
        response = self._get(url)
        try:
            return response.text
        finally:
            response.close()

    # -- HLS: the VOD playlist's segments joined into one .ts, from the segment of the resume point
    # 0.8.1 (owner 11-24, E2iPlayer's HLS buffer: 70% CPU, 3-10 s jumps): plain byte-append, no ffmpeg; each
    # segment whole in RAM (one at a time) and written only once complete; a segment is never skipped.
    def _hls(self):
        url = self._target()                                  # the media playlist the player plays (hls_media_ready)
        text = self._text(url)
        if "#EXT-X-STREAM-INF" in text:                       # still a master: the player's own pick
            folder = url.split("?")[0].split("#")[0].rsplit("/", 1)[0]     # the children resolve against the master
            pick = playback.start_quality(playback.hls_ladder(text, folder), self.hls_height)
            url = pick["stream_url"] if pick else playback.hls_variant(text, folder)
            if not url:
                raise Failed("unsupported")
            text = self._text(url)
        media = downloadrules.hls_media(text, url)
        durations = rules.hls_durations(text)
        first, base = rules.hls_start(durations, self.start_at if self.mode == "offset" else 0)
        problem = rules.hls_refusal(text, first)
        if problem is None and len(durations) != len(media["segments"]):
            problem = "unsupported"
        if problem:
            log("hls not buffered: %s" % problem)
            raise Failed(problem)
        self._set(state="running", duration=sum(durations), base_s=base, start_s=base, end_s=base)
        end, written, total = base, 0, len(durations) - first
        with open(self.path, "wb") as handle:
            for index in range(first, len(durations)):
                self._check()
                body = self._segment(media["segments"][index])
                self._room(len(body), written, index - first, total)
                self._append(handle, body)
                written += len(body)
                end += durations[index]
                self._set(end_s=end, written=written, ready_path=self.path)
                body = None

    def _append(self, handle, body):
        handle.write(body)
        handle.flush()

    def _room(self, nbytes, written, done, total):
        """The disk never goes under the 1 GB reserve: the job stops growing (Failed "space"), the file keeps what is
        there and plays on until the stall -> stream rule. Once, after the first segment, an estimate that the rest
        does not fit is logged (the whole episode on a small disk)."""
        try:
            free = self.disk_usage(self.folder).free
        except OSError:
            raise Failed("space") from None
        if done == 1 and written:
            rest = downloadrules.hls_estimate(written, done, total) - written
            if not downloadrules.enough_space(free, rest):
                log("space: the rest (~%s) does not fit; buffering until the reserve" % downloadrules.gb(rest))
        if free - nbytes < downloadrules.RESERVE_BYTES:
            raise Failed("space")

    def _segment(self, url):
        """One whole segment (bytes), after WAITS_S on a broken / short / refused fetch; Failed("expired") at once on
        403/410 (downloadrules.should_reresolve), else Failed after the last wait. Never a part of a segment."""
        code = "network"
        for attempt in range(len(WAITS_S) + 1):
            self._check()
            body = None
            try:
                response = self.http(url, self._headers(), TIMEOUT)
            except Exception:  # noqa: BLE001 - timeout, reset, DNS
                response, code = None, "network"
            if response is not None:
                try:
                    status = response.status_code
                    if downloadrules.should_reresolve(status):
                        raise Failed("expired")
                    if status in (200, 206):
                        body = self._whole(response)
                        code = "network"
                    else:
                        code = "http"
                except Failed:
                    raise
                except Exception:  # noqa: BLE001 - the connection broke mid-segment
                    body, code = None, "network"
                finally:
                    response.close()
            if body:
                return body
            if attempt >= len(WAITS_S):
                break
            self.sleep(WAITS_S[attempt])
        raise Failed(code)

    @staticmethod
    def _whole(response):
        """The body, or None when it is shorter than its Content-Length (cut mid-segment)."""
        body = response.content
        length = next((str(v) for k, v in (response.headers or {}).items() if str(k).lower() == "content-length"), "")
        if length.isdigit() and int(length) != len(body):
            return None
        return body

    # -- one progressive file (MP4 with its index at the start, MKV, TS)
    def _file(self):
        url = self._target()
        response = self._get(url)
        total = downloadrules.total_size(response.status_code, response.headers, 0)
        self._set(state="running", size=total)
        with open(self.path, "wb") as handle:
            pass
        chunks, done = response.iter_content(CHUNK), 0
        if self.fmt == "mp4":
            response, chunks, done = self._mp4_head(url, response, chunks, total)
        self._body(url, response, chunks, done, total)

    def _mp4_head(self, url, response, chunks, total):
        """Read until the moov is complete (it must come before mdat: FIX10 rule), then go on from the bytes
        read (the same chunk iterator), or Range-jump to the resume point."""
        prefix = bytearray()
        try:
            for chunk in chunks:
                self._check()
                if not chunk:
                    continue
                prefix += chunk
                state, start, end = rules.mp4_moov_span(prefix)
                if state in ("end", "bad"):
                    raise Failed("moov_end" if state == "end" else "unsupported")
                if state == "start":
                    break
                if len(prefix) > MOOV_MAX:
                    raise Failed("moov_far")
            else:
                raise Failed("network")
        except (Failed, Cancelled):
            response.close()
            raise
        except Exception:  # noqa: BLE001
            response.close()
            raise Failed("network") from None
        index = rules.mp4_index(bytes(prefix[start:end]), total)
        if index is None:
            response.close()
            raise Failed("unsupported")
        self._check()                                # stop() may have deleted the file meanwhile
        with open(self.path, "r+b") as handle:
            handle.write(prefix)
        self.index = index
        self._set(duration=index["duration"])
        jump = rules.mp4_offset(index, self.start_at) if self.mode == "offset" else 0
        if jump > len(prefix) + JUMP_BYTES:
            response.close()
            response = self._get(url, jump)
            if response.status_code != 206:
                response.close()
                raise Failed("range")
            self.region = jump
            self._set(start_s=rules.mp4_start_time(index, jump))
            return response, response.iter_content(CHUNK), jump
        self.region = 0
        self._publish(len(prefix))
        return response, chunks, len(prefix)

    def _publish(self, end):
        """The playable range after the bytes [region, end) are on the disk."""
        if self.index is not None:
            end_s = rules.mp4_end_time(self.index, self.region, end) if end > self.region else self.start_s
        else:
            duration = self.duration_hint if self.fmt == "mkv" else 0
            end_s = rules.seconds_of(end, rules.bitrate(self.size, duration))
        self._set(end_s=end_s, written=end - self.region, ready_path=self.path if end > self.region else None)

    def _body(self, url, response, chunks, done, total):
        """Bytes from `done` on into the file at that position (Range resume after a broken connection)."""
        attempt, checked = 0, done
        while True:
            try:
                with open(self.path, "r+b") as handle:
                    handle.seek(done)
                    for chunk in chunks:
                        self._check()
                        if not chunk:
                            continue
                        handle.write(chunk)
                        done += len(chunk)
                        handle.flush()
                        self._publish(done)
                        if done - checked >= SPACE_CHECK_BYTES:
                            checked = done
                            if not self._free_ok():
                                raise Failed("space")
            except (Failed, Cancelled):
                raise
            except Exception:  # noqa: BLE001 - the connection broke: Range resume below
                pass
            finally:
                response.close()
            if not total or done >= total:
                return
            if attempt >= len(WAITS_S):
                raise Failed("network")
            self.sleep(WAITS_S[attempt])
            attempt += 1
            response = self._get(url, done)
            if response.status_code != 206:
                response.close()
                raise Failed("range")
            chunks = response.iter_content(CHUNK)
