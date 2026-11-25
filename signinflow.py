"""The QR sign-in poll loop as plain decisions (no Enigma2 imports).

Every answer is ("wait", seconds) - poll again later - or ("approved", token, name, flags) or
("stop", text_key); flags = account.account_flags (is_pro, pro_until).
Never a spinner forever: the link lives at most 10 minutes, whatever the server or the network does.
"""
from .account import account_flags, account_name

MAX_LIFE_S, DEFAULT_INTERVAL_S, BACKOFF_S = 600, 5, 5


def _positive(value, default):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else default


class Flow:
    def __init__(self):
        self.secret, self.interval, self.deadline = None, DEFAULT_INTERVAL_S, 0.0

    def start(self, data: dict, now: float):
        self.secret = data.get("device_secret") if isinstance(data, dict) else None
        if not isinstance(self.secret, str) or not self.secret:
            return ("stop", "signin.error")
        self.interval = _positive(data.get("interval"), DEFAULT_INTERVAL_S)
        self.deadline = now + min(_positive(data.get("expires_in"), MAX_LIFE_S), MAX_LIFE_S)
        return ("wait", self.interval)

    def polled(self, answer, now: float):
        """answer = the poll's JSON dict, or the ApiError it raised."""
        status = answer.get("status") if isinstance(answer, dict) else None
        if status == "approved":
            token = answer.get("access_token")
            if not isinstance(token, str) or not token:
                return ("stop", "signin.error")
            account = answer.get("account") if isinstance(answer.get("account"), dict) else {}
            return ("approved", token, account_name(account), account_flags(account))
        if status in ("denied", "expired"):
            return ("stop", "signin.%s" % status)
        if now >= self.deadline:
            return ("stop", "signin.expired")
        if status == "slow_down" or getattr(answer, "code", None) == "rate_limited":
            server = answer.get("interval") if isinstance(answer, dict) else None
            self.interval = max(self.interval + BACKOFF_S, _positive(server, 0))
        return ("wait", self.interval)
