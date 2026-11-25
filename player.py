"""Player: tries the servers in order, plays on Kanasa exteplayer3, saves resume, offers Up next.

FIX32 (owner 2026-11-20): our own exteplayer3 process (extplayer.py) is THE player for films, episodes, torrents and
site titles. Enigma2's 4097 (GStreamer) / 5002 (ServiceApp) VOD paths are gone; Enigma2's service is only stopped
(and the live-TV channel played again on exit, livetv.py)."""
from bisect import insort
import os
import shutil
import time

from enigma import ePoint, eSize, eTimer, gFont
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Screens.Screen import Screen

from . import analytics, api, aspect, async_call, bufferrules, failover, i18n, livetv, noservers, playback, playerlayout, playernav, searchcard, skins, subs, subtitlelayout, theme
from . import bufferjob, downloadmanager, downloadpick, serviceflow, torrengine, torrentrules, torrents
from . import extplayer, extproto, playerguard, playerinstall
from . import devicejobs, devicesubs, hostclient, localhosts
from .source import offline_error as source_offline

NO_PICTURE_S, RESOLVE_S, POLL_MS, MAX_POLLS, SAVE_EVERY_S = 20, 45, 2000, 60, 30
WORKER_WAIT_S, WORKER_WAIT_MS = 6, 300   # 085: a player opened with no title page first waits for the worker
STALL_S, FRESH_S = 30, 600     # frozen picture = broken server; server refs older than this are re-fetched
TOAST_MS = 1500                              # the number-key jump toast
TORRENT_PICTURE_S, TORRENT_STATUS_S = 90, 2   # a torrent buffers longer than a hoster; peers on the card
STALL_NOTE_S = 4                             # FIX31: a frozen torrent shows its peers/speed after this
LOCAL_RETRIES = 1                            # FIX33: a downloaded copy that did not start is played again once
LOCAL_SETTINGS_WAIT_S = 4                    # FIX33: a disk play waits this long for the account's subtitle prefs
EXT_CUES_MAX = 800            # in-video atoms kept (a long film sends thousands); older ones are dropped

CONTROLS_MS = 8000
ICONS = os.path.join(os.path.dirname(__file__), "icons")
PAUSE_GLYPH = "II"


def log(message):
    """Enigma2 writes stdout to its debug log: nothing in the switch path fails silently."""
    print("[Kanasa] %s" % message)
    analytics.note(message)                  # 083: Settings → Send logs carries this session's lines


def _read_file(path):
    with open(path, "rb") as handle:
        return handle.read()


class Player(Screen):
    ext, ext_on = None, False        # Kanasa exteplayer3 (set per play in launch); class defaults for tests/teardown
    ext_missing = False              # FIX32: /usr/bin/exteplayer3 is not on the box: one message, no server tries
    card_pick = False                # spec 081: the searching card shows its Servers button (it has the focus)
    # FIX33 (class defaults for tests/teardown; set per player in __init__)
    local, local_tries, local_failed, net_down, settings_deadline = False, 0, False, False, None
    subtitle_rule_unlisted = False
    quality_url, resolved_result = None, None
    video_aspect = None              # owner 2026-11-22: the aspect ratio's proc state (aspect.Control), per player
    # 085 T104: local mode is decided at the first poll and kept for this player (one consistent list)
    local_header, local_decided, device_jobs, server_data = None, False, None, None
    worker_wait_until = None
    # box 2026-11-24: a resume waits for the device jobs when the saved server is not in the list yet (try_next)
    resume_wait_since, resume_timer, resume_wait_off = None, None, False

    def __init__(self, session, lang, client, source, target):
        self.skin = skins.player(lang)
        Screen.__init__(self, session)
        # a HelpableActionMap (OpenATV 7.6 Components/ActionMap.py) appends to the screen's helpList; a plain
        # Screen has none, so without this the Player would raise on every open
        self.helpList = []
        self.lang, self.client, self.source, self.target = lang, client, source, target
        self.item = {"kind": target["kind"], "id": target["id"]}
        self.old_service = session.nav.getCurrentlyPlayingServiceReference()
        self.sources, self.attempted, self.polls, self.gen = {}, set(), 0, 0
        self.failures = []
        self.current, self.result, self.proxied = None, None, False
        self.trying, self.started, self.none_shown, self.closed, self.paused = False, False, False, False, False
        self.start_at = float(target.get("start_at") or 0)
        self.deadline, self.last_pos, self.last_save = 0.0, None, 0.0
        self.switching, self.next_step = False, None
        self.video_missing_since, self.video_seen, self.audio_only_logged = None, False, False
        # FIX27: the card stays from the first moving position until the picture is on screen
        self.video_size_seen, self.picture_since = False, 0.0
        self.pause_at = None
        self.seek_target, self.seek_at = None, 0.0
        # FIX26 item 7: presses move only the target; one real seek SEEK_SETTLE_MS after the last one
        self.seek_pending, self.seek_hold, self.seek_press, self.seek_gen = False, None, None, None
        self.sources_at, self.pick, self.stall_pos, self.stall_since = 0.0, None, None, 0.0
        self.panel, self.pending_finish, self.finishing_panel = None, None, False
        # the search card (searchcard.py) and what it names
        self.card_on, self.card_since, self.card_args, self.try_since = False, 0.0, None, 0.0
        self.last_failed, self.seek_wait, self.resume_seek, self.queue, self.pick_phase = None, None, None, None, False
        self.bar_on, self.icon_state = False, {}
        # Site subtitle files are drawn by this screen. The dict is shared with the open panel so updates
        # never require closing it. Positive offset means "later" and is subtracted at lookup time.
        self.subtitle_key = subs.content_key(target)
        self.subtitle_state = {"status": "loading", "tracks": [], "embedded": [], "active": None,
                               "embedded_active": None, "offset": 0.0, "size": "m",
                               "prefs": dict(subs.DEFAULT_PREFS)}
        self.subtitle_cues, self.subtitle_choice_value = [], None
        self.subtitle_prefs_ready = self.subtitle_choice_ready = False
        self.subtitle_lookup_started = self.subtitle_default_done = False
        self.subtitle_file_gen = 0
        # FIX6: the box's own torrent list and engine (torrents.py, torrengine.py)
        self.torrent_state, self.torrent_auto_tried, self.torrent_wait_since = None, False, None
        self.torrent_progress, self.torrent_status_at, self.torrent_playing = {}, 0.0, None
        self.torrent_file_bytes = 0
        self.engine = torrengine.EngineClient()
        self.local = False                       # FIX9: playing a download from the box's disk
        # FIX33: the disk copy is retried before anything else; offline it never falls back to the servers
        self.local_tries, self.local_failed, self.net_down, self.settings_deadline = 0, False, False, None
        # FIX12 "play with buffering": a temporary download runs ahead and the player plays that growing file.
        # phase: fill (before the picture) | play | stall (paused: the player caught up with the download)
        self.buffer_job, self.buffer_phase, self.buffer_since, self.buffer_played = None, None, 0.0, False
        self.buffer_offset, self.buffer_offset_known, self.buffer_goal = 0.0, False, 0
        self.buffer_progress, self.last_at, self.buffer_dir = {}, None, None
        # box 11-24: was the running exteplayer3 launched with -o 1; the stuck watchdog's clock and its relaunches
        self.ext_progressive, self.buffer_still, self.buffer_relaunches = False, None, []
        self.hls_prepared = False
        self.report_sent, self.report_id, self.report_flagged = False, None, False
        self.manual_wait = False
        # Kanasa exteplayer3: our own process (extplayer.py). ext_on = a play runs on it; Enigma2's service APIs
        # see nothing then, every value comes from its JSON lines.
        self.ext, self.ext_on, self.ext_missing = None, False, False
        self.audio_saved, self.audio_saved_loaded, self.ext_audio_done = None, False, False
        # the box's own picture policy is saved now and put back on every exit road (finish, ext_closed)
        self.video_aspect = aspect.Control(aspect.ProcFs())
        self.video_aspect.save()
        self["upnext"] = Label("")
        self["subline1"], self["subline2"] = Label(""), Label("")
        self["subbox1"], self["subbox2"] = Label(""), Label("")
        self.rtl = lang == "ar"
        self.nav = playernav.Nav(playernav.buttons(target, self.rtl), self.rtl)
        for name in ("dimtop", "dimbottom", "topbox", "topframe", "top", "badge", "elapsed", "total", "track",
                     "buffered", "fill", "head", "btnframe", "paused", "toast") + skins.PLAYER_CARD + skins.PLAYER_RING:
            self[name] = Label("")
        self["topicon"] = Pixmap()
        self["cardpickicon"] = Pixmap()          # FIX32 (spec 081): the searching card's Servers button icon
        for name in skins.PLAYER_BUTTONS:
            label = i18n.t("player.btn." + name, lang)
            self["btn_" + name] = Label((label + "   ") if self.rtl else ("   " + label))
            self["btnicon_" + name] = Pixmap()
        if "quality" not in self.nav.names:
            self["btn_quality"].hide(); self["btnicon_quality"].hide()
        self.poll_timer = eTimer(); self.poll_timer.callback.append(self.poll)
        self.tick_timer = eTimer(); self.tick_timer.callback.append(self.tick)
        self.bar_timer = eTimer(); self.bar_timer.callback.append(self.hide_bar)
        self.switch_timer = eTimer(); self.switch_timer.callback.append(self.switched)
        self.service_timer = eTimer()
        self.service_flow = serviceflow.ServiceFlow(session.nav, self.service_timer, self.service_generation_valid)
        self.service_timer.callback.append(self.service_flow.ready)
        self.toast_timer = eTimer(); self.toast_timer.callback.append(lambda: self["toast"].hide())
        self.seek_timer = eTimer(); self.seek_timer.callback.append(self.seek_settled)
        self.torrent_timer = eTimer(); self.torrent_timer.callback.append(self.torrent_waited)
        self.subtitle_timer = eTimer(); self.subtitle_timer.callback.append(self.subtitle_tick)
        self.resume_timer = eTimer(); self.resume_timer.callback.append(self.try_next)
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
            "cancel": self.back, "green": self.servers, "blue": self.take_next, "yellow": self.report_problem,
            "ok": lambda: self.key("ok"), "left": lambda: self.key("left"), "right": lambda: self.key("right"),
            "up": lambda: self.key("up"), "down": lambda: self.key("down")}, -1)
        # the remote's AUDIO key: the file's audio tracks (owner 2026-11-18: a torrent with several languages)
        self["audiokey"] = ActionMap(["InfobarAudioSelectionActions"], {"audioSelection": self.audio_tracks}, -1)
        # owner 2026-11-22: a direct key for the aspect ratio. HELP is unused in the player (no HelpableScreen);
        # an image's own aspect key (InfobarAspectSelectionActions, where the keymap has it) does the same.
        self["aspectkey"] = ActionMap(["HelpActions", "InfobarAspectSelectionActions"],
                                      {"displayHelp": self.cycle_aspect, "aspectSelection": self.cycle_aspect}, -1)
        # number keys 1/3 4/6 7/9 jump by the box's own seek steps, bound like e2iplayer's player (actions
        # seekdef:N, XMP:446-451); InfobarSeekActions maps the same keys on images without the MediaPlayer keymap
        self["seekkeys"] = ActionMap(["MediaPlayerSeekActions", "InfobarSeekActions"],
                                     dict(("seekdef:%d" % key, (lambda key=key: self.jump(key)))
                                          for key in playback.NUMBER_SEEK_KEYS), -1)
        self.onLayoutFinish.append(self.begin)
        playerguard.claim(self)                  # FIX33: one Player at a time (playerguard.open_player)
        on_close = getattr(self, "onClose", None)
        if on_close is not None:
            on_close.append(self.ext_closed)

    def ext_closed(self):
        """The screen closes by any road (finish already stopped it; standby or a crash in a panel may not):
        our exteplayer3 never outlives the player, nor does our aspect ratio."""
        playerguard.release(self)
        self.restore_aspect()
        ext = self.__dict__.get("ext")
        if ext is not None and ext.busy():
            ext.stop()

    def gone(self):
        """FIX27: closed, or torn down. Enigma2's Screen.doClose clears __dict__, so a late callback that read
        self.closed raised AttributeError (box log 2026-11-19 20:04): every late entry asks this first."""
        return bool(getattr(self, "closed", True))

    # --- servers -------------------------------------------------------------------------------
    def begin(self):
        if self["topicon"].instance:
            self["topicon"].instance.setPixmapFromFile(os.path.join(ICONS, "pill_back_r_surface.png" if self.rtl else "pill_back_surface.png"))
        if self["cardpickicon"].instance:
            self["cardpickicon"].instance.setPixmapFromFile(os.path.join(ICONS, "pill_servers_accent.png"))   # on the focused (accent) pill
        self.hide_bar(); self["upnext"].hide(); self["toast"].hide()
        self.hide_card()                     # the skin shows the card boxes until told otherwise (empty box on disk play)
        self["subline1"].hide(); self["subline2"].hide(); self["subbox1"].hide(); self["subbox2"].hide()
        if not extplayer.installed():
            self.player_missing()
            return
        self.an_play = analytics.play_start(self.target)     # 083: one start + one final ok/fail per attempt
        if self.target.get("local_path"):
            self.play_local()
            return
        self.load_subtitle_settings()
        self.show_looking()
        self.stop_service()
        self.tick_timer.start(1000, False)
        self.subtitle_timer.start(200, False)
        # Account server preferences are deliberately re-read for every title.  A local cache must never
        # override a star/mute change made moments ago in the Android app.
        if self.source.signed_in():
            async_call.run(self.source.load_server_groups, on_done=lambda _: self.begin_online(),
                           on_error=lambda error: self.begin_online())
        else:
            self.begin_online()

    def begin_online(self):
        if self.gone():
            return
        self.load_torrents()
        self.poll()
        self.resume_torrent_now()

    def resume_torrent_now(self):
        """FIX19: a resume on a saved torrent starts that torrent AT ONCE from a row built from its key, in
        parallel with the sources/torrent fetches (they only feed the Servers panel, or the failover)."""
        t = self.target
        if t.get("site_id") or self.attempted or self.trying or self.started or self.switching \
                or self.pick is not None:
            return
        row = torrents.resume_start(t.get("resume_hoster"), self.start_at, self.source.server_autoplay(),
                                    self.source.server_group_muted(), t.get("season") is not None,
                                    t.get("season"), t.get("episode"))
        if row is None:
            return
        self.torrent_auto_tried = True           # the "before" rule must not start it a second time
        log("resume starts the saved torrent at once")
        self.attempt(torrents.candidate(row, self.lang))

    def player_missing(self):
        """FIX32: no /usr/bin/exteplayer3 (the package Depends on it, so a broken install): one clear message and
        a Back button; no server is resolved, nothing plays."""
        log("exteplayer3 is missing: nothing can play")
        self.ext_missing, self.none_shown, self.trying, self.started = True, True, False, False
        # 0.6.10: ipks up to 0.6.9 did not Depend on it; the plugin installs it (once per run, in the background)
        installing = playerinstall.ensure() == "installing"
        self.show_search("player_installing" if installing else "missing")

    # --- a download on the box's disk (FIX9) -----------------------------------------------------
    def play_local(self, retry=False):
        """The downloaded copy first; its saved subtitles are the tracks. FIX33: if it does not play (or a growing
        file runs out) it is played once more from the disk; then the online servers take over at the same
        second only when they can be reached - with no network a clear card says the download did not play.
        Nothing a disk play does on the network may block it or end it (every call fails silently)."""
        self.local = True
        self.subtitle_lookup_started = True               # the saved files are this video's site tracks
        if not retry:
            tracks = downloadpick.saved_tracks(self.target.get("local_subtitles"))
            self.subtitle_state.update(status="ok" if tracks else "none", tracks=tracks)
            self.settings_deadline = time.monotonic() + LOCAL_SETTINGS_WAIT_S
            self.load_subtitle_settings()
            self.load_local_sources()
        self.current = {"hoster": "", "cid": "device", "section": self.target.get("local_section") or "device"}
        if self.target.get("local_torrent"):
            self.current["local_torrent"] = True         # spec 081 §2: a downloaded torrent starts like a stream
        self.result = {"local": True}
        self.trying, self.started = True, False
        self.deadline, self.last_pos = time.monotonic() + NO_PICTURE_S, None
        self.try_since = time.monotonic()
        log("playing the downloaded copy%s" % (" again" if retry else ""))
        self.launch(self.target["local_path"])
        self.subtitle_timer.start(200, False)

    def load_local_sources(self):
        """FIX33: the Servers panel of a disk play lists the online servers when the network is there; offline
        the fetch fails silently and the panel says so (net_down). Display data only: nothing autoplays."""
        t = self.target
        if t.get("site_id") or t.get("id") is None:
            return
        try:
            ident = int(t["id"])
        except (TypeError, ValueError):
            return
        async_call.run(self.client.sources, t["kind"], ident, t.get("season"), t.get("episode"),
                       on_done=self.local_sources_loaded, on_error=self.local_sources_failed)

    def local_sources_loaded(self, data):
        if self.gone() or not self.local:
            return
        self.net_down = False
        self.sources, self.sources_at = self.source.rank_sources(data), time.monotonic()
        if self.panel is not None and hasattr(self.panel, "sources_updated"):
            self.panel.sources_updated(self.sources)

    def local_sources_failed(self, error):
        if self.gone() or not self.local:
            return
        log("servers for the Servers panel not loaded (%s): the downloaded copy plays on"
            % getattr(error, "code", repr(error)))
        if source_offline(error):
            self.net_down = True
            if self.panel is not None and hasattr(self.panel, "offline_updated"):
                self.panel.offline_updated(True)

    def local_settings_fallback(self):
        """FIX33: a disk play does not wait for the account's subtitle prefs/choice longer than
        LOCAL_SETTINGS_WAIT_S (offline the requests end only at their timeout): the box's own values."""
        if self.settings_deadline is None or time.monotonic() < self.settings_deadline:
            return
        self.settings_deadline = None
        if not self.subtitle_prefs_ready:
            log("subtitle settings: the account did not answer, the box's own")
            self.subtitle_prefs_loaded(self.source.prefs.subtitle_prefs())
        if not self.subtitle_choice_ready and self.subtitle_key:
            self.subtitle_choice_loaded(self.source.prefs.subtitle_choice(self.subtitle_key))

    def service_generation_valid(self, generation):
        return generation is None or (not self.gone() and generation == self.gen)

    def cancel_service_timers(self):
        """No detection/source timer may fire immediately after a blocking native service start."""
        self.tick_timer.stop()
        self.poll_timer.stop()
        self.torrent_timer.stop()
        self.switch_timer.stop()

    def stop_service(self):
        self.cancel_service_timers()
        if self.ext is not None:
            self.ext.stop()                      # q -> exit (a waiting launch is cancelled too)
        self.service_flow.stop()

    def switch_service(self, ref, after=None, generation="current"):
        """Enigma2's own service (only the live-TV channel the player found, on exit): stop -> GUI-loop delay ->
        one generation-checked playService."""
        self.cancel_service_timers()
        self.video_missing_since, self.video_seen, self.audio_only_logged = None, False, False
        self.video_size_seen = False
        wanted = self.gen if generation == "current" else generation

        def started():
            self.video_size_seen = False         # a size event from the stopped service is not this picture
            if after is not None:
                after()
            if not self.gone() and (self.trying or self.started):
                self.tick_timer.start(1000, False)
        self.service_flow.switch(ref, wanted, started)

    def launch(self, url, headers=None, after=None, generation="current"):
        """Every play starts here, on Kanasa exteplayer3 (FIX32: the only player)."""
        wanted = self.gen if generation == "current" else generation
        self.launch_ext(url, headers or {}, after, wanted)

    def ext_process(self):
        if self.ext is None:
            self.ext = extplayer.ExtPlayer(self.ext_event, self.ext_exit)
        return self.ext

    def launch_ext(self, url, headers, after, wanted):
        """Our own exteplayer3 (e2iplayer's way): Enigma2's service stops and playService(None) frees the DVB
        decoder (XMP:385); the process starts only after the previous one exited (extplayer.launch)."""
        self.cancel_service_timers()
        self.video_missing_since, self.video_seen, self.audio_only_logged = None, False, False
        self.video_size_seen = False
        self.service_flow.stop()
        try:
            self.session.nav.playService(None)
        except Exception as error:  # noqa: BLE001 - an image whose playService refuses None: stopService did it
            log("playService(None): %r" % (error,))
        ext = self.ext_process()
        active = self.subtitle_state.get("embedded_active") if self.ext_on else None
        index = ext.sub_index_for(url, active)
        if index is None:                        # another file: its in-video list comes with its s_l line
            if self.subtitle_state.get("embedded_active") is not None:
                self.subtitle_cues = []
            self.subtitle_state.update(embedded=[], embedded_active=None)
        else:
            self.subtitle_cues = []              # the same track comes back with -9 (an index into s_l)
        self.ext_on = True
        self.apply_aspect()                      # the remembered aspect ratio, on every play (and every server)
        # -t: the remembered audio language as an INDEX into a_l, known only for a relaunch of the same file;
        # any other file switches at runtime once its a_l / a_c lines arrive (ext_apply_saved_audio)
        audio = ext.audio_index_for(url, playernav.audio_pick(getattr(ext, "last_audio", None), self.saved_audio()))
        self.ext_audio_done = False
        try:
            hls = str(url).startswith(("http://", "https://")) and \
                bufferrules.container(getattr(self, "result", None)) == "hls"   # FIX29: ffmpeg -f options
            job = getattr(self, "buffer_job", None)
            # 0.8.1 (box 11-24): a buffer .ts still growing needs -o 1, else exteplayer3 stops at its open length
            growing = job is not None and bufferrules.progressive(url, job.snapshot()["done"])
            argv = extproto.build_argv(url, headers, audio_index=audio, sub_index=index, hls=hls, progressive=growing)
            self.ext_progressive = growing
        except ValueError:
            self.failed(wanted, "url not playable by exteplayer3")
            return

        def done(ok, reason):
            if self.gone() or not self.service_generation_valid(wanted) or not self.ext_on:
                if ok:
                    ext.stop()                   # a start nobody wants any more: it goes at once
                return
            if not ok:
                self.failed(wanted, "exteplayer3: %s" % reason)
                return
            if after is not None:
                after()
            if self.trying or self.started:
                self.tick_timer.start(1000, False)
        log("exteplayer3 %s" % url.split("?")[0])
        ext.launch(argv, done)

    def ext_event(self, event):
        """One JSON line of the running exteplayer3 (extproto.Event)."""
        if self.gone() or not self.ext_on:
            return
        kind, data = event
        if kind == "video":
            self.video_size_seen = True          # FIX27's picture signal: the card may go
            if self.started:
                self.hide_card_on_picture()
        elif kind in ("open", "play") and data["sts"] != 0 and not self.started:
            self.failed(reason="exteplayer3 %s sts=%s" % (kind, data["sts"]))
        elif kind == "error":
            log("exteplayer3 FF_ERROR %s (%s)" % (data["msg"][:200], data["code"]))
            if not self.started:
                self.failed(reason="exteplayer3 error: %s" % data["msg"][:120])
        elif kind == "stop" and not self.started:
            self.failed(reason="exteplayer3 stopped before picture")
        elif kind == "sub_list":
            self.subtitle_state["embedded"] = subtitlelayout.in_video_rows(data, self.lang)
            self.subtitle_panel_updated()
            if getattr(self, "subtitle_rule_unlisted", False) and self.subtitle_state["embedded"]:
                self.subtitle_rule_unlisted, self.subtitle_default_done = False, False   # FIX33: listed now
            if not getattr(self, "subtitle_default_done", True):
                self.maybe_subtitle_default()    # spec 081 §2: an in-file track may start ON once listed
        elif kind == "sub_atom":
            if data["id"] == self.subtitle_state.get("embedded_active"):
                cues = self.subtitle_cues
                if len(cues) >= EXT_CUES_MAX:
                    del cues[:len(cues) - EXT_CUES_MAX + 1]
                insort(cues, subs.Cue(data["start"], data["end"], data["text"]))
        elif kind == "sub_flush":
            if self.subtitle_state.get("embedded_active") is not None:
                self.subtitle_cues = []          # a seek, a track switch: the new place's atoms follow at once
                self.subtitle_tick()
        elif kind in ("audio_list", "audio_current"):
            self.ext_apply_saved_audio()
        elif kind == "audio_selected":
            log("exteplayer3 audio_selected %s" % (data,))
            if data["sts"] != 0:
                self.show_toast(i18n.t("player.audio_failed", self.lang))
        elif kind in ("sub_selected", "sub_current", "version"):
            log("exteplayer3 %s %s" % (kind, data))
        # position.lms: the grey buffer bar reads it through ExtPlayer.buffered() (buffered_position)

    def saved_audio(self):
        """This title's remembered audio choice (box prefs), read once per player."""
        if not getattr(self, "audio_saved_loaded", False):
            self.audio_saved_loaded = True
            key = getattr(self, "subtitle_key", None)
            try:
                self.audio_saved = self.source.prefs.audio_choice(key) if key else None
            except Exception as error:  # noqa: BLE001 - a broken prefs file never stops the play
                log("audio choice not read: %r" % (error,))
                self.audio_saved = None
        return getattr(self, "audio_saved", None)

    def ext_apply_saved_audio(self):
        """Once per run, when a_l and a_c are both known: switch to the remembered language if it is in this
        file and not already playing (a -t relaunch already plays it)."""
        ext = self.ext
        if getattr(self, "ext_audio_done", False) or ext is None or not ext.audio or ext.audio_current is None:
            return
        self.ext_audio_done = True
        want = playernav.audio_pick(ext.audio, self.saved_audio())
        if want is not None and want != ext.audio_current.get("id"):
            log("exteplayer3 audio: the remembered track %s" % want)
            ext.audio_track(want)

    def ext_exit(self, code):
        """exteplayer3 ended by itself (we did not ask): before the picture = a failed server; after it = the
        end of the film or a broken stream, which on_eof tells apart (Up next / same second on the next server)."""
        if self.gone() or not self.ext_on:
            return
        log("exteplayer3 exited (%s)" % (code,))
        if not self.started:
            self.failed(reason="exteplayer3 exited before picture (%s)" % (code,))
            return
        self.on_eof()

    def go_online(self):
        self.show_looking()
        self.begin_online()

    # --- the Torrents group (FIX6) ---------------------------------------------------------------
    def load_torrents(self):
        t = self.target
        if t.get("site_id"):                     # a Turkish-site title (018): no TMDb identity, no torrents
            self.torrent_state = {"state": "none", "rows": []}
            return
        self.torrent_state = torrents.loading()
        episode = t.get("season") is not None
        async_call.run(self.client.torrents, t["kind"], int(t["id"]), t.get("season"), t.get("episode"),
                       on_done=lambda data: self.torrents_loaded(torrents.from_payload(data, episode)),
                       on_error=lambda error: self.torrents_loaded(
                           torrents.from_error(getattr(error, "code", "network"))))

    def torrents_loaded(self, state):
        if self.gone():
            return
        log("torrents: %s, %d rows" % (state["state"], len(state["rows"])))
        self.torrent_state = state
        playing = (self.current or {}).get("torrent")
        if torrents.merge_listed(playing, state):   # FIX19: the key-only resume row gets its release name
            self.current.update(label=playing["name"] or torrents.quality_text(playing),
                                meta=torrents.meta_text(playing, self.lang))
        if self.panel is not None and hasattr(self.panel, "torrents_updated"):
            self.panel.torrents_updated(state)
        if self.trying or self.started or self.switching:
            # A list requested alongside the sources may finish after a hoster has taken over.  It is
            # still useful to the Servers panel, but its old wait callback no longer owns autoplay.
            self.torrent_timer.stop()
            self.torrent_wait_since = None
            return
        if self.torrent_wait_since is not None:
            self.torrent_waited()

    def torrent_turn(self, stage):
        """True when the torrent rule took over (a torrent starts, or the list is awaited)."""
        if self.gone() or self.trying or self.started or self.switching or self.pick is not None:
            return False                         # only an idle initial/failover decision may start a server
        if not self.source.server_autoplay():
            return False                         # Auto off (066): no torrent auto-switch
        order, muted = self.source.server_group_order(), self.source.server_group_muted()
        waited = time.monotonic() - self.torrent_wait_since if self.torrent_wait_since is not None else 0.0
        action, row = torrents.auto_step(stage, self.torrent_state, muted, order[0] if order else None,
                                         self.target.get("resume_hoster"), self.attempted,
                                         self.torrent_auto_tried, waited)
        if action == "wait":
            self.torrent_stage = stage
            if self.torrent_wait_since is None:
                self.torrent_wait_since = time.monotonic()
            self.show_looking()
            self.torrent_timer.start(1000, True)
            return True
        self.torrent_wait_since = None
        if action == "play":
            self.torrent_auto_tried = True
            log("torrent starts by itself (%s)" % stage)
            self.attempt(torrents.candidate(row, self.lang))
            return True
        return False

    def torrent_waited(self):
        if self.gone():
            return
        self.torrent_timer.stop()
        if not self.trying and not self.started and not self.switching \
                and self.pick is None and not self.none_shown:
            if getattr(self, "torrent_stage", "before") == "before":
                self.try_next()
            elif not self.torrent_turn("after"):
                self.show_none()

    def poll(self):
        if self.gone() or self.trying or self.started:
            return                               # a stale poll timer cannot compete with the current server
        if self.wait_for_worker():
            return
        self.polls += 1
        t = self.target
        local = self.local_mode()
        if t.get("site_id"):                     # 018 §5.3: the site handle's own servers
            async_call.run(self.client.site_sources, t["site_id"], local, on_done=self.sources_loaded,
                           on_error=self.sources_failed)
            return
        async_call.run(self.client.sources, t["kind"], int(t["id"]), t.get("season"), t.get("episode"), local,
                       on_done=self.sources_loaded, on_error=self.sources_failed)

    # --- 085: hosts on the box (devicejobs.py) -------------------------------------------------------------------
    def wait_for_worker(self) -> bool:
        """085: Continue watching, Up Next and resumes open the player with no title page, so nothing warmed the
        worker and the first list would be the server's (box 2026-11-24: Ömür Usta played via /stream/proxy).
        Start it now and hold the first poll until its setup answered, at most WORKER_WAIT_S (box cold start
        2.2-4.5 s); then local mode is decided as usual (still no worker = today's server list)."""
        if self.local_decided or not hostclient.CLIENT.available() or localhosts.header_value():
            return False
        now = time.monotonic()
        if self.worker_wait_until is None:
            self.worker_wait_until = now + WORKER_WAIT_S
            localhosts.warm()
        if now >= self.worker_wait_until:
            log("host worker not ready after %d s: server list" % WORKER_WAIT_S)
            return False
        self.poll_timer.start(WORKER_WAIT_MS, True)
        return True

    def local_mode(self):
        """The X-Kanasa-Local-Hosts value for this player's polls, fixed at the first poll: a worker that answered
        setup now -> local mode for the whole play; none -> today's server list for the whole play."""
        if not self.local_decided:
            self.local_decided = True
            self.local_header = localhosts.header_value()
            if self.local_header:
                log("local hosts on (%s)" % self.local_header)
        return self.local_header

    def device_merge(self, data):
        """The server's answer with this player's device rows in it; starts the answer's new device jobs."""
        if not self.local_header or not devicejobs.local_answer(data) and self.device_jobs is None:
            return data
        if self.device_jobs is None:
            self.device_jobs = devicejobs.Runner(hostclient.CLIENT.call, on_change=self.device_changed,
                                                 report=self.device_report)
        self.device_jobs.start(data)
        return self.device_jobs.merge(data)

    def device_changed(self):
        """A device job or row check answered: the list again (panel too), and the autoplay that waited for it."""
        if self.gone() or self.server_data is None or self.device_jobs is None:
            return
        self.sources = self.source.rank_sources(self.device_jobs.merge(self.server_data))
        if self.panel is not None and hasattr(self.panel, "sources_updated"):
            self.panel.sources_updated(self.sources)
        if self.trying or self.started or self.switching or self.pick is not None or self.local:
            return
        if self.none_shown or self.manual_wait or not self.source.server_autoplay():
            return
        self.try_next()

    def device_report(self, body):
        """T105: a final device job state to the shared match store, signed in only, in the background."""
        if not self.source.signed_in():
            return
        async_call.run(self.client.device_match, body, on_done=lambda answer: None,
                       on_error=lambda error: log("device-match report not sent (%s)" % getattr(error, "code", "?")))

    def device_resolve(self, candidate, gen, fresh_only=False):
        """A device row's stream: its check resolve when still fresh, else the worker resolves it now."""
        runner = self.device_jobs
        cached = None if fresh_only or runner is None else runner.fresh_result(candidate.get("cid"))
        if cached is not None:
            runner.forget(candidate.get("cid"))     # a token is played once; a retry resolves again
            self.device_resolved(gen, True, cached, ready=True)
            return
        hostclient.CLIENT.call("resolve", {"ref": candidate.get("ref"), "client": dict(devicejobs.CLIENT_CAPS)},
                               lambda ok, value: self.device_resolved(gen, ok, value))

    def device_resolved(self, gen, ok, value, ready=False):
        if self.gone() or gen != self.gen:
            return
        result = value if ready else (devicejobs.play_result(value) if ok else None)
        if result is None:
            code = value.get("error") if isinstance(value, dict) else value
            log("device resolve failed for %s: %s" % ((self.current or {}).get("hoster"),
                                                       code if isinstance(code, str) else "no_stream"))
            self.failed(gen, "device resolve: %s" % (code if isinstance(code, str) else "no_stream"))
            return
        from . import plans
        if plans.max_height() is not None and isinstance(result.get("qualities"), list):
            result = dict(result, qualities=plans.cap_qualities(result["qualities"]))     # 084 §5 max_height
        self.resolved(result, gen)

    def device_hls(self):
        """FIX29's media-playlist step for a device row: the worker fetches the master with the bundle's headers
        (never /stream/proxy); the answer has client.hls_media's shape, so the quality ladder is unchanged."""
        gen = self.gen
        headers = playback.device_headers(self.result)

        def answered(ok, value):
            if self.gone() or gen != self.gen:
                return
            answer = devicejobs.hls_answer(value) if ok else None
            from . import plans
            if answer is not None and plans.max_height() is not None:
                capped = plans.cap_qualities(answer["variants"])
                if capped:
                    answer["url"] = max(capped, key=lambda q: (q.get("height") or 0,
                                                               q.get("bandwidth") or 0))["stream_url"]
                answer["variants"] = capped if len(capped) > 1 else []
            self.hls_media_ready(answer)
        hostclient.CLIENT.call("hls_media", {"stream_url": self.result.get("stream_url") or "", "headers": headers},
                               answered)

    def reresolve_device(self):
        """T107: the device row failed (403 / no picture): one fresh resolve on the box, one more play."""
        candidate, gen = self.current or {}, self.gen
        self.device_reresolved = set(getattr(self, "device_reresolved", None) or ()) | {candidate.get("cid")}
        self.result, self.hls_prepared = None, False
        self.deadline, self.last_pos = time.monotonic() + RESOLVE_S, None
        self.try_since = time.monotonic()
        log("re-resolving %s on the box" % candidate.get("hoster"))
        self.device_resolve(candidate, gen, fresh_only=True)

    def sources_loaded(self, data):
        if self.gone():
            return
        self.server_data = data
        self.sources, self.sources_at = self.source.rank_sources(self.device_merge(data)), time.monotonic()
        if self.panel is not None and hasattr(self.panel, "sources_updated"):
            self.panel.sources_updated(self.sources)
        if self.trying or self.started:
            return                               # an in-flight re-poll is display data, never a second autoplay
        coming = failover.still_coming(data) and self.polls < MAX_POLLS
        if coming:
            self.poll_timer.start(POLL_MS, True)
        if not self.source.server_autoplay() and self.pick is None:
            if not self.manual_wait and not self.trying and not self.started:
                self.manual_wait = True           # Auto off (066): the panel opens once; nothing starts
                self.trying, self.started, self.none_shown = False, False, True
                self.show_search("pick")
                self.servers()
            return
        if self.pick is not None:
            self.start_pick()
        elif not self.trying and not self.started and not self.switching:   # failed() is already switching
            self.try_next()

    def sources_failed(self, error):
        log("sources failed: %r" % (error,))
        if self.gone() or self.trying or self.started:
            return
        if self.pick is not None:
            self.start_pick()                    # the refs we have are better than none
            return
        if self.polls < MAX_POLLS:
            self.poll_timer.start(POLL_MS, True)
        elif not self.trying and not self.started and not self.torrent_turn("after"):
            self.show_none()

    def try_next(self):
        if self.gone() or self.trying or self.started or self.switching or self.pick is not None:
            return                               # stale timers/callbacks never replace a live attempt or picture
        if not self.sources_at or time.monotonic() - self.sources_at > FRESH_S:
            # no answer yet (FIX19: a resume torrent failed before the first one), or the refs expired on
            # the server: fetch fresh ones first
            self.trying, self.polls = False, 0
            self.show_looking()
            self.poll()                          # sources_loaded calls try_next again
            return
        if not self.attempted and not self.queue and self.torrent_turn("before"):
            return                               # a saved or starred torrent goes before the hosters
        order, muted = self.source.server_group_order(), self.source.server_group_muted()
        candidate = failover.pick_next(self.sources, self.attempted, order[0] if order else None,
                                       self.target.get("resume_hoster"), self.queue, muted)
        if self.device_jobs is not None and not self.queue and self.device_jobs.holds(
                self.sources, candidate, order[0] if order else None, self.target.get("resume_hoster")):
            self.show_looking()                  # a device group ranked first is still searching (device_changed)
            return
        if self.resume_waits(candidate):
            self.show_looking()                  # "Resuming on your last server X" (device_changed / resume_timer)
            return
        if candidate is None:
            if failover.still_coming(self.sources) and self.polls < MAX_POLLS:
                self.show_looking()              # the next poll calls try_next again
                # an answer that came during a torrent try scheduled no poll (sources_loaded returns early)
                self.poll_timer.start(POLL_MS, True)
            elif not self.torrent_turn("after"):  # every hoster failed: a torrent, once (website rule)
                self.show_none()
            return
        self.attempt(candidate)

    def resume_waits(self, candidate) -> bool:
        """Box 2026-11-24: the saved server is a device row (krmzi's cdnplus.sbs) that is not in the list yet while
        this title's device jobs run: wait for them (devicejobs.RESUME_WAIT_S at most) instead of starting the
        first server row. Only before the first try, never after a hand pick, never for a saved torrent."""
        wanted = str(self.target.get("resume_hoster") or "").strip()
        if self.resume_wait_off or self.attempted or self.queue or self.device_jobs is None or not wanted \
                or torrents.parse_key(wanted):
            self.end_resume_wait()
            return False
        now = time.monotonic()
        if self.resume_wait_since is None:
            self.resume_wait_since = now
        waited = now - self.resume_wait_since
        if devicejobs.resume_hold(candidate, wanted, self.device_jobs.busy(), waited):
            if self.resume_timer is not None and waited == 0:
                log("resume: waiting up to %d s for the box's hosts (saved server %s)"
                    % (devicejobs.RESUME_WAIT_S, wanted))
                self.resume_timer.start(int(devicejobs.RESUME_WAIT_S * 1000), True)
            return True
        if waited >= devicejobs.RESUME_WAIT_S:
            log("resume: %s not found in %d s: the usual order" % (wanted, devicejobs.RESUME_WAIT_S))
        self.end_resume_wait()
        return False

    def end_resume_wait(self):
        """The resume wait is over for this play (the saved server came, the jobs ended, the cap, a pick)."""
        if not self.resume_wait_off:
            self.resume_wait_off = True
            if self.resume_timer is not None:
                self.resume_timer.stop()

    def attempt(self, candidate, phase="trying"):
        if self.gone() or self.trying or self.started or self.switching:
            return                               # one automatic/manual server start at a time
        if candidate.get("torrent"):
            self.attempt_torrent(candidate)
            return
        first = not self.attempted
        self.attempted.add(candidate.get("cid"))
        self.trying, self.started, self.none_shown, self.proxied = True, False, False, False
        self.current, self.result, self.gen = candidate, None, self.gen + 1
        self.hls_prepared = False
        self.deadline, self.last_pos = time.monotonic() + RESOLVE_S, None   # the server may take a while to resolve
        gen = self.gen
        total = max(failover.total(self.sources, self.source.server_group_muted()), len(self.attempted))
        wanted = failover.hoster_key(self.target.get("resume_hoster"))
        resume = first and bool(wanted) and failover.hoster_key(candidate.get("hoster")) == wanted
        self.try_since = time.monotonic()
        self.show_search(phase, candidate, len(self.attempted), total, resume, self.last_failed)
        log("trying %s (%s)" % (candidate.get("hoster"), candidate.get("cid")))
        if devicejobs.is_device(candidate):      # 085: resolved by the box's worker, never by /api/resolve_one
            self.device_resolve(candidate, gen)
            return
        async_call.run(self.client.resolve, candidate.get("ref"),
                       on_done=lambda result: self.resolved(result, gen), on_error=lambda e: self.resolve_failed(e, gen))

    # --- a torrent on the box's engine (FIX6) ------------------------------------------------------
    def attempt_torrent(self, candidate):
        self.engine.buffer_minutes = bufferrules.clean(self.source.prefs.buffer_minutes())
        self.attempted.add(candidate.get("cid"))
        self.trying, self.started, self.none_shown, self.proxied = True, False, False, True
        self.current, self.result, self.gen = candidate, None, self.gen + 1
        self.deadline, self.last_pos = time.monotonic() + 24 * 3600, None   # each engine step has its own limit
        self.torrent_progress, self.try_since = {"step": "engine"}, time.monotonic()
        resume = len(self.attempted) == 1 and torrents.key(candidate["torrent"]) == \
            str(self.target.get("resume_hoster") or "").strip().lower()
        self.show_search("torrent", candidate, resume=resume)
        log("trying torrent %s" % candidate.get("cid"))
        gen, directory = self.gen, torrengine.installed_dir()
        if directory:
            self.start_engine(gen, directory)
            return
        rows = [(i18n.t("torrent.install_yes", self.lang), "yes"), (i18n.t("torrent.install_no", self.lang), "no")]
        from .choice import Choice
        self.session.openWithCallback(lambda picked: self.install_answer(picked, gen), Choice, self.lang,
                                      i18n.t("torrent.install_ask", self.lang), rows,
                                      i18n.t("torrent.install_body", self.lang))

    def install_answer(self, picked, gen):
        if self.gone() or gen != self.gen:
            return
        if not picked or picked[1] != "yes":
            self.failed(gen, "torrent install declined")
            return
        asset, directory = torrentrules.asset_for(torrengine.machine()), torrengine.install_dir()
        if asset is None or directory is None:
            self.torrent_message("torrent.unsupported" if asset is None else "torrent.no_space")
            self.failed(gen, "torrent engine unsupported" if asset is None else "no space for torrent engine")
            return
        self.torrent_progress = {"step": "install", "percent": 0}
        progress = self.torrent_progress
        async_call.run(torrengine.download, directory, asset, progress, None, lambda: self.gone(),
                       on_done=lambda path: self.start_engine(gen, directory),
                       on_error=lambda error: self.install_failed(error, gen))

    def install_failed(self, error, gen):
        log("engine download failed: %r" % (error,))
        if not self.gone():
            self.torrent_message("torrent.install_failed")
        self.failed(gen, "engine download: %s" % getattr(error, "code", repr(error)))

    def torrent_message(self, key):
        from Screens.MessageBox import MessageBox
        self.session.open(MessageBox, i18n.t(key, self.lang), MessageBox.TYPE_ERROR, timeout=8)

    def start_engine(self, gen, directory):
        if self.gone() or gen != self.gen:
            return
        self.torrent_progress = {"step": "engine"}

        def echoed(alive):
            if self.gone() or gen != self.gen:
                return
            # /echo can belong to an orphan from the Enigma2 process that just died.  Reuse an
            # engine only when this process owns its container; start() reaps our recorded orphan.
            if alive and torrengine.ENGINE.running():
                self.engine_ready(gen)
            elif not torrengine.ENGINE.start(directory):
                log("engine did not start")
                self.failed(gen, "torrent engine did not start")
            else:
                async_call.run(self.engine.wait_ready, on_done=lambda ok: self.engine_ready(gen) if ok
                               else self.failed(gen, "torrent engine echo deadline"),
                               on_error=lambda error: self.failed(gen, "torrent engine readiness error: %r" % error))
        async_call.run(self.engine.echo, on_done=echoed, on_error=lambda error: echoed(False))

    def engine_ready(self, gen):
        if self.gone() or gen != self.gen:
            return
        self.torrent_progress = {"step": "peers"}
        row, progress = self.current["torrent"], self.torrent_progress
        self.torrent_playing = row["hash"]
        async_call.run(self.engine.open_file, row, progress, lambda: self.gone() or gen != self.gen,
                       on_done=lambda found: self.play_torrent(found[0], gen, found[1]),
                       on_error=lambda error: self.torrent_failed(error, gen))

    def torrent_failed(self, error, gen):
        log("torrent failed: %s" % getattr(error, "code", repr(error)))
        self.failed(gen, "torrent %s" % getattr(error, "code", repr(error)))

    def play_torrent(self, url, gen, entry=None):
        if self.gone() or gen != self.gen:
            return
        self.torrent_progress["step"] = "buffering"
        self.result = {"torrent": True}           # a stream of ours runs: its end event counts
        self.torrent_file_bytes = (entry or {}).get("length") or 0
        self.ensure_quality_button()
        self.deadline, self.last_pos = time.monotonic() + TORRENT_PICTURE_S, None
        log("torrent stream %s" % url.split("?")[0])
        self.launch(url, None, None, gen)

    def drop_torrent(self):
        """Let the engine forget the torrent we leave (best effort, off the GUI thread) - never one that the
        download manager is fetching (FIX9: one engine, one torrent)."""
        info_hash, self.torrent_playing = self.torrent_playing, None
        if info_hash and not downloadmanager.manager().busy_hash(info_hash):
            async_call.run(self.engine.drop, info_hash, on_done=lambda _: None, on_error=lambda e: None)

    def resolve_failed(self, error, gen):
        if self.gone():
            return
        log("resolve failed for %s: %r" % ((self.current or {}).get("hoster"), error))
        self.failed(gen, "resolve error: %r" % (error,))

    def resolved(self, result, gen):
        if self.gone() or gen != self.gen:
            return
        self.result = result
        # FIX33: the resolve as it came (its proxy/referer rules belong to its own qualities) and the quality
        # that plays (the Quality panel checks it)
        self.resolved_result = result
        self.quality_url = (result or {}).get("stream_url") if isinstance(result, dict) else None
        self.ensure_quality_button()
        self.start_play()

    def video_height(self):
        ext = self.ext if self.ext_on else None
        return playback.effective_height(ext.width(), ext.height()) if ext is not None else None

    def ensure_quality_button(self, height=None):
        """The Quality button exists when there is something to show: several qualities, or one line (the playing
        quality, from the picture height or the server's own text)."""
        if "quality" in self.nav.names:
            return
        rows = (self.result or {}).get("qualities") or []
        choices, _ = playback.quality_panel(rows, height, self.current)
        if choices:
            self.nav.names = playernav.with_button(self.nav.names, "quality", skins.PLAYER_BUTTONS, self.rtl)
            # Discovery can happen while the search card is up.  show_bar owns the widgets; exposing them
            # here leaves an orphaned Quality pill at the bottom after the rest of the bar was hidden.
            self["btn_quality"].hide(); self["btnicon_quality"].hide()
            if self.started and self.bar_on:
                self.show_bar()

    # --- play with buffering (FIX12) ---------------------------------------------------------------
    def start_play(self):
        """Settings → Buffer on, a download disk, and a row the download engine can fetch: a temporary buffer
        download plays from the disk (E2iPlayer's "play with buffering"). Anything else: today's stream."""
        # exteplayer3 failed on the live brqz master because its rewritten media children are root-relative.
        # Resolve one media playlist first; if that GET cannot prepare it, the master itself is tried.
        if bufferrules.container(self.result) == "hls" and not self.hls_prepared:
            self.hls_prepared = True
            if self.result.get("device") is True:
                self.device_hls()                # 085 T106: the worker, with the bundle's headers
                return
            async_call.run(self.client.hls_media, self.result, on_done=self.hls_media_ready,
                           on_error=lambda error: self.hls_media_ready(None))
            return
        minutes = self.source.prefs.buffer_minutes()
        directory, free = None, None
        if bufferrules.clean(minutes) != bufferrules.OFF:
            try:
                directory = downloadmanager.manager().directory
                free = shutil.disk_usage(directory.rstrip("/").rsplit("/", 1)[0] or "/").free if directory else None
            except Exception:  # noqa: BLE001 - the disk went away
                directory = None
        mode, reason = bufferrules.plan(minutes, directory, self.result, self.start_at, free,
                                        torrent=bool((self.current or {}).get("torrent")), local=self.local)
        if mode != "buffer":
            log("streaming (%s)" % reason)
            self.play(proxy=False)
            return
        self.start_buffer(minutes, directory)

    def hls_media_ready(self, answer):
        """answer: client.hls_media's {"url", "variants"} (a bare URL string from older callers/tests)."""
        if self.gone():
            return
        url, ladder = (answer.get("url"), answer.get("variants") or []) if isinstance(answer, dict) else (answer, [])
        wish = getattr(self.source.prefs, "hls_height", None)
        start = playback.start_quality(ladder, wish() if callable(wish) else None)
        if start is not None:
            url = start["stream_url"]                # the highest, or the quality the viewer last picked
            log("HLS start quality %sp" % start["height"])
        if url:
            self.result = dict(self.result, stream_url=url, needs_proxy=False, referer="")
            log("HLS media variant prepared for exteplayer3")
        else:
            log("HLS media variant unavailable; exteplayer3 tries the master")
        self.take_hls_ladder(ladder, url)
        self.start_play()

    def take_hls_ladder(self, ladder, playing):
        """FIX33 (owner 2026-11-20: an Arabic-subs server with 4 qualities showed only the playing one): the
        master's qualities become the Quality panel's rows, unless the resolve brought its own list."""
        own = [q for q in (self.result or {}).get("qualities") or [] if isinstance(q, dict) and q.get("stream_url")]
        rows = [q for q in ladder or [] if isinstance(q, dict) and q.get("stream_url")]
        if len(rows) < 2 or len(own) >= 2:
            return
        self.result = dict(self.result, qualities=rows)
        if playing:
            self.quality_url = playing
        log("HLS master: %d qualities (%s)" % (len(rows), ", ".join("%sp" % q.get("height") for q in rows)))
        self.ensure_quality_button()

    def start_buffer(self, minutes, directory):
        self.stop_buffer()
        self.buffer_dir = directory
        log("buffering %s from %d s" % (bufferrules.container(self.result), int(self.start_at)))
        wish = getattr(self.source.prefs, "hls_height", None)
        self.buffer_job = bufferjob.start(self.result, self.start_at, directory, minutes, base=api.BASE,
                                          hls_height=wish() if callable(wish) else None)
        self.buffer_phase, self.buffer_since, self.buffer_played = "fill", time.monotonic(), False
        self.buffer_offset, self.buffer_offset_known = 0.0, False
        self.buffer_goal = bufferrules.goal_s(minutes)
        self.buffer_progress = {"percent": 0}
        self.buffer_still, self.buffer_relaunches = None, []
        self.proxied = False
        self.trying, self.started = True, False
        self.deadline, self.last_pos = time.monotonic() + 24 * 3600, None   # the fill has its own rules
        self.show_search("buffering", self.current)
        self.tick_timer.start(1000, False)

    def stop_buffer(self):
        """Close / server switch / next episode / a fallback: the buffer download stops and its file goes."""
        job, self.buffer_job = self.buffer_job, None
        self.buffer_phase, self.buffer_played = None, False
        self.buffer_offset, self.buffer_offset_known = 0.0, False
        if job is not None:
            bufferjob.stop(job)

    def buffer_tick(self, at):
        """Every second while a buffer runs. True = this tick is done (the normal checks wait)."""
        job = self.buffer_job
        snap = job.snapshot()
        now = time.monotonic()
        if self.buffer_phase != "play":
            self.buffer_still = None                     # the watchdog starts afresh after a fill / stall
        if self.buffer_phase == "fill":
            ahead = bufferrules.ahead(snap["end_s"], self.start_at)
            if snap["state"] in ("failed", "stopped"):
                # 0.8.1: what is on the disk still plays (then must_pause -> the stream at that second)
                if snap["state"] == "failed" and snap["path"] and bufferrules.playable_after_failure(ahead):
                    log("buffer failed (%s): playing the %d s on the disk first" % (snap["error"], int(ahead)))
                    self.play_buffer(snap)
                    return True
                log("buffer failed (%s): streaming" % snap["error"])
                self.buffer_fallback(self.start_at)
                return True
            self.buffer_progress["percent"] = bufferrules.percent(ahead, self.buffer_goal, snap["done"])
            # the goal: the chosen minutes, or 1 min after the file ran out (buffer_refill)
            if snap["path"] and bufferrules.ready(ahead, self.buffer_goal // 60, snap["done"], now - self.buffer_since):
                self.play_buffer(snap)
            elif bufferrules.give_up_fill(ahead, self.buffer_goal // 60, snap["done"], now - self.buffer_since,
                                          has_data=bool(snap["path"])):
                log("buffer too slow (%d s ahead after %d s): streaming" % (int(ahead), int(now - self.buffer_since)))
                self.buffer_fallback(self.start_at)
            return True
        if not self.started or at is None:
            return False
        self.last_at = at
        job.play_at = at
        if snap["fmt"] == "mkv" and not job.duration_hint:
            length = self.position()[1]
            if length:
                job.duration_hint = length               # MKV: its header's length gives the bitrate
        if self.buffer_phase == "play" and not self.paused and \
                bufferrules.must_pause(at, snap["end_s"], snap["done"]):
            if snap["state"] in ("failed", "stopped"):
                log("buffer ran out (%s): streaming from here" % snap["error"])
                self.buffer_fallback(at)
                return True
            self.buffer_phase = "stall"
            self.set_pause(True)
            self.buffer_goal = bufferrules.RESUME_AHEAD_S
            self.buffer_progress["percent"] = bufferrules.percent(
                bufferrules.ahead(snap["end_s"], at), self.buffer_goal, False)
            self.show_search("buffering", self.current)
            return True
        if self.buffer_phase == "stall":
            if snap["state"] in ("failed", "stopped") and not snap["done"]:
                self.buffer_fallback(at)
                return True
            self.buffer_progress["percent"] = bufferrules.percent(
                bufferrules.ahead(snap["end_s"], at), self.buffer_goal, snap["done"])
            if bufferrules.may_resume(at, snap["end_s"], snap["done"]):
                self.buffer_phase = "play"
                self.set_pause(False)
                self.hide_card()
            else:
                self.draw_card()
            self.stall_pos = None                            # waiting for the download is not a frozen server
            return True
        return self.buffer_watch(snap, now)

    def buffer_watch(self, snap, now):
        """Box 11-24: a -o 1 play that sat at the end of a completed file - black, J frozen, for minutes. A play
        whose J has not moved for bufferrules.STUCK_S (not paused, no seek or launch in flight) ends the episode
        near a complete job's end, else plays the file again at that second (twice a minute at most)."""
        ext = self.ext if self.ext_on else None
        raw = ext.raw_position() if ext is not None else None
        watched = not self.paused and not getattr(self, "seek_pending", False) and \
            getattr(self, "resume_seek", None) is None and not (ext is not None and ext.seeking())
        at = raw + self.buffer_offset if raw is not None else None
        self.buffer_still = bufferrules.stuck_clock(getattr(self, "buffer_still", None), at, now, watched)
        relaunches = getattr(self, "buffer_relaunches", None)
        if relaunches is None:
            relaunches = self.buffer_relaunches = []
        action = bufferrules.stuck_action(self.buffer_still, now, snap["duration"] or self.buffer_job.duration,
                                          snap["done"], relaunches)
        if action == "watch":
            return False
        stuck_at, self.buffer_still = self.buffer_still[0], None
        if action == "end":
            log("buffer play stuck at %d s, the end of the file: end of the episode" % int(stuck_at))
            self.on_eof()
            return True
        if action == "hold":
            log("buffer play stuck at %d s: %d relaunches this minute, waiting" % (int(stuck_at), len(relaunches)))
            return False
        relaunches.append(now)
        self.buffer_relaunch(stuck_at, "buffer play stuck at %d s" % int(stuck_at))
        return True

    def buffer_relaunch(self, target, reason):
        """The buffer file again, started at `target` (film seconds) by the start-position path every relaunch
        uses (start_at -> picture_arrived's seek). -o 1 only while the job still runs (launch_ext)."""
        snap = self.buffer_job.snapshot()
        log("%s: relaunch at %d s%s" % (reason, int(target), " (-o 1)" if bufferrules.progressive(
            snap["path"], snap["done"]) else " without -o 1"))
        self.stop_service()
        self.start_at = max(0.0, float(target))
        self.started, self.trying, self.paused, self.pause_at = False, True, False, None
        self.resume_seek, self.seek_wait, self.stall_pos = None, None, None
        self.play_buffer(snap)

    def play_buffer(self, snap):
        self.buffer_phase = "play"
        log("playing the buffer file (%s)" % snap["fmt"])
        self.deadline, self.last_pos = time.monotonic() + NO_PICTURE_S, None
        self.try_since = time.monotonic()                # the audio-only check counts from the file's start
        self.launch(snap["path"])                        # a file on disk: no network buffer to set

    def set_pause(self, paused):
        if self.ext_on and self.ext is not None:
            self.ext.set_pause(paused)

    def buffer_fallback(self, at):
        """The buffer cannot carry on (it failed, or a seek it cannot follow): today's stream of the same
        server at `at` — never a blocked play."""
        self.stop_buffer()
        self.stop_service()
        self.start_at = at or 0
        self.started, self.trying, self.paused = False, True, False
        self.try_since = time.monotonic()
        total = max(failover.total(self.sources, self.source.server_group_muted()), len(self.attempted))
        self.show_search("trying", self.current, len(self.attempted), total)
        # the stopped file's own end event arrives a moment later: let it drain first (as failed() does)
        self.switch_timer.stop()
        self.next_step, self.switching = lambda: self.play(proxy=False), True
        self.switch_timer.start(600, True)

    def buffer_restart(self, target):
        """A seek outside the downloaded range of a format that can start mid-film: a new buffer there."""
        minutes, directory = self.source.prefs.buffer_minutes(), self.buffer_dir
        self.stop_service()
        self.start_at = target
        self.started, self.paused = False, False
        self.try_since = time.monotonic()
        self.start_buffer(minutes, directory)

    def play(self, proxy):
        url, headers = playback.stream_target(self.result, api.BASE, proxy)
        self.proxied = (proxy or bool(self.result.get("needs_proxy"))) and self.result.get("device") is not True
        self.deadline, self.last_pos = time.monotonic() + NO_PICTURE_S, None
        self.launch(url, headers)

    def failed(self, gen=None, reason="unspecified failure"):
        if self.gone() or self.switching or (gen is not None and gen != self.gen):
            return
        hoster = (self.current or {}).get("hoster") or "unknown server"
        log("failure on %s: %s" % (hoster, reason))
        if self.current is not None:
            self.failures.append(dict(self.current, failure=reason))
        self.stop_service()
        self.drop_torrent()
        self.resume_seek = None
        self.gen += 1                            # a late resolve answer for this try is dropped
        if self.buffer_job is not None:
            done = self.buffer_job.snapshot()["done"]
            if bufferrules.on_failure(self.buffer_played, done) == "stream":
                log("the buffer file did not play (%s); streaming the same server" % reason)
                self.stop_buffer()
                self.trying, self.started = True, False
                self.next_step = lambda: self.play(proxy=False)
                self.switching = True
                self.switch_timer.start(600, True)
                return
            self.stop_buffer()
        if self.local:
            self.local_did_not_play(reason)
            return
        if not self.source.server_autoplay():
            self.last_failed = self.current
            self.trying, self.started, self.result, self.none_shown = False, False, None, True
            self.show_search("pick", failed=self.last_failed)
            self.servers()
            return
        if playback.device_retry_worth(self.current, self.result, getattr(self, "device_reresolved", None)):
            # 085 T107: a device row never goes through the server - a fresh resolve on the box, one more play
            self.next_step = self.reresolve_device
        elif self.result is not None and not self.proxied and playback.proxy_retry_worth(self.result):
            # one retry through the server, like the website (e.g. a CDN with a broken TLS chain)
            self.next_step = self.retry_through_proxy
        else:
            if devicejobs.is_device(self.current) and self.device_jobs is not None:
                self.device_jobs.drop(self.current.get("cid"))    # failed after its re-resolve: off this list
            log(playback.did_not_play_message(hoster, reason))
            self.last_failed = self.current          # the card says so on the next try
            self.trying, self.result = False, None
            self.next_step = self.try_next
        # the stopped stream's own end event arrives a moment later: let it drain before the next try
        self.switching = True
        self.switch_timer.start(600, True)

    def local_did_not_play(self, reason):
        """FIX33: the disk copy did not play. Once more from the disk (a transient decoder/start race is likely:
        the file is right there); then the online servers only when they can be reached - with no network a
        card says the download could not be played (Back), never "looking for servers"."""
        self.trying, self.started = True, False
        if self.local_tries < LOCAL_RETRIES:
            self.local_tries += 1
            log("the downloaded copy did not play (%s); playing it again" % reason)
            self.show_search("local_retry")
            self.next_step = lambda: self.play_local(retry=True)
        else:
            log("the downloaded copy did not play again (%s); checking the network" % reason)
            self.show_search("local_check")
            self.next_step = self.local_check_network
        # the stopped run's own exit arrives a moment later: let it drain first
        self.switching = True
        self.switch_timer.start(600, True)

    def local_check_network(self):
        """Can the online servers be reached? The sources answer itself is the test (and is used at once)."""
        t, gen = self.target, self.gen
        if t.get("site_id") or t.get("id") is None:
            self.local_online()
            return
        async_call.run(self.client.sources, t["kind"], int(t["id"]), t.get("season"), t.get("episode"),
                       on_done=lambda data: self.local_network_answer(gen, None),
                       on_error=lambda error: self.local_network_answer(gen, error))

    def local_network_answer(self, gen, error):
        if self.gone() or gen != self.gen or not self.local:
            return
        if error is not None and source_offline(error):
            log("no network (%s): the download could not be played; no online servers" %
                getattr(error, "code", repr(error)))
            self.net_down = True
            self.local_failed, self.trying, self.started, self.none_shown = True, False, False, True
            self.card_nav = None
            self.show_search("local_failed")
            return
        self.local_online()

    def local_online(self):
        """Today's fallback: the online servers at the same second."""
        log("the downloaded copy did not play; online servers next")
        self.local, self.trying, self.result, self.current = False, False, None, None
        self.local_failed, self.none_shown = False, False
        self.go_online()

    def switched(self):
        if self.gone():
            return
        self.switching = False
        step, self.next_step = self.next_step, None
        if step:
            step()

    def retry_through_proxy(self):
        self.play(proxy=True)

    def show_none(self):
        last = self.failures[-1].get("section") if self.failures else None
        analytics.play_fail(getattr(self, "an_play", None), last, "all_failed" if self.failures else "no_servers")
        self.trying, self.none_shown = False, True
        self.show_search("none")
        if not self.report_sent:
            body = noservers.payload(self.target, self.sources, self.failures, self.lang)
            self.report_sent = body is not None
            if body is not None:
                async_call.run(self.client.no_servers_report, body, on_done=self.report_done,
                               on_error=lambda error: None)

    def report_done(self, answer):
        if self.gone():
            return
        report_id = answer.get("report_id") if isinstance(answer, dict) else None
        if isinstance(report_id, int) and report_id > 0:
            self.report_id = report_id
            if self.none_shown:
                self.draw_card()

    def report_problem(self):
        """The none card's Report button flags the already-created report once; it never resends the report."""
        if not self.none_shown or not self.report_id or self.report_flagged:
            return
        self.report_flagged = True
        self.draw_card()
        async_call.run(self.client.no_servers_flag, self.report_id, on_done=lambda answer: None,
                       on_error=lambda error: None)

    # --- watching ------------------------------------------------------------------------------
    def position(self):
        if not self.ext_on or self.ext is None:
            return None, None
        at, total = self.ext.position(), self.ext.duration()   # J (run on by the clock) / PLAYBACK_LENGTH
        if self.buffer_job is not None and self.buffer_phase in ("play", "stall"):
            if at is not None:
                at += self.buffer_offset             # an HLS buffer that begins mid-film (bufferrules.position_base)
            known = self.buffer_job.duration
            # a growing file's own length is not the film's; for HLS (-o 1) PLAYBACK_LENGTH stays the length at
            # open forever (box 11-24): only the job's EXTINF sum counts, never a stale end / next episode
            total = known or (None if getattr(self.buffer_job, "fmt", None) == "hls" else total)
        return at, total

    def shown_position(self):
        """What the bar shows: the pending seek target while a key is held (FIX26 item 7), else the player's."""
        at, length = self.position()
        if getattr(self, "seek_pending", False) and self.seek_target is not None:
            at = self.seek_target
        return at, length

    def tick(self):
        if self.gone() or not self.trying and not self.started:
            return
        if self.ext_on and self.ext is not None:
            self.ext.poll()                      # l + j: the J line answers before the next tick
        if self.local and getattr(self, "settings_deadline", None) is not None:
            self.local_settings_fallback()
        at, length = self.position()
        self.torrent_status()
        if self.bar_on:
            self.draw_timeline(*self.shown_position())
        if self.buffer_job is not None and self.buffer_tick(at):
            return
        self.tick_card(at)
        if not self.started:
            if self.ext_on and self.ext is not None and self.ext.running() and self.ext.picture():
                self.picture_arrived()           # FIX31: two rising J lines (v_c alone is a dead torrent too)
            elif time.monotonic() > self.deadline:
                self.failed(reason="no picture before deadline")
            self.last_pos = at
            return
        now = time.monotonic()
        # FIX31: the stall clock reads the raw J (position() runs on between two frozen J lines and kept resetting
        # it), and a gf waiting for its answer (SEEK_REPLY_S at most) holds it
        ext = self.ext if self.ext_on else None
        stall_at = ext.raw_position() if ext is not None else at
        seeking = self.resume_seek is not None
        if seeking:
            action, self.resume_seek = playback.resume_seek_step(self.resume_seek, at, now)
            if action in ("resend", "last_resend"):
                log("resume seek retry %d/%d" % (self.resume_seek["attempts"], playback.RESUME_MAX_ATTEMPTS))
                self.seek_to(self.resume_seek["target"])
                if action == "last_resend":
                    log("resume seek retries exhausted; continuing from the current position")
                    self.resume_seek, self.seek_wait, self.start_at = None, None, 0
                    self.stall_pos, self.stall_since = stall_at, now
                    seeking = False
            elif action in ("landed", "give_up"):
                if action == "give_up":
                    log("resume seek did not land; continuing from the current position")
                self.seek_wait, self.start_at = None, 0
                self.stall_pos, self.stall_since = None, now
                seeking = False
        if self.started and not self.local:
            width = self.video_width()
            if width and "quality" not in self.nav.names:
                self.ensure_quality_button(self.video_height())
            since, seen, audio_only = playback.video_probe(
                self.video_missing_since, now, width, audio_present=at is not None)
            self.video_missing_since, self.video_seen = since, self.video_seen or seen
            if not self.video_seen and audio_only and not self.audio_only_logged:
                # FIX21 (box 2026-11-18): sVideoWidth reads 0 on this box while a picture plays (torrent H.264,
                # vidtube), so a missing width is no proof of an audio-only stream; switching on it dropped working
                # servers and started a second stream (the gstplaysink crash trigger). Log only; the viewer can
                # pick another server by hand.
                self.audio_only_logged = True
                log("no video width reported after the grace period (kept playing)")
        if seeking or (ext is not None and ext.seeking()):
            self.stall_pos = None                # a resume seek's re-buffer is not a frozen server
        elif self.paused:
            self.stall_pos = None                # a pause is not a stall
        elif stall_at is not None:
            if self.stall_pos is None or abs(stall_at - self.stall_pos) > 0.2:
                self.stall_pos, self.stall_since = stall_at, now
            elif now - self.stall_since >= STALL_S:
                self.broke(at, length, "stall at %d s" % int(at if at is not None else stall_at))
                return
            elif ext is not None:
                self.stall_note(now - self.stall_since)
        if not seeking and at is not None and now - self.last_save >= SAVE_EVERY_S:
            self.save(at, length)
        if self.target.get("has_next") and at is not None and playback.upnext_due(at, length):
            self["upnext"].setText(i18n.t("player.upnext", self.lang) % self.next_numbers())
            self["upnext"].show()

    def stall_note(self, frozen):
        """FIX31: a torrent frozen on our exteplayer3 says what the engine sees (peers, speed) until it moves on
        or STALL_S ends the try; the status comes from torrent_status(), already polled every tick."""
        progress = getattr(self, "torrent_progress", None) or {}
        if frozen < STALL_NOTE_S or not getattr(self, "torrent_playing", None) or progress.get("step") != "buffering":
            return
        self.show_toast(searchcard.torrent_status(self.lang, progress), ms=2500)

    def picture_arrived(self):
        analytics.play_ok(getattr(self, "an_play", None), (self.current or {}).get("section"))
        self.started, self.trying, self.last_save = True, False, time.monotonic()
        self.stall_pos, self.last_failed = None, None
        # The first advancing frame is the ownership boundary: no pending source/torrent callback may cover
        # or replace this server.  Do not keep the card through a resume seek; the picture is already real.
        self.poll_timer.stop()
        self.torrent_timer.stop()
        self.torrent_wait_since = None
        # FIX27: the position moves ~1 s before the first frame shows. The card keeps its last status until
        # the service reports a picture (evVideoSizeChanged / a width), or searchcard.PICTURE_WAIT_S at most.
        self.picture_since = time.monotonic()
        self.hide_card_on_picture()
        if self.bar_on:
            self.show_bar()                      # reveal a discovered Quality button only with the full bar
        if self.pick_phase:                  # a hand pick is saved soon, the way the website saves it
            self.last_save, self.pick_phase = time.monotonic() - SAVE_EVERY_S + 5, False
        log("playing on %s" % (self.current or {}).get("hoster"))
        if self.buffer_job is not None:
            self.buffer_played = True
            if not self.buffer_offset_known:
                at, _ = self.position()
                self.buffer_offset = bufferrules.position_base(at, self.buffer_job.base_s)
                self.buffer_offset_known = True
        self.seek_wait = self.start_at if self.start_at > 0 else None
        if self.start_at > 0:
            self.seek_to(self.start_at)
            self.resume_seek = playback.resume_seek_started(self.start_at, self.position()[0], time.monotonic())
        self.subtitle_panel_updated()            # exteplayer3's s_l line filled the in-video rows (ext_event)
        self.subtitle_group_check()
        self.load_subtitle_tracks()
        # FIX33 (box 19:40): settings ready before the picture (a disk play's 4 s fallback) ran the start rule
        # too early; nothing else re-ran it until the account request timed out 15 s later
        self.maybe_subtitle_default()

    def video_width(self):
        return (self.ext.width() or 0) if self.ext_on and self.ext is not None else 0

    def hide_card_on_picture(self):
        if not self.card_on or not self.started:
            return
        waited = time.monotonic() - getattr(self, "picture_since", 0.0)
        if searchcard.picture_up(self.video_width(), getattr(self, "video_size_seen", False), waited):
            seek_wait = self.seek_wait           # a resume seek in flight stays known (broke() needs it)
            self.hide_card()
            self.seek_wait = seek_wait

    def next_numbers(self):
        nxt = self.target.get("next") or (0, 0)
        return nxt[0], nxt[1]

    def on_eof(self):
        if self.gone() or self.switching or not self.trying and not self.started:
            return                               # our own stopService, nothing being tried
        if self.buffer_phase == "fill":
            return                               # the buffer fills: no stream of ours runs (a stopped one's end)
        if not self.started and self.result is None:
            return                               # still resolving: no stream of ours runs, a late end event
        if not self.started:
            self.failed(reason="end event before picture")
            return
        if self.buffer_job is not None and not self.buffer_job.snapshot()["done"]:
            self.buffer_refill()                 # the player read past the downloaded end
            return
        at, length = self.position()
        if length and at is not None and at < length - 120 and not playback.upnext_due(at, length):
            self.broke(at, length, "end event at %d s" % int(at))  # the stream died mid-film
            return
        self.save(length or at or 0, length)
        self.finish("next" if self.target.get("has_next") else None)

    def buffer_refill(self):
        """End of a file still downloading: wait for 1 min ahead, then play it again at the same second."""
        at = self.last_at if self.last_at is not None else self.start_at
        log("the buffer file ran out at %d s; waiting" % int(at or 0))
        self.stop_service()
        self.start_at = at or 0
        self.started, self.trying, self.paused = False, True, False
        self.buffer_phase, self.buffer_since = "fill", time.monotonic()
        self.buffer_goal = bufferrules.RESUME_AHEAD_S
        self.buffer_progress = {"percent": 0}
        self.deadline = time.monotonic() + 24 * 3600
        self.show_search("buffering", self.current)

    def broke(self, at, length, reason="stream stopped"):
        """The server stopped after the picture came: save the place, go on at the same second."""
        if at is not None:
            self.save(at, length)
            # Do not replace an unlanded resume with the failed stream's few seconds at the beginning.
            pending = playback.pending_resume_seek(self.seek_wait or self.start_at, at)
            self.start_at = pending if pending is not None else at
            self.seek_wait = None
        self.started, self.trying, self.stall_pos = False, True, None
        self.failed(reason=reason)

    def save(self, at, length):
        self.last_save = time.monotonic()
        if self.target.get("site_id"):          # FIX28: by handle (018 6.1), the account only
            async_call.run(self.source.save_site_progress, self.target["site_id"], self.target.get("series_site"),
                           at, length, (self.current or {}).get("hoster"), on_done=lambda _: None,
                           on_error=lambda e: None)
            return
        row = (self.current or {}).get("torrent")
        args = (self.item, self.target.get("season"), self.target.get("episode"), at, length,
                (self.current or {}).get("hoster"), torrents.source_object(row) if row else None)
        # Account saves are network I/O and visitor saves load+replace a JSON file. Both must stay off the
        # GUI thread: this runs every 30 seconds and flash/HDD latency otherwise freezes the player OSD.
        async_call.run(self.source.save_progress, *args, on_done=lambda _: None, on_error=lambda e: None)

    # --- keys ----------------------------------------------------------------------------------
    def ok(self):
        if self.none_shown and not self.source.server_autoplay():
            self.servers()                       # Auto off: OK opens the list again, nothing starts by itself
            return
        if self.none_shown:
            self.attempted, self.polls, self.queue, self.last_failed = set(), 0, None, None
            self.failures, self.report_sent, self.report_id, self.report_flagged = [], False, None, False
            self.torrent_auto_tried, self.torrent_wait_since = False, None
            if (self.torrent_state or {}).get("state") == "unavailable":
                self.load_torrents()             # the index may answer now
            self.show_looking()
            self.poll()
            return
        if self["upnext"].visible:
            self.take_next()
            return
        self.toggle_pause()

    def take_next(self):
        if not self.target.get("has_next"):
            return
        if not self.target.get("site_id"):
            self.source.clear_progress(self.item, self.target.get("season"), self.target.get("episode"))
        self.finish("next")

    def key(self, name):
        """Keys like the app's player: the timeline first, DOWN to the button row (playernav)."""
        if self.none_shown and name in ("left", "right", "ok"):
            self.card_key(name)                  # FIX26: the card's buttons own the arrows while it shows
            return
        if name in ("up", "down", "left", "right", "ok") and self.card_pick_shown(bar=True):
            # spec 081 + owner 11-21: the card's Servers button has the focus; UP brings the bar with the focus on
            # Back (to the title), DOWN on the button row; from there DOWN (Back) / UP (buttons) is the card's
            # button again. Before the picture there is nothing to seek: the centre and timeline are skipped.
            area = playernav.card_area(name, getattr(self, "bar_on", False),
                                       getattr(getattr(self, "nav", None), "area", "timeline"))
            if area == "stay":
                return
            if area == "card":
                if self.bar_on:
                    self.hide_bar()
                elif name == "ok":
                    self.servers_from_card()
                return
            if area is not None:
                self.nav.area = area
                self.show_bar()
                return
        if name == "ok" and self.nav.area == "timeline" and self["upnext"].visible:
            self.ok()
            return
        if not self.bar_on and name == "ok" and self.started:
            self.toggle_pause()                  # owner 2026-11-18: OK pauses at once (and shows the controls)
            return
        if not self.bar_on and name in ("up", "down", "left", "right", "ok"):
            self.show_bar()                      # the first press only brings the controls up
            return
        action = self.nav.key(name)
        if action == "back":
            self.stop()
            return
        if action in ("seek-10", "seek+10"):
            if not self.started:
                return
            self.seek(int(action[4:]))
            return
        if action == "toggle" and self.started:
            self.toggle_pause()                  # FIX32: it shows (pause) or hides (resume) the bar itself
            return
        if action == "next":
            self.take_next()
        elif action == "episodes":
            self.episodes()
        elif action == "subtitles":
            self.subtitles()
        elif action == "audio":
            self.audio_tracks()
        elif action == "aspect":
            self.cycle_aspect()                  # the controls stay open (show_bar below), like the TV app
        elif action == "quality":
            self.quality()
        elif action == "servers":
            self.servers()
        self.show_bar()

    def toggle_pause(self):
        if self.buffer_phase == "stall":
            return                               # the buffer resumes by itself
        if not (self.ext_on and self.started and self.ext is not None and self.ext.running()):
            return
        self.paused = not self.paused
        self.pause_at = None
        self.ext.set_pause(self.paused)          # p / c: exteplayer3 holds a pause itself
        if playernav.after_toggle(self.paused) == "show":
            self.show_bar()                      # a pause keeps the controls up, as before
        else:
            self.bar_timer.stop()                # FIX32: OK resume = a clean picture at once, no auto-hide wait
            self.hide_bar()

    def back(self):
        """FIX32: Back while the bar/buttons show hides them (a clean picture); with nothing shown it leaves the
        player as before. The panels (Servers/Subtitles/Audio/Quality/Episodes) are screens with their own Back."""
        if playernav.back_action(self.bar_on) == "hide":
            self.bar_timer.stop()
            self.hide_bar()
            return
        self.stop()

    def seek(self, seconds):
        """FIX26 item 7: a press (or key repeat) only moves the on-screen target, by a step that grows while
        the key is held; seek_settled sends ONE real seek SEEK_SETTLE_MS after the last press."""
        at, length = self.position()
        if not self.started or at is None:
            return
        now = time.monotonic()
        # quick presses add up: the player reports the old position until a seek has landed
        recent = self.seek_target is not None and (self.seek_pending or now - self.seek_at < 3)
        self.seek_hold = playback.seek_hold_since(self.seek_hold, self.seek_press, now)
        self.seek_press = now
        self.seek_target = playback.seek_target(self.seek_target if recent else at, seconds, now - self.seek_hold,
                                                length)
        self.seek_at, self.seek_pending, self.seek_gen = now, True, self.gen
        self.seek_timer.start(playback.SEEK_SETTLE_MS, True)      # restarted per press
        self.show_bar()                                            # drawn at the target at once

    def seek_values(self):
        """config.seek.selfdefined_13/46/79 read now (the viewer may change them in the box's menu)."""
        try:
            from Components.config import config
            seek = config.seek
        except Exception:  # noqa: BLE001 - no Enigma2 config (tests) or no seek section
            seek = None
        return playback.number_seek_values(seek)

    def jump(self, key):
        """Number keys, every player: a fixed jump through the same debounced seek (the target moves at once,
        one real seek after the last press) and a short toast ("+1 min", "−15 s")."""
        seconds = playback.number_seek_seconds(key, self.seek_values())
        if seconds is None or self.gone():
            return
        at, length = self.position()
        if not self.started or at is None:
            return
        now = time.monotonic()
        recent = self.seek_target is not None and (self.seek_pending or now - self.seek_at < 3)
        self.seek_hold, self.seek_press = None, None     # a number key is no held arrow: no growing step
        self.seek_target = playback.seek_jump(self.seek_target if recent else at, seconds, length)
        self.seek_at, self.seek_pending, self.seek_gen = now, True, self.gen
        self.seek_timer.start(playback.SEEK_SETTLE_MS, True)
        self.show_bar()
        self.show_toast(playernav.jump_text(seconds, self.lang))

    def aspect_mode(self):
        try:
            return aspect.clean(self.source.prefs.aspect_mode())
        except Exception:  # noqa: BLE001 - unreadable prefs: Fit
            return "fit"

    def apply_aspect(self, mode=None):
        control = self.__dict__.get("video_aspect")
        if control is not None:
            control.apply(mode or self.aspect_mode())

    def restore_aspect(self):
        control = self.__dict__.get("video_aspect")
        if control is not None:
            control.restore()

    def cycle_aspect(self):
        """OK on the Aspect chip (or the direct key): the next mode, remembered for the whole box, applied at once
        while a play runs (else on the next launch), and a 1.5 s toast "Aspect ratio: Fill"."""
        if self.gone():
            return
        mode = aspect.next_mode(self.aspect_mode())
        try:
            self.source.prefs.set_aspect_mode(mode)
        except Exception as error:  # noqa: BLE001 - a read-only /etc still changes this play
            log("aspect ratio not saved: %r" % (error,))
        if self.ext_on:
            self.apply_aspect(mode)
        self.show_toast(aspect.toast(mode, self.lang), TOAST_MS)

    def show_toast(self, text, ms=TOAST_MS):
        self["toast"].setText(text)
        self["toast"].show()
        self.toast_timer.start(ms, True)

    def seek_settled(self):
        """The key was released SEEK_SETTLE_MS ago: the one real seek to the accumulated target."""
        if self.gone() or not self.seek_pending:
            return
        self.seek_pending, self.seek_hold, self.seek_press = False, None, None
        if self.gone() or not self.started or self.seek_gen != self.gen or self.seek_target is None:
            return                               # another file/server started meanwhile: drop it
        self.seek_at = time.monotonic()
        log("seek to %d s" % int(self.seek_target))
        if self.buffer_job is not None:
            snap = self.buffer_job.snapshot()
            action = bufferrules.seek_action(self.seek_target, snap["start_s"], snap["end_s"], snap["done"],
                                             snap["can_restart"] and snap["state"] != "failed")
            if action != "inside":
                target, self.seek_target = self.seek_target, None
                log("seek outside the buffer: %s" % action)
                self.hide_bar()
                self.buffer_restart(target) if action == "restart" else self.buffer_fallback(target)
                return
            if bufferrules.inside_seek(getattr(self, "ext_progressive", False)) == "relaunch":
                target, self.seek_target = self.seek_target, None
                self.hide_bar()
                self.buffer_relaunch(target, "seek on a -o 1 play")
                return
        self.seek_to(self.seek_target)
        self.show_bar()

    def seek_to(self, seconds):
        if not self.ext_on or self.ext is None:
            return
        seconds -= self.buffer_offset if self.buffer_job is not None else 0.0
        self.ext.seek(max(0.0, seconds))         # c + gf<s>: a paused film plays on from the target
        self.paused, self.pause_at = False, None
        if self.subtitle_state.get("embedded_active") is not None:
            self.subtitle_cues = []              # exteplayer3 sends s_f too; clear defensively

    def show_bar(self):
        self.bar_on = True
        self.draw_card_focus()                   # the card's Servers button: no focus look while the bar is up
        at, length = self.shown_position()
        t = self.target
        self["top"].setText(t.get("title") or "")
        if self["top"].instance:                  # a film has no S·E badge: the title takes its room
            width = 150 if t.get("season") is not None else 270
            self["top"].instance.resize(eSize(width, 80))
            self["top"].instance.move(ePoint(1920 - 112 - width if self.rtl else 112, 56))
        self["badge"].setText("S%s · E%s" % (t.get("season"), t.get("episode"))
                              if t.get("season") is not None else "")
        self.draw_timeline(at, length)
        for name in ("dimtop", "dimbottom", "topbox", "top", "topicon", "elapsed", "total", "track", "fill", "head"):
            self[name].show()
        self["badge"].show() if t.get("season") is not None else self["badge"].hide()
        area = self.nav.area
        self["topframe"].show() if area == "back" else self["topframe"].hide()
        for name in skins.PLAYER_RING:
            self[name].show() if area == "timeline" else self[name].hide()
        # the centre play/pause circle (app PlayerCentrePlayButton): accent when focused
        paused = self["paused"]
        paused.setText(theme.GLYPHS["play"] if self.paused else PAUSE_GLYPH)
        if paused.instance:
            paused.instance.setBackgroundColor(theme.rgb(theme.ACCENT if area == "centre" else theme.SURFACE))
            paused.instance.invalidate()   # the box repaints a colour only on invalidate
        paused.show()
        for index, name in enumerate(skins.PLAYER_BUTTONS):
            button = self["btn_" + name]
            if name in self.nav.names and button.instance:
                button.instance.move(ePoint(playerlayout.button_x(name, self.rtl), skins.PLAYER_BTN_Y))
                button.show()
                icon = self["btnicon_" + name]
                focused = area == "buttons" and self.nav.names[self.nav.button] == name
                if icon.instance:
                    icon_x = playerlayout.button_x(name, self.rtl) + (playerlayout.BUTTON_WIDTHS[name] - 44
                                                                     if self.rtl else 8)
                    icon.instance.move(ePoint(icon_x, skins.PLAYER_BTN_Y + (skins.PLAYER_BTN_H - 36) // 2))
                    state = "player"                            # the focus is the ring; the pill stays dark
                    if self.icon_state.get(name) != state:     # a PNG decode per key press made keys lag
                        icon_name = "list" if name == "subtitles" else name
                        icon.instance.setPixmapFromFile(os.path.join(ICONS, "pill_%s_%s.png" % (icon_name, state)))
                        self.icon_state[name] = state
                    icon.show()
            else:
                button.hide()
                self["btnicon_" + name].hide()
        frame = self["btnframe"]
        if frame.instance and area == "buttons" and self.nav.names:
            name = self.nav.names[self.nav.button]
            frame.instance.move(ePoint(playerlayout.button_x(name, self.rtl) - 3, skins.PLAYER_BTN_Y - 3))
            frame.instance.resize(eSize(playerlayout.BUTTON_WIDTHS[name] + 6, skins.PLAYER_BTN_H + 6))
            frame.show()
        else:
            frame.hide()
        self.bar_timer.start(CONTROLS_MS, True)
        self.position_subtitles(True)

    def buffered_position(self, at, length):
        if bufferrules.clean(self.source.prefs.buffer_minutes()) == bufferrules.OFF or at is None:
            return None
        if self.buffer_job is not None:
            return bufferrules.buffered_end(at, length, end_s=self.buffer_job.snapshot()["end_s"])
        if self.torrent_playing:
            return bufferrules.buffered_end(at, length, preloaded_bytes=self.torrent_progress.get("preloaded"),
                                            file_bytes=self.torrent_file_bytes)
        loaded = self.ext.buffered() if self.ext_on and self.ext is not None else None   # J.lms: how far it loaded
        if loaded is None:
            return None
        return bufferrules.buffered_end(at, length, end_s=loaded)

    def draw_timeline(self, at, length):
        if getattr(self, "seek_pending", False) and self.seek_target is not None:
            at = self.seek_target                # FIX26: time label + fill + head at the target at once
        self["elapsed"].setText(playback.clock(at or 0))
        self["total"].setText(playback.clock(length) if length else "")
        end = self.buffered_position(at, length)
        if end is not None and end > (at or 0) and self["buffered"].instance:
            left, width, _ = playerlayout.progress(end, length, self.rtl)
            self["buffered"].instance.resize(eSize(max(1, width), 4))
            self["buffered"].instance.move(ePoint(left, skins.TRACK_Y))
            self["buffered"].show()
        else:
            self["buffered"].hide()
        fill_x, width, head_x = playerlayout.progress(at, length, self.rtl)
        if self["fill"].instance and self["head"].instance:
            self["fill"].instance.resize(eSize(max(1, width), 4))
            self["fill"].instance.move(ePoint(fill_x, skins.TRACK_Y))
            self["head"].instance.move(ePoint(head_x, skins.TRACK_Y - 41))

    def hide_bar(self):
        if self.gone():
            return
        self.bar_on = False
        self.draw_card_focus()
        self.nav.area = "timeline"
        for name in ("dimtop", "dimbottom", "topbox", "topframe", "top", "topicon", "badge", "elapsed", "total",
                     "track", "buffered", "fill", "head", "btnframe", "paused") + skins.PLAYER_RING + \
                tuple("btn_" + n for n in skins.PLAYER_BUTTONS) + tuple("btnicon_" + n for n in skins.PLAYER_BUTTONS):
            self[name].hide()
        if self.paused:
            self["paused"].setText(theme.GLYPHS["play"])
            if self["paused"].instance:
                self["paused"].instance.setBackgroundColor(theme.rgb(theme.SURFACE))
                self["paused"].instance.invalidate()   # the box repaints a colour only on invalidate
            self["paused"].show()
        self.position_subtitles(False)

    # --- the search card -------------------------------------------------------------------------
    def show_looking(self):
        """FIX19: with a saved server and nothing tried yet, the card says "Resuming on your last server" (and
        names it) from the first frame; "Looking for a working server…" only when there is none to resume."""
        saved = str(self.target.get("resume_hoster") or "").strip()
        if saved and not self.attempted:
            row = torrents.parse_key(saved)
            label = i18n.t("torrents.group", self.lang) if row else saved
            self.show_search("resuming", {"hoster": saved, "label": label})
            return
        self.show_search("looking")

    def show_search(self, phase, candidate=None, position=0, total=0, resume=False, failed=None):
        if self.started:
            return                               # only broke()/picked() may clear started and begin a new card
        self.card_args = (phase, candidate, position, total, resume, failed)
        if not self.card_on:
            self.card_on, self.card_since = True, time.monotonic()
        self["toast"].hide(); self.toast_timer.stop()
        self.draw_card()

    def draw_card(self):
        phase, candidate, position, total, resume, failed = self.card_args
        seconds = time.monotonic() - self.try_since if phase in ("trying", "switching") else None
        if phase == "torrent":
            seconds = time.monotonic() - self.try_since
        progress = self.buffer_progress if phase == "buffering" else self.torrent_progress
        view = searchcard.view(self.lang, self.target.get("title") or "", phase, candidate, position, total,
                               resume, failed, seconds, progress, auto=self.auto_walk())
        if phase == "none" and getattr(self, "report_flagged", False):
            view["hint"] = i18n.t("player.report_known", self.lang)     # FIX26: after the Report button
        self["cardtitle"].setText(view["title"])
        self["cardstatus"].setText(view["status"])
        self["cardquality"].setText(view["quality"])
        self["cardserver"].setText(view["server"])
        self["cardsecs"].setText(view["seconds"])
        self["cardhint"].setText(view["hint"])
        for name in ("card", "cardtitle", "cardstatus"):
            self[name].show()
        for name in ("cardrow", "cardquality", "cardserver", "cardsecs"):
            self[name].show() if view["row"] else self[name].hide()
        self["cardhint"].show() if view["hint"] else self["cardhint"].hide()
        self.draw_card_buttons(phase in ("none", "missing", "player_installing", "local_failed"))
        self.draw_card_pick(view)

    def auto_walk(self):
        """Settings → Servers → Auto: the player walks the servers by itself (the card offers the viewer's pick)."""
        source = getattr(self, "source", None)
        return True if source is None else source.server_autoplay()

    def draw_card_pick(self, view):
        """Spec 081 §1: while searching, the info line and a focused Servers button (the player's icon + label)
        in the middle of the card; OK opens the same Servers panel as the bar."""
        self.card_pick = bool(view.get("pick"))
        names = ("cardinfo", "cardpickframe", "cardpick", "cardpickicon")
        geometry = playerlayout.card_pick_layout(view.get("row"), self.card_pick)
        card = self["card"].instance
        if card:
            card.resize(eSize(playerlayout.CARD_W, geometry["card_h"]))
        if not self.card_pick:
            for name in names:
                self[name].hide()
            return
        self["cardinfo"].setText(view.get("info") or "")
        label, rtl = i18n.t("player.btn.servers", self.lang), getattr(self, "rtl", False)
        self["cardpick"].setText((label + "   ") if rtl else ("   " + label))
        x = playerlayout.card_button_x(0, 1)
        moves = (("cardinfo", playerlayout.CARD_INFO_X, geometry["info_y"]),
                 ("cardpickframe", x - 3, geometry["button_y"] - 3),
                 ("cardpick", x, geometry["button_y"]),
                 ("cardpickicon", playerlayout.card_pick_icon_x(rtl),
                  geometry["button_y"] + (playerlayout.CARD_BTN_H - 36) // 2))
        for name, left, top in moves:
            if self[name].instance:
                self[name].instance.move(ePoint(left, top))
            self[name].show()
        self.draw_card_focus()

    def draw_card_focus(self):
        """The card's Servers button looks focused (accent + white ring) only while the bar is not up."""
        if not getattr(self, "card_pick", False):
            return
        focused = not getattr(self, "bar_on", False)
        self["cardpickframe"].show() if focused else self["cardpickframe"].hide()
        if getattr(self, "card_pick_look", None) == focused:
            return
        self.card_pick_look = focused
        button, icon = self["cardpick"], self["cardpickicon"]
        if button.instance:
            button.instance.setBackgroundColor(theme.rgb(theme.ACCENT if focused else "#1c1f2b"))
            button.instance.invalidate()          # the box repaints a colour only on invalidate
        if icon.instance:
            icon.instance.setPixmapFromFile(os.path.join(ICONS, "pill_servers_%s.png" %
                                                         ("accent" if focused else "player")))

    def card_pick_shown(self, bar=False):
        """The searching card's Servers button is on screen (and has the focus). bar: also while the bar is up."""
        return bool(self.card_on and getattr(self, "card_pick", False) and not self.started and not self.none_shown
                    and (bar or not self.bar_on))

    def servers_from_card(self):
        """OK on the card's Servers button: the bar's Servers panel. The automatic walk goes on meanwhile; a pick
        stops it (picked), closing without one changes nothing."""
        if not self.servers():
            self.show_toast(i18n.t("player.servers_loading", self.lang), 2500)   # no row has come yet

    def card_buttons(self):
        """FIX26: the no-servers card's buttons in physical order (Report only while it can be pressed)."""
        names = ["back"] if getattr(self, "ext_missing", False) or getattr(self, "local_failed", False) else \
            playerlayout.visual_buttons(playernav.none_buttons(getattr(self, "report_id", None),
                                                               getattr(self, "report_flagged", False)),
                                        getattr(self, "rtl", False))
        nav = getattr(self, "card_nav", None)
        if nav is None:
            self.card_nav = nav = playernav.CardNav(names)
        elif nav.names != names:
            nav.set_names(names)
        return nav

    def draw_card_buttons(self, shown):
        slots = ["cardbtn%d" % i for i in range(playerlayout.CARD_BTN_SLOTS)]
        if not shown:
            for name in slots + ["cardbtnframe"]:
                self[name].hide()
            return
        nav = self.card_buttons()
        labels = {"retry": i18n.t("player.card.retry" if self.source.server_autoplay() else "player.btn.servers",
                                  self.lang),
                  "report": i18n.t("player.report", self.lang), "back": i18n.t("player.card.back", self.lang)}
        for slot, widget_name in enumerate(slots):
            widget = self[widget_name]
            if slot >= len(nav.names):
                widget.hide()
                continue
            widget.setText(labels[nav.names[slot]])
            if widget.instance:
                widget.instance.move(ePoint(playerlayout.card_button_x(slot, len(nav.names)),
                                            playerlayout.CARD_BTN_Y))
            widget.show()
        frame = self["cardbtnframe"]
        if frame.instance and nav.names:
            frame.instance.move(ePoint(playerlayout.card_button_x(nav.index, len(nav.names)) - 3,
                                       playerlayout.CARD_BTN_Y - 3))
        frame.show() if nav.names else frame.hide()

    def card_key(self, name):
        """FIX26: LEFT/RIGHT/OK on the no-servers card's buttons."""
        action = self.card_buttons().key(name)
        if action == "retry":
            self.card_nav = None                 # the next none card starts on Try again again
            self.ok()
        elif action == "report":
            self.report_problem()
        elif action == "back":
            self.stop()
        else:
            self.draw_card()

    def tick_card(self, at):
        if not self.card_on:
            return
        if not self.started:
            if self.card_args and self.card_args[0] in ("trying", "switching", "torrent", "buffering"):
                self.draw_card()                 # the second counter (and the torrent's peers)
            self.torrent_status()
            return
        self.hide_card_on_picture()              # FIX27: the picture is on screen (or the deadline passed)

    def torrent_status(self):
        """While a torrent buffers: its peers and speed for the card, every TORRENT_STATUS_S."""
        info_hash, now = self.torrent_playing, time.monotonic()
        if not info_hash or self.torrent_progress.get("step") != "buffering" \
                or now - self.torrent_status_at < TORRENT_STATUS_S:
            return
        self.torrent_status_at, progress = now, self.torrent_progress

        def got(state):
            progress.update(peers=state["peers"], speed=state["speed"], preloaded=state.get("preloaded"))
        async_call.run(self.engine.status, info_hash, on_done=got, on_error=lambda e: None)

    def hide_card(self):
        self.card_on, self.seek_wait, self.card_pick = False, None, False
        for name in skins.PLAYER_CARD:
            self[name].hide()
        self["cardpickicon"].hide()

    # --- subtitles -----------------------------------------------------------------------------
    def load_subtitle_settings(self):
        """Prefs + per-video choice; account calls stay off the Enigma2 GUI thread."""
        if not self.subtitle_key:
            self.subtitle_state["status"] = "unavailable"
            self.subtitle_prefs_ready = self.subtitle_choice_ready = True
            return
        if not self.source.signed_in():
            self.subtitle_prefs_loaded(self.source.prefs.subtitle_prefs())
            self.subtitle_choice_loaded(self.source.prefs.subtitle_choice(self.subtitle_key))
            return
        async_call.run(self.client.subtitle_prefs, on_done=self.subtitle_prefs_loaded,
                       on_error=lambda error: self.subtitle_prefs_loaded(self.source.prefs.subtitle_prefs()))
        async_call.run(self.client.subtitle_choice, self.subtitle_key, on_done=self.subtitle_choice_loaded,
                       on_error=lambda error: self.subtitle_choice_loaded(self.source.prefs.subtitle_choice(
                           self.subtitle_key)))

    def subtitle_prefs_loaded(self, value):
        if self.gone():
            return
        # the size (FIX19) and the font live on the box, never taken from the account
        prefs = subs.device_prefs(value, self.source.prefs.subtitle_prefs())
        self.subtitle_state.update(prefs=prefs, size=prefs["size"])
        self.subtitle_prefs_ready = True
        self.apply_subtitle_size()
        self.maybe_subtitle_default()
        self.subtitle_panel_updated()

    def subtitle_choice_loaded(self, value):
        if self.gone():
            return
        self.subtitle_choice_value = subs.clean_choice(value, self.subtitle_key)
        self.subtitle_choice_ready = True
        self.maybe_subtitle_default()

    def load_subtitle_tracks(self):
        """Once per video, after the playing row is known (torrent release matching needs it)."""
        if self.subtitle_lookup_started or not self.subtitle_key:
            return
        self.subtitle_lookup_started = True
        self.refresh_subtitle_tracks()

    def refresh_subtitle_tracks(self):
        """Ask again after a language edit; the currently showing hand-picked file stays unless removed."""
        languages = subs.effective_languages(self.subtitle_state.get("prefs", {}).get("languages"), self.lang)
        self.subtitle_state["status"] = "loading"
        # 086 (owner 11-24): a worker whose bundle runs sub-scene fetches it from the box, never via our server
        device = hostclient.CLIENT.subscene_ready()
        local = localhosts.header_value() if device else None
        self.subtitle_lookup_gen = getattr(self, "subtitle_lookup_gen", 0) + 1
        generation = self.subtitle_lookup_gen
        old = getattr(self, "subtitle_lookup", None)
        if old is not None:
            old.cancel()
        self.subtitle_lookup = None
        # async_call.run takes no keyword arguments for fn: device and local are positional
        async_call.run(self.client.subtitles, self.subtitle_key, languages, subs.torrent_release(self.current),
                       device, local,
                       on_done=lambda payload: self.subtitle_payload(payload, generation),
                       on_error=lambda error: self.subtitle_tracks_failed(error, generation))

    def subtitle_payload(self, payload, generation):
        """086: the server's answer; its sub-scene job (if any) runs on the worker first, then the merged list."""
        if self.gone() or generation != getattr(self, "subtitle_lookup_gen", generation):
            return
        if devicesubs.job_of(payload) is None:
            self.subtitle_tracks_loaded(payload)
            return
        self.subtitle_lookup = devicesubs.Lookup(
            hostclient.CLIENT.call, lambda merged: self.subtitle_tracks_loaded(merged, generation))
        self.subtitle_lookup.start(payload)

    def local_saved_tracks(self):
        """FIX33: on a disk play the subtitle files saved with the download stay listed whatever the web says."""
        return downloadpick.saved_tracks(self.target.get("local_subtitles")) if self.local else []

    def subtitle_tracks_loaded(self, payload, generation=None):
        if self.gone() or generation is not None and generation != getattr(self, "subtitle_lookup_gen", generation):
            return
        status = payload.get("status") if isinstance(payload, dict) else "unavailable"
        saved = self.local_saved_tracks()
        tracks = payload.get("tracks") or [] if isinstance(payload, dict) else []
        self.subtitle_state.update(status="ok" if saved else subs.panel_status(status), tracks=saved + tracks)
        self.maybe_subtitle_default()
        self.subtitle_panel_updated()

    def subtitle_tracks_failed(self, error, generation=None):
        if generation is not None and generation != getattr(self, "subtitle_lookup_gen", generation):
            return
        if not self.gone():
            saved = self.local_saved_tracks()     # FIX33: offline, a disk play keeps its saved files
            self.subtitle_state.update(status="ok" if saved else "unavailable", tracks=saved)
            self.maybe_subtitle_default()
            self.subtitle_panel_updated()

    def maybe_subtitle_default(self):
        if getattr(self, "subtitle_default_done", True) or not self.started or not self.subtitle_prefs_ready \
                or not self.subtitle_choice_ready or self.subtitle_state.get("status") == "loading":
            return
        self.subtitle_default_done = True
        prefs = self.subtitle_state["prefs"]
        current = self.current or {}
        self.subtitle_section = current.get("section")
        # FIX27 (website subtitles.js): the Arabic-subs group starts OFF on every play; a torrent starts ON when
        # a site/external track is in a preferred language; other plays follow the saved preference.
        # FIX33: a downloaded torrent file played from the disk is a torrent for this rule (spec 081 §2)
        torrent = bool(current.get("torrent") or current.get("local_torrent"))
        rule = subs.start_rule(self.subtitle_section, torrent, bool(prefs.get("enabled")),
                               self.subtitle_state.get("tracks"),
                               self.subtitle_state.get("embedded") if self.ext_on else [],
                               subs.effective_languages(prefs.get("languages"), self.lang),
                               self.subtitle_choice_value)
        # FIX33: the rule ran before exteplayer3's s_l line listed the in-file tracks: it runs once more on it
        self.subtitle_rule_unlisted = rule["action"] is None and torrent and self.ext_on and \
            not self.subtitle_state.get("embedded")
        if rule["action"] == "off":
            self.subtitle_off(save=False)
        elif rule["action"] == "embedded":
            self.select_embedded_subtitle(rule["row"], automatic=rule["automatic"])
        elif rule["action"] == "track":
            self.select_site_subtitle(rule["row"], rule["offset_ms"] / 1000.0, automatic=rule["automatic"])

    def subtitle_group_check(self):
        """FIX27: another server plays now (failover / hand pick). Into the Arabic-subs group: off (for this
        play, never saved); out of it, or nothing showing: the start rule of the new group runs again."""
        if not getattr(self, "subtitle_default_done", False):
            return
        state = self.subtitle_state
        active = state.get("active") is not None or state.get("embedded_active") is not None \
            or state.get("pending") is not None
        change = subs.group_change(getattr(self, "subtitle_section", None), (self.current or {}).get("section"),
                                   active)
        if change == "keep":
            return
        self.subtitle_off(save=False)
        self.subtitle_section = (self.current or {}).get("section")
        if change == "reset":
            self.subtitle_default_done = False
            self.maybe_subtitle_default()
        self.subtitle_panel_updated()

    def subtitle_off(self, save=True):
        """No subtitle shows. save=False: an automatic off, the saved preference stays as it is."""
        self.subtitle_file_gen += 1
        self.subtitle_state.update(pending=None, notice=None)
        self.disable_embedded_subtitle()
        self.subtitle_cues = []
        self.subtitle_state["active"] = None
        if save:
            self.set_subtitle_enabled(False)
        self.subtitle_tick()

    def disable_embedded_subtitle(self):
        if self.subtitle_state.get("embedded_active") is not None:
            if self.ext_on and self.ext is not None:
                self.ext.subtitle(None)          # s-1
            self.subtitle_cues = []
            log("in-video subtitle off (exteplayer3)")
        self.subtitle_state["embedded_active"] = None

    def select_embedded_subtitle(self, row, automatic=False):
        """An in-video track: exteplayer3 sends its atoms (s<id>), this screen draws them like a site file."""
        ident = row.get("index")
        if not self.ext_on or not isinstance(ident, int) or self.ext is None or not self.ext.subtitle(ident):
            log("in-video subtitle: exteplayer3 is not running")
            if not automatic:
                self.subtitle_state.update(pending=None, notice="failed")
                self.show_toast(i18n.t("subtitles.in_video_failed", self.lang), 4000)
                self.subtitle_panel_updated()
            return
        log("in-video subtitle on: %s (exteplayer3 track %d)" % (row.get("label"), ident))
        self.subtitle_file_gen += 1
        self.subtitle_cues = []
        self.subtitle_state.update(active=None, embedded_active=row.get("index"), offset=0.0,
                                   pending=None, notice=None)
        self.set_subtitle_enabled(True, save=not automatic)
        self.save_subtitle_choice("embedded", str(row.get("lang") or "").upper(), track=row.get("label"))
        self.subtitle_tick(); self.subtitle_panel_updated()

    def select_site_subtitle(self, track, offset=0.0, automatic=False):
        ident = track.get("id")
        if not ident:
            return
        # FIX31: the subtitle that shows now (in-video or site) stays until the new file has loaded, so a
        # failed load leaves it as it was (subtitle_file_loaded turns the in-video one off)
        self.subtitle_file_gen += 1
        generation = self.subtitle_file_gen
        # FIX26: the row says "Loading…" at once and further OKs wait (subtitlelayout.blocked)
        self.subtitle_state.update(pending=ident, notice="loading")
        self.subtitle_panel_updated()
        if devicesubs.is_device(track):          # 086: sub-scene from the box (its vtt, or the worker's file job)
            if track.get("vtt"):
                self.subtitle_file_loaded(track["vtt"], track, offset, generation, automatic)
                return
            hostclient.CLIENT.call("subscene_file", devicesubs.file_args(track),
                                   lambda ok, value: self.device_subtitle_file(ok, value, track, offset, generation,
                                                                               automatic))
            return
        if ident.startswith("saved:"):           # FIX9: saved next to the download, read from the disk
            async_call.run(_read_file, ident[len("saved:"):],
                           on_done=lambda body: self.subtitle_file_loaded(body, track, offset, generation, automatic),
                           on_error=lambda error: self.subtitle_file_failed(error, track, generation, automatic))
            return
        async_call.run(self.client.subtitle_file, ident,
                       on_done=lambda body: self.subtitle_file_loaded(body, track, offset, generation, automatic),
                       on_error=lambda error: self.subtitle_file_failed(error, track, generation, automatic))

    def subtitle_file_loaded(self, body, track, offset, generation, automatic=False):
        if self.gone() or generation != self.subtitle_file_gen:
            return
        cues = subs.parse_vtt(body)
        if not cues:
            self.subtitle_file_failed(None, track, generation, automatic)
            return
        self.disable_embedded_subtitle()
        self.subtitle_cues = cues
        self.subtitle_state.update(active=track.get("id"), embedded_active=None, offset=float(offset),
                                   pending=None, notice=None)
        self.set_subtitle_enabled(True, save=not automatic)   # FIX27: an automatic start never rewrites prefs
        self.save_subtitle_choice(track.get("id"), str(track.get("lang") or "").upper())
        self.subtitle_tick(); self.subtitle_panel_updated()

    def device_subtitle_file(self, ok, value, track, offset, generation, automatic=False):
        vtt, code = devicesubs.file_answer(ok, value)
        if vtt is not None:
            self.subtitle_file_loaded(vtt, track, offset, generation, automatic)
        else:
            self.subtitle_file_failed(devicesubs.FileError(code), track, generation, automatic)

    def subtitle_file_failed(self, error, track, generation, automatic=False):
        if self.gone() or generation != self.subtitle_file_gen:
            return
        # A bad archive/season-pack must not make the whole language look unavailable: the next track of the
        # same language once (forward only, so it ends); never on the daily limit (every track would hit it).
        notice = subtitlelayout.failure_notice(error)
        log("site subtitle %s not loaded: %s" % (track.get("id"), subtitlelayout.failure_code(error)))
        next_track = None if notice == "limit" else subtitlelayout.next_same_language(
            self.subtitle_state.get("tracks") or [], track)
        if next_track:
            self.select_site_subtitle(next_track, 0.0, automatic)
            return
        # FIX31 (box 2026-11-20): never silently back to Off - the previous subtitle state stays, the panel
        # says why and, for the viewer's own pick, a toast says it over the picture too
        self.subtitle_state.update(pending=None, notice=notice)
        self.subtitle_tick(); self.subtitle_panel_updated()
        if not automatic:
            self.show_toast(subtitlelayout.notice(self.subtitle_state, self.lang), 4000)

    def select_subtitle_row(self, row):
        self.subtitle_rule_unlisted = False      # the viewer's own pick: no automatic start any more
        kind = row.get("kind")
        if kind == "off":
            self.subtitle_off()
        elif kind == "track":
            saved = self.subtitle_choice_value
            offset = (saved.get("offset_ms", 0) / 1000.0) if saved and saved.get("id") == row.get("id") else 0.0
            self.select_site_subtitle(row.get("track") or {}, offset)
        elif kind == "embedded":
            self.select_embedded_subtitle(row)
        self.subtitle_panel_updated()

    def shift_subtitle(self, amount):
        if not self.subtitle_state.get("active"):
            return
        self.subtitle_state["offset"] = max(-600.0, min(600.0, self.subtitle_state["offset"] + amount))
        track = next((row for row in self.subtitle_state["tracks"]
                      if row.get("id") == self.subtitle_state["active"]), {})
        self.save_subtitle_choice(self.subtitle_state["active"], str(track.get("lang") or "").upper())
        self.subtitle_tick(); self.subtitle_panel_updated()

    def resize_subtitle(self, name, direction=None, value=None):
        """A Settings card of the Subtitles panel changed (size, colour, background, opacity, height, font):
        a pill sets `value` (FIX20), a −/+ stepper moves one `direction` step."""
        if direction is None and value is None:  # the old (direction) call: the size row
            name, direction = "size", name
        prefs = dict(self.subtitle_state["prefs"], size=self.subtitle_state.get("size") or "m")
        prefs = subs.set_style(prefs, name, value) if value is not None else subs.adjust(prefs, name, direction)
        self.subtitle_state["prefs"], self.subtitle_state["size"] = prefs, prefs["size"]
        self.apply_subtitle_size()
        self.subtitle_tick()
        self.save_subtitle_prefs()

    def edit_subtitle_language(self, action, code):
        prefs = dict(self.subtitle_state["prefs"])
        before = prefs.get("languages") or []
        after = subtitlelayout.edit_languages(before, action, code)
        if after == before:
            return
        active = self.subtitle_state.get("active")
        active_track = next((row for row in self.subtitle_state.get("tracks") or [] if row.get("id") == active), None)
        prefs["languages"] = after
        self.subtitle_state["prefs"] = prefs
        if action == "remove" and active_track and str(active_track.get("lang") or "").upper() == str(code).upper():
            self.subtitle_file_gen += 1
            self.subtitle_cues = []
            self.subtitle_state.update(active=None, pending=None, notice=None)   # FIX26: no load waits now
            self.subtitle_default_done = False
            self.subtitle_tick()
        self.save_subtitle_prefs()
        self.refresh_subtitle_tracks()
        self.subtitle_panel_updated()

    def set_subtitle_enabled(self, enabled, save=True):
        """The viewer turned subtitles on/off: saved, except in the Arabic-subs group (FIX27, website sessionOn:
        on/off there is for this play only) and for an automatic start."""
        if not save or subs.muted_group((self.current or {}).get("section")):
            return
        self.subtitle_state["prefs"]["enabled"] = bool(enabled)
        self.save_subtitle_prefs()

    def save_subtitle_prefs(self):
        values = self.subtitle_state["prefs"]
        if self.source.signed_in():
            async_call.run(self.client.subtitle_prefs_set, subs.account_fields(values),
                           on_done=lambda value: None, on_error=lambda error: None)
            # the box's own size (FIX19: per device) and font are not account fields
            box = dict(self.source.prefs.subtitle_prefs(), size=values.get("size") or "m")
            if values.get("font"):
                box["font"] = values["font"]
            self.source.prefs.set_subtitle_prefs(box)
        else:
            self.source.prefs.set_subtitle_prefs(values)

    def save_subtitle_choice(self, ident, lang, track=None):
        if isinstance(ident, str) and ident.startswith("saved:"):
            return                               # a file on this box is not a choice the account can share
        fallback = subs.effective_languages(self.subtitle_state.get("prefs", {}).get("languages"), self.lang)[0]
        choice = {"key": self.subtitle_key, "lang": lang or fallback, "id": ident,
                  "offset_ms": int(round(self.subtitle_state.get("offset", 0) * 1000))}
        if track is not None:
            choice["track"] = track
        self.subtitle_choice_value = choice
        if self.source.signed_in():
            async_call.run(self.client.subtitle_choice_set, choice, on_done=lambda value: None,
                           on_error=lambda error: None)
        else:
            self.source.prefs.set_subtitle_choice(self.subtitle_key, choice)

    def subtitle_style(self):
        return subs.render_style(dict(self.subtitle_state.get("prefs") or {},
                                      size=self.subtitle_state.get("size") or "m"))

    def apply_subtitle_size(self):
        """Size, font, colour, outline / box and height of the two lines, as the Settings rows say."""
        style = self.subtitle_style()
        for name in ("subline1", "subline2"):
            label = self[name].instance
            if not label:
                continue
            label.setFont(gFont(style["face"], style["px"]))
            label.resize(eSize(1600, style["line_h"]))
            label.setForegroundColor(theme.rgb(style["color"]))
            try:                                 # openATV eLabel: the outline round the text
                label.setBorderWidth(4 if style["outline"] else 0)
                label.setBorderColor(theme.rgb("#000000"))
            except AttributeError:
                pass
        self.position_subtitles(self.bar_on)

    def position_subtitles(self, controls):
        style = self.subtitle_style()
        tops = style["tops"]
        if controls:                             # above the controls bar: the lines end where they did at the
            tops = subs.above_controls(style)    # default size, bigger text grows upwards (FIX19b)
        for index, name in enumerate(("subline1", "subline2")):
            if self[name].instance:
                self[name].instance.move(ePoint(160, tops[index]))
        self.subtitle_boxes()

    def subtitle_boxes(self):
        """Background "Box": a dark box behind each shown line, as wide as its text."""
        style = self.subtitle_style()
        for name, box in (("subline1", "subbox1"), ("subline2", "subbox2")):
            label, back = self[name].instance, self[box].instance
            if not label or not back:
                continue
            if not style["box"] or not self[name].visible or not self[name].getText():
                self[box].hide()
                continue
            try:
                width = min(1640, label.calculateSize().width() + 40)
            except AttributeError:
                width = 1640
            position = label.position()
            back.setBackgroundColor(theme.rgb(style["box"]))
            back.resize(eSize(width, style["line_h"]))
            back.move(ePoint(960 - width // 2, position.y()))
            self[box].show()

    def subtitle_tick(self):
        if self.gone():
            return
        at, _ = self.position()
        # render_offset is the inverse of the viewer's positive-delay value, so the requested
        # cue_at(position + offset) sampling also follows the public subtitle contract.
        render_offset = -float(self.subtitle_state.get("offset", 0))
        text = subs.cue_at(self.subtitle_cues, at + render_offset) if at is not None else None
        style = self.subtitle_style()
        lines = subs.wrap_text(text, width=style["wrap"]) if text else ["", ""]
        changed = False
        for index, name in enumerate(("subline1", "subline2")):
            changed = changed or self[name].getText() != lines[index]
            self[name].setText(lines[index])
            self[name].show() if lines[index] else self[name].hide()
        if changed:
            self.subtitle_boxes()

    def subtitle_panel_updated(self):
        panel = getattr(self, "panel", None)
        if panel is not None and hasattr(panel, "updated"):
            panel.updated()

    def subtitles(self):
        from .subtitlepanel import SubtitlesPanel
        self.panel = self.session.openWithCallback(self.subtitle_panel_closed, SubtitlesPanel, self.lang,
                                                   self.subtitle_state, self.select_subtitle_row,
                                                   self.shift_subtitle, self.resize_subtitle,
                                                   self.edit_subtitle_language)

    def quality(self):
        rows = [q for q in (self.result or {}).get("qualities") or []
                if isinstance(q, dict) and q.get("stream_url")]
        choices, read_only = playback.quality_panel(rows, self.video_height(), self.current,
                                                    i18n.t("download.auto", self.lang), self.quality_url)
        if not choices:
            return
        from .choice import Choice
        if read_only:                 # one quality: its line is already checked; OK only closes
            self.session.open(Choice, self.lang, i18n.t("player.btn.quality", self.lang), choices)
            return
        self.session.openWithCallback(self.quality_picked, Choice, self.lang,
                                      i18n.t("player.btn.quality", self.lang), choices)

    def audio_tracks(self):
        """Kanasa exteplayer3: the a_l rows ("Portuguese · EAC3"), a_c checked; a pick sends a<id>. FIX26: 0-1
        tracks open a read-only list (never an empty one)."""
        self.ext_audio_tracks()

    def ext_audio_tracks(self):
        """Kanasa exteplayer3: the a_l rows ("Portuguese · EAC3"), a_c checked; a pick sends a<id>."""
        ext = self.ext
        tracks = list(ext.audio) if ext is not None and self.started else []
        current = (ext.audio_current or {}).get("id") if ext is not None and ext.audio_current else None
        rows, read_only = playernav.ext_audio_rows(tracks, current, self.lang)
        from .choice import Choice
        if read_only:
            self.session.open(Choice, self.lang, i18n.t("player.audio", self.lang), rows)
            return
        self.session.openWithCallback(lambda picked: self.ext_audio_picked(tracks, picked), Choice, self.lang,
                                      i18n.t("player.audio", self.lang), rows)

    def ext_audio_picked(self, tracks, picked):
        if self.gone() or not self.ext_on or self.ext is None or not picked:
            return
        ident = picked[1]
        if not isinstance(ident, int) or isinstance(ident, bool):
            return
        track = next((t for t in tracks if t.get("id") == ident), None)
        if track is None:
            return
        current = (self.ext.audio_current or {}).get("id") if self.ext.audio_current else None
        if ident != current:
            sent = self.ext.audio_track(ident)       # a_s confirms, a_c names the new current track
            log("exteplayer3 audio pick id=%d sent=%s" % (ident, sent))
        self.remember_audio(track)

    def remember_audio(self, track):
        """Per title, like the subtitle choice: the next play (and a relaunch with -t) starts on this language."""
        choice = playernav.audio_choice(track)
        self.audio_saved, self.audio_saved_loaded = choice, True
        key = getattr(self, "subtitle_key", None)
        if key:
            try:
                self.source.prefs.set_audio_choice(key, choice)
            except Exception as error:  # noqa: BLE001 - a read-only /etc never breaks the pick itself
                log("audio choice not saved: %r" % (error,))

    def quality_picked(self, picked):
        if self.gone() or not picked or not isinstance(picked[1], dict):
            return
        row = picked[1]
        if row.get("stream_url") and row.get("stream_url") == self.quality_url and self.started:
            return                               # the quality that plays: nothing to reload
        at, _ = self.position()
        if at is not None:
            self.start_at = at                   # the same second on the new quality
        if row.get("media"):
            try:
                self.source.prefs.set_hls_height(row.get("height"))     # the next HLS play starts here
            except (OSError, AttributeError):
                pass
            # FIX33: a quality of the HLS master is already a media playlist through our proxy: played as it is
            self.result = dict(self.result or {}, stream_url=row["stream_url"], needs_proxy=False, referer="")
            self.hls_prepared = True
        else:
            # one of the resolve's own qualities: its own proxy/referer rules (the playing result may carry the
            # prepared media playlist's), its list kept
            base = self.resolved_result or self.result or {}
            self.result = dict(base, stream_url=row["stream_url"],
                               qualities=(self.result or {}).get("qualities") or base.get("qualities") or [])
            for key in ("needs_proxy", "referer", "kind", "headers"):   # 085: a device quality's own headers
                if key in row:
                    self.result[key] = row[key]
            self.hls_prepared = False            # FIX29: a new HLS master is prepared again
        self.quality_url = row.get("stream_url")
        self.stop_service()
        self.stop_buffer()
        self.started, self.trying = False, True
        self.start_play()

    def subtitle_panel_closed(self, unused=None):
        self.panel_closed()

    def servers(self):
        """The Servers panel (bar button, green key, the searching card). False when it did not open."""
        if getattr(self, "ext_missing", False) or self.panel is not None:
            return False
        current = self.current
        order, muted = self.source.server_group_order(), self.source.server_group_muted()
        local = self.current if self.local else None
        offline = bool(local) and getattr(self, "net_down", False)    # FIX33: a disk play with no network
        if not playernav.server_groups(self.sources, self.lang, order[0] if order else None, muted,
                                       self.torrent_state, rank=self.source.server_group_rank(), complete=True,
                                       local=local, offline=offline):
            return False
        from .sidepanel import ServersPanel
        waiting = not self.source.server_autoplay() and not self.started and not self.trying
        self.panel = self.session.openWithCallback(
            self.server_panel_closed, ServersPanel, self.lang, self.sources, None if waiting else current,
            self.source, self.torrent_state, self.target.get("resume_hoster") if waiting else None,
            self.last_failed if waiting else None, self.target, local, offline,
            player_feeds=bool(self.local_header) and not local)
        return True

    def episodes(self):
        t = self.target
        if t.get("episodes"):
            from .sidepanel import EpisodesPanel
            self.panel = self.session.openWithCallback(self.episode_panel_closed, EpisodesPanel, self.lang,
                                                       self.client, t.get("_posters"), t)

    def panel_closed(self):
        """True when the answer is used up here (a finish was waiting, or the player is gone)."""
        if self.gone():
            return True
        self.panel = None
        if self.finishing_panel:
            answer, self.pending_finish = self.pending_finish, None
            self.finishing_panel = False
            self.finish(answer)
            return True
        return False

    def server_panel_closed(self, candidate=None):
        if not self.panel_closed() and candidate and not candidate.get("local"):
            self.picked((None, candidate))

    def episode_panel_closed(self, picked=None):
        if not self.panel_closed():
            self.episode_picked(picked)

    def episode_picked(self, picked=None):
        if picked is not None and picked != (self.target.get("season"), self.target.get("episode")):
            self.stop_with(("play", picked[0], picked[1]))

    def stop_with(self, answer):
        if self.started:
            at, length = self.position()
            if at is not None:
                self.save(at, length)
        self.finish(answer)

    def picked(self, choice):
        if not choice or self.gone():
            return
        at, _ = self.position()
        if self.started and at:
            self.start_at = at                   # a manual pick continues at the same second
        self.stop_service()
        self.stop_buffer()
        self.drop_torrent()
        self.gen += 1                            # spec 081: a late answer of the walk's try never plays now
        self.torrent_timer.stop()
        self.torrent_wait_since = None
        # fresh refs first (they expire on the server); the fetch also lets the stopped stream's end event drain
        self.switch_timer.stop()
        self.switching, self.next_step, self.pick = True, None, choice[1]
        self.end_resume_wait()                   # the viewer's own pick: no more waiting for the saved server
        self.started, self.trying, self.polls = False, False, 0
        self.last_failed, self.paused = None, False
        self.local_failed = False                # FIX33: the viewer's own pick after "could not be played"
        self.hide_bar()
        self.try_since = time.monotonic()
        self.show_search("switching", choice[1])      # names THAT server at once (owner 2026-11-17)
        log("picked %s (%s)" % (choice[1].get("hoster"), choice[1].get("cid")))
        self.poll()

    def start_pick(self):
        wanted, self.pick, self.switching = self.pick, None, False
        candidates = [c for c in self.sources.get("candidates") or [] if isinstance(c, dict)]
        fresh = [c for c in candidates if c.get("cid") == wanted.get("cid")] or \
            [c for c in candidates if playernav.same_server(c, wanted)]
        chosen = fresh[0] if fresh else wanted
        # if it fails: the servers AFTER it in panel order, never a jump back to the first one
        self.queue = failover.after_pick(self.sources, chosen.get("cid"), self.attempted | {chosen.get("cid")},
                                         self.source.server_group_muted())
        self.pick_phase = True
        self.attempt(chosen, phase="switching")

    def stop(self):
        if self.started:
            at, length = self.position()
            if at is not None:
                self.save(at, length)
        self.finish(None)

    def finish(self, answer):
        if self.gone():
            return
        if self.panel is not None:
            self.pending_finish = answer
            self.finishing_panel = True
            self.panel.close(None)
            return
        self.closed = True
        for timer in (self.poll_timer, self.tick_timer, self.bar_timer, self.switch_timer, self.service_timer, self.toast_timer,
                      self.torrent_timer, self.subtitle_timer, self.seek_timer):
            timer.stop()
        if self.resume_timer is not None:
            self.resume_timer.stop()
        self.disable_embedded_subtitle()
        self.restore_aspect()                    # the box's own TV picture never stays changed
        exiting = self.ext is not None and self.ext.busy()
        if exiting:                              # our exteplayer3 owns the decoder until it has EXITED
            self.cancel_service_timers()
            self.service_flow.stop()
        else:
            self.stop_service()
        self.stop_buffer()
        self.drop_torrent()
        if self.device_jobs is not None:
            self.device_jobs.cancel()            # late worker answers for this list are ignored
        restore = livetv.player_restores(self.old_service)    # FIX3: not while the plugin holds live TV off

        def gone_for_good(exited=True):
            if restore and exited:               # never playService while an exteplayer3 may hold /dev/dvb
                self.switch_service(self.old_service, lambda: self.close(answer), generation=None)
            else:
                if restore:
                    log("exteplayer3 did not exit: live TV not restored")
                self.close(answer)
        if exiting:
            self.ext.stop(gone_for_good)
        else:
            gone_for_good()
