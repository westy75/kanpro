"""Title page keys: a row of buttons, then (series) the episode list (no Enigma2 imports)."""
from . import layout


class Nav:
    def __init__(self, series: bool):
        self.series, self.area, self.button = series, "buttons", 0

    def buttons(self) -> list:
        return ["play", "episodes", "mylist"] if self.series else ["play", "mylist"]

    def key(self, name: str, rtl: bool, at_top: bool):
        """An action for the screen to perform, or None when only the focus moved."""
        if self.area == "episodes":
            return self._episodes(name, rtl, at_top)
        if name in ("left", "right"):
            d = layout.step(1 if name == "right" else -1, rtl)
            self.button = max(0, min(len(self.buttons()) - 1, self.button + d))
            return None
        if name == "down" and self.series:
            self.area = "episodes"
            return None
        if name == "ok":
            action = self.buttons()[self.button]
            if action == "episodes":
                self.area = "episodes"
                return None
            return action
        return None

    def _episodes(self, name: str, rtl: bool, at_top: bool):
        if name in ("left", "right"):
            return "season+1" if layout.step(1 if name == "right" else -1, rtl) > 0 else "season-1"
        if name == "up":
            if at_top:
                self.area = "buttons"
                return None
            return "episode-up"
        if name == "down":
            return "episode-down"
        if name == "ok":
            return "play-episode"
        return None
