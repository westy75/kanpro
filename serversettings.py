"""Settings → Servers (066): Auto switch, group rows with ▲ ▼ ★ ⊘, Reset (no Enigma2 imports).

One row per group in the effective order (failover.effective_order). ▲/▼ move a row one step inside its
band only — the starred row, the normal rows, the muted rows — and write the rank as the shown order with
that row moved. ★ and ⊘ are the player panel's exclusive star / "Not interested" toggles."""
from . import failover

BUTTONS = ("up", "down", "star", "mute")


def groups(star=None, rank=None, muted=None) -> list:
    """Every group the page lists: the effective order (the defaults plus any ranked/starred/muted id)."""
    return failover.effective_order(star, rank, muted)


def band(group_id, star=None, muted=None) -> int:
    star = [star] if isinstance(star, str) else list(star or [])
    if group_id in (muted or []):
        return 2
    return 0 if group_id in star[:1] else 1


def rows(star=None, rank=None, muted=None) -> list:
    """[{"id", "starred", "muted", "up", "down"}]; up/down False on a band's first/last row."""
    shown = groups(star, rank, muted)
    out = []
    for index, group_id in enumerate(shown):
        mine = band(group_id, star, muted)
        up = index > 0 and band(shown[index - 1], star, muted) == mine
        down = index + 1 < len(shown) and band(shown[index + 1], star, muted) == mine
        out.append({"id": group_id, "starred": mine == 0, "muted": mine == 2, "up": up, "down": down})
    return out


def move(group_id, step, star=None, rank=None, muted=None):
    """The new rank (the shown order with that row moved one step), or None when the move is disabled."""
    shown = groups(star, rank, muted)
    if group_id not in shown:
        return None
    index = shown.index(group_id)
    row = rows(star, rank, muted)[index]
    if (step < 0 and not row["up"]) or (step > 0 and not row["down"]):
        return None
    other = index + (1 if step > 0 else -1)
    shown[index], shown[other] = shown[other], shown[index]
    return shown


def toggle(group_id, kind, star=None, muted=None):
    """(star list, muted list) after ★ ("star") or ⊘ ("mute") on a row: exclusive, as in the panel (063)."""
    order = [star] if isinstance(star, str) else list(star or [])
    if kind == "star":
        return failover.group_preferences(order, muted, star=group_id)
    return failover.group_preferences(order, muted, mute=group_id)


def reset():
    """Reset to default: no star, no mutes, default rank, Auto mode on."""
    return {"server_group_order": [], "server_group_muted": [], "server_group_rank": [], "server_autoplay": True}


def resume_focus(groups_list, resume_hoster):
    """(group index, server index) of the saved server in the panel groups (Auto off: focused, not started)."""
    wanted = failover.hoster_key(resume_hoster)
    if not wanted:
        return None
    for g, group in enumerate(groups_list or []):
        for s, candidate in enumerate(group.get("servers") or []):
            if failover.hoster_key(candidate.get("hoster")) == wanted or \
                    (candidate.get("cid") or "").lower() == wanted:
                return g, s
    return None
