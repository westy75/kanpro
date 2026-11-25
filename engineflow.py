"""Reusable Enigma2 UI for installing the pinned torrent engine from Home or Settings."""
import time

from enigma import eTimer
from Screens.MessageBox import MessageBox

from . import async_call, i18n, torrengine, torrentrules
from .choice import Choice


class EngineInstallFlow:
    def __init__(self, owner, on_installed=None):
        self.owner = owner
        self.session, self.lang, self.prefs = owner.session, owner.lang, owner.prefs
        self.on_installed = on_installed
        self.asset, self.directory = None, None
        self.progress, self.progress_state = None, {}
        self.cancelled, self.finished = False, False
        self.timer = eTimer()
        self.timer.callback.append(self._tick)
        # a popup opened while the progress box is closing can be lost: open the result a moment later
        self.result, self.result_timer = None, eTimer()
        self.result_timer.callback.append(self._show_result)

    def offer(self, force=False, update_popup=False):
        installed = torrengine.installed_dir()
        self.asset = torrentrules.asset_for(torrengine.machine())
        self.directory = torrengine.install_dir() if self.asset else None
        if installed:
            return False
        if not force and not torrentrules.should_prompt(
                installed, self.asset, self.directory, self.prefs.engine_prompt_deferred_at(),
                int(time.time()), update_popup):
            return False
        if self.asset is None or self.directory is None:
            if force:
                self._message("torrent.unsupported" if self.asset is None else "torrent.no_space",
                              MessageBox.TYPE_ERROR, 8)
            return False
        rows = [(i18n.t("torrent.install_yes", self.lang), "yes"),
                (i18n.t("torrent.install_no", self.lang), "no")]
        self.session.openWithCallback(self._picked, Choice, self.lang,
                                      i18n.t("torrent.engine_missing_title", self.lang), rows,
                                      i18n.t("torrent.engine_missing_body", self.lang))
        return True

    def _picked(self, picked):
        if not picked or picked[1] != "yes":
            if picked and picked[1] == "no":
                self.prefs.set_engine_prompt_deferred_at(int(time.time()))
            return
        self.cancelled, self.finished = False, False
        self.progress_state = {"percent": 0}
        self.progress = self.session.openWithCallback(
            self._progress_closed, MessageBox, i18n.t("torrent.installing", self.lang) % 0,
            MessageBox.TYPE_INFO, timeout=0)
        self.timer.start(500, False)
        async_call.run(torrengine.download, self.directory, self.asset, self.progress_state, None,
                       lambda: self.cancelled, on_done=self._installed, on_error=self._failed)

    def _tick(self):
        if self.progress is not None:
            percent = max(0, min(100, int(self.progress_state.get("percent") or 0)))
            self.progress["text"].setText(i18n.t("torrent.installing", self.lang) % percent)

    def _progress_closed(self, *args):
        self.progress = None
        if not self.finished:
            self.cancelled = True

    def _close_progress(self):
        self.timer.stop()
        self.finished = True
        if self.progress is not None:
            progress, self.progress = self.progress, None
            progress.close()

    def _installed(self, path):
        self._close_progress()
        self._later(i18n.t("torrent.installed", self.lang), MessageBox.TYPE_INFO)
        if self.on_installed is not None:
            self.on_installed()

    def _failed(self, error):
        self._close_progress()
        reason = getattr(error, "code", "unknown")
        if reason == "cancelled":
            return
        reason_key = "torrent.reason." + reason
        if reason_key not in i18n.STRINGS:
            reason_key = "torrent.reason.unknown"
        text = i18n.t("torrent.install_failed_reason", self.lang) % i18n.t(reason_key, self.lang)
        self._later(text, MessageBox.TYPE_ERROR)

    def _later(self, text, kind):
        """The result stays until OK (owner: show when it finishes)."""
        self.result = (text, kind)
        self.result_timer.start(300, True)

    def _show_result(self):
        if self.result:
            (text, kind), self.result = self.result, None
            self.session.open(MessageBox, text, kind, timeout=0)

    def _message(self, key, kind, timeout):
        self.session.open(MessageBox, i18n.t(key, self.lang), kind, timeout=timeout)
