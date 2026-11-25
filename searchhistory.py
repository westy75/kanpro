"""Recent searches (033, like the TV app): shown before the keyboard when a signed-in viewer has history.

Closes with ("new", None) = open the keyboard, ("run", query) = search that, or None (EXIT). RED removes the
focused query, BLUE (or the last row) clears all after a confirm. Both update the list at once and call the
server in the background; a failed call reloads the server's list. Box lessons kept: full-screen opaque page,
only bound methods in onLayoutFinish, RowList (right-aligned in Arabic)."""
from Components.ActionMap import ActionMap
from Components.Label import Label
from Screens.Screen import Screen

from . import async_call, i18n, searchtext, skins, theme
from .rowlist import RowList


class RecentSearches(Screen):
    def __init__(self, session, lang, client, recent):
        self.skin = skins.recent_searches(lang)
        Screen.__init__(self, session)
        self.lang, self.client, self.recent = lang, client, list(recent or [])
        self.rows = []
        self["title"] = Label(i18n.t("search.recent", lang))
        self["hint"] = Label(i18n.t("search.recent_hint", lang))
        self["status"] = Label("")
        self["list"] = RowList([], 1200 - 12, 64, theme.font("bold", 30, lang), lang == "ar")
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
            "ok": self.ok, "cancel": self.leave, "red": self.remove, "blue": self.ask_clear,
            "up": self.up, "down": self.down}, -1)
        self.onLayoutFinish.append(self.fill)

    def fill(self, keep=0):
        self.rows = searchtext.recent_rows(self.recent, self.lang)
        self["list"].setList([label for label, _, _ in self.rows])
        for _ in range(min(keep, len(self.rows) - 1)):
            self["list"].down()

    def focused(self):
        index = self["list"].getSelectedIndex()
        return (index,) + self.rows[index][1:] if self.rows and index is not None and index < len(self.rows) \
            else (0, None, None)

    def up(self):
        self["list"].up()

    def down(self):
        self["list"].down()

    def leave(self):
        self.close(None)

    def ok(self):
        _, kind, item = self.focused()
        if kind == "new":
            self.close(("new", None))
        elif kind == "run":
            self.close(("run", item["query"]))
        elif kind == "clear":
            self.ask_clear()

    # --- remove / clear ----------------------------------------------------------------------------------
    def remove(self):
        index, kind, item = self.focused()
        if kind != "run":
            return
        self.recent = [entry for entry in self.recent if entry is not item]
        if not self.recent:
            self.close(("new", None))             # nothing left: straight to the keyboard
        else:
            self.fill(index)                       # the focus stays where it was (now the next query)
        async_call.run(self.client.search_history_remove, item["id"], on_done=_ignore, on_error=self.failed)

    def ask_clear(self):
        if not self.recent:
            return
        from .choice import Choice
        rows = [(i18n.t("search.clear_yes", self.lang), "yes"), (i18n.t("search.cancel", self.lang), "no")]
        self.session.openWithCallback(self.clear_answer, Choice, self.lang, i18n.t("search.clear_ask", self.lang),
                                      rows)

    def clear_answer(self, answer=None):
        if not answer or answer[1] != "yes":
            return
        self.recent = []
        async_call.run(self.client.search_history_clear, on_done=_ignore, on_error=_ignore)
        self.close(("new", None))

    def failed(self, error=None):
        self["status"].setText(i18n.t("search.recent_failed", self.lang))
        async_call.run(self.client.search_history, on_done=self.reloaded, on_error=_ignore)

    def reloaded(self, recent):
        self.recent = list(recent or [])
        self.fill()


def _ignore(*args):
    return None
