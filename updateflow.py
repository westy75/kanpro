"""Thin Enigma2 UI around the pure updater module."""
import os
import time

from enigma import eConsoleAppContainer
from Screens.MessageBox import MessageBox
from Screens.Standby import TryQuitMainloop

from . import __version__, analytics, api, async_call, i18n, updater
from .choice import Choice


CHECK_SECONDS = 6 * 60 * 60
DOWNLOAD_PATH = "/tmp/kanasa_update.ipk"


class UpdateError(Exception):
    pass


def _remove():
    try:
        os.unlink(DOWNLOAD_PATH)
    except OSError:
        pass


def _download(client, manifest):
    """Runs off the GUI thread: stream the ipk to /tmp, then check its size and sha256."""
    try:
        client.download(manifest.get("url"), DOWNLOAD_PATH)
    except Exception as exc:  # noqa: BLE001 - one safe reason; the error must not carry the bearer
        _remove()
        raise UpdateError("invalid" if isinstance(exc, api.ApiError) and exc.code == "bad_url"
                          else "download") from None
    if not updater.check_file(DOWNLOAD_PATH, manifest.get("size"), manifest.get("sha256")):
        _remove()
        raise UpdateError("invalid")
    return DOWNLOAD_PATH


class UpdateFlow:
    def __init__(self, owner, on_checked=None):
        self.owner = owner
        self.session, self.lang = owner.session, owner.lang
        self.client, self.prefs = owner.client, owner.prefs
        self.manifest, self.progress, self.console = None, None, None
        self.on_checked = on_checked

    def _finished_check(self, popup_shown):
        callback, self.on_checked = self.on_checked, None
        if callback is not None:
            callback(bool(popup_shown))

    def check(self, force=False):
        now = int(time.time())
        if not force and not updater.must_check(now, self.prefs.update_checked_at(), self.prefs.forced_update(),
                                                __version__, CHECK_SECONDS):
            self._finished_check(False)
            return
        self.prefs.set_update_checked_at(now)
        async_call.run(self.client.release, "test", on_done=lambda payload: self._checked(payload, force),
                       on_error=lambda error: self._check_failed(force, error))

    def _check_failed(self, force, error=None):
        if not force:
            self._finished_check(False)
            return
        no_release = isinstance(error, api.ApiError) and error.code == "http_404"
        self._message("update.current" if no_release else "update.check_failed")

    def _checked(self, payload, force):
        manifest = updater.verify(payload.get("manifest"), payload.get("sig")) \
            if isinstance(payload, dict) else None
        if manifest is None:
            self._check_failed(force)
            return
        decision = updater.decide(manifest, __version__, None if force else self.prefs.skipped_update())
        # a mandatory release is asked for again on every open until it is installed
        self.prefs.set_forced_update(manifest.get("version") if decision == "force" else None)
        if decision == "none":
            if force:
                self._message("update.current")
            self._finished_check(False)
            return
        self.manifest = manifest
        title = i18n.t("update.available", self.lang) % manifest["version"]
        body = "\n".join("• " + line for line in updater.notes_for(manifest, self.lang))
        rows = [(i18n.t("update.now", self.lang), "update")]
        if decision == "offer":
            rows.append((i18n.t("update.later", self.lang), "later"))
        analytics.update_event("update_shown", manifest["version"], decision == "force")
        self.session.openWithCallback(lambda picked: self._picked(picked, decision), Choice,
                                      self.lang, title, rows, body)
        self._finished_check(True)

    def _picked(self, picked, decision):
        action = picked[1] if picked else None
        analytics.update_event("update_accepted" if action == "update" else "update_skipped",
                               self.manifest["version"], decision == "force")
        if action == "update":
            self._start_download()
        elif action == "later":
            self.prefs.set_skipped_update(self.manifest["version"])
        elif decision == "force":
            self.owner.close()

    def _start_download(self):
        # the viewer may close the progress box early; the update carries on and reports at the end
        self.progress = self.session.openWithCallback(self._progress_closed, MessageBox,
                                                      i18n.t("update.downloading", self.lang),
                                                      MessageBox.TYPE_INFO, timeout=0)
        async_call.run(_download, self.client, self.manifest, on_done=self._downloaded,
                       on_error=lambda error: self._failed(error.args[0] if isinstance(error, UpdateError)
                                                           else "download"))

    def _downloaded(self, path):
        self._progress_text("update.installing")
        self.console = eConsoleAppContainer()
        self.console.appClosed.append(self._installed)
        if self.console.execute("opkg install " + DOWNLOAD_PATH):
            self._failed("install")

    def _installed(self, code):
        _remove()
        if code:
            self._failed("install")
            return
        self._progress_text("update.restarting")
        self.session.open(TryQuitMainloop, 3)

    def _failed(self, reason):
        _remove()
        key = "update.invalid" if reason == "invalid" else "update.install_failed" \
            if reason == "install" else "update.download_failed"
        if self.progress is not None:
            progress, self.progress = self.progress, None
            progress.close()
        self._message(key)

    def _progress_closed(self, *args):
        self.progress = None

    def _progress_text(self, key):
        if self.progress is not None:
            self.progress["text"].setText(i18n.t(key, self.lang))

    def _message(self, key):
        self.session.open(MessageBox, i18n.t(key, self.lang), MessageBox.TYPE_INFO, timeout=6)
