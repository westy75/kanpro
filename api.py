"""The plugin's only client for kanasastream.online (no Enigma2 imports; transport injectable)."""
import json
import re

from . import __version__

BASE = "https://kanasastream.online"
TIMEOUT = 20.0
SOURCES_TIMEOUT = 45.0     # FIX29: a cold title's server list takes 20-40 s on the server (box 11-20: three 200
                           # answers were lost to the 20 s timeout as ApiError('network'))
_ITEM_KEYS = ("kind", "id", "title", "year", "poster", "rating", "progress", "category", "account")
_ROW_PATH = re.compile(r"^/api/catalog/row/([\w-]+)/([\w-]+)(?:\?|$)")
_CURSOR = re.compile(r"^/(m|tv)/(\d+)$")
LOCAL_HEADER = "X-Kanasa-Local-Hosts"      # 085 FR-001: this request runs the bundle's sites on the box
_LOCAL_VALUE = re.compile(r"^enigma2;bundle=[1-9][0-9]{0,5}$")
DEVICE_MATCH_KEYS = ("tmdb_key", "site", "season", "episode", "state", "page_url", "episode_page_url", "hosters")


def _resume_server(saved) -> str:
    """The structured source is the exact media that played; `server` can be an older fallback label."""
    source = saved.get("source") if isinstance(saved, dict) else None
    if isinstance(source, dict) and source.get("kind") == "torrent":
        info_hash, file_idx = source.get("info_hash"), source.get("file_idx")
        valid_idx = file_idx is None or isinstance(file_idx, int) and not isinstance(file_idx, bool) and file_idx >= 0
        if isinstance(info_hash, str) and re.match(r"^[0-9a-fA-F]{40}$", info_hash) and valid_idx:
            return "torrent:%s:%s" % (info_hash.lower(), "" if file_idx is None else file_idx)
    return (saved.get("server") or "") if isinstance(saved, dict) else ""


def _detail_progress(saved):
    if not isinstance(saved, dict):
        return None
    out = {"t": saved.get("position"), "h": _resume_server(saved) or None, "d": saved.get("duration")}
    if saved.get("watched") is True:
        out["watched"] = True
    return out


class ApiError(Exception):
    def __init__(self, code: str, reason=None):
        super().__init__(code)
        self.code, self.reason = code, reason      # reason: the server's {"error": "..."} code, when it sent one


def _error_reason(body):
    """The server's error code of a failed call, else None: {"error": "<code>"} (FIX31: 503 daily_limit; 084:
    409 profile_required) or {"detail": {"code": "<code>"}} (084: the profiles routes)."""
    try:
        data = json.loads(bytes(body or b"")[:2048].decode("utf-8"))
    except (ValueError, AttributeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("error")
    detail = data.get("detail")
    if not isinstance(value, str) and isinstance(detail, dict):
        value = detail.get("code")
    return value if isinstance(value, str) and re.match(r"^[a-z0-9_]{1,40}$", value) else None


PROFILE_REQUIRED = "profile_required"
SEARCH_HISTORY_MAX = 20


def _requests_transport(url, params, headers, timeout):
    import requests  # on the box; imported late so tests never need it
    response = requests.get(url, params=params, headers=headers, timeout=timeout)
    return response.status_code, response.content


def _requests_sender(method, url, json_body, headers, timeout):
    import requests  # on the box; imported late so tests never need it
    response = requests.request(method, url, json=json_body, headers=headers, timeout=timeout)
    return response.status_code, response.content


def _requests_downloader(url, headers, path, timeout):
    import requests  # on the box; imported late so tests never need it
    with requests.get(url, headers=headers, timeout=timeout, stream=True) as response:
        if response.status_code != 200:
            return response.status_code
        with open(path, "wb") as handle:
            for chunk in response.iter_content(64 * 1024):
                if chunk:
                    handle.write(chunk)
    return 200


def _call(fn, *args):
    """fn(*args), every failure one "network" error. Raised OUTSIDE the except block: `from None` only
    hides the cause from the traceback, __context__ would still hold the request with its bearer."""
    failed = False
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001
        failed = True
    if failed:
        raise ApiError("network")


def _title_cursor(kind: str, tmdb_id: int) -> str:
    return ("/m/%d" if kind == "movie" else "/tv/%d") % tmdb_id


class Client:
    def __init__(self, base: str = BASE, lang: str = "en", bearer=None, transport=None, sender=None,
                 on_unauthorized=None, downloader=None, on_profile_required=None):
        self.base = base.rstrip("/")
        self.lang = lang
        self.bearer = bearer
        self.transport = transport or _requests_transport
        self.sender = sender or _requests_sender
        self.downloader = downloader or _requests_downloader
        self.on_unauthorized = on_unauthorized
        # 084: a 409 profile_required from ANY call (any thread) -> back to the "Who's watching?" picker
        self.on_profile_required = on_profile_required

    def _headers(self) -> dict:
        headers = {"X-Interface-Language": self.lang, "User-Agent": "Kanasa-Enigma2/" + __version__}
        if self.bearer:
            headers["Authorization"] = "Bearer " + self.bearer
            headers["X-Kanasa-Profiles"] = "1"     # 084 §1: this box shows a profile picker
        return headers

    def _failed(self, status: int, body) -> None:
        """Raise the ApiError of a non-2xx answer. 409 profile_required is its own code and fires the signal."""
        reason = _error_reason(body)
        if status == 409 and reason == PROFILE_REQUIRED:
            if self.on_profile_required is not None:
                self.on_profile_required()
            raise ApiError(PROFILE_REQUIRED, reason)
        if status == 401:
            raise ApiError("unauthorized", reason)
        if status == 429:
            raise ApiError("rate_limited", reason)
        raise ApiError("http_%d" % status, reason)

    def _signed_out(self) -> None:
        """A 401 with a bearer: the token is dead (revoked on the website). Carry on as a visitor."""
        self.bearer = None
        if self.on_unauthorized is not None:
            self.on_unauthorized()

    def _get(self, path: str, params=None, timeout=TIMEOUT, local=None):
        had_bearer = bool(self.bearer)
        headers = self._headers()
        if isinstance(local, str) and _LOCAL_VALUE.match(local):
            headers[LOCAL_HEADER] = local          # anything else: no header, today's answer (fail closed)
        status, body = _call(self.transport, self.base + path, dict(params or {}), headers, timeout)
        if status == 401 and had_bearer:
            self._signed_out()
            return self._get(path, params, timeout, local)   # once, now without the bearer
        return self._decode(status, body)

    def _raw_get(self, path: str, params=None) -> bytes:
        """GET a non-JSON response with the same bearer/401 rules as _get."""
        had_bearer = bool(self.bearer)
        status, body = _call(self.transport, self.base + path, dict(params or {}), self._headers(), TIMEOUT)
        if status == 401 and had_bearer:
            self._signed_out()
            return self._raw_get(path, params)
        if status != 200:
            self._failed(status, body)
        return body

    def _send(self, method: str, path: str, body) -> dict:
        had_bearer = bool(self.bearer)
        status, raw = _call(self.sender, method, self.base + path, body, self._headers(), TIMEOUT)
        if status == 401 and had_bearer:
            self._signed_out()
        return self._decode(status, raw)

    def _decode(self, status: int, body: bytes) -> dict:
        if status == 204:
            return {}                            # e.g. DELETE /api/library/lists/{id}
        if status not in (200, 201):             # 201: POST /api/library/lists
            self._failed(status, body)
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError("bad_json") from None
        if not isinstance(data, dict):
            raise ApiError("bad_json")
        return data

    def _items(self, raw) -> list:
        return [{key: item.get(key) for key in _ITEM_KEYS} for item in (raw or []) if isinstance(item, dict)]

    def _entries(self, raw) -> list:
        """018 6.3: TMDb titles and site titles in the server's order; a site title opens SiteTitle."""
        out = []
        for row in raw or []:
            if not isinstance(row, dict):
                continue
            if row.get("kind") in ("movie", "tv"):
                out.extend(self._items([row]))
            elif row.get("kind") == "site" and isinstance(row.get("id"), str):
                entry = {key: row.get(key) for key in
                         ("kind", "id", "title", "year", "poster", "category", "season", "episode")}
                # FIX28: a Continue watching site entry (6.3) plays its handle at the saved second
                if isinstance(row.get("progress"), dict):
                    entry["progress"] = dict(row["progress"])
                for key in ("series_id", "up_next"):
                    if key in row:
                        entry[key] = row.get(key)
                out.append(entry)
        return out

    def _row_items(self, row: dict) -> list:
        # a row with entries draws entries, else items (Arabic films/plays have only site titles)
        if isinstance(row.get("entries"), list):
            return self._entries(row["entries"])
        return self._items(row.get("items"))

    def _rows(self, data: dict, tab_id: str) -> list:
        rows = []
        for row in data.get("rows") or []:
            items = self._row_items(row)
            if not items:
                continue
            # page through the row's own see-all row: a Home row is a copy of a row on another tab,
            # and /row/home/<id> is 404 for all but trending
            source = _ROW_PATH.match(row.get("see_all") or "")
            rows.append({"id": row.get("id"), "label": row.get("label") or "", "tab": tab_id,
                         "next_page": row.get("next_page") if source else None,
                         "page_tab": source.group(1) if source else None,
                         "page_row": source.group(2) if source else None, "items": items})
        return rows

    def home(self) -> list:
        return self._rows(self._get("/api/catalog/home"), "home")

    def release(self, channel="test") -> dict:
        # Signed-in boxes ask for test; the server authorizes admins and silently downgrades everyone else.
        wanted = channel if self.bearer else "stable"
        return self._get("/api/enigma2/release", {"channel": wanted})

    def download(self, url: str, path: str) -> None:
        """Stream a release file to path. The bearer goes only to this site's /enigma2/ files (a test
        release is admin-only); any other URL is refused rather than fetched."""
        if not isinstance(url, str) or not url.startswith(self.base + "/enigma2/"):
            raise ApiError("bad_url")
        status = _call(self.downloader, url, self._headers(), path, 120.0)
        if status != 200:
            raise ApiError("http_%d" % status)

    def bundle_manifest(self, channel="test") -> dict:
        """085: the signed host bundle manifest {"manifest": b64, "sig": b64} (bundlestore verifies it). Signed-in
        boxes ask for test; the server gives it to admins only and stable to everyone else."""
        wanted = channel if self.bearer else "stable"
        return self._get("/api/app/bundle/manifest", {"channel": wanted} if wanted == "test" else None)

    def bundle_zip(self, version: int, path: str) -> None:
        """085: stream /api/app/bundle/<version>.zip to path (a test-only zip needs the admin's bearer)."""
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ApiError("bad_url")
        status = _call(self.downloader, "%s/api/app/bundle/%d.zip" % (self.base, version), self._headers(),
                       path, 120.0)
        if status != 200:
            raise ApiError("http_%d" % status)

    def tab(self, tab_id: str) -> list:
        return self._rows(self._get("/api/catalog/tab/%s" % tab_id), tab_id)

    def row_page(self, tab_id: str, row_id: str, page: int) -> dict:
        row = self._get("/api/catalog/row/%s/%s" % (tab_id, row_id), {"page": page}).get("row") or {}
        return {"items": self._row_items(row), "next_page": row.get("next_page")}

    def title(self, kind: str, tmdb_id: int) -> dict:
        data = self._get("/api/catalog/%s/%d" % ("movie" if kind == "movie" else "tv", tmdb_id))
        images = data.get("images") or {}
        item = data.get("item") or {}
        marks, saved = item.get("account"), item.get("progress")
        cast = [{key: person.get(key) for key in ("id", "name", "character", "photo")}
                for person in (data.get("cast") or []) if isinstance(person, dict)]
        recommendations = self._items(data.get("recommendations"))
        return {"item": {key: item.get(key) for key in _ITEM_KEYS},
                "in_list": bool(marks.get("watch_later")) if isinstance(marks, dict) else None,
                "progress": _detail_progress(saved),
                "overview": data.get("overview") or "", "tagline": data.get("tagline") or "",
                "runtime": data.get("runtime_minutes"), "genres": list(data.get("genres") or []),
                "genre_refs": [{key: ref.get(key) for key in ("kind", "id", "name")}
                               for ref in (data.get("genre_refs") or []) if isinstance(ref, dict)],
                "backdrop": images.get("backdrop"), "cast": cast, "recommendations": recommendations,
                "seasons": [{"number": s.get("number"), "label": s.get("label") or "",
                             "episode_count": s.get("episode_count"), "air_date": s.get("air_date")}
                            for s in (data.get("seasons") or []) if isinstance(s, dict)]}

    def person(self, person_id: int, page: int = 1) -> dict:
        data = self._get("/api/catalog/person/%d" % person_id, {"page": page})
        person, row = data.get("person") or {}, data.get("results") or {}
        return {"title": person.get("name") or "", "items": self._items(row.get("items")),
                "next_page": row.get("next_page"),
                "person": {key: person.get(key) for key in
                           ("name", "photo", "biography", "birthday", "place_of_birth")}}

    def genre(self, kind: str, genre_id: int, page: int = 1) -> dict:
        data = self._get("/api/catalog/genre/%s/%d" % (kind, genre_id), {"page": page})
        genre, row = data.get("genre") or {}, data.get("results") or {}
        return {"title": genre.get("name") or "", "items": self._items(row.get("items")),
                "next_page": row.get("next_page")}

    def season(self, tmdb_id: int, number: int) -> list:
        data = self._get("/api/catalog/tv/%d/season/%d" % (tmdb_id, number))
        keys = ("number", "name", "overview", "still", "released", "progress", "air_date")
        return [{key: ep.get(key) for key in keys} for ep in (data.get("episodes") or []) if isinstance(ep, dict)]

    def search(self, query: str) -> list:
        data = self._get("/api/catalog/search", {"q": query})
        turkish = data.get("turkish") or {}
        raw_entries = turkish.get("entries") if isinstance(turkish.get("entries"), list) else turkish.get("items")
        return {"items": self._items((data.get("results") or {}).get("items")),
                "turkish": self._entries(raw_entries), "turkish_label": turkish.get("label") or ""}

    def site_title(self, site_id: str) -> dict:
        from .searchtext import site_id_ok
        if not site_id_ok(site_id):
            raise ApiError("http_404")
        return self._get("/api/catalog/site/%s" % site_id)

    def site_sources(self, site_id: str, local=None) -> dict:
        """local: localhosts.header_value() (085 T104) - the answer then carries page_jobs for the box."""
        from .searchtext import site_id_ok
        if not site_id_ok(site_id):
            raise ApiError("http_404")
        return self._get("/api/catalog/site/%s/sources" % site_id, {"mkv": "1"}, SOURCES_TIMEOUT, local)

    def cards(self, keys: list) -> list:
        if not keys:
            return []
        raw = self._get("/api/catalog/cards", {"ids": ",".join(keys[:60])}).get("cards") or []
        return self._items([dict(card, id=card.get("tmdb_id")) for card in raw if isinstance(card, dict)])

    def sources(self, kind: str, tmdb_id: int, season=None, episode=None, local=None) -> dict:
        """local: localhosts.header_value() (085 T104): "enigma2;bundle=<N>" only while a verified bundle's worker
        answered setup; the answer then carries device_sites jobs instead of server rows for those sites."""
        params = {"kind": kind, "id": tmdb_id}
        if season is not None:
            params.update(season=season, episode=episode)
        params["mkv"] = "1"
        return self._get("/api/catalog/sources", params, SOURCES_TIMEOUT, local)

    def device_match(self, body: dict) -> dict:
        """085 T105 (contract 008 §6): one device job's final state. Signed in only; exactly the 008 keys, never a
        ref, a stream URL or anything else (unknown keys are a 400 on the server anyway)."""
        if not self.bearer:
            raise ApiError("unauthorized")
        if not isinstance(body, dict) or tuple(sorted(body)) != tuple(sorted(DEVICE_MATCH_KEYS)):
            raise ApiError("bad_body")
        return self._send("POST", "/api/app/device-match", body)

    # --- torrents (FIX6) -------------------------------------------------------------------------
    @staticmethod
    def _torrent_query(kind: str, tmdb_id: int, season=None, episode=None) -> dict:
        """Exactly the keys the website accepts: kind=movie&id= or kind=tv&id=&season=&episode=."""
        params = {"kind": "movie" if kind == "movie" else "tv", "id": int(tmdb_id)}
        if params["kind"] == "tv":
            params.update(season=int(season), episode=int(episode))
        return params

    @staticmethod
    def _torrents_allowed():
        """084 §5: a free account without torrents asks nothing (torrents.from_error: no Torrents group)."""
        from . import plans
        if not plans.torrents_allowed():
            raise ApiError("plan_limit")

    def torrent_job(self, kind: str, tmdb_id: int, season=None, episode=None) -> dict:
        """049: the lookup job the app's host bundle runs on the device (the box uses torrents())."""
        self._torrents_allowed()
        job = self._get("/api/torrents/job", self._torrent_query(kind, tmdb_id, season, episode)).get("job")
        if not isinstance(job, dict):
            raise ApiError("bad_json")
        return job

    def torrents(self, kind: str, tmdb_id: int, season=None, episode=None) -> dict:
        """The keyless torrent list (GET /api/torrents): metadata only, never a magnet or a URL."""
        self._torrents_allowed()
        return self._get("/api/torrents", self._torrent_query(kind, tmdb_id, season, episode))

    def resolve(self, ref: str) -> dict:
        from . import plans
        data = self._get("/api/resolve_one", {"ref": ref, "mkv": "1"})
        if plans.max_height() is not None and isinstance(data.get("qualities"), list):
            data = dict(data, qualities=plans.cap_qualities(data["qualities"]))     # 084 §5 max_height
        return data

    def hls_media(self, result: dict) -> dict:
        """Fetch our rewritten master (GET only): {"url": the absolute media playlist to play (the highest
        bandwidth; None for a media playlist), "variants": every quality of the master (FIX33, playback.hls_ladder:
        the Quality panel lists them like the website's hls.js levels)}."""
        from . import playback, plans
        raw = self._raw_get("/stream/proxy", {"url": result.get("stream_url") or "",
                                               "referer": result.get("referer") or ""})
        text = raw.decode("utf-8", "replace")
        url, variants = playback.hls_variant(text, self.base), playback.hls_ladder(text, self.base)
        if plans.max_height() is not None:      # 084 §5: a free account plays and lists up to max_height
            capped = plans.cap_qualities(variants)      # [] = one height only: nothing to choose
            if capped:
                url = max(capped, key=lambda q: (q.get("height") or 0, q.get("bandwidth") or 0))["stream_url"]
            variants = capped if len(capped) > 1 else []
        return {"url": url, "variants": variants}

    def no_servers_report(self, body: dict) -> dict:
        return self._send("POST", "/api/report/no-servers", body)

    def no_servers_flag(self, report_id: int) -> dict:
        return self._send("POST", "/api/report/no-servers/%d/flag" % int(report_id), None)

    # --- subtitles -----------------------------------------------------------------------------
    def subtitles(self, key: str, languages: list, release=None, device=False, local=None) -> dict:
        """device (086): the box's worker runs sub-scene itself - device_subs=1, and the answer keeps `local`
        (its subscene job; devicesubs.Lookup runs it). local: the 085 header value, like sources()."""
        params = {"key": key, "langs": ",".join(str(code).upper() for code in languages)}
        if release:
            params["release"] = str(release)[:300]
        if device:
            params["device_subs"] = "1"
        data = self._get("/api/subtitles", params, local=local if device else None)
        out = {"status": data.get("status") or "unavailable",
               "tracks": [track for track in data.get("tracks") or [] if isinstance(track, dict)]}
        if device and isinstance(data.get("local"), dict):
            out["local"] = data["local"]
        return out

    def subtitle_file(self, track_id: str) -> bytes:
        return self._raw_get("/api/subtitles/file/%s.vtt" % track_id)

    def subtitle_prefs(self) -> dict:
        if not self.bearer:
            raise ApiError("unauthorized")
        return self._get("/api/subtitles/prefs").get("prefs") or {}

    def subtitle_prefs_set(self, values: dict) -> dict:
        if not self.bearer:
            raise ApiError("unauthorized")
        return self._send("PUT", "/api/subtitles/prefs", values).get("prefs") or values

    def subtitle_choice(self, key: str):
        if not self.bearer:
            raise ApiError("unauthorized")
        return self._get("/api/subtitles/choice", {"key": key}).get("choice")

    def subtitle_choice_set(self, choice: dict) -> dict:
        if not self.bearer:
            raise ApiError("unauthorized")
        return self._send("PUT", "/api/subtitles/choice", choice).get("choice") or choice

    # --- account (bearer) ----------------------------------------------------------------------
    def device_start(self, name: str) -> dict:
        return self._send("POST", "/api/auth/device", {"device_name": name})

    def device_poll(self, secret: str) -> dict:
        return self._send("POST", "/api/auth/device/poll", {"device_secret": secret})

    def logout(self) -> None:
        self._send("POST", "/api/auth/logout", {})

    def library(self) -> list:
        """My List: `entries` (FIX28) = TMDb titles and site-only titles (3sk / qrmzi) in the list's order;
        an older server sends `items` (TMDb only)."""
        data = self._get("/api/library", {"tab": "later", "kind": "all"})
        if isinstance(data.get("entries"), list):
            return self._entries(data["entries"])
        return self._items(data.get("items"))

    def library_lists(self) -> list:
        return [{"id": row.get("id"), "name": row.get("name") or "", "count": row.get("count") or 0}
                for row in self._get("/api/library/lists").get("lists") or [] if isinstance(row, dict)]

    def library_list_create(self, name: str) -> dict:
        """201 {"id", "name", "count"} (title-actions.md); an older {"list": {...}} wrapper is read too."""
        data = self._send("POST", "/api/library/lists", {"name": name})
        return data.get("list") if isinstance(data.get("list"), dict) else data

    def library_list_rename(self, list_id: int, name: str) -> dict:
        data = self._send("PATCH", "/api/library/lists/%d" % int(list_id), {"name": name})
        return data.get("list") if isinstance(data.get("list"), dict) else data

    def library_list_delete(self, list_id: int) -> bool:
        self._send("DELETE", "/api/library/lists/%d" % int(list_id), {})
        return True

    def library_list(self, list_id: int) -> list:
        """A named list's saved rows (host, kind, title, poster, cursor) as cards; the id comes from the
        catalogue cursor (/m/<id>, /tv/<id>). Legacy site rows have none and are left out."""
        cards = []
        for row in self._get("/api/library/lists/%d" % int(list_id)).get("items") or []:
            match = _CURSOR.match(row.get("cursor") or "") if isinstance(row, dict) else None
            if match:
                cards.append({"kind": "movie" if match.group(1) == "m" else "tv", "id": int(match.group(2)),
                              "title": row.get("title") or "", "year": None, "poster": row.get("poster"),
                              "rating": None, "progress": None, "category": None, "account": None})
        return cards

    def library_title_state(self, kind: str, tmdb_id: int) -> dict:
        return self._get("/api/library/title-state", {"kind": kind, "tmdb_id": int(tmdb_id)})

    def library_list_set(self, list_id: int, kind: str, tmdb_id: int, on: bool) -> bool:
        data = self._send("POST" if on else "DELETE", "/api/library/lists/%d/items" % int(list_id),
                          {"cursor": _title_cursor(kind, tmdb_id)})
        return bool(data.get("active"))

    def library_set(self, kind: str, tmdb_id: int, on: bool) -> bool:
        data = self._send("POST" if on else "DELETE", "/api/library/later", {"cursor": _title_cursor(kind, tmdb_id)})
        return bool(data.get("active"))

    def episode_watched(self, tmdb_id: int, season: int, episode: int, watched: bool) -> bool:
        cursor = "/tv/%d/s/%d/e/%d" % (int(tmdb_id), int(season), int(episode))
        data = self._send("POST", "/api/progress/watched", {"cursor": cursor, "watched": bool(watched)})
        return bool(data.get("watched"))

    def progress_save(self, kind, tmdb_id, season, episode, position, duration, server, source=None) -> None:
        if kind == "movie":
            cursor, series = "/m/%d" % tmdb_id, ""
        else:
            cursor, series = "/tv/%d/s/%d/e/%d" % (tmdb_id, season, episode), "/tv/%d" % tmdb_id
        body = {"cursor": cursor, "series_cursor": series, "position": position,
                "duration": duration, "server": server or ""}
        if source is not None:              # a torrent: the website's resume-source object
            body["source"] = source
        self._send("POST", "/api/progress", body)

    def site_progress_save(self, site_id, series_site, position, duration, server) -> None:
        """018 contract 6.1: the progress route's site form, keyed by the handle (FIX28)."""
        from .searchtext import site_id_ok
        if not site_id_ok(site_id):
            raise ApiError("http_404")
        body = {"site": site_id, "position": position, "duration": duration, "server": server or ""}
        if site_id_ok(series_site):
            body["series_site"] = series_site
        self._send("POST", "/api/progress", body)

    def server_group_prefs(self) -> dict:
        """The shared star/mute choices. This endpoint deliberately has no visitor form."""
        if not self.bearer:
            raise ApiError("unauthorized")
        data = self._get("/api/progress/prefs")
        return {"server_group_order": data.get("server_group_order") or [],
                "server_group_muted": data.get("server_group_muted") or [],
                "server_group_rank": data.get("server_group_rank") or [],
                "server_autoplay": data.get("server_autoplay", True)}

    def server_group_prefs_set(self, order: list, muted: list) -> dict:
        if not self.bearer:
            raise ApiError("unauthorized")
        data = self._send("POST", "/api/progress/prefs",
                          {"server_group_order": order, "server_group_muted": muted})
        return {"server_group_order": data.get("server_group_order") or [],
                "server_group_muted": data.get("server_group_muted") or [],
                "server_group_rank": data.get("server_group_rank") or [],
                "server_autoplay": data.get("server_autoplay", True)}

    def server_settings_set(self, fields: dict) -> dict:
        """066: POST any subset of the four fields; the answer carries all four."""
        if not self.bearer:
            raise ApiError("unauthorized")
        data = self._send("POST", "/api/progress/prefs", dict(fields))
        return {"server_group_order": data.get("server_group_order") or [],
                "server_group_muted": data.get("server_group_muted") or [],
                "server_group_rank": data.get("server_group_rank") or [],
                "server_autoplay": data.get("server_autoplay", True)}

    def account_import(self, payload: dict) -> dict:
        return self._send("POST", "/api/account/import", payload)

    # --- 033 search history (per viewer; a bearer needs no CSRF token) ------------------------------
    def search_history(self) -> list:
        """The viewer's recent searches, newest first: [{"id": int, "query": str}] (the server keeps 20)."""
        out = []
        for row in self._get("/api/search/history").get("items") or []:
            if not isinstance(row, dict) or type(row.get("id")) is not int or not isinstance(row.get("query"), str):
                continue
            query = row["query"].strip()
            if query:
                out.append({"id": row["id"], "query": query})
        return out[:SEARCH_HISTORY_MAX]

    def search_history_add(self, query: str) -> None:
        self._send("POST", "/api/search/history", {"query": str(query)[:100]})

    def search_history_remove(self, item_id: int) -> None:
        self._send("DELETE", "/api/search/history/%d" % int(item_id), None)

    def search_history_clear(self) -> None:
        self._send("DELETE", "/api/search/history", None)

    # --- 084 profiles + plans (contract specs/084-profiles-plans) ---------------------------------
    def profiles(self) -> dict:
        return self._get("/api/profiles")

    def select_profile(self, profile_id: int, pin=None) -> dict:
        body = {"profile": int(profile_id)}
        if pin is not None:
            body["pin"] = str(pin)
        return self._send("POST", "/api/profiles/select", body)

    def create_profile(self, name: str, color: int) -> dict:
        return self._send("POST", "/api/profiles", {"name": name, "color": int(color)})

    def update_profile(self, profile_id: int, name=None, color=None) -> dict:
        body = {}
        if name is not None:
            body["name"] = name
        if color is not None:
            body["color"] = int(color)
        return self._send("POST", "/api/profiles/%d" % int(profile_id), body)

    def set_profile_pin(self, profile_id: int, pin: str) -> dict:
        return self._send("POST", "/api/profiles/%d/pin" % int(profile_id), {"pin": str(pin)})

    def remove_profile_pin(self, profile_id: int, password=None) -> dict:
        body = {"remove": True}
        if password is not None:
            body["password"] = password
        return self._send("POST", "/api/profiles/%d/pin" % int(profile_id), body)

    def delete_profile(self, profile_id: int) -> dict:
        return self._send("DELETE", "/api/profiles/%d" % int(profile_id), None)

    def entitlement(self) -> dict:
        return self._get("/api/account/entitlement")
