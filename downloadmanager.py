"""The box's download queue: one item at a time, in the background, on the HDD/USB disk (no Enigma2 imports).

Thin I/O around downloadrules.py. The module-level MANAGER lives as long as the Enigma2 process, so a download
goes on when the plugin's screens close; the queue is a JSON file on the download disk and continues (HTTP Range
from the stored .part) the next time the plugin opens after a GUI restart. Source links are never logged.
"""
import json
import os
import shutil
import threading
import time
import uuid

from . import analytics, bufferjob, devicejobs, devicesubs, downloadpick, downloadrules as rules, failover, playback, subs, \
    subtitlelayout, torrengine, torrentrules

INDEX = "downloads.json"
SAVE_EVERY_S = 5.0                       # progress reaches the disk at most this often
CHUNK = 256 * 1024
TIMEOUT = 30
NETWORK_TRIES = 8                        # 10 s, 30 s, 2 min, then 10 min: about an hour, then Failed + Retry
HTTP_TRIES = 4                           # other HTTP answers (5xx, 400...) before the item fails
SEGMENT_TRIES = 3                        # a broken HLS segment is retried, never skipped
PREPARED_S = 600                         # a resolve the download page made is used if this fresh
LOW_SPACE = 256 * 1024 ** 2              # stop writing below this (the .part stays for Retry)
SPACE_CHECK_BYTES = 64 * 1024 ** 2


def log(message):
    print("[Kanasa] downloads: %s" % message)


BUNDLE_WAIT_TRIES, BUNDLE_WAIT_S = 10, 3   # 085: a resume at plugin open waits for the host bundle check (<= 30 s)


class DownloadError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Cancelled(Exception):
    pass


class _Hold(Exception):
    """The player's buffer (FIX12) runs and is not yet well ahead: this download lets go of its connection."""


class _Restart(Exception):
    """A re-resolved HLS stream is not the same one: start the item over on the fresh result."""
    def __init__(self, result):
        super().__init__("restart")
        self.result = result


def _requests_http(url, headers, timeout):
    import requests  # on the box; imported late so tests never need it
    return requests.get(url, headers=headers, stream=True, timeout=timeout)


def _gui_call(fn, *args):
    """Run fn on the Enigma2 GUI (reactor) thread and wait: eConsoleAppContainer must start there."""
    from twisted.internet import reactor
    from twisted.internet.threads import blockingCallFromThread
    return blockingCallFromThread(reactor, fn, *args)


def _unlink(path):
    try:
        if path:
            os.unlink(path)
    except OSError:
        pass


def _video_ext(name) -> str:
    """A torrent file's own extension (.mkv when the name says nothing)."""
    name = torrentrules.basename(name or "")
    return os.path.splitext(name)[1].lower() if torrentrules.is_video(name) else ".mkv"


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    os.replace(tmp, path)


# --- the queue file ---------------------------------------------------------------------------------
class Repository:
    """downloads.json + one metadata JSON per item next to its file. Thread-safe."""

    def __init__(self, directory, clock=time.monotonic):
        self.directory, self.clock = directory, clock
        self.path = os.path.join(directory, INDEX)
        self.lock = threading.RLock()
        self.saved_at = 0.0
        self.items = self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as handle:
                rows = json.load(handle)
        except (OSError, ValueError):
            return []
        rows = [row for row in rows if isinstance(row, dict) and row.get("id") and row.get("key")] \
            if isinstance(rows, list) else []
        for row in rows:                  # the GUI restarted mid-download: it queues again, the .part stays
            if row.get("state") in ("resolving", "downloading", "waiting"):
                row["state"] = "queued"
        return rows

    def snapshot(self) -> list:
        with self.lock:
            return [dict(row) for row in self.items]

    def get(self, item_id):
        with self.lock:
            row = next((r for r in self.items if r.get("id") == item_id), None)
            return dict(row) if row else None

    def save(self, row=None):
        """The index, and the item's own metadata JSON when its state changed."""
        with self.lock:
            os.makedirs(self.directory, exist_ok=True)
            _write_json(self.path, self.items)
            self.saved_at = self.clock()
            if row is not None and row.get("meta"):
                try:
                    _write_json(row["meta"], {k: row.get(k) for k in (
                        "title", "year", "poster", "kind", "tmdb_id", "season", "episode", "source", "section",
                        "quality", "size", "state", "subtitles", "path", "added")})
                except OSError:
                    pass

    def add(self, row):
        with self.lock:
            self.items.append(row)
            self.save(row)
            return dict(row)

    def update(self, item_id, progress=False, **values):
        """progress=True: only bytes moved; written at most every SAVE_EVERY_S."""
        with self.lock:
            row = next((r for r in self.items if r.get("id") == item_id), None)
            if row is None:
                return None
            row.update(values)
            if not progress:
                self.save(row)
            elif self.clock() - self.saved_at >= SAVE_EVERY_S:
                self.save()
            return dict(row)

    def remove(self, item_id):
        with self.lock:
            row = next((r for r in self.items if r.get("id") == item_id), None)
            if row is None:
                return None
            self.items.remove(row)
            self.save()
            return row

    def delete_files(self, row):
        """Only files inside the download directory: the media, its .part, subtitles and metadata."""
        paths = [row.get("path"), row.get("part"), row.get("meta")] + list(row.get("subtitles") or [])
        for path in paths:
            if isinstance(path, str) and os.path.dirname(os.path.abspath(path)) == os.path.abspath(self.directory):
                _unlink(path)


# --- the manager ------------------------------------------------------------------------------------
class Manager:
    def __init__(self, directory=None, client=None, http=None, sleep_event=None, gui_call=None,
                 disk_usage=None, engine=None, now=time.time, device_resolve=None, device_call=None, device_subs=None):
        self._directory, self.client = directory, client
        # 085 T108: a device row's ref is resolved by the box's host worker (device_resolve(ref) -> (ok, value));
        # its own headers (the bundle's exact User-Agent + Referer) go on every request of that item
        self.device_resolve = device_resolve
        self.device_call = device_call      # (op, args) -> (ok, value): a resume searches the site again
        self.device_subs = device_subs      # 086: () -> the local header value while the worker runs sub-scene, or None
        self.stream_headers = {}
        self.http = http or _requests_http
        self.gui_call = gui_call or _gui_call
        self.disk_usage = disk_usage or (lambda path: shutil.disk_usage(path))
        self.engine = engine                         # tests inject a fake EngineClient
        self.now = now
        self.lock = threading.RLock()
        self.wake = sleep_event or threading.Event()
        self.thread, self.repo_, self.workers, self.running = None, None, 0, set()
        self.cancelled, self.prepared, self.pending_deletes = set(), {}, {}
        self.plugin_open, self.hls_referers, self.limit = False, {}, None
        self.engine_lock = threading.Lock()          # the downloads share the one torrent engine

    @property
    def current(self):
        """One running item's id (None when nothing runs)."""
        return next(iter(sorted(self.running)), None)

    def parallel(self) -> int:
        """Settings → Simultaneous downloads (1-4, default 1); tests set .limit."""
        if self.limit:
            return self.limit
        try:
            from .prefs import Prefs
            return Prefs().simultaneous_downloads()
        except Exception:  # noqa: BLE001
            return 1

    # -- the disk (looked up late: the HDD may mount after Enigma2 imported the plugin)
    @property
    def directory(self):
        if self._directory is None:
            try:
                self._directory = rules.choose_dir(torrengine.disks(), torrengine.movie_paths(),
                                                   self.folder_choice())
            except Exception:  # noqa: BLE001 - no /media: no disk
                self._directory = None
        return self._directory

    @staticmethod
    def folder_choice():
        try:
            from .prefs import Prefs
            return Prefs().download_folder()
        except Exception:  # noqa: BLE001
            return "auto"

    def change_folder(self):
        """Settings changed the download folder: look again. False while a download runs (it keeps its disk)."""
        with self.lock:
            if self.running:
                return False
            self._directory, self.repo_ = None, None
        self.start()
        return True

    @property
    def repo(self):
        if self.repo_ is None and self.directory:
            self.repo_ = Repository(self.directory)
        return self.repo_

    def items(self) -> list:
        repo = self.repo
        return repo.snapshot() if repo else []

    def find(self, kind, tmdb_id, season=None, episode=None):
        try:
            return rules.find(self.items(), rules.item_key(kind, tmdb_id, season, episode))
        except (TypeError, ValueError):
            return None

    def space(self):
        """(used by downloads, free on the disk) in bytes, or None without a disk."""
        if not self.directory:
            return None
        try:
            free = self.disk_usage(os.path.dirname(self.directory.rstrip("/")) or "/").free
        except OSError:
            free = 0
        return rules.used_bytes(self.items()), free

    # -- the plugin's lifetime
    def configure(self, client):
        self.client = client
        self.plugin_open = True
        self.clean_buffer()
        self.start()
        return self

    def clean_buffer(self):
        """The plugin starts: a buffer the last GUI run left behind (FIX12) goes; never the running one's file."""
        job = bufferjob.ACTIVE
        try:
            removed = bufferjob.cleanup(self.directory, keep=job.path if job else None)
        except Exception:  # noqa: BLE001 - no disk, or it went away
            removed = 0
        if removed:
            log("removed %d leftover buffer file(s)" % removed)

    def plugin_closed(self):
        """The engine stops with the plugin unless a torrent downloads (then when that download ends)."""
        self.plugin_open = False
        if not self.torrent_active():
            torrengine.shutdown()

    def torrent_active(self) -> bool:
        return any(row.get("source_kind") == "torrent" and row.get("state") in rules.ACTIVE for row in self.items())

    def busy_hash(self, info_hash) -> bool:
        """The player must not drop a torrent this manager is downloading."""
        return any(row.get("info_hash") == info_hash and row.get("state") in rules.ACTIVE for row in self.items())

    # -- queue actions (GUI thread)
    def enqueue(self, metadata, prepared=None):
        """Queue one film/episode (replacing an older download of it). prepared: the page's resolve result."""
        repo = self.repo
        if repo is None:
            raise DownloadError("no_disk")
        space = self.space()
        if space and not rules.enough_space(space[1], rules._int(metadata.get("size"))):
            raise DownloadError("space")
        old = rules.find(repo.items, metadata.get("key"))
        if old is not None:
            self.delete(old["id"])
        row = dict(metadata)
        row.update(id=uuid.uuid4().hex, state="queued", done=0, error=None, added=int(self.now()))
        analytics.download_event("download_start", row)
        row.setdefault("size", 0)
        base = os.path.join(repo.directory, rules.stem(row))
        taken = {r.get("base") for r in repo.items} | {r.get("base") for r in self.pending_deletes.values()}
        if base in taken:                   # e.g. the replaced item's files are still being let go of
            base += " " + row["id"][:6]
        ext = ".mp4" if row.get("source_kind") != "torrent" else _video_ext(row.get("torrent_filename"))
        row.update(base=base, meta=base + ".json", path=base + ext, part=base + ext + ".part", subtitles=[])
        if prepared is not None:
            self.prepared[row["id"]] = (prepared, time.monotonic())
        added = repo.add(row)
        self.start()
        return added

    def retry(self, item_id):
        if self.repo and self.repo.update(item_id, state="queued", error=None, tries=0):
            self.start()

    def delete(self, item_id):
        """Cancel and delete at once; the running item's files go when its thread lets go of them."""
        repo = self.repo
        if repo is None:
            return False
        with self.lock:
            row = repo.remove(item_id)
            if row is None:
                return False
            self.prepared.pop(item_id, None)
            if item_id in self.running:
                self.cancelled.add(item_id)
                self.pending_deletes[item_id] = row
                self.wake.set()
            else:
                repo.delete_files(row)
        return True

    def start(self):
        """Up to Settings → Simultaneous downloads workers, each running one item at a time."""
        with self.lock:
            if self.repo is None:
                return
            limit = self.parallel()
            waiting = sum(1 for row in self.repo.items if row.get("state") == "queued"
                          and row.get("id") not in self.running)
            while self.workers < limit and waiting > 0:
                waiting -= 1
                self.workers += 1
                self.thread = threading.Thread(target=self._loop, name="KanasaDownloads", daemon=True)
                self.thread.start()

    # -- the worker threads
    def _loop(self):
        while True:
            with self.lock:
                item = rules.next_to_start(self.repo.items, self.running, self.parallel(), self._free())
                if item is None:
                    self.workers = max(0, self.workers - 1)
                    if not self.workers:
                        self.thread = None
                    break
                self.running.add(item["id"])
                self.wake.clear()
            self._one(item["id"])
        if not self.running and not self.plugin_open and not self.torrent_active():
            try:
                self.gui_call(torrengine.shutdown)
            except Exception:  # noqa: BLE001
                pass

    def _one(self, item_id):
        try:
            self._run(item_id)
        except Cancelled:
            pass
        except DownloadError as error:
            log("failed: %s" % error.code)
            self.repo.update(item_id, state="failed", error=error.code)
            analytics.download_event("download_fail", self.repo.get(item_id), error.code)
        except Exception as error:  # noqa: BLE001 - one item never stops the queue
            log("failed: %s" % type(error).__name__)
            self.repo.update(item_id, state="failed", error="error")
            analytics.download_event("download_fail", self.repo.get(item_id), "error")
        finally:
            with self.lock:
                self.running.discard(item_id)
                self.hls_referers.pop(item_id, None)
                self.stream_headers.pop(item_id, None)
                if item_id in self.cancelled:
                    self.cancelled.discard(item_id)
                    row = self.pending_deletes.pop(item_id, None)
                    if row:
                        self.repo.delete_files(row)

    def _check(self, item_id):
        if item_id in self.cancelled:
            raise Cancelled()

    def _wait(self, item_id, seconds, attempt_state="waiting", error="network"):
        self.repo.update(item_id, state=attempt_state, error=error)
        self.wake.wait(seconds)
        self.wake.clear()
        self._check(item_id)

    def _hold(self, item_id):
        """The player's buffer is the highest priority (FIX12): wait without a connection while it holds."""
        while bufferjob.holding():
            self.wake.wait(2)
            self.wake.clear()
            self._check(item_id)

    def _free(self):
        try:
            return self.disk_usage(os.path.dirname(self.repo.directory.rstrip("/")) or "/").free
        except OSError:
            return 0

    def _set_ext(self, item_id, ext):
        """The real container once known (mkv from the host or the torrent's file), while nothing is stored."""
        item = self.repo.get(item_id)
        if not item or item["path"].endswith(ext) or (os.path.exists(item["part"]) and os.path.getsize(item["part"])):
            return
        _unlink(item["part"])
        self.repo.update(item_id, path=item["base"] + ext, part=item["base"] + ext + ".part")

    def _run(self, item_id):
        item = self.repo.get(item_id)
        if item is None:
            raise Cancelled()
        if not rules.enough_space(self._free(), rules.remaining(item)):
            raise DownloadError("space")
        if item.get("source_kind") == "auto":
            item = self._auto(item_id, item)
        if item.get("source_kind") == "torrent":
            return self._torrent(item_id)
        self.repo.update(item_id, state="resolving", error=None)
        result = self._first_result(item)
        allowed, reason = rules.downloadable(result)
        if not allowed:
            raise DownloadError("proxy" if reason == "proxy" else reason)
        if str(result.get("kind")).lower() == "hls":
            for _ in range(2):
                try:
                    return self._hls(item_id, result)
                except _Restart as restart:
                    result = restart.result
            raise DownloadError("source_gone")
        url, height = rules.pick_quality(result)
        self._take_headers(item_id, result, url)
        self._set_ext(item_id, rules.extension(result.get("kind"), url))
        self.repo.update(item_id, quality=height and "%dp" % height)
        self._subtitles(item_id)

        def refresh(why):
            fresh = self._reresolve(item_id)
            fresh_url = rules.pick_quality(fresh)[0]
            self._take_headers(item_id, fresh, fresh_url)
            return fresh_url, fresh.get("referer")
        self._file(item_id, url, result.get("referer"), refresh)
        self._done(item_id)

    def _auto(self, item_id, item):
        """Auto (season Auto, "use this server for the rest"): the server is picked now, in auto-play order."""
        self.repo.update(item_id, state="resolving", error=None)
        if not self.client:
            raise DownloadError("network")

        def wait(seconds):
            self.wake.wait(seconds)
            self._check(item_id)
        try:
            fields, prepared = downloadpick.auto_pick(self.client, item, wait, torrengine.installed_dir() is not None)
        except downloadpick.NoSource:
            raise DownloadError("no_source") from None
        if prepared is not None:
            self.prepared[item_id] = (prepared, time.monotonic())
        return self.repo.update(item_id, **fields) or item

    def _take_headers(self, item_id, result, url=None):
        """A device result's headers for this item's requests (a quality may carry its own); server rows: none."""
        if not isinstance(result, dict) or result.get("device") is not True:
            self.stream_headers.pop(item_id, None)
            return
        headers = playback.device_headers(result)
        for row in result.get("qualities") or []:
            if isinstance(row, dict) and url and row.get("stream_url") == url and isinstance(row.get("headers"), dict):
                headers = playback.device_headers({"headers": row["headers"]}) or headers
        self.stream_headers[item_id] = headers

    def _device_result(self, item):
        """085 T108: the row's ref resolved again by the box's worker (a ref from a worker that restarted is gone:
        source_gone). Never the server's resolve, never /stream/proxy."""
        call = self.device_resolve
        if call is None:
            from . import localhosts
            call = lambda ref: localhosts.resolve_blocking(ref, self.gui_call)   # noqa: E731
        for _ in range(BUNDLE_WAIT_TRIES):
            try:
                ok, value = call(item.get("ref"))
            except Exception:  # noqa: BLE001 - no worker, a closed reactor
                ok, value = False, "worker_died"
            if ok or value != "no_bundle":
                break
            # box 2026-11-24: a resume at plugin open ran before the bundle check configured the worker
            self.wake.wait(BUNDLE_WAIT_S)
            if item.get("id"):
                self._check(item["id"])
        result = devicejobs.play_result(value) if ok else None
        if result is None:
            code = value.get("error") if isinstance(value, dict) else value
            if code in devicejobs.RERUN_CODES or not ok:
                result = self._device_search_again(item)
        if result is None:
            raise DownloadError("source_gone")
        return result

    def _device_search_again(self, item):
        """Box 2026-11-24: the plugin closed (or Enigma2 restarted) mid-download, so the ref died with the worker and
        the resume failed with source_gone. The site is searched again on the box with the saved job and the same
        server (cid, else hoster) resolved there; its new ref is kept. None when that is not possible."""
        saved = item.get("device_job")
        if not isinstance(saved, dict) or saved.get("op") not in ("sources", "page_sources") \
                or not isinstance(saved.get("job"), dict):
            return None
        call = self.device_call
        if call is None:
            from . import localhosts
            call = lambda op, args: localhosts.call_blocking(op, args, self.gui_call)   # noqa: E731
        try:
            ok, answer = call(saved["op"], {"job": saved["job"]})
            candidate = devicejobs.same_server(answer, item) if ok else None
            if candidate is None:
                return None
            ok, value = call("resolve", {"ref": candidate["ref"], "client": dict(devicejobs.CLIENT_CAPS)})
        except Exception:  # noqa: BLE001
            return None
        result = devicejobs.play_result(value) if ok else None
        if result is not None and item.get("id"):
            self.repo.update(item["id"], ref=candidate["ref"], cid=candidate.get("cid") or item.get("cid"))
        return result

    def _first_result(self, item):
        prepared = self.prepared.pop(item["id"], None)
        if prepared and time.monotonic() - prepared[1] < PREPARED_S:
            return prepared[0]
        if item.get("device") is True:
            return self._device_result(item)
        if not self.client:
            raise DownloadError("network")
        try:
            return self.client.resolve(item.get("ref"))
        except Exception:  # noqa: BLE001 - the ref expired (404) or the server restarted: find the row again
            return self._reresolve(item["id"])

    def _reresolve(self, item_id):
        """The same server again by its stable cid (047 §1), never another one."""
        item = self.repo.get(item_id) or {}
        allowed, stamps = rules.reresolve_budget(item.get("reresolves"), self.now())
        self.repo.update(item_id, reresolves=stamps)
        if allowed and item.get("device") is True:
            return self._device_result(item)
        if not allowed or not self.client:
            raise DownloadError("source_gone")
        candidate = None
        for wait in (0, 2, 5, 10, 10, 10):
            if wait:
                self.wake.wait(wait)
                self._check(item_id)
            try:
                data = self.client.sources(item.get("kind"), int(item.get("tmdb_id")), item.get("season"),
                                           item.get("episode"))
            except Exception:  # noqa: BLE001
                continue
            candidate = next((c for c in data.get("candidates") or []
                              if isinstance(c, dict) and c.get("cid") == item.get("cid")), None)
            if (candidate and candidate.get("check") == "ok") or not failover.still_coming(data):
                break
        if not candidate or candidate.get("check") != "ok":
            raise DownloadError("source_gone")
        self.repo.update(item_id, ref=candidate.get("ref"))
        try:
            result = self.client.resolve(candidate.get("ref"))
        except Exception:  # noqa: BLE001
            raise DownloadError("source_gone") from None
        if not rules.downloadable(result)[0]:
            raise DownloadError("source_gone")
        return result

    # one progressive file (a hoster's mp4/mkv, or the engine's stream of a torrent file)
    def _file(self, item_id, url, referer, refresh, refresh_on_network=False):
        network, other = 0, 0
        while True:
            self._check(item_id)
            item = self.repo.get(item_id)
            if item is None:
                raise Cancelled()
            part = item["part"]
            done = os.path.getsize(part) if os.path.exists(part) else 0
            try:
                response = self.http(url, rules.range_headers(done, referer, self.stream_headers.get(item_id)),
                                     TIMEOUT)
            except Exception:  # noqa: BLE001 - timeout, reset, DNS: wait, then the same link
                if network >= NETWORK_TRIES:
                    raise DownloadError("network") from None
                self._wait(item_id, rules.backoff(network))
                network += 1
                if refresh_on_network:
                    url, referer = refresh("network")
                continue
            status = response.status_code
            action = rules.resume_action(done, status)
            if status == 416 and done and done == item.get("remote_size"):
                response.close()
                return                                           # every byte is already here
            if action == "reresolve":
                response.close()
                url, referer = refresh("expired")
                continue
            if action == "retry":
                response.close()
                other += 1
                if other >= HTTP_TRIES:
                    raise DownloadError("http")
                self._wait(item_id, rules.backoff(other - 1))
                continue
            total = rules.total_size(status, response.headers, done)
            if action == "append" and not rules.same_file(item.get("remote_size"), total):
                response.close()                                 # another file now: never mix the bytes
                _unlink(part)
                self.repo.update(item_id, done=0, remote_size=total)
                continue
            if action in ("write", "restart"):
                done = 0
            if not rules.enough_space(self._free(), max(0, total - done)):
                response.close()
                raise DownloadError("space")
            self.repo.update(item_id, state="downloading", error=None, remote_size=total or None,
                             size=total or item.get("size") or 0, done=done)
            try:
                done = self._stream(item_id, response, part, "ab" if action == "append" else "wb", done)
            except (Cancelled, DownloadError):
                raise
            except _Hold:
                response.close()
                self._hold(item_id)                              # then Range resume from the .part
                continue
            except Exception:  # noqa: BLE001 - the connection broke mid-file: Range resume after a wait
                if network >= NETWORK_TRIES:
                    raise DownloadError("network") from None
                self._wait(item_id, rules.backoff(network))
                network += 1
                if refresh_on_network:
                    url, referer = refresh("network")
                continue
            finally:
                response.close()
            if total and done < total:                            # the server closed early
                network += 1
                if network > NETWORK_TRIES:
                    raise DownloadError("network")
                continue
            return

    def _stream(self, item_id, response, part, mode, done):
        checked = done
        with open(part, mode) as handle:
            for chunk in response.iter_content(CHUNK):
                self._check(item_id)
                if not chunk:
                    continue
                handle.write(chunk)
                done += len(chunk)
                self.repo.update(item_id, progress=True, done=done)
                if bufferjob.holding():
                    self.repo.update(item_id, done=done)
                    raise _Hold()
                if done - checked >= SPACE_CHECK_BYTES:
                    checked = done
                    if self._free() < LOW_SPACE:
                        raise DownloadError("space")
        self.repo.update(item_id, done=done)
        return done

    def _done(self, item_id):
        item = self.repo.get(item_id)
        if item is None:
            raise Cancelled()
        os.replace(item["part"], item["path"])
        size = os.path.getsize(item["path"])
        self.repo.update(item_id, state="downloaded", done=size, size=size, error=None, finished=int(self.now()))
        analytics.download_event("download_done", item)
        log("finished")

    # HLS: a VOD playlist's segments joined into one .ts (or .mp4 for fMP4 with an init segment)
    def _hls(self, item_id, result):
        media, result = self._playlist(item_id, result)
        problem = rules.hls_problem(media)
        if problem:
            raise DownloadError(problem)
        item = self.repo.get(item_id)
        fingerprint = rules.hls_fingerprint(media)
        start, done = item.get("hls_next") or 0, item.get("done") or 0
        if item.get("hls_fp") and not rules.hls_same(item["hls_fp"], fingerprint):
            start, done = 0, 0                     # another stream after a re-resolve: start over
        if start == 0:
            ext = ".mp4" if media.get("init") else ".ts"
            self.repo.update(item_id, path=item["base"] + ext, part=item["base"] + ext + ".part")
            _unlink(item.get("part"))
            item = self.repo.get(item_id)
        part, total = item["part"], len(media["segments"])
        self.repo.update(item_id, state="downloading", hls_fp=fingerprint, hls_next=start, done=done, error=None)
        self._subtitles(item_id)
        with open(part, "ab") as handle:
            handle.truncate(done)                  # bytes past the last whole segment are dropped
            handle.seek(done)
            if start == 0 and media.get("init"):
                body, media = self._segment(item_id, media, None)
                handle.write(body)
                done += len(body)
            index = start
            while index < total:
                self._check(item_id)
                self._hold(item_id)
                body, media = self._segment(item_id, media, index)
                handle.write(body)
                done += len(body)
                index += 1
                self.repo.update(item_id, progress=index < total, done=done, hls_next=index,
                                 size=rules.hls_estimate(done, index, total))
                if index % 50 == 0 and self._free() < LOW_SPACE:
                    raise DownloadError("space")
        self._done(item_id)

    def _get_text(self, item_id, url, referer):
        tries = 0
        while True:
            try:
                response = self.http(url, rules.range_headers(0, referer, self.stream_headers.get(item_id)), TIMEOUT)
            except Exception:  # noqa: BLE001
                if tries >= NETWORK_TRIES:
                    raise DownloadError("network") from None
                self._wait(item_id, rules.backoff(tries))
                tries += 1
                continue
            try:
                if response.status_code == 200:
                    return None, response.text
                return response.status_code, None
            finally:
                response.close()

    def _playlist(self, item_id, result):
        for _ in range(rules.RERESOLVE_MAX + 1):
            url, referer = result["stream_url"], result.get("referer")
            self._take_headers(item_id, result, url)
            status, text = self._get_text(item_id, url, referer)
            if status is None:
                variant = rules.pick_variant(rules.hls_variants(text, url))
                if variant:
                    status, text = self._get_text(item_id, variant, referer)
                    url = variant
            if status is None:
                self.hls_referers[item_id] = referer
                return rules.hls_media(text, url), result
            if not rules.should_reresolve(status):
                raise DownloadError("http")
            result = self._reresolve(item_id)
        raise DownloadError("source_gone")

    def _segment(self, item_id, media, index):
        """One segment's bytes (index None = the init segment); an expired link re-resolves the same server
        and must give the same stream (047 fingerprint), else the item restarts."""
        tries = 0
        while True:
            self._check(item_id)
            url = media["init"] if index is None else media["segments"][index]
            try:
                response = self.http(url, rules.range_headers(0, self.hls_referers.get(item_id),
                                                              self.stream_headers.get(item_id)), TIMEOUT)
                status = response.status_code
                body = response.content if status in (200, 206) else None
                response.close()
            except Exception:  # noqa: BLE001
                status, body = None, None
            if body is not None:
                return body, media
            if status is not None and rules.should_reresolve(status):
                new_media, fresh = self._playlist(item_id, self._reresolve(item_id))
                if not rules.hls_same(rules.hls_fingerprint(media), rules.hls_fingerprint(new_media)):
                    self.repo.update(item_id, hls_next=0, done=0, hls_fp=None)
                    raise _Restart(fresh)
                media = new_media
                continue
            tries += 1
            if tries > SEGMENT_TRIES + NETWORK_TRIES:
                raise DownloadError("network")
            self._wait(item_id, 2 if tries <= SEGMENT_TRIES else rules.backoff(tries - SEGMENT_TRIES - 1))

    # a torrent: the on-box engine streams the release's file, written with Range resume like a hoster file
    def _torrent(self, item_id):
        directory = torrengine.installed_dir()
        if not directory:
            raise DownloadError("engine")
        engine = self.engine or torrengine.EngineClient()
        item = self.repo.get(item_id)
        row = {"hash": item.get("info_hash"), "filename": item.get("torrent_filename")}
        self.repo.update(item_id, state="resolving", error=None)

        def open_file(why=None):
            tries = 0
            while True:
                self._check(item_id)
                with self.engine_lock:              # two workers never start the engine twice
                    # A positive echo is reusable only for the Process owned by this Enigma2.
                    # Injected clients are test-only stand-ins for that managed process.
                    owned = torrengine.ENGINE.running() or self.engine is not None
                    if not (engine.echo() and owned):
                        if not self.gui_call(torrengine.ENGINE.start, directory) or not engine.wait_ready():
                            raise DownloadError("engine")
                try:
                    url, entry = engine.open_file(row, cancelled=lambda: item_id in self.cancelled)
                except torrengine.EngineError as error:
                    if error.code == "cancelled":
                        raise Cancelled() from None
                    if error.code == "no_video":
                        raise DownloadError("no_video") from None
                    if tries >= NETWORK_TRIES:
                        raise DownloadError("no_peers") from None
                    self._wait(item_id, rules.backoff(tries), error="no_peers")
                    tries += 1
                    continue
                if entry.get("length"):
                    self.repo.update(item_id, size=entry["length"])
                self._set_ext(item_id, _video_ext(entry.get("path")))
                return url, None
        url, _ = open_file()
        self._subtitles(item_id)
        self._file(item_id, url, None, open_file, refresh_on_network=True)
        self._done(item_id)

    # subtitles next to the media, saved once when the download starts (a failure never fails the item)
    def _subtitles(self, item_id):
        item = self.repo.get(item_id) or {}
        if item.get("subs_tried") or not self.client:
            return
        self.repo.update(item_id, subs_tried=True)
        key = subs.content_key({"kind": item.get("kind"), "id": item.get("tmdb_id"), "season": item.get("season"),
                                "episode": item.get("episode")})
        if not key:
            return
        languages, choice = list(item.get("sub_langs") or ["AR", "EN"]), item.get("sub_choice")
        if self.client.bearer:
            try:
                languages = subs.clean_prefs(self.client.subtitle_prefs())["languages"]
            except Exception:  # noqa: BLE001
                pass
            try:
                choice = subs.clean_choice(self.client.subtitle_choice(key), key)
            except Exception:  # noqa: BLE001
                pass
        local = self._device_subs()
        try:
            if local:
                payload = self.client.subtitles(key, languages, item.get("release"), True, local)
            else:
                payload = self.client.subtitles(key, languages, item.get("release"))
        except Exception:  # noqa: BLE001
            return
        tracks = payload.get("tracks")
        job = devicesubs.job_of(payload) if local else None
        if job is not None:                 # 086: sub-scene Arabic from the box, first in the Arabic group
            ok, value = self._worker_call("subscene", {"job": job})
            tracks = devicesubs.merge(tracks, devicesubs.tracks_of(value, job) if ok else [])
        saved = []
        for track in rules.subtitle_tracks(tracks, languages, choice):
            body, tried = None, 0
            while track is not None and body is None and tried < 3:   # a bad file: the next of its language
                body, tried = self._subtitle_body(track), tried + 1
                track = track if body is not None else subtitlelayout.next_same_language(tracks, track)
            if body is None:
                continue
            path = rules.subtitle_name(item["base"], track.get("lang"), saved)
            try:
                with open(path, "wb") as handle:
                    handle.write(body)
            except OSError:
                continue
            saved.append(path)
        self.repo.update(item_id, subtitles=saved, sub_langs_saved=[str(t).upper() for t in languages])

    def _device_subs(self):
        try:
            if self.device_subs is not None:
                return self.device_subs()
            from . import hostclient, localhosts
            return localhosts.header_value() if hostclient.CLIENT.subscene_ready() else None
        except Exception:  # noqa: BLE001 - no worker: today's server lookup
            return None

    def _worker_call(self, op, args):
        call = self.device_call
        if call is None:
            from . import localhosts
            call = lambda op, args: localhosts.call_blocking(op, args, self.gui_call)   # noqa: E731
        try:
            return call(op, args)
        except Exception:  # noqa: BLE001
            return False, "worker_died"

    def _subtitle_body(self, track):
        """One subtitle file's bytes, or None. A device sub-scene track: its vtt or the worker's file job, never
        our server (086)."""
        if devicesubs.is_device(track):
            vtt = track.get("vtt")
            if not vtt:
                vtt, _ = devicesubs.file_answer(*self._worker_call("subscene_file", devicesubs.file_args(track)))
            return vtt.encode("utf-8") if vtt else None
        try:
            return self.client.subtitle_file(track["id"])
        except Exception:  # noqa: BLE001
            return None


MANAGER = Manager()


def manager(client=None):
    """The process-wide manager; passing the Home screen's client (re)starts the queue with it."""
    return MANAGER.configure(client) if client is not None else MANAGER
