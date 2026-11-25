"""Search: Recent searches first (signed in, 033), the box's on-screen keyboard (Arabic + English), then
results as poster rows. Every search that runs is added to the viewer's history in the background."""
from enigma import eTimer
from Screens.VirtualKeyBoard import VirtualKeyBoard

from . import async_call, grid, i18n, profiles, searchtext, tabs
from .home import COLS, ROWS, Home


class Search(Home):
    """Reuses Home's layout and drawing; only loading differs."""

    def __init__(self, session, lang, client, source, posters):
        Home.__init__(self, session, lang)
        self.client, self.source, self.store, self.posters = client, source, source.store, posters
        self.bar.loaded = self.bar.cursor = next(i for i, tab in enumerate(tabs.TABS) if tab[0] == "search")
        self.in_tabs = False
        # 0.7.0 crash (owner's box + a user, OpenATV 7.6/8.0): 084 made Home register ONE start hook (account,
        # profile picker, rows) instead of load + start_import, and remove(self.load) raised -> GSOD. Search
        # loads on its own and must never run Home's start (it could open the picker over the keyboard).
        self.onLayoutFinish.remove(self.start)
        # Home's start set the top-bar avatar; Search skips it, so it shows the profile Home already picked
        self.profile_state = profiles.last_state()
        self.onLayoutFinish.append(self.draw_profile)
        # a modal keyboard may only open once this screen runs (opening it during layout crashes Enigma2)
        self.fetching = False                       # one history call at a time (UP while it loads)
        self.ask_timer = eTimer()
        self.ask_timer.callback.append(self.recent_or_ask)
        self.onLayoutFinish.append(self.ask_soon)   # openATV exec()s non-method entries: bound methods only

    def ask_soon(self):
        self.ask_timer.start(0, True)

    def recent_or_ask(self):
        """Signed in: the viewer's recent searches first (none, or a failed call: the keyboard as before)."""
        if not self.source.signed_in():
            self.ask()
            return
        if self.fetching:
            return
        self.fetching = True
        async_call.run(self.client.search_history, on_done=self.recent_loaded, on_error=self.recent_failed)

    def recent_loaded(self, recent):
        self.fetching = False
        if not recent:
            self.ask()
            return
        from .searchhistory import RecentSearches
        self.session.openWithCallback(self.recent_done, RecentSearches, self.lang, self.client, recent)

    def recent_failed(self, error=None):
        self.fetching = False
        self.ask()

    def recent_done(self, answer=None):
        if not answer:
            if not self.grid.rows:
                self.close()
            return
        if answer[0] == "new":
            self.ask()
        elif answer[0] == "run":
            self.typed(answer[1])

    def ask(self):
        self.session.openWithCallback(self.typed, VirtualKeyBoard, title=i18n.t("search.prompt", self.lang))

    def typed(self, text=None):
        query = searchtext.clean(text)
        if query is None:
            if not self.grid.rows:
                self.close()
            return
        self["status"].setText(i18n.t("home.loading", self.lang))
        if self.source.signed_in():         # history in the background: never blocks, failures ignored
            async_call.run(self.client.search_history_add, query, on_done=_ignore, on_error=_ignore)
        self.generation += 1
        generation = self.generation
        async_call.run(self.client.search, query, on_done=lambda items: self.found(items, query, generation),
                       on_error=lambda e: self.load_failed(generation))

    def found(self, items, query, generation):
        if generation != self.generation:
            return
        self.grid = grid.Grid(searchtext.result_rows(items, query), COLS, ROWS)
        count = len(items.get("items", [])) + len(items.get("turkish", [])) if isinstance(items, dict) else len(items)
        self["status"].setText("" if count else i18n.t("search.none", self.lang))
        self.draw()

    def open_settings(self):
        pass                 # Settings from Home only: a sign-in must restart Home, which opened this screen

    def load(self):      # the tab bar is not used here: green/OK on empty results asks again
        self.recent_or_ask()

    def move(self, d_row, d_col):
        if not self.grid.move(d_row, d_col) and d_row < 0:
            self.recent_or_ask()
        self.draw()


def _ignore(*args):
    return None
