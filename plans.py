"""084 plans on the box: GET /api/account/entitlement and the limits of a free account (no Enigma2 imports).

Apply `effective`, never decide access here (§5). While the owner's free != pro switch is OFF `effective` is
always "pro" and nothing changes. A failed call behaves as pro: the server enforces every limit anyway."""
PLANS_URL = "kanasastream.online/plans"

_PRO = {"effective": "pro", "limits": {}}
_state = dict(_PRO)


def parse(body) -> dict:
    if not isinstance(body, dict) or body.get("effective") not in ("pro", "free"):
        return {"effective": "pro", "limits": {}}
    limits = body.get("limits") if isinstance(body.get("limits"), dict) else {}
    return {"effective": body["effective"], "limits": dict(limits)}


def set_state(body) -> dict:
    _state.clear()
    _state.update(parse(body))
    return dict(_state)


def reset() -> None:
    set_state(None)


def is_free() -> bool:
    return _state["effective"] == "free"


def _limit(key):
    return _state["limits"].get(key) if is_free() else None


def allowed_groups():
    """The server groups a free account may use, or None (all)."""
    groups = _limit("groups")
    return [g for g in groups if isinstance(g, str)] if isinstance(groups, list) else None


def torrents_allowed() -> bool:
    return _limit("torrents") is not False


def downloads_allowed() -> bool:
    return _limit("downloads") is not False


def max_height():
    value = _limit("max_height")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def filter_sources(data):
    """A sources answer with only the allowed groups (the same object when nothing is limited)."""
    groups = allowed_groups()
    if groups is None or not isinstance(data, dict):
        return data
    sections = [s for s in data.get("sections") or [] if isinstance(s, dict) and s.get("id") in groups]
    candidates = [c for c in data.get("candidates") or [] if isinstance(c, dict) and c.get("section") in groups]
    return dict(data, sections=sections, candidates=candidates)


def cap_qualities(rows):
    """Quality rows at or under max_height; the smallest one stays when every row is above it."""
    cap = max_height()
    if cap is None or not isinstance(rows, list):
        return rows
    sized = [q for q in rows if isinstance(q, dict) and isinstance(q.get("height"), int)]
    under = [q for q in sized if q["height"] <= cap]
    if under:
        return under
    return [min(sized, key=lambda q: q["height"])] if sized else rows
