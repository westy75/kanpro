"""The lines our own exteplayer3 reads on stdin (phase 1). Pure: each function returns the exact lines.

e2iplayer's rule (iptvextmovieplayer.py:2429-2438): every command except l j i o n t s goes after a `c`,
because exteplayer3 must be in PLAY state; switching a subtitle track needs PLAY as well.
Measured on the box: `gf<s>` = absolute seek (PLAYBACK_SEEK_ABS), `kc<s>` = relative (PLAYBACK_SEEK),
`s<id>` / `a<id>` take the track id from s_l / a_l, `s-1` turns subtitles off, `q` stops within 3 s.
"""


def _seconds(value) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def _ident(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("track id must be an int")
    return value


def pause() -> list:
    return ["p"]


def resume() -> list:
    return ["c"]


def seek_abs(seconds) -> list:
    return ["c", "gf%d" % max(0, _seconds(seconds))]


def seek_rel(seconds) -> list:
    return ["c", "kc%d" % _seconds(seconds)]


def audio(track_id) -> list:
    return ["c", "a%d" % _ident(track_id), "ac"]


def sub(track_id) -> list:
    return ["c", "s%d" % _ident(track_id), "sc"]


def sub_off() -> list:
    return ["s-1", "sc"]


def poll() -> list:
    return ["l", "j"]


def position() -> list:
    return ["j"]


def info() -> list:
    return ["i"]


def quit() -> list:  # noqa: A001 - the name of exteplayer3's command
    return ["q"]


def encode(lines) -> str:
    """What is written to stdin: one command per line."""
    return "".join("%s\n" % line for line in lines)
