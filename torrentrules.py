"""Pure rules for the on-box torrent engine, TorrServer (no Enigma2 imports, no I/O).

Measured on the owner's Vu+ Zero 4K (armv7l, 2026-11-17): TorrServer MatriX.145.2 linux-arm7 runs with
`--port 8090 --path <dir>`, /echo answers the version, idle RSS ~50 MB, streams ~24 Mbit/s.
"""
import posixpath
import re
from urllib.parse import quote

VERSION = "MatriX.145.2"
RELEASE_URL = "https://github.com/YouROK/TorrServer/releases/download/%s/" % VERSION
PORT = 8090
BINARY = "TorrServer"
DIR_NAME = "kanasa-torrent"
ROOT_DIR = "/usr/share/" + DIR_NAME
MIN_FREE = 300 * 1024 * 1024
PROMPT_AGAIN_S = 24 * 60 * 60
START_S, METADATA_S = 15, 30
CACHE_BYTES, PRELOAD_PERCENT = 128 * 1024 * 1024, 25        # preload 25 % of 128 MB = 32 MB

# Pinned per asset: (size in bytes, sha256), computed 2026-11-17 from the GitHub release assets.
ASSETS = {
    "TorrServer-linux-arm7": (61997216, "04a002c956417f24b6d8544f927c132423a06c706d1f05d760cbe9ebcba4e014"),
    "TorrServer-linux-arm64": (60752032, "7447ea61d806890d3a890f428ead86a3221128484f50ed03cbb1a6c6efac171e"),
    "TorrServer-linux-mipsle": (65798335, "36920b67db2f53cc069e65f2255bf53f4707f3782d4dadea14dc019dc3a93bf7"),
}
_ARCH = {"armv7l": "TorrServer-linux-arm7", "armv7": "TorrServer-linux-arm7", "armv7a": "TorrServer-linux-arm7",
         "aarch64": "TorrServer-linux-arm64", "arm64": "TorrServer-linux-arm64",
         "mips": "TorrServer-linux-mipsle", "mipsel": "TorrServer-linux-mipsle", "mipsle": "TorrServer-linux-mipsle"}
VIDEO_EXT = (".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".mpg", ".mpeg", ".webm")

# The app's pinned safety values (TorrServerClient.pinnedSettings), with the box's smaller cache.
PINNED = {"CacheSize": CACHE_BYTES, "UseDisk": False, "EnableDLNA": False, "ConnectionsLimit": 25,
          "PreloadCache": PRELOAD_PERCENT, "ReaderReadAHead": 95, "TorrentDisconnectTimeout": 30}
BUFFER_PRELOAD = {0: 0, 1: 10, 4: PRELOAD_PERCENT, 10: 75}


def asset_for(machine):
    """The release asset for `uname -m`, or None (this box cannot run the engine)."""
    return _ARCH.get(str(machine or "").strip().lower())


def asset_url(asset: str) -> str:
    return RELEASE_URL + asset


def install_roots(media_names) -> list:
    """Where to look, best first: the HDD, then USB sticks, then the flash (/usr/share)."""
    names = sorted(n for n in media_names or [] if isinstance(n, str))
    roots = ["/media/hdd"] if "hdd" in names else []
    roots += ["/media/" + n for n in names if n.startswith("usb")]
    return roots


def choose_install_dir(disks, root_free):
    """disks: [{"path", "mounted", "writable", "free"}] in install_roots order. The first mounted,
    writable disk with MIN_FREE free; else the flash when / has MIN_FREE free; else None (refuse)."""
    for disk in disks or []:
        if disk.get("mounted") and disk.get("writable") and not disk.get("noexec") \
                and (disk.get("free") or 0) >= MIN_FREE:
            return posixpath.join(disk["path"], DIR_NAME)
    if isinstance(root_free, int) and root_free >= MIN_FREE:
        return ROOT_DIR
    return None


def should_prompt(installed, asset, directory, deferred_at, now, update_popup=False) -> bool:
    """Home prompt rule: only an eligible box, never over an update popup, at most once per 24 h."""
    if installed or not asset or not directory or update_popup:
        return False
    deferred = deferred_at if isinstance(deferred_at, int) and not isinstance(deferred_at, bool) else 0
    current = now if isinstance(now, int) and not isinstance(now, bool) else 0
    return deferred <= 0 or current - deferred >= PROMPT_AGAIN_S


def binary_path(directory: str) -> str:
    return posixpath.join(directory, BINARY)


def command(directory: str, port: int = PORT) -> str:
    return "%s --port %d --path %s" % (binary_path(directory), port, directory)


def is_echo(body) -> bool:
    """An engine answers /echo with its version. Any MatriX engine on the port is reused."""
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    return isinstance(body, str) and body.strip().startswith("MatriX")


def settings_patch(current, minutes=4) -> dict:
    """Get-modify-set: every key the engine returned goes back unchanged except the pins."""
    patched = dict(current) if isinstance(current, dict) else {}
    patched.update(PINNED)
    patched["PreloadCache"] = BUFFER_PRELOAD.get(minutes, PRELOAD_PERCENT)
    return patched


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def parse_status(data) -> dict:
    """POST /torrents {"action":"get"} -> files, peers, speed and cache/preload bytes."""
    data = data if isinstance(data, dict) else {}
    files = []
    for entry in data.get("file_stats") or []:
        if isinstance(entry, dict) and isinstance(entry.get("id"), int) and isinstance(entry.get("path"), str):
            files.append({"id": entry["id"], "path": entry["path"], "length": _number(entry.get("length")) or 0})
    peers = _number(data.get("active_peers"))
    speed = _number(data.get("download_speed"))
    preloaded = next((_number(data.get(key)) for key in
                      ("preloaded_bytes", "preload_bytes")
                      if _number(data.get(key)) is not None), None)
    return {"files": files, "peers": int(peers) if peers is not None else None,
            "speed": int(speed) if speed is not None else None,
            "preloaded": int(preloaded) if preloaded is not None else None,
            "stat": data.get("stat_string") or ""}


def basename(path: str) -> str:
    return re.split(r"[\\/]", path or "")[-1]


def is_video(path: str) -> bool:
    return basename(path).lower().endswith(VIDEO_EXT)


def match_file(files, filename, episode=None):
    """The engine's file id for the release's file. TorrServer ids are its own (1-based, its order):
    torrentio file_idx 6 was TorrServer id 2 for Interstellar. Match the file NAME (basename, case
    insensitive); then (FIX19, a resume row built from its key has no name) the one video named for
    `episode` = (season, number); else the largest video file. None = nothing playable."""
    files = [f for f in files or [] if isinstance(f, dict)]
    wanted = basename(filename).lower() if isinstance(filename, str) and filename.strip() else ""
    if wanted:
        for entry in files:
            if basename(entry.get("path")).lower() == wanted:
                return entry
    videos = [f for f in files if is_video(f.get("path"))]
    if episode and len(episode) == 2 and all(isinstance(n, int) for n in episode):
        mark = re.compile(r"(?:s0*%d[ ._-]*e0*%d|(?<![0-9])%dx0*%d)(?![0-9])" % (episode * 2), re.I)
        named = [f for f in videos if mark.search(basename(f.get("path")))]
        if len(named) == 1:
            return named[0]
    return max(videos, key=lambda f: f.get("length") or 0) if videos else None


def stream_url(info_hash: str, file_entry: dict, port: int = PORT) -> str:
    name = quote(basename(file_entry.get("path")) or "video", safe="")
    return "http://127.0.0.1:%d/stream/%s?link=%s&index=%d&play" % (port, name, info_hash, file_entry["id"])


def speed_text(speed) -> str:
    if not isinstance(speed, int) or speed <= 0:
        return ""
    mbit = speed * 8 / 1e6
    return ("%.1f Mbit/s" % mbit) if mbit < 10 else ("%d Mbit/s" % round(mbit))
