"""Thin fixed-slot Servers and player-Episodes side panels."""
import os
from enigma import ePoint, eSize, eTimer
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Screens.Screen import Screen
from . import async_call, downloadmanager, episodeslayout, failover, i18n, layout, panellayout, panelnav, playernav, serversettings, skins, theme, torrents
from .source import offline_error

ICONS = os.path.join(os.path.dirname(__file__), "icons")
SLOTS = panellayout.SERVER_SLOTS
POLL_MS, MAX_POLLS = 3000, 30


class ServersPanel(Screen):
    """Closes with the picked candidate (or None). `current` = the playing candidate dict.

    FIX26: the top row ([★ Open first] [⊘ Not interested] [X]) is reachable with UP from the first row of
    either column; in the Torrents group the right column starts with a "Sort & filter" line that opens in
    place like a drop-down (one line per sort / filter choice; the cards below shift down)."""
    def __init__(self, session, lang, sources, current, source, torrent_state=None, focus_hoster=None,
                 failed=None, target=None, local=None, offline=False, player_feeds=False):
        self.skin = skins.servers_panel(lang)
        Screen.__init__(self, session)
        self.lang, self.rtl, self.current, self.sources, self.source = lang, lang == "ar", current, sources, source
        self.torrent_state, self.failed, self.target, self.local = torrent_state, failed, target or {}, local
        self.offline = bool(offline and local)          # FIX33: a disk play with no network: say so, never "Looking…"
        self.polls, self.closed = 0, False
        # 085: with hosts on the box the player feeds this panel (its device rows merged in); a panel poll of its own
        # had no local header, so the server ran those sites itself and its rows replaced the box's (box 2026-11-24)
        self.player_feeds = bool(player_feeds)
        self.torrent_sort, self.torrent_filter = source.prefs.torrent_view()
        self.sort_filter_open = False                       # closed by default (owner)
        self.groups = self.build_groups()
        self.playing = playernav.find_server(self.groups, current)
        # Auto off (066): nothing plays; the focus starts on the saved server (then on the one that failed)
        focus = self.playing or serversettings.resume_focus(self.groups, focus_hoster) or \
            playernav.find_server(self.groups, failed)
        group, server = focus if focus else (0, 0)
        self.lines = self.column(group)
        line = panelnav.item_of_server(self.lines, server)
        self.nav = panelnav.Nav(len(self.groups), len(self.lines), self.rtl, group, line,
                                top=panelnav.top_items(self.groups[group] if self.groups else None))
        self.offset, self.boxes, self.playing_line = panellayout.column_offset(self.kinds(), 0, line), {}, None
        for name in ("dimleft", "panel", "ptitle", "navframe", "mainframe", "groupselect", "groupselected",
                     "serverplaying", "serverplayingbox", "topframe", "sortfilterbox"): self[name] = Label("")
        for name in ("close", "grouparrow", "serverplayicon"): self[name] = Pixmap()
        self["grouphint"] = Label(i18n.t("player.group_hint", lang))
        self["groupstar"] = Label("")
        self["groupmute"] = Label("")
        self["sortfilter"] = Label("")
        self["ptitle"].setText(i18n.t("player.servers", lang))
        for i in range(panellayout.GROUP_SLOTS):
            self["groupbox%d" % i], self["group%d" % i], self["groupcount%d" % i] = Label(""), Label(""), Label("")
            self["groupmute%d" % i] = Pixmap()
        for i in range(panellayout.OPTION_SLOTS):
            self["sfoptbox%d" % i], self["sfopt%d" % i] = Label(""), Label("")
        for i in range(SLOTS):
            for kind in ("server", "quality", "serverlabel", "servermeta", "servertracks", "checked"):
                self["%s%d" % (kind, i)] = Label("")
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
            "cancel": lambda: self.close(None), "ok": self.ok, "up": lambda: self.vertical(-1),
            "down": lambda: self.vertical(1), "left": lambda: self.horizontal("left"),
            "right": lambda: self.horizontal("right"), "yellow": self.yellow,
            "blue": self.blue, "red": self.toggle_sort_filter,
            "green": self.cycle_torrent_filter}, -1)
        self.onLayoutFinish.append(self.start)
        self.refresh_timer = eTimer(); self.refresh_timer.callback.append(self.refresh_sources)
        self.onClose.append(self.stopped)

    def start(self):
        for name, asset in (("close", "close_n.png"), ("grouparrow", "selected.png"),
                            ("serverplayicon", "selected.png")):
            if self[name].instance: self[name].instance.setPixmapFromFile(os.path.join(ICONS, asset))
        self.close_icon = "close_n.png"
        for i in range(panellayout.GROUP_SLOTS):
            if self["groupmute%d" % i].instance:
                self["groupmute%d" % i].instance.setPixmapFromFile(os.path.join(ICONS, "eye_off.png"))
        self.draw_groups(); self.draw_servers()
        self.refresh_sources()

    def stopped(self):
        self.closed = True
        self.refresh_timer.stop()

    def refresh_sources(self):
        """Panel-owned source polling: update its rows only; never autoplay."""
        if self.closed or self.polls >= MAX_POLLS or self.target.get("site_id") or self.player_feeds:
            return
        self.polls += 1
        t = self.target
        async_call.run(self.source.client.sources, t.get("kind"), int(t.get("id")), t.get("season"),
                       t.get("episode"), on_done=self.refreshed, on_error=self.refresh_failed)

    def refresh_failed(self, error):
        """FIX33: on a disk play, a fetch that cannot reach the server shows the offline note (the polls go on:
        the rows come when the network is back)."""
        if self.closed:
            return
        if self.local and offline_error(error) and not self.offline:
            self.offline_updated(True)
        self.refreshed(None)

    def offline_updated(self, offline):
        self.offline = bool(offline and self.local)
        self.torrents_updated(self.torrent_state)

    def refreshed(self, data):
        if self.closed:
            return
        if data is not None:
            self.offline = False
            self.sources_updated(self.source.rank_sources(data))
        if (data is None or failover.still_coming(data)) and self.polls < MAX_POLLS:
            self.refresh_timer.start(POLL_MS, True)

    # --- model -------------------------------------------------------------------------------------------
    def build_groups(self):
        order, muted = self.source.server_group_order(), self.source.server_group_muted()
        return playernav.server_groups(self.sources, self.lang, order[0] if order else None, muted,
                                       self.torrent_state, rank=self.source.server_group_rank(),
                                       torrent_view=(self.torrent_sort, self.torrent_filter), complete=True,
                                       local=self.local, offline=getattr(self, "offline", False))

    def selected_servers(self):
        return self.groups[self.nav.selected]["servers"] if self.groups else []

    def column(self, group_index):
        """The right column's lines for group `group_index` (panelnav.column_items)."""
        group = self.groups[group_index] if 0 <= group_index < len(self.groups) else None
        torrent = bool(group) and group["id"] == torrents.GROUP
        return panelnav.column_items(group["servers"] if group else [], torrent, self.sort_filter_open,
                                     torrents.SORTS, torrents.FILTERS)

    def kinds(self):
        return [kind for kind, _ in self.lines]

    def focused_server(self):
        return panelnav.server_at(self.lines, self.nav.positions["main"])

    def refresh_column(self, enter=False, line=None, server=None):
        """Rebuild the selected group's lines; the focus goes to `line`, else to server `server`, else stays."""
        self.lines = self.column(self.nav.selected)
        if line is None:
            line = panelnav.item_of_server(self.lines, server) if server is not None else self.nav.positions["main"]
        self.nav.set_main(len(self.lines), enter)
        self.nav.positions["main"] = max(0, min(line, len(self.lines) - 1))
        self.nav.set_top(panelnav.top_items(self.groups[self.nav.selected] if self.groups else None))
        self.playing = playernav.find_server(self.groups, self.current)
        self.offset = panellayout.column_offset(self.kinds(), self.offset, self.nav.positions["main"])

    def regroup(self, group_id, line=None, server=None):
        """The groups were rebuilt: select `group_id` again (or the first) and refill its column."""
        self.nav.selected = next((i for i, group in enumerate(self.groups) if group["id"] == group_id), 0)
        self.nav.counts["nav"] = len(self.groups)
        self.nav.positions["nav"] = min(self.nav.positions["nav"], max(0, len(self.groups) - 1))
        self.refresh_column(line=line, server=server)

    # --- drawing -----------------------------------------------------------------------------------------
    def draw_groups(self):
        for i in range(panellayout.GROUP_SLOTS):
            group = self.groups[i] if i < len(self.groups) else None
            for kind in ("groupbox", "group", "groupcount"):
                self["%s%d" % (kind, i)].show() if group else self["%s%d" % (kind, i)].hide()
            self["groupmute%d" % i].show() if group and group["muted"] else self["groupmute%d" % i].hide()
            title = group["title"] + ("  " + theme.GLYPHS["star"] if group and group["starred"] else "") \
                if group else ""
            self["group%d" % i].setText(title)
            self["groupcount%d" % i].setText("" if not group or group.get("count") is None else
                                               str(group["count"]))
            fill = theme.SELECTED if i == self.nav.selected else theme.SURFACE
            count = self["groupcount%d" % i]
            if group and count.instance:            # the selected row's ▶ sits where the count was: shift it
                _, top, _, _ = panellayout.server_group(i, self.rtl)
                left = 928 - (64 if i == self.nav.selected else 0)
                count.instance.move(ePoint(layout.x(left, 84, self.rtl), top))
            for kind in ("group", "groupcount"):
                if self["%s%d" % (kind, i)].instance:
                    self["%s%d" % (kind, i)].instance.setBackgroundColor(theme.rgb(fill))
                    color = theme.DIM if group and group["muted"] else theme.TEXT if kind == "group" else theme.DIM
                    self["%s%d" % (kind, i)].instance.setForegroundColor(theme.rgb(color))
                    self["%s%d" % (kind, i)].instance.invalidate()   # the box repaints a colour only on invalidate
        for name in ("groupselect", "groupselected", "grouparrow"): self[name].hide()
        if self.groups and self["groupselect"].instance:
            x, y, _, _ = panellayout.server_group(self.nav.selected, self.rtl)
            self["groupselect"].instance.move(ePoint(x - 3, y - 3)); self["groupselect"].show()
            self["groupselected"].instance.move(ePoint(x, y)); self["groupselected"].show()
            self["grouparrow"].instance.move(ePoint(x + (8 if self.rtl else 344), y + 11)); self["grouparrow"].show()
        self.draw_hint()

    def draw_top_controls(self):
        group = self.groups[self.nav.selected] if self.groups else None
        enabled = group is not None and group.get("id") != "device"
        self["groupstar"].setText((("✓ " if group and group.get("starred") else "") +
                                   i18n.t("servers.open_first", self.lang)) if enabled else "")
        self["groupmute"].setText((("✓ " if group and group.get("muted") else "") +
                                   i18n.t("servers.not_interested", self.lang)) if enabled else "")
        self["groupstar"].show() if enabled else self["groupstar"].hide()
        self["groupmute"].show() if enabled else self["groupmute"].hide()
        self.nav.set_top(panelnav.top_items(group))

    def draw_hint(self):
        text = i18n.t("torrents.controls", self.lang) % (
            i18n.t("torrents.sort." + self.torrent_sort, self.lang),
            i18n.t("torrents.filter." + self.torrent_filter, self.lang)) \
            if self.sort_filter_open and self.torrent_selected() else i18n.t("player.group_hint", self.lang)
        self["grouphint"].setText(text)
        self.draw_top_controls()

    def torrent_selected(self):
        return bool(self.groups) and self.groups[self.nav.selected]["id"] == torrents.GROUP

    def draw_servers(self):
        """The selected group's column from self.offset: the Sort & filter lines, then the server cards
        (all of them, SLOTS cards at a time); the playing one is marked."""
        servers = self.selected_servers()
        playing = self.playing[1] if self.playing and self.playing[0] == self.nav.selected else None
        placed = panellayout.column_layout(self.kinds(), self.offset, self.rtl)
        self.boxes, self.playing_line, playing_box = dict(placed), None, None
        server_slot, option_slot, header = 0, 0, False
        for line, box in placed:
            kind, value = self.lines[line]
            if kind == "header":
                header = True
                self.draw_header(box)
            elif kind in ("sort", "filter") and option_slot < panellayout.OPTION_SLOTS:
                self.draw_option(option_slot, kind, value, box)
                option_slot += 1
            elif kind == "server" and server_slot < SLOTS and value < len(servers):
                if self.draw_card(server_slot, servers[value], value == playing, box):
                    self.playing_line, playing_box = line, box
                server_slot += 1
        if not header:
            self["sortfilterbox"].hide(); self["sortfilter"].hide()
        for i in range(option_slot, panellayout.OPTION_SLOTS):
            self["sfoptbox%d" % i].hide(); self["sfopt%d" % i].hide()
        for i in range(server_slot, SLOTS):
            for kind in ("server", "quality", "serverlabel", "servermeta", "servertracks", "checked"):
                self["%s%d" % (kind, i)].hide()
        for name in ("serverplaying", "serverplayingbox", "serverplayicon"): self[name].hide()
        if playing_box is not None and self["serverplaying"].instance:
            x, y, _, _ = playing_box
            self["serverplaying"].instance.move(ePoint(x - 3, y - 3)); self["serverplaying"].show()
            self["serverplayingbox"].instance.move(ePoint(x, y)); self["serverplayingbox"].show()
            self["serverplayicon"].instance.move(ePoint(x + (panellayout.SERVER_W - 82 if self.rtl else 14),
                                                        y + (panellayout.SERVER_H - 68) // 2))
            self["serverplayicon"].show()
        self.draw_focus()

    def place_line(self, box_name, text_name, box):
        x, y, w, h = box
        for name, left, width in ((box_name, x, w), (text_name, x + 32, w - 64)):
            widget = self[name]
            if widget.instance:
                widget.instance.move(ePoint(left, y))
                widget.instance.resize(eSize(width, h))
            widget.show()

    def draw_header(self, box):
        glyph = theme.GLYPHS["open" if self.sort_filter_open else "closed"]
        self["sortfilter"].setText(i18n.t("torrents.sort_filter", self.lang) % (
            i18n.t("torrents.sort." + self.torrent_sort, self.lang),
            i18n.t("torrents.filter." + self.torrent_filter, self.lang)) + "   " + glyph)
        self.place_line("sortfilterbox", "sortfilter", box)

    def draw_option(self, slot, kind, value, box):
        chosen = value == (self.torrent_sort if kind == "sort" else self.torrent_filter)
        label = i18n.t(("torrents.sort." if kind == "sort" else "torrents.filter.") + value, self.lang)
        text = i18n.t("torrents.sort_line" if kind == "sort" else "torrents.filter_line", self.lang) % label
        self["sfopt%d" % slot].setText(("✓  " if chosen else "     ") + text)
        fill = theme.rgb(theme.SELECTED if chosen else theme.SURFACE)
        for name in ("sfoptbox%d" % slot, "sfopt%d" % slot):
            if self[name].instance:
                self[name].instance.setBackgroundColor(fill)
                self[name].instance.invalidate()   # the box repaints a colour only on invalidate
        self.place_line("sfoptbox%d" % slot, "sfopt%d" % slot, box)

    def draw_card(self, i, candidate, is_playing, box):
        """One server card in slot i at `box`; True when it is the playing one."""
        view = playernav.server_view(candidate, is_playing)
        top = box[1]
        for kind in ("server", "quality", "serverlabel", "servermeta", "servertracks", "checked"):
            self["%s%d" % (kind, i)].show()
        if self["server%d" % i].instance:
            self["server%d" % i].instance.move(ePoint(box[0], top))
        shift = 64 if view["playing"] else 0        # the red play mark sits left of the text
        fill = theme.rgb(theme.SELECTED if view["playing"] else theme.SURFACE)
        meta_width = 744 if view.get("wide") else 500       # a torrent's meta line has no ✓ checked
        for kind, left, dy, width in (("quality", 1100, 8, 720), ("serverlabel", 1100, 44, 720),
                                      ("servermeta", 1100, 96, meta_width),
                                      ("servertracks", 1100, 126, meta_width),
                                      ("checked", 1600, 126, 244)):
            widget = self["%s%d" % (kind, i)]
            if widget.instance:
                widget.instance.setBackgroundColor(fill)
                widget.instance.invalidate()   # the box repaints a colour only on invalidate
                grow = 0 if kind == "checked" else shift
                widget.instance.move(ePoint(layout.x(left + grow, width - grow, self.rtl), top + dy))
                widget.instance.resize(eSize(width - grow, widget.instance.size().height()))
        self["quality%d" % i].setText(view["quality"])
        # no quality: the hoster is already the big line, so the label line would only repeat it
        self["serverlabel%d" % i].setText("" if view["label"] == view["quality"] else view["label"])
        self["servermeta%d" % i].setText(view["meta"])
        self["servertracks%d" % i].setText(view.get("tracks") or "")
        failed_here = self.failed is not None and playernav.same_server(candidate, self.failed)
        self["checked%d" % i].setText(i18n.t("player.row_failed", self.lang) if failed_here else
                                      i18n.t("player.checked", self.lang) if view["checked"] else "")
        if self["checked%d" % i].instance:
            self["checked%d" % i].instance.setForegroundColor(theme.rgb(theme.ACCENT if failed_here
                                                                        else theme.DIM))
        return bool(view["playing"])

    def draw_focus(self):
        for name in ("navframe", "mainframe", "topframe"): self[name].hide()
        rings = panelnav.left_rings(self.nav.area, self.nav.index, self.nav.selected)
        if self.groups:                              # white replaces the accent ring on the focused selected group
            self["groupselect"].show() if rings["accent"] else self["groupselect"].hide()
        icon = "close_w.png" if self.nav.top_name == "close" else "close_n.png"
        if getattr(self, "close_icon", None) != icon and self["close"].instance:
            self["close"].instance.setPixmapFromFile(os.path.join(ICONS, icon))
            self.close_icon = icon
        if self.playing_line is not None and self["serverplaying"].instance:
            # focused playing card: the white focus ring replaces its accent ring (fill + red ▶ stay)
            on_it = self.nav.area == "main" and self.nav.index == self.playing_line
            self["serverplaying"].hide() if on_it else self["serverplaying"].show()
        if self.nav.area == "top":
            name = self.nav.top_name
            if name in ("star", "mute") and self["topframe"].instance:
                x, y, w, h = panellayout.top_button(name, self.rtl)
                self["topframe"].instance.move(ePoint(x - 3, y - 3))
                self["topframe"].instance.resize(eSize(w + 6, h + 6))
                self["topframe"].show()
            return
        if rings["focus"] is not None:
            if self["navframe"].instance:
                x, y, _, _ = panellayout.server_group(rings["focus"], self.rtl)
                self["navframe"].instance.move(ePoint(x - 3, y - 3)); self["navframe"].show()
            return
        box = self.boxes.get(self.nav.index)
        if box is None or not self["mainframe"].instance:
            return
        self["mainframe"].instance.move(ePoint(box[0] - 3, box[1] - 3))
        self["mainframe"].instance.resize(eSize(box[2] + 6, box[3] + 6))
        self["mainframe"].show()

    # --- remote ------------------------------------------------------------------------------------------
    def moved(self):
        if self.nav.area == "main":
            offset = panellayout.column_offset(self.kinds(), self.offset, self.nav.index)
            if offset != self.offset:
                self.offset = offset; self.draw_servers(); return
        self.draw_focus()

    def vertical(self, direction):
        self.nav.vertical(direction)
        self.moved()

    def horizontal(self, key):
        self.nav.horizontal(key)
        self.moved()

    def ok(self):
        if self.nav.area == "top":       # FIX26: the top row acts on the selected group, like yellow/blue
            name = self.nav.top_name
            if name == "close":
                self.close(None)
            elif name in ("star", "mute"):
                self.toggle_group(name)
            return
        if self.nav.area == "nav":       # only OK opens a group's servers (owner 2026-11-17)
            if self.nav.activate_nav():
                self.sort_filter_open = False
                self.offset = 0
                self.refresh_column(enter=True, line=0)
                self.draw_groups(); self.draw_servers()
            return
        kind, value = self.lines[self.nav.index] if self.nav.index < len(self.lines) else (None, None)
        if kind == "header":
            self.toggle_sort_filter()
        elif kind == "sort":
            self.torrent_sort = value
            self.apply_torrent_view(line=self.nav.index)
        elif kind == "filter":
            self.torrent_filter = value
            self.apply_torrent_view(line=self.nav.index)
        elif kind == "server":
            servers = self.selected_servers()
            if value < len(servers) and not servers[value].get("info"):
                self.close(servers[value])

    def toggle_sort_filter(self):
        """OK on the Sort & filter line (or RED): open/close the drop-down in place, keeping the focus."""
        if not self.torrent_selected():
            return
        server = self.focused_server() if self.nav.area == "main" else None
        self.sort_filter_open = not self.sort_filter_open
        line = None if server is not None or self.nav.area != "main" else 0
        self.refresh_column(line=line, server=server)
        self.draw_hint(); self.draw_servers()

    def yellow(self):
        self.cycle_torrent_sort() if self.sort_filter_open and self.torrent_selected() else self.toggle_group("star")

    def blue(self):
        self.cycle_torrent_filter() if self.sort_filter_open and self.torrent_selected() else self.toggle_group("mute")

    def cycle_torrent_sort(self):
        if self.torrent_selected():
            self.torrent_sort = torrents.next_sort(self.torrent_sort)
            self.apply_torrent_view()

    def cycle_torrent_filter(self):
        if self.sort_filter_open and self.torrent_selected():
            empty = self.torrent_filter != "all" and not torrents.display_rows(
                (self.torrent_state or {}).get("rows") or [], self.torrent_sort, self.torrent_filter)
            self.torrent_filter = torrents.next_filter(self.torrent_filter, empty)
            self.apply_torrent_view()

    def apply_torrent_view(self, line=None):
        """The sort/filter changed: re-sort at once; the focus stays on its line, else on its server."""
        focused = None
        server = self.focused_server()
        servers = self.selected_servers()
        if server is not None and server < len(servers):
            focused = servers[server].get("cid")
        self.source.prefs.set_torrent_view(self.torrent_sort, self.torrent_filter)
        self.groups = self.build_groups()
        servers = next((group["servers"] for group in self.groups if group["id"] == torrents.GROUP), [])
        server = next((i for i, row in enumerate(servers) if row.get("cid") == focused), 0)
        self.regroup(torrents.GROUP, line=line, server=None if line is not None else server)
        self.nav.positions["nav"] = self.nav.selected
        self.draw_groups(); self.draw_servers()

    def toggle_group(self, kind):
        if not self.groups:
            return
        group_index = self.nav.index if self.nav.area == "nav" else self.nav.selected
        group_id = self.groups[group_index]["id"]
        if group_id == "device":
            return
        save_account = self.source.toggle_server_group(group_id, kind)
        selected_id = self.groups[self.nav.selected]["id"]
        server = self.focused_server()
        self.groups = self.build_groups()
        regroup_id = group_id if self.nav.area == "nav" else selected_id
        self.regroup(regroup_id, server=server if regroup_id == selected_id else None)
        self.nav.positions["nav"] = self.nav.selected
        self.draw_groups(); self.draw_servers()
        if save_account:
            async_call.run(self.source.save_server_groups, on_done=lambda saved: None, on_error=lambda error: None)

    def sources_updated(self, sources):
        """A later poll while the panel waits for the viewer's pick (Auto off): new rows, same focus."""
        self.sources = sources
        self.torrents_updated(self.torrent_state)

    def torrents_updated(self, torrent_state):
        """The box's torrent list arrived while the panel is open: redraw with it, keeping the focus."""
        self.torrent_state = torrent_state
        group_id = self.groups[self.nav.selected]["id"] if self.groups else None
        self.groups = self.build_groups()
        self.regroup(group_id)
        self.draw_groups(); self.draw_servers()


class EpisodesPanel(Screen):
    def __init__(self, session, lang, client, posters, target):
        self.skin = skins.player_episodes_panel(lang)
        Screen.__init__(self, session)
        self.lang, self.rtl, self.client, self.posters, self.target = lang, lang == "ar", client, posters, target
        self.seasons, self.episodes = list(target.get("seasons") or []), list(target.get("episodes") or [])
        numbers = [s.get("number") for s in self.seasons]
        selected = numbers.index(target.get("season")) if target.get("season") in numbers else 0
        current = next((i for i, ep in enumerate(self.episodes) if ep.get("number") == target.get("episode")), 0)
        self.nav = panelnav.Nav(len(self.seasons), len(self.episodes), self.rtl, selected, current)
        self.offset, self.generation = max(0, current - 1), 0
        self.shown_season = target.get("season")          # the season whose episodes are on screen
        for name in ("dimleft", "panel", "ptitle", "psub", "navframe", "mainframe", "seasonselect",
                     "seasonselected"): self[name] = Label("")
        for name in ("close", "seasonarrow"): self[name] = Pixmap()
        self["ptitle"].setText(i18n.t("player.btn.episodes", lang))
        self["psub"].setText(target.get("title") or "")
        for i in range(7): self["seasonbox%d" % i], self["season%d" % i] = Label(""), Label("")
        for i in range(3):
            self["still%d" % i], self["watched%d" % i] = Pixmap(), Pixmap()
            for kind in ("epcard", "eptitle", "epdate", "epoverview", "now", "watchedbox", "ondisk"):
                self["%s%d" % (kind, i)] = Label("")
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions"], {
            "cancel": lambda: self.close(None), "ok": self.ok, "up": lambda: self.vertical(-1),
            "down": lambda: self.vertical(1), "left": lambda: self.horizontal("left"),
            "right": lambda: self.horizontal("right")}, -1)
        self.onLayoutFinish.append(self.start)

    def start(self):
        for name, asset in (("close", "close_n.png"), ("seasonarrow", "selected.png")):
            if self[name].instance: self[name].instance.setPixmapFromFile(os.path.join(ICONS, asset))
        for i in range(3):
            if self["watched%d" % i].instance:
                self["watched%d" % i].instance.setPixmapFromFile(os.path.join(ICONS, "watched.png"))
        self.draw()

    def draw(self):
        for i in range(7):
            s = self.seasons[i] if i < len(self.seasons) else None
            for kind in ("seasonbox", "season"):
                self["%s%d" % (kind, i)].show() if s else self["%s%d" % (kind, i)].hide()
            self["season%d" % i].setText((s.get("label") or "Season %s" % s.get("number")) if s else "")
            if self["season%d" % i].instance:
                self["season%d" % i].instance.setBackgroundColor(theme.rgb(theme.SURFACE))
                self["season%d" % i].instance.invalidate()   # the box repaints a colour only on invalidate
        season = self.shown_season
        disk = self.disk_numbers(season)
        views = playernav.episode_views(self.episodes, season, self.target.get("episode")
                                           if season == self.target.get("season") else None)
        for slot in range(3):
            index, view = self.offset + slot, None
            if index < len(views): view = views[index]
            for kind in ("epcard", "eptitle", "epdate", "epoverview", "now", "watchedbox", "watched", "ondisk"):
                self["%s%d" % (kind, slot)].show() if view else self["%s%d" % (kind, slot)].hide()
            if not view: self["still%d" % slot].hide(); continue
            self["eptitle%d" % slot].setText(view["label"]); self["epdate%d" % slot].setText(view["air_date"])
            self["epoverview%d" % slot].setText(view["overview"])
            self["now%d" % slot].setText(i18n.t("player.now_playing", self.lang) if view["now_playing"] else "")
            self["now%d" % slot].show() if view["now_playing"] else self["now%d" % slot].hide()
            self["watched%d" % slot].show() if view["watched"] else self["watched%d" % slot].hide()
            on_disk = view["number"] in disk                                 # FIX31: as on the title page
            self["ondisk%d" % slot].setText(i18n.t("episode.on_disk", self.lang) if on_disk else "")
            self["ondisk%d" % slot].show() if on_disk else self["ondisk%d" % slot].hide()
            self.load_still(slot, view)
        self.draw_focus()

    def disk_numbers(self, season):
        """The shown season's episodes with a playable copy on the box's disk (never fails the panel)."""
        try:
            return episodeslayout.disk_numbers(downloadmanager.manager().find, self.target.get("kind") or "tv",
                                               self.target.get("id"), season, self.episodes)
        except Exception as error:  # noqa: BLE001 - no disk / a broken store: no marks, the panel still opens
            print("[Kanasa] episodes panel: downloads not read: %r" % (error,))
            return set()

    def load_still(self, slot, view):
        widget, url = self["still%d" % slot], view.get("still")
        widget.hide(); widget.kanasa_url = url
        def done(path):
            if path and widget.instance and getattr(widget, "kanasa_url", None) == url:
                widget.instance.setPixmapFromFile(path); widget.show()
        async_call.run(self.posters.get, url or "", (300, 169), theme.RADIUS_CARD, None,
                       view["released"], view["percent"], on_done=done, on_error=lambda e: None)

    def vertical(self, direction):
        self.nav.vertical(direction)
        if self.nav.area == "main":
            offset = layout.keep_visible(self.offset, self.nav.index, len(self.episodes), 3)
            if offset != self.offset:
                self.offset = offset; self.draw(); return
        self.draw_focus()

    def horizontal(self, key):
        self.nav.horizontal(key)
        if self.nav.area == "main":
            offset = layout.keep_visible(self.offset, self.nav.index, len(self.episodes), 3)
            if offset != self.offset:
                self.offset = offset; self.draw(); return
        self.draw_focus()

    def load_season(self):
        if not self.seasons: return
        self.generation += 1; generation = self.generation
        number = self.seasons[self.nav.selected].get("number")
        async_call.run(self.client.season, int(self.target.get("id")), int(number),
                       on_done=lambda rows: self.loaded(rows, generation), on_error=lambda e: None)

    def loaded(self, rows, generation):
        if generation == self.generation:
            self.episodes, self.offset = list(rows), 0
            self.shown_season = self.seasons[self.nav.selected].get("number")
            self.nav.set_main(len(rows), enter=True)
            self.draw()

    def ok(self):
        if self.nav.area == "nav":       # only OK changes the right column (owner 2026-11-17)
            if self.nav.activate_nav(): self.draw_focus(); self.load_season()
            return
        if self.nav.index >= len(self.episodes): return
        ep = self.episodes[self.nav.index]
        if not ep.get("released", True): return
        season = self.shown_season
        if season is None: return
        self.target["episodes"] = list(self.episodes)     # the title page plays it from this season's list
        self.close((int(season), int(ep.get("number"))))

    def draw_focus(self):
        self["navframe"].hide(); self["mainframe"].hide()
        self["seasonselect"].hide(); self["seasonselected"].hide(); self["seasonarrow"].hide()
        if self.seasons and self["seasonselect"].instance:
            x, y, _, _ = panellayout.episode_season(self.nav.selected, self.rtl)
            for name in ("seasonselect", "seasonselected"):
                self[name].instance.move(ePoint(x - 3 if name == "seasonselect" else x,
                                                y - 3 if name == "seasonselect" else y)); self[name].show()
            self["seasonarrow"].instance.move(ePoint(x + (8 if self.rtl else 224), y + 11)); self["seasonarrow"].show()
            widget = self["season%d" % self.nav.selected]
            if widget.instance: widget.instance.setBackgroundColor(theme.rgb(theme.SELECTED)); widget.instance.invalidate()
        if self.nav.area == "nav" and self["navframe"].instance:
            x, y, _, _ = panellayout.episode_season(self.nav.index, self.rtl)
            self["navframe"].instance.move(ePoint(x - 3, y - 3))
            self["navframe"].show()
        elif self["mainframe"].instance:
            slot = self.nav.index - self.offset; x, y, _, _ = panellayout.panel_episode(slot, self.rtl)
            self["mainframe"].instance.move(ePoint(x - 3, y - 3)); self["mainframe"].show()
