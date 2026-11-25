"""From a title to the player and back: the resume question, the saved server first, Up next and the player's
Episodes pick. The title page's Play button and Home's Continue watching row (FIX19) share it; no Enigma2
imports at module level (the Player and the Choice popup are imported when they open)."""
from . import async_call, downloadmanager, downloadpick, playback, playerguard


def continue_action(item, row_id):
    """FIX19: what OK on a Home card does. "play" for a film or series in the Continue watching row (row id
    "continue" from /api/catalog/home: the website's cards there link to play_path, not the title page);
    None = open the title page as before (other rows, site titles, downloads, a watched film, a card
    without its progress)."""
    if row_id != "continue" or not isinstance(item, dict) or item.get("kind") not in ("movie", "tv"):
        return None
    progress = item.get("progress")
    if not isinstance(progress, dict):
        return None
    if item.get("kind") == "movie":
        if progress.get("watched") or playback.finished(progress.get("position") or 0, progress.get("duration")):
            return None                          # a watched film is not resumed: its title page
        return "play"
    season, episode = progress.get("season"), progress.get("episode")
    if not isinstance(season, int) or not isinstance(episode, int) or isinstance(season, bool) \
            or isinstance(episode, bool):
        return None
    return "play"


def episode_watched(item) -> bool:
    progress = item.get("progress") if isinstance(item.get("progress"), dict) else {}
    return bool(progress.get("watched")) or playback.finished(progress.get("position") or 0, progress.get("duration"))


class PlayFlow(object):
    """One title's plays. `detail` = client.title()'s answer (seasons, the account's progress);
    on_closed(answer) runs after every player close (the title page redraws, Home reloads its rows)."""

    def __init__(self, session, lang, client, source, posters, item, detail=None, on_closed=None):
        self.session, self.lang, self.client, self.source, self.posters = session, lang, client, source, posters
        self.item, self.detail, self.on_closed, self.playing = item, detail, on_closed, None
        self.on_started = None                   # Home (FIX19): the player opens, its loading text goes

    def open_player(self, season, episode, episodes, ask_resume=True, fetched=False, resume_now=False):
        """resume_now (FIX19, Continue watching): no question, the saved point at once (0 when there is none)."""
        if (ask_resume or resume_now) and not fetched and season is not None and self.source.signed_in() and \
                playback.needs_episode_rows(episodes, episode) and self.item.get("kind") != "movie":
            # a fresh box has no local point: the account's progress comes with the season's rows
            async_call.run(self.client.season, int(self.item.get("id")), int(season),
                           on_done=lambda rows: self.open_player(season, episode, list(rows), ask_resume, True,
                                                                 resume_now),
                           on_error=lambda error: self.open_player(season, episode, episodes, ask_resume, True,
                                                                   resume_now))
            return
        saved = self.source.resume(self.item, self.detail, season, episode, episodes)
        offer = playback.resume_offer(saved) if (ask_resume or resume_now) else None
        target = {"kind": self.item.get("kind"), "id": self.item.get("id"), "title": self.item.get("title"),
                  "season": season, "episode": episode, "start_at": 0, "episodes": list(episodes),
                  "seasons": list((self.detail or {}).get("seasons") or []),
                  "_posters": self.posters,
                  # the server saved with the progress is tried first (website resumeServer)
                  "resume_hoster": (saved or {}).get("h") or None}
        if season is not None:
            nxt = playback.next_episode(target["seasons"], episodes, season, episode)
            target["has_next"], target["next"] = nxt is not None, nxt
        if resume_now:
            self.start(dict(target, start_at=offer or 0))
            return
        rows = playback.resume_rows(offer, self.lang)
        if not rows:
            self.start(target)
            return
        # like the app/site: "Resume from MM:SS" (focused, the default) or "Play from start"; both try the
        # saved server first (resume_hoster)
        from .choice import Choice
        self.session.openWithCallback(lambda picked: picked and self.start(
            dict(target, start_at=offer) if picked[1] == "resume" else target), Choice, self.lang,
            self.item.get("title") or "", rows)

    def start(self, target):
        if playerguard.busy():
            # FIX33: a second OK while a Player is open/opening (or a late async answer): no second Player
            playerguard.log("a Player is already open: %s %s not started" % (target.get("kind"), target.get("id")))
            return
        self.playing = target
        if self.on_started is not None:
            self.on_started()
        # FIX9: a downloaded (or progressively downloading) copy plays first, from the box's disk
        target = downloadpick.with_local(target, downloadmanager.manager().find(
            target.get("kind"), target.get("id"), target.get("season"), target.get("episode")))
        playerguard.open_player(self.session, self.player_closed, self.lang, self.client, self.source, target)

    def player_closed(self, answer=None):
        target = self.playing
        if target:
            self.refresh_resume(target)
        if self.on_closed is not None:
            self.on_closed(answer)
        if isinstance(answer, tuple) and answer[0] == "play":
            self.open_player(answer[1], answer[2], target.get("episodes") or []); return
        if answer == "next" and target and target.get("next"):
            self.open_player(target["next"][0], target["next"][1], target.get("episodes") or [], False)

    def refresh_resume(self, target):
        """Refresh the title and cached episode row from the same source used by the resume question."""
        season, episode = target.get("season"), target.get("episode")
        rows = target.get("episodes") or []
        saved = self.source.resume(self.item, self.detail, season, episode, rows)
        playback.refresh_resume_state(self.item, self.detail, target, saved)

    # --- Home's Continue watching row (FIX19) ----------------------------------------------------------
    def play_continue(self, on_fallback):
        """Load the title's detail, then play the card's episode/film like the title page's Resume (the saved
        point, the saved server first) without its page or question. A watched episode plays the next one;
        nothing next, or the title does not load: on_fallback() (Home opens the title page)."""
        def loaded(detail):
            self.detail = detail
            self.item.update({k: v for k, v in (detail.get("item") or {}).items()
                              if v is not None and k != "progress"})
            if self.item.get("kind") == "movie":
                self.open_player(None, None, [], resume_now=True)
                return
            progress = self.item["progress"]
            season, episode = progress["season"], progress["episode"]
            if not episode_watched(self.item):
                self.open_player(season, episode, [], resume_now=True)
                return
            async_call.run(self.client.season, int(self.item.get("id")), int(season),
                           on_done=lambda rows: self.play_after(season, episode, list(rows), on_fallback),
                           on_error=lambda error: on_fallback())
        async_call.run(self.client.title, self.item.get("kind"), int(self.item.get("id")),
                       on_done=loaded, on_error=lambda error: on_fallback())

    def play_after(self, season, episode, rows, on_fallback):
        """A watched episode: the next one from its start, like the title page's Up next (no question)."""
        nxt = playback.next_episode((self.detail or {}).get("seasons") or [], rows, season, episode)
        if nxt is None:
            on_fallback()
            return
        self.open_player(nxt[0], nxt[1], rows if nxt[0] == season else [], False)
