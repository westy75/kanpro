"""Sign in with a phone: QR code + typed code, polls until the website approves (signinflow decides)."""
import os
import time

from enigma import eTimer
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Screens.Screen import Screen

from . import async_call, i18n, qr, signinflow, skins

QR_PATH, QR_SIZE = "/tmp/kanasa_qr.png", 360
DEVICE_NAME = "Vu+ (Kanasa)"

class SignIn(Screen):
    """Closes with the account name after a sign-in, else None."""

    def __init__(self, session, lang, client, account_file):
        self.skin = skins.signin(lang)
        Screen.__init__(self, session)
        self.lang, self.client, self.account_file = lang, client, account_file
        self.flow, self.gen, self.stopped, self.closed = signinflow.Flow(), 0, False, False
        self["title"] = Label(i18n.t("signin.title", lang))
        self["steps"] = Label(i18n.t("signin.steps", lang))
        self["qr"], self["qrcard"] = Pixmap(), Label("")
        self["code"], self["or_type"] = Label(""), Label("")
        self["status"] = Label(i18n.t("home.loading", lang))
        self.start_timer = eTimer(); self.start_timer.callback.append(self.start)
        self.poll_timer = eTimer(); self.poll_timer.callback.append(self.poll)
        self.close_timer = eTimer(); self.close_timer.callback.append(self.done)
        self["actions"] = ActionMap(["OkCancelActions"], {"ok": self.ok, "cancel": self.cancel}, -1)
        self.onLayoutFinish.append(self.start_soon)   # bound methods only (openATV exec()s others)
        self.onClose.append(self.cleanup)

    def start_soon(self):
        self["qr"].hide()
        self["qrcard"].hide()
        self.start_timer.start(0, True)

    # --- the flow ------------------------------------------------------------------------------
    def start(self):
        self.gen += 1
        gen, self.stopped = self.gen, False
        self.poll_timer.stop()
        self["qr"].hide()
        self["qrcard"].hide()
        self["code"].setText("")
        self["or_type"].setText("")
        self["status"].setText(i18n.t("home.loading", self.lang))
        async_call.run(self.client.device_start, DEVICE_NAME,
                       on_done=lambda data: self.started(data, gen),
                       on_error=lambda error: self.act(("stop", "signin.error"), gen))

    def started(self, data, gen):
        if self.closed or gen != self.gen:
            return
        action = self.flow.start(data, time.monotonic())
        if action[0] == "wait":
            code = data.get("user_code") or ""
            self["code"].setText(code)
            self["or_type"].setText(i18n.t("signin.or_type", self.lang) % (data.get("verification_uri") or ""))
            self["status"].setText(i18n.t("signin.waiting", self.lang))
            link = data.get("verification_uri_complete") or data.get("verification_uri") or ""
            async_call.run(qr.png_for, link, QR_PATH, QR_SIZE,
                           on_done=lambda path: self.show_qr(path, gen), on_error=lambda error: None)
        self.act(action, gen)

    def show_qr(self, path, gen):
        if path and not self.closed and gen == self.gen and self["qr"].instance:
            self["qr"].instance.setPixmapFromFile(path)
            self["qr"].show()
            self["qrcard"].show()

    def poll(self):
        gen = self.gen
        async_call.run(self.client.device_poll, self.flow.secret,
                       on_done=lambda answer: self.polled(answer, gen),
                       on_error=lambda error: self.polled(error, gen))

    def polled(self, answer, gen):
        if self.closed or gen != self.gen:
            return
        self.act(self.flow.polled(answer, time.monotonic()), gen)

    def act(self, action, gen):
        if self.closed or gen != self.gen:
            return
        if action[0] == "wait":
            self.poll_timer.start(int(action[1] * 1000), True)
            return
        if action[0] == "approved":
            _, token, name, flags = action
            try:
                self.account_file.save(token, name, flags)
            except OSError:
                action = ("stop", "signin.error")
            else:
                self.name = name
                self["status"].setText(i18n.t("signin.done", self.lang) % name)
                self.close_timer.start(1500, True)
                return
        self.stopped = True                 # denied / expired / error: OK asks for a new code
        self["qr"].hide()
        self["qrcard"].hide()
        self["status"].setText(i18n.t(action[1], self.lang))

    # --- keys ----------------------------------------------------------------------------------
    def ok(self):
        if self.stopped:
            self.start()

    def done(self):
        self.finish()

    def cancel(self):
        self.finish()                       # EXIT during "Signed in as …" still reports the sign-in

    def finish(self):
        if self.closed:
            return
        answer = getattr(self, "name", None)
        self.closed = True
        for timer in (self.start_timer, self.poll_timer, self.close_timer):
            timer.stop()
        self.close(answer)

    def cleanup(self):
        try:
            os.remove(QR_PATH)
        except OSError:
            pass
