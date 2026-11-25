"""Owner 2026-11-23: KanasaStream in the image's main menu (ON by default); Settings → Main menu hides it. Pure.

plugin.py registers a WHERE_MENU descriptor (openATV calls its fnc(menuid) every time it builds a menu, the main
menu with menuid "mainmenu"); the setting is read at that moment, so a change shows the next time the main menu is
built (a GUI restart at the latest, which the Settings hint says)."""
MENU_ID = "mainmenu"
KEY = "kanasastream"
WEIGHT = 5                     # low = near the top of the main menu


def entries(menuid, shown, name, fnc) -> list:
    """The (text, fnc(session, **kwargs), key, weight) rows Enigma2's Menu takes for `menuid`."""
    if menuid != MENU_ID or not shown:
        return []
    return [(name, fnc, KEY, WEIGHT)]
