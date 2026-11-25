"""085 T104/T105: the device jobs of one server list, run by the host worker (hostclient). Pure: no Enigma2.

A local-mode sources answer (api.Client.sources(..., local=...)) carries `device_sites` jobs (or `page_jobs` for a
site-only title) instead of server rows for the bundle's sites. A Runner runs those jobs on the worker at most LIMIT
calls at a time (the box has 2 cores), turns each hit into rows of the job's group, and checks every row with a
device resolve: a row is "checking" until its resolve answers, "ok" with the resolve kept for the play, or dropped
when it cannot play here (R4: hidden, never proxied). merge() puts the rows into the server's answer, so failover,
the Servers panel and the download page read one list exactly as before.

A site that answers `blocked` (a Cloudflare challenge on the box's network) is hidden and skipped for 15 minutes on
this box (FR-037: no challenge solving here). Every final state of a `sources` job (hit / not_found / blocked) goes to
`report` as the 008 §6 device-match body - never a ref, a stream URL, a header or a challenge. Page jobs are not
reported (contract §4: there is no TMDb key).
"""
import time

from . import failover

LIMIT = 3                      # FR-035: jobs and row checks together, never more than this on the worker
BLOCKED_SKIP_S = 900.0         # FR-037: a blocked site is not asked again for 15 minutes
RESULT_FRESH_S = 120.0         # a row's check resolve is played if it is this fresh, else resolved again
CLIENT_CAPS = {"platform": "enigma2", "headers_ok": True, "mkv": True}   # exteplayer3: -u/-h on every request, MKV
FINAL_STATES = ("hit", "not_found", "blocked")
HEADER_NAMES = ("Referer", "User-Agent")
MAX_HOSTERS = 40
# The worker died (or was restarted) after the search: its refs died with it. The site's search runs once more on
# the new worker instead of losing the whole group (box 2026-11-24: kill -9 dropped all 5 krmzi rows).
RERUN_CODES = ("worker_died", "unknown_ref")
# Box 2026-11-24: a resume whose saved server is a device row (krmzi's cdnplus.sbs) started the first server row
# (vdesk.live) ~1 s after opening; the device jobs answered 5-9 s later. The player waits for them, at most this long.
RESUME_WAIT_S = 12.0

_blocked = {}                  # site -> clock value until which it is skipped (this plugin run, every screen)


def log(message):
    print("[Kanasa] devicejobs: %s" % message)


def skipped(site, now) -> bool:
    until = _blocked.get(site)
    return until is not None and now < until


def block(site, now) -> None:
    _blocked[site] = now + BLOCKED_SKIP_S


def reset_blocked() -> None:
    _blocked.clear()


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _http(value) -> bool:
    return isinstance(value, str) and value.startswith(("https://", "http://")) and \
        not any(c in value for c in "\r\n\0 ")


def local_answer(data) -> bool:
    """A sources answer of local mode (the server read our header): it has the `local` object."""
    return isinstance(data, dict) and isinstance(data.get("local"), dict)


def jobs_of(data) -> list:
    """[(op, job)] to run for a local-mode answer; [] for any other answer (an old 008 answer's device_sites are the
    Android app's, never the box's)."""
    if not local_answer(data):
        return []
    out = []
    for op, key in (("sources", "device_sites"), ("page_sources", "page_jobs")):
        for job in data.get(key) or []:
            if isinstance(job, dict) and isinstance(job.get("site"), str) and job["site"]:
                out.append((op, job))
    return out


def job_key(op, job) -> tuple:
    return (op, job.get("site"), job.get("section"), job.get("page_url") if op == "page_sources" else None)


def clean_headers(value) -> dict:
    """Only Referer and User-Agent, exactly as the bundle gave them (the User-Agent is part of AnaFast's and Vidoba's
    token, research §(f)); a value that could break a header line is left out."""
    out = {}
    if not isinstance(value, dict):
        return out
    for name in HEADER_NAMES:
        text = value.get(name)
        if isinstance(text, str) and text and len(text) <= 2048 and not any(c in text for c in "\r\n\0"):
            out[name] = text
    return out


def play_result(answer):
    """A device.resolve answer as the player's result, or None (no stream, an error code, no User-Agent).
    {"stream_url", "kind", "qualities", "headers", "referer", "device": True}: never `needs_proxy` (FR-012)."""
    if not isinstance(answer, dict) or answer.get("error") or not _http(answer.get("stream_url")):
        return None
    headers = clean_headers(answer.get("headers"))
    if not headers.get("User-Agent"):
        return None                       # the player would send its own UA: a 403 on UA-bound tokens
    qualities = []
    for row in answer.get("qualities") or []:
        if not isinstance(row, dict) or not _http(row.get("stream_url")):
            continue
        quality = {"height": _int(row.get("height")), "stream_url": row["stream_url"],
                   "label": row.get("label") if isinstance(row.get("label"), str) else ""}
        own = clean_headers(row.get("headers"))
        if own.get("User-Agent") and own != headers:
            quality["headers"] = own
        qualities.append(quality)
    result = {"stream_url": answer["stream_url"], "kind": str(answer.get("kind") or ""), "qualities": qualities,
              "headers": headers, "referer": headers.get("Referer", ""), "device": True}
    for key in ("container", "codecs", "width", "height", "size"):
        if key in answer:
            result[key] = answer[key]
    return result


def resume_hold(candidate, resume_hoster, busy, waited) -> bool:
    """A resume waits for the device jobs while the saved server is not the next candidate (not in the list yet, or
    still being checked) and device work runs, for at most RESUME_WAIT_S. Names compare like the website's
    hosterKey (progress.server is the row's hoster as played)."""
    wanted = failover.hoster_key(resume_hoster)
    if not wanted or not busy or waited >= RESUME_WAIT_S:
        return False
    return candidate is None or failover.hoster_key(candidate.get("hoster")) != wanted


def is_device(row) -> bool:
    return isinstance(row, dict) and row.get("device") is True


def hls_answer(value):
    """The worker's hls_media answer as api.Client.hls_media's shape {"url", "variants"} (variants are media
    playlists: "media": True, the Quality panel plays them as they are), or None."""
    if not isinstance(value, dict) or value.get("error") or not _http(value.get("url")):
        return None
    variants = []
    for row in value.get("variants") or []:
        if isinstance(row, dict) and _http(row.get("stream_url")) and _int(row.get("height")):
            variants.append({"height": row["height"], "bandwidth": _int(row.get("bandwidth")) or 0,
                             "stream_url": row["stream_url"], "media": True})
    variants.sort(key=lambda q: q["height"])
    return {"url": value["url"], "variants": variants if len(variants) > 1 else []}


def report_body(job, answer):
    """The 008 §6 device-match body of one sources job's final state, or None. Exactly the 8 keys; season/episode
    are the job's (public) coordinates; hosters are names only."""
    state = answer.get("state") if isinstance(answer, dict) else None
    query = job.get("query") if isinstance(job, dict) else None
    if state not in FINAL_STATES or not isinstance(query, dict):
        return None
    kind, tmdb_id = query.get("kind"), _int(query.get("tmdb_id"))
    if kind not in ("movie", "tv") or not tmdb_id or tmdb_id <= 0:
        return None
    season = episode = None
    if kind == "tv":
        season, episode = _int(job.get("season")), _int(job.get("episode"))
        if season is None or episode is None:
            return None
    page_url = answer.get("page_url") if isinstance(answer.get("page_url"), str) else ""
    episode_page_url = answer.get("episode_page_url") if isinstance(answer.get("episode_page_url"), str) else ""
    if state == "hit" and not page_url:
        return None                       # the server refuses a hit without its page (400)
    hosters = []
    if state == "hit":
        for row in answer.get("candidates") or []:
            name = row.get("hoster") if isinstance(row, dict) else None
            if isinstance(name, str) and name and len(name) <= 64 and name not in hosters:
                hosters.append(name)
    return {"tmdb_key": "%s:%d" % (kind, tmdb_id), "site": job["site"], "season": season, "episode": episode,
            "state": state, "page_url": page_url, "episode_page_url": episode_page_url if kind == "tv" else "",
            "hosters": hosters[:MAX_HOSTERS]}


class Runner:
    """The device jobs of one screen (a player or the download page). Every method runs on the reactor thread;
    call(op, args, callback(ok, value)) is hostclient.HostClient.call (it may answer a refusal at once)."""

    def __init__(self, call, on_change=None, report=None, clock=time.monotonic, limit=LIMIT):
        self.call, self.on_change, self.report, self.clock, self.limit = call, on_change, report, clock, limit
        self.jobs = {}                    # job_key -> {"op", "job", "state": queued|running|done|skipped, "answer"}
        self.rows = []                    # device rows in arrival order
        self.results = {}                 # cid -> (play_result, clock)
        self.queue = []                   # ("job", key) | ("check", cid) waiting for a slot
        self.running = 0
        self.cancelled = False
        self.quiet, self.dirty = False, False
        self.reran = set()                # job keys searched again after a worker restart (once each)

    # --- jobs ---------------------------------------------------------------------------------------------------
    def start(self, data) -> int:
        """Queue the answer's jobs not seen yet (a re-poll names the same jobs: they run once). Returns how many."""
        if self.cancelled:
            return 0
        now, added = self.clock(), 0
        self.quiet = True
        try:
            for op, job in jobs_of(data):
                key = job_key(op, job)
                if key in self.jobs:
                    continue
                if skipped(job["site"], now):
                    self.jobs[key] = {"op": op, "job": job, "state": "skipped", "answer": None}
                    log("%s skipped: blocked on this box less than 15 min ago" % job["site"])
                    continue
                self.jobs[key] = {"op": op, "job": job, "state": "queued", "answer": None}
                self.queue.append(("job", key))
                added += 1
            self._pump()
        finally:
            self.quiet, self.dirty = False, False
        return added

    def _pump(self):
        while not self.cancelled and self.running < self.limit and self.queue:
            # a job before any row check: a slow check (Vidoba 12-25 s on the box) never delays a group's search
            index = next((i for i, (kind, _) in enumerate(self.queue) if kind == "job"), 0)
            kind, key = self.queue.pop(index)
            if kind == "job":
                self._run_job(key)
            else:
                self._run_check(key)

    def _run_job(self, key):
        entry = self.jobs[key]
        entry["state"] = "running"
        self.running += 1
        self.call(entry["op"], {"job": entry["job"]}, lambda ok, value, key=key: self._job_done(key, ok, value))

    def _job_done(self, key, ok, value):
        if self.cancelled:
            return
        entry = self.jobs.get(key)
        if entry is None or entry["state"] != "running":
            return
        self.running -= 1
        entry["state"] = "done"
        answer = value if ok and isinstance(value, dict) else {"state": "error",
                                                               "reason": value if isinstance(value, str) else "error"}
        entry["answer"] = answer
        job, state = entry["job"], answer.get("state")
        log("%s %s: %s (%s)" % (entry["op"], job["site"], state, answer.get("reason") or ""))
        if state == "blocked":
            block(job["site"], self.clock())
        elif state == "hit":
            self._add_rows(job, answer.get("candidates"), entry["op"])
        if entry["op"] == "sources" and self.report is not None and state in FINAL_STATES:
            body = report_body(job, answer)
            if body is not None:
                try:
                    self.report(body)
                except Exception as error:  # noqa: BLE001 - a report never breaks the list
                    log("report failed: %r" % (error,))
        self._pump()
        self._changed()

    def _add_rows(self, job, candidates, op="sources"):
        known = {row["cid"] for row in self.rows}
        for candidate in candidates or []:
            if not isinstance(candidate, dict):
                continue
            cid, ref, hoster = candidate.get("cid"), candidate.get("ref"), candidate.get("hoster")
            if not isinstance(cid, str) or not cid or cid in known or not isinstance(ref, str) or not ref:
                continue
            hoster = hoster if isinstance(hoster, str) else ""
            label = candidate.get("label") if isinstance(candidate.get("label"), str) else "[%s]" % hoster
            self.rows.append({"cid": cid, "hoster": hoster, "label": label, "ref": ref,
                              "section": job.get("section"), "check": "checking", "device": True,
                              "site": job["site"], "device_job": {"op": op, "job": job}})
            known.add(cid)
            self.queue.append(("check", cid))

    # --- row checks -------------------------------------------------------------------------------------------------
    def _row(self, cid):
        return next((row for row in self.rows if row["cid"] == cid), None)

    def _run_check(self, cid):
        row = self._row(cid)
        if row is None or row["check"] != "checking":
            return
        self.running += 1
        self.call("resolve", {"ref": row["ref"], "client": dict(CLIENT_CAPS)},
                  lambda ok, value, cid=cid: self._check_done(cid, ok, value))

    def _check_done(self, cid, ok, value):
        if self.cancelled:
            return
        self.running -= 1
        result = play_result(value) if ok else None
        row = self._row(cid)
        code = value.get("error") if isinstance(value, dict) else value
        if row is not None and result is None and code in RERUN_CODES and self._rerun(row):
            self._pump()
            self._changed()
            return
        if row is not None:
            if result is not None:
                row["check"] = "ok"
                self.results[cid] = (result, self.clock())
            else:
                code = value.get("error") if isinstance(value, dict) else value
                log("%s %s dropped (%s)" % (row["site"], row["hoster"], code if isinstance(code, str) else "no_stream"))
                self.rows.remove(row)
        self._pump()
        self._changed()

    def _rerun(self, row) -> bool:
        """Search the row's site again (once): its rows go, the job is queued again. False: already done once."""
        key = next((k for k, entry in self.jobs.items() if entry["op"] in ("sources", "page_sources")
                    and entry["job"].get("site") == row["site"] and entry["job"].get("section") == row["section"]),
                   None)
        if key is None or key in self.reran or self.jobs[key]["state"] != "done":
            return False
        self.reran.add(key)
        log("%s searched again: the host worker restarted" % row["site"])
        gone = {r["cid"] for r in self.rows if r["site"] == row["site"] and r["section"] == row["section"]}
        self.rows = [r for r in self.rows if r["cid"] not in gone]
        for cid in gone:
            self.results.pop(cid, None)
        self.queue = [item for item in self.queue if not (item[0] == "check" and item[1] in gone)]
        self.jobs[key].update(state="queued", answer=None)
        self.queue.append(("job", key))
        return True

    def fresh_result(self, cid):
        """The row's check resolve when it is fresh enough to play, else None (then the player resolves again)."""
        found = self.results.get(cid)
        if found is None or self.clock() - found[1] >= RESULT_FRESH_S:
            return None
        return dict(found[0])

    def forget(self, cid) -> None:
        self.results.pop(cid, None)

    def drop(self, cid) -> None:
        """A row that did not play even after its re-resolve (T107): "failed" for this screen (failover skips it, the
        no-servers report still names it), never retried through the server."""
        row = self._row(cid)
        self.results.pop(cid, None)
        if row is not None and row["check"] != "failed":
            row["check"] = "failed"
            self._changed()

    # --- state ------------------------------------------------------------------------------------------------------
    def busy(self) -> bool:
        if self.cancelled:
            return False
        return any(entry["state"] in ("queued", "running") for entry in self.jobs.values()) or \
            any(row["check"] == "checking" for row in self.rows)

    def pending_sections(self) -> set:
        out = {entry["job"].get("section") for entry in self.jobs.values() if entry["state"] in ("queued", "running")}
        return out | {row["section"] for row in self.rows if row["check"] == "checking"}

    def merge(self, data):
        """The server's answer with the device rows in it (after the server's rows of the same group). While a job or
        a check runs the answer says `pending` (failover.still_coming: "Looking…", never "no servers"); a device
        group that ended with no row is left out, like an empty server group."""
        if not isinstance(data, dict) or (not self.jobs and not self.rows):
            return data
        out = dict(data)
        server = [c for c in data.get("candidates") or [] if isinstance(c, dict)]
        known = {c.get("cid") for c in server}
        rows = [dict(row) for row in self.rows if row["cid"] not in known]
        out["candidates"] = server + rows
        busy = self.busy()
        if busy:
            out["pending"] = True
        live = {c.get("section") for c in out["candidates"]} | (self.pending_sections() if busy else set())
        out["sections"] = [s for s in data.get("sections") or [] if not (
            isinstance(s, dict) and s.get("device") is True and not s.get("count") and s.get("id") not in live)]
        return out

    def holds(self, sources, candidate, starred=None, resume_hoster=None) -> bool:
        """Autoplay waits while a group ranked BEFORE the next candidate's group still has device work (the viewer's
        group order holds for device rows too); with no candidate at all it waits while anything runs."""
        if not self.busy():
            return False
        if candidate is None:
            return True
        wanted = failover.hoster_key(resume_hoster)
        if wanted and failover.hoster_key(candidate.get("hoster")) == wanted:
            return False
        ids = [s.get("id") for s in (sources or {}).get("sections") or [] if isinstance(s, dict)]
        if starred in ids:
            ids = [starred] + [s for s in ids if s != starred]
        rank = {section: index for index, section in enumerate(ids)}
        mine = rank.get(candidate.get("section"), len(rank))
        return any(rank.get(section, len(rank)) < mine for section in self.pending_sections())

    def cancel(self) -> None:
        """The screen closed: late answers are ignored, nothing new starts."""
        self.cancelled, self.queue = True, []

    def _changed(self):
        if self.quiet:
            self.dirty = True
            return
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception as error:  # noqa: BLE001
                log("change callback failed: %r" % (error,))


def same_server(answer, item):
    """The candidate of a fresh search that is the download's server: its cid, else its hoster (a cid can change
    when the site renames its links), or None."""
    rows = [c for c in (answer.get("candidates") or []) if isinstance(c, dict) and isinstance(c.get("ref"), str)] \
        if isinstance(answer, dict) and answer.get("state") == "hit" else []
    found = next((c for c in rows if c.get("cid") == item.get("cid")), None)
    if found is None and item.get("source"):
        found = next((c for c in rows if c.get("hoster") == item.get("source")), None)
    return found
