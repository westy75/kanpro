"""Viewer settings kept on the box (no Enigma2 imports)."""
import json
import os
import re

from . import i18n

DEFAULT_PATH = "/etc/enigma2/kanasa_prefs.json"
LANGS = ("auto", "ar", "en")             # auto = follow the box language
_GROUP_ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_GROUPS = 20
SUBTITLE_SIZES = ("s", "m", "l")


def clean_groups(value) -> list:
    result = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, str) and _GROUP_ID.match(item) and item not in result:
            result.append(item)
    return result[:MAX_GROUPS]


def clean_audio_choice(value):
    if not isinstance(value, dict):
        return None
    lang = str(value.get("lang") or "").strip().lower()
    codec = str(value.get("codec") or "").strip()
    if not re.match(r"^[a-z]{2,3}$", lang) or len(codec) > 32:
        return None
    return {"lang": lang, "codec": codec}


class Prefs:
    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def lang(self) -> str:
        value = self._load().get("lang")
        return value if value in LANGS else "auto"

    def set_lang(self, value: str) -> None:
        if value not in LANGS:
            raise ValueError("unknown language")
        self._set("lang", value)

    def stop_live_tv(self) -> bool:
        """Stop live TV while KanasaStream is open (owner 2026-11-17: default ON)."""
        value = self._load().get("stop_live_tv")
        return value if isinstance(value, bool) else True

    def set_stop_live_tv(self, value: bool) -> None:
        self._set("stop_live_tv", bool(value))

    def main_menu(self) -> bool:
        """KanasaStream in the image's main menu (owner 2026-11-23: default ON)."""
        value = self._load().get("main_menu")
        return value if isinstance(value, bool) else True

    def set_main_menu(self, value: bool) -> None:
        self._set("main_menu", bool(value))

    def hls_height(self):
        """The HLS quality to start at: the last one the viewer picked in the Quality panel, else None = the
        highest (owner 2026-11-23: keep the highest by default; the Quality button offers the others)."""
        value = self._load().get("hls_height")
        return value if isinstance(value, int) and not isinstance(value, bool) and 144 <= value <= 4320 else None

    def set_hls_height(self, value: int) -> None:
        if isinstance(value, int) and not isinstance(value, bool) and 144 <= value <= 4320:
            self._set("hls_height", value)

    def buffer_minutes(self) -> int:
        from . import bufferrules
        return bufferrules.clean(self._load().get("buffer_minutes"))

    def set_buffer_minutes(self, value: int) -> None:
        from . import bufferrules
        self._set("buffer_minutes", bufferrules.clean(value))

    def downloads_button(self) -> str:
        value = self._load().get("downloads_button")
        return value if value in ("red", "green", "yellow", "blue", "off") else "red"

    def set_downloads_button(self, value: str) -> None:
        if value not in ("red", "green", "yellow", "blue", "off"):
            raise ValueError("unknown colour key")
        self._set("downloads_button", value)

    def download_folder(self) -> str:
        """"auto" or the mount point the viewer picked in Settings → Download folder."""
        value = self._load().get("download_folder")
        return value if isinstance(value, str) and (value == "auto" or value.startswith("/")) else "auto"

    def set_download_folder(self, value: str) -> None:
        if not isinstance(value, str) or not (value == "auto" or value.startswith("/")):
            raise ValueError("bad folder")
        self._set("download_folder", value)

    def simultaneous_downloads(self) -> int:
        value = self._load().get("simultaneous_downloads")
        return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 4 else 1

    def set_simultaneous_downloads(self, value: int) -> None:
        self._set("simultaneous_downloads", max(1, min(4, int(value))))

    def update_checked_at(self) -> int:
        value = self._load().get("update_checked_at")
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    def set_update_checked_at(self, value: int) -> None:
        self._set("update_checked_at", max(0, int(value)))

    def skipped_update(self):
        value = self._load().get("skipped_update")
        return value if isinstance(value, str) else None

    def set_skipped_update(self, value: str) -> None:
        self._set("skipped_update", value)

    def forced_update(self):
        """A mandatory release this box was shown and has not installed yet (version), else None."""
        value = self._load().get("forced_update")
        return value if isinstance(value, str) else None

    def set_forced_update(self, value) -> None:
        self._set("forced_update", value if isinstance(value, str) else None)

    def engine_prompt_deferred_at(self) -> int:
        value = self._load().get("engine_prompt_deferred_at")
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    def set_engine_prompt_deferred_at(self, value: int) -> None:
        self._set("engine_prompt_deferred_at", max(0, int(value)))

    def server_group_order(self) -> list:
        return clean_groups(self._load().get("server_group_order"))

    def server_group_muted(self) -> list:
        return clean_groups(self._load().get("server_group_muted"))

    def set_server_groups(self, order, muted) -> None:
        order = clean_groups(order)
        muted = [group for group in clean_groups(muted) if group not in order]
        data = self._load()
        data["server_group_order"], data["server_group_muted"] = order, muted
        self._write(data)

    def server_group_rank(self) -> list:
        return clean_groups(self._load().get("server_group_rank"))

    def server_autoplay(self) -> bool:
        value = self._load().get("server_autoplay")
        return value if isinstance(value, bool) else True

    def set_server_settings(self, rank, autoplay) -> None:
        data = self._load()
        data["server_group_rank"] = clean_groups(rank)
        data["server_autoplay"] = bool(autoplay)
        self._write(data)

    def torrent_view(self) -> tuple:
        from . import torrents
        data = self._load()
        return torrents.clean_sort(data.get("torrent_sort")), torrents.clean_filter(data.get("torrent_filter"))

    def set_torrent_view(self, sort, quality_filter) -> None:
        from . import torrents
        data = self._load()
        data["torrent_sort"] = torrents.clean_sort(sort)
        data["torrent_filter"] = torrents.clean_filter(quality_filter)
        self._write(data)

    def subtitle_prefs(self) -> dict:
        from . import subs
        return subs.clean_prefs(self._load().get("subtitles"))

    def set_subtitle_prefs(self, value) -> None:
        from . import subs
        data = self._load()
        data["subtitles"] = subs.clean_prefs(value)
        self._write(data)

    def subtitle_choice(self, key: str):
        from . import subs
        choices = self._load().get("subtitle_choices")
        return subs.clean_choice(choices.get(key), key) if isinstance(choices, dict) else None

    def set_subtitle_choice(self, key: str, value) -> None:
        from . import subs
        choice = subs.clean_choice(value, key)
        if choice is None:
            return
        data = self._load()
        choices = data.get("subtitle_choices")
        choices = dict(choices) if isinstance(choices, dict) else {}
        choices[key] = choice
        # Keep this bounded on boxes that have been used for years.
        if len(choices) >= 200:
            choices = dict(list(choices.items())[-199:])
            choices[key] = choice
        data["subtitle_choices"] = choices
        self._write(data)

    def audio_choice(self, key: str):
        """The audio language picked for this title in Kanasa exteplayer3 mode ({"lang", "codec"}), or None."""
        choices = self._load().get("audio_choices")
        value = choices.get(key) if isinstance(choices, dict) and isinstance(key, str) else None
        return clean_audio_choice(value)

    def set_audio_choice(self, key: str, value) -> None:
        choice = clean_audio_choice(value)
        if choice is None or not isinstance(key, str) or not key:
            return
        data = self._load()
        choices = data.get("audio_choices")
        choices = dict(choices) if isinstance(choices, dict) else {}
        choices.pop(key, None)
        choices[key] = choice
        if len(choices) > 200:                   # bounded like subtitle_choices: the oldest go
            choices = dict(list(choices.items())[-200:])
        data["audio_choices"] = choices
        self._write(data)

    def last_profile(self):
        """084: the profile last picked on this box (pre-focused when the server has no `current`)."""
        value = self._load().get("last_profile")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def set_last_profile(self, value) -> None:
        self._set("last_profile", value if isinstance(value, int) and not isinstance(value, bool) else None)

    def aspect_mode(self) -> str:
        """The player's aspect ratio (aspect.MODES), one choice for the whole box; anything else = fit."""
        from . import aspect
        return aspect.clean(self._load().get("aspect"))

    def set_aspect_mode(self, value) -> None:
        from . import aspect
        self._set("aspect", aspect.clean(value))

    def _set(self, key: str, value) -> None:
        data = self._load()
        data[key] = value
        self._write(data)

    def _write(self, data: dict) -> None:
        data.pop("player", None)        # FIX32: Settings → Player is gone (Kanasa exteplayer3 only); old value dropped
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(tmp, self.path)


DOWNLOAD_KEYS = ("red", "green", "yellow", "blue", "off")


def next_downloads_key(value: str) -> str:
    return DOWNLOAD_KEYS[(DOWNLOAD_KEYS.index(value) + 1) % len(DOWNLOAD_KEYS)] if value in DOWNLOAD_KEYS \
        else DOWNLOAD_KEYS[0]


def downloads_key(choice: str, used=()) -> str:
    """The colour key that opens Downloads on a screen, or None: "off", or the screen already uses it."""
    return choice if choice in DOWNLOAD_KEYS[:4] and choice not in (used or ()) else None


def next_lang(value: str) -> str:
    return LANGS[(LANGS.index(value) + 1) % len(LANGS)] if value in LANGS else LANGS[0]


def resolve_lang(choice: str, box_lang: str) -> str:
    return choice if choice in ("ar", "en") else i18n.lang_from_box(box_lang)
