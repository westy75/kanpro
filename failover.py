"""The order servers are tried in, like the website's player (no Enigma2 imports)."""


def hoster_key(value) -> str:
    """The website's KanasaPlayer.hosterKey: lower case, no leading www., at most 100 characters."""
    key = value.strip().lower() if isinstance(value, str) else ""
    return (key[4:] if key.startswith("www.") else key)[:100]


def group_preferences(order=None, muted=None, star=None, mute=None):
    """Apply one panel toggle. A star and a mute can never name the same group."""
    order = list(dict.fromkeys(order or []))
    muted = list(dict.fromkeys(muted or []))
    if star is not None:
        if star in order:
            order = [item for item in order if item != star]
        else:
            order = [star]
            muted = [item for item in muted if item != star]
    if mute is not None:
        if mute in muted:
            muted = [item for item in muted if item != mute]
        else:
            muted.append(mute)
            order = [item for item in order if item != mute]
    return order, muted


DEFAULT_GROUPS = ("arabic_subs", "no_subs", "french", "torrents")   # = the site's catalog SECTION_ORDER


def effective_order(star=None, rank=None, muted=None, default=DEFAULT_GROUPS) -> list:
    """066 contract (= accounts/user_settings.effective_order): the starred group (unless muted), then the
    viewer's rank, then groups the rank leaves out in default order; muted groups last, in that same order."""
    star = [g for g in ([star] if isinstance(star, str) else list(star or [])) if isinstance(g, str)][:1]
    rank = [g for g in rank or [] if isinstance(g, str)]
    muted = [g for g in muted or [] if isinstance(g, str)]
    base = []
    for group in rank + list(default) + muted:
        if group not in base:
            base.append(group)
    head = [group for group in star if group not in muted]
    return head + [g for g in base if g not in muted and g not in head] + [g for g in base if g in muted]


def group_index(order_list, group_id) -> int:
    """Position of a group in an effective order; groups it does not know go after them (stable sorts)."""
    return order_list.index(group_id) if group_id in order_list else len(order_list)


def ranked(sources, star=None, rank=None, muted=None) -> dict:
    """A copy of a sources answer with its sections in the viewer's effective order (visitors on the box:
    the site cannot know the box's rank). The candidates keep their order inside each section."""
    if not isinstance(sources, dict) or not isinstance(sources.get("sections"), list):
        return sources
    wanted = effective_order(star, rank, muted)
    sections = sorted(sources["sections"], key=lambda s: group_index(wanted, s.get("id") if isinstance(s, dict)
                                                                     else None))
    return dict(sources, sections=sections)


def order(sources: dict, starred=None, resume_hoster=None, muted=None) -> list:
    muted = set(muted or [])
    candidates = [c for c in (sources.get("candidates") or [])
                  if isinstance(c, dict) and c.get("check") == "ok" and c.get("section") not in muted]
    section_ids = [s.get("id") for s in (sources.get("sections") or [])
                   if isinstance(s, dict) and s.get("id") not in muted]
    if starred in section_ids:
        section_ids = [starred] + [s for s in section_ids if s != starred]
    rank = {section: index for index, section in enumerate(section_ids)}
    # sorted() is stable: candidates keep the server's order inside a section
    ordered = sorted(candidates, key=lambda c: rank.get(c.get("section"), len(rank)))
    # resume: the server saved with the progress goes first, whatever its group (the website's
    # firstOkIndex(list, resumeServer)); then the normal order
    wanted = hoster_key(resume_hoster)
    if wanted:
        for index, candidate in enumerate(ordered):
            if hoster_key(candidate.get("hoster")) == wanted:
                ordered.insert(0, ordered.pop(index))
                break
    return ordered


def panel_order(sources: dict, muted=None) -> list:
    """Every HOSTER candidate in the Servers panel's order (the box's own Torrents group is not here), then the
    server's order inside a group (playernav.server_groups)."""
    candidates = [c for c in (sources.get("candidates") or []) if isinstance(c, dict)]
    ordered = []
    muted = set(muted or [])
    for section in sources.get("sections") or []:
        if isinstance(section, dict) and section.get("id") != "torrents" and section.get("id") not in muted:
            ordered += [c for c in candidates if c.get("section") == section.get("id")]
    return ordered


def after_pick(sources: dict, picked_cid, attempted: set, muted=None) -> list:
    """cids to try when a hand-picked server fails: the working servers AFTER it in panel order,
    wrapping once (the website's failoverOrder(list, start, attempted)); never a silent jump back to
    the first server."""
    ordered = panel_order(sources, muted)
    cids = [c.get("cid") for c in ordered]
    start = cids.index(picked_cid) if picked_cid in cids else -1
    queue = []
    for step in range(1, len(ordered) + 1):
        candidate = ordered[(start + step) % len(ordered)]
        cid = candidate.get("cid")
        if candidate.get("check") == "ok" and cid not in attempted and cid != picked_cid and cid not in queue:
            queue.append(cid)
    return queue


def pick_next(sources: dict, attempted: set, starred=None, resume_hoster=None, queue=None, muted=None):
    # a server is remembered by its cid: the ref handle is new on every poll
    if queue:                            # after a hand pick: the servers after it first
        by_cid = {c.get("cid"): c for c in order(sources, muted=muted)}
        for cid in queue:
            if cid not in attempted and cid in by_cid:
                return by_cid[cid]
    for candidate in order(sources, starred, resume_hoster, muted):
        if candidate.get("cid") not in attempted:
            return candidate
    return None


def total(sources: dict, muted=None) -> int:
    return len(order(sources, muted=muted))


def still_coming(sources: dict) -> bool:
    """The website's sourcesStillComing: the server is still checking or listing servers."""
    candidates = [c for c in (sources.get("candidates") or []) if isinstance(c, dict)]
    return bool(sources.get("pending") or sources.get("refreshing") or sources.get("state") == "partial"
                or any(c.get("check") == "checking" for c in candidates))
