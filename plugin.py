from Components.config import config
from Plugins.Plugin import PluginDescriptor

from . import i18n, livetv, mainmenu, prefs, theme


def _fonts():
    """The plugin's own font names (theme.font), so the look never depends on the user's skin."""
    try:
        from enigma import addFont
        for name, path in theme.FONT_FILES.items():
            addFont(path, name, 100, False)
    except Exception:  # noqa: BLE001 - a missing file: Enigma2 falls back to its default font
        pass


_fonts()


def _lang():
    try:
        box = config.osd.language.value
    except Exception:  # noqa: BLE001
        box = "en"
    try:
        return prefs.resolve_lang(prefs.Prefs().lang(), box)
    except Exception:  # noqa: BLE001
        return i18n.lang_from_box(box)


_FIRST = object()


def _stop_live_tv(session):
    """FIX3: remember the channel playing now and stop it (setting on); None when nothing was stopped."""
    try:
        enabled = prefs.Prefs().stop_live_tv()
    except Exception:  # noqa: BLE001 - an unreadable prefs file keeps the default
        enabled = True
    try:
        current = session.nav.getCurrentlyPlayingServiceOrGroup()
    except Exception:  # noqa: BLE001 - older images only have the reference call
        current = session.nav.getCurrentlyPlayingServiceReference()
    saved = livetv.remember(enabled, current)
    livetv.holding = enabled
    if saved is not None:
        session.nav.stopService()
    return saved


def _bearer():
    try:
        from . import account
        signed = account.Account().load()
        return signed["bearer"] if signed else None
    except Exception:  # noqa: BLE001
        return None


def _analytics_open(lang):
    """083: app_open (sent at once), and a crash of the previous run (its Enigma2 crash log goes up when
    sharing is on; with sharing off, Home asks first)."""
    try:
        from . import analytics
        analytics.context(lang, _bearer())
        crash = analytics.opened()
        if crash:
            analytics.record("crash")
            if analytics.sharing():
                import threading
                threading.Thread(target=analytics.send_log, args=("crash", lang, _bearer(), crash),
                                 name="kanasa-crashlog", daemon=True).start()
            else:
                analytics.pending_crash = crash
        analytics.record("app_open")
    except Exception:  # noqa: BLE001 - analytics never stops the plugin
        pass


def _analytics_close():
    try:
        from . import analytics
        analytics.closed()
        analytics.flush_later(_lang(), _bearer(), force=True)
    except Exception:  # noqa: BLE001
        pass


def main(session, kanasa_live=_FIRST, **kwargs):
    # A GUI/Enigma2 restart can orphan its eConsoleAppContainer child.  This only signals the PID
    # persisted by us after an exact command-line check; another user's TorrServer is untouched.
    try:
        from . import torrengine
        torrengine.reap_orphan(torrengine.installed_dir())
    except Exception:  # noqa: BLE001 - plugin start must still work without an engine/disk
        pass
    try:                                  # the same for our own exteplayer3 (Settings → Player = Kanasa exteplayer3)
        from . import extplayer
        extplayer.reap_orphan()
    except Exception:  # noqa: BLE001
        pass
    try:                                  # 0.6.10: ipks up to 0.6.9 did not Depend on exteplayer3 (the only player)
        from . import playerinstall
        playerinstall.ensure()
    except Exception:  # noqa: BLE001
        pass
    from .home import Home
    saved = _stop_live_tv(session) if kanasa_live is _FIRST else kanasa_live
    lang = _lang()
    if kanasa_live is _FIRST:
        _analytics_open(lang)
        try:                              # 084: a new plugin open asks "Who's watching?" again (TV rule §6)
            from . import profiles
            profiles.forget_pick()
        except Exception:  # noqa: BLE001
            pass
        try:                              # 085 T104: the signed host bundle + worker (once per plugin run)
            from . import api, localhosts
            localhosts.boot(api.Client(lang=lang, bearer=_bearer()))
        except Exception:  # noqa: BLE001 - the plugin works without device hosts (today's server list)
            pass

    def closed(answer=None):
        try:                              # FIX6: the torrent engine this plugin started stops with it
            from . import downloadmanager     # FIX9: ...unless a torrent downloads; then when that download ends
            downloadmanager.manager().plugin_closed()
        except Exception:  # noqa: BLE001 - closing the plugin must never fail
            pass
        if answer != "restart":
            try:                          # 085: the host worker's RAM goes back to Enigma2 (FR-031)
                from . import hostclient, localhosts
                hostclient.shutdown()
                localhosts.reset()        # the next plugin open checks the bundle again
            except Exception:  # noqa: BLE001
                pass
        if answer == "restart":           # Settings changed the language or the account
            main(session, kanasa_live=saved)
        else:
            _analytics_close()
            livetv.holding = False
            if livetv.restore_on_close(answer, saved):
                session.nav.playService(saved)
    session.openWithCallback(closed, Home, lang)


def _main_menu_shown():
    try:
        return prefs.Prefs().main_menu()
    except Exception:  # noqa: BLE001 - an unreadable prefs file keeps the default (shown)
        return True


def menu(menuid, **kwargs):
    """Owner 2026-11-23: the main-menu entry, read at every menu build (Settings → Main menu hides it)."""
    return mainmenu.entries(menuid, _main_menu_shown(), i18n.t("plugin.name", _lang()), main)


def Plugins(**kwargs):
    lang = _lang()
    return [PluginDescriptor(name=i18n.t("plugin.name", lang), description=i18n.t("plugin.description", lang),
                             where=[PluginDescriptor.WHERE_PLUGINMENU, PluginDescriptor.WHERE_EXTENSIONSMENU],
                             icon="plugin.png", fnc=main),
            PluginDescriptor(name=i18n.t("plugin.name", lang), description=i18n.t("plugin.description", lang),
                             where=PluginDescriptor.WHERE_MENU, fnc=menu)]
