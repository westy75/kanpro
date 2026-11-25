"""The tab bar: ids in display order and how each tab loads (no Enigma2 imports)."""
TABS = [("home", "tab.home"), ("movies", "tab.movies"), ("series", "tab.series"), ("anime", "tab.anime"),
        ("asian", "tab.asian"), ("arabic", "tab.arabic"), ("turkish", "tab.turkish"), ("hindi", "tab.hindi"),
        ("search", "tab.search"), ("mylist", "tab.mylist"), ("downloads", "tab.downloads"), ("profile", "tab.profile"),
        ("settings", "tab.settings")]
# round icon buttons on the far side (owner 2026-11-16); FIX31: Downloads is one of them (no text tab any more)
ICONS = {"search", "mylist", "downloads", "profile", "settings"}


def loader(client, source, tab_id: str):
    if tab_id in ("search", "profile", "settings", "downloads"):     # open their own screens, nothing to load
        return None
    if tab_id == "home":
        return client.home
    if tab_id == "mylist":
        return source.my_list_rows        # the account's list when signed in
    return lambda: client.tab(tab_id)
