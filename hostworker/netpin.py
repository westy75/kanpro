"""085 T017 (box 2026-11-23): how the worker's host fetches connect.

Measured on the owner's box: DNS (1.1.1.1) answers www.qrmzi.tv now with 104.21.41.240/172.67.196.240 and now with
188.114.96.5/188.114.97.5. The home ISP drops SYNs to the 188.114.96.5/.97.5 pair, so urllib3 spent its whole 8 s
connect timeout on EACH of them (16 s) and device.sources hit its 12 s deadline: krmzi timed out 3 runs of 3.
The box has no global IPv6 (every AAAA fails at once with ENETUNREACH).

The guard replaces urllib3's create_connection inside the worker process only:
- addresses in getaddrinfo order, but an address that failed recently goes last (10 min memory);
- each address gets at most PER_ADDRESS_S to connect, the request's own timeout still bounds reads;
- family: IPv4 only when the box has no global IPv6 address (the player, ffmpeg, then also lands on IPv4, so an
  IP-signed link is minted and played from the same address); otherwise the system order, as ffmpeg uses it.
It never changes WHICH host is contacted: the netguard checks before the request stay as they are."""
import socket
import threading
import time

PER_ADDRESS_S = 2.0
DEAD_TTL_S = 600.0
RESOLVE_TRIES = 3
_dead = {}
_lock = threading.Lock()
state = {"family": socket.AF_UNSPEC, "installed": False}


def has_global_ipv6(path="/proc/net/if_inet6") -> bool:
    """Any global-scope (scope 00) IPv6 address on a non-loopback interface."""
    try:
        with open(path, encoding="ascii") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 6 and parts[3] == "00" and parts[5] != "lo" and not parts[0].startswith("fe80"):
                    return True
    except OSError:
        pass
    return False


def choose_family(mode="auto", inet6_path="/proc/net/if_inet6"):
    if mode == "4":
        return socket.AF_INET
    if mode == "6":
        return socket.AF_INET6
    if mode == "any":
        return socket.AF_UNSPEC
    return socket.AF_UNSPEC if has_global_ipv6(inet6_path) else socket.AF_INET


def _ordered(infos, now):
    with _lock:
        for key in [k for k, until in _dead.items() if until <= now]:
            del _dead[key]
        dead = set(_dead)
    alive = [info for info in infos if info[4][0] not in dead]
    return alive + [info for info in infos if info[4][0] in dead]


def mark_dead(address, now=None):
    with _lock:
        _dead[address] = (time.monotonic() if now is None else now) + DEAD_TTL_S


def _connect_one(info, timeout, source_address, socket_options, make_socket):
    family, kind, proto, _, sockaddr = info
    sock = (make_socket or socket.socket)(family, kind, proto)
    try:
        for option in socket_options or ():
            sock.setsockopt(*option)
        limit = PER_ADDRESS_S
        if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT and timeout is not None:
            limit = min(float(timeout), PER_ADDRESS_S)
        sock.settimeout(limit)
        if source_address:
            sock.bind(source_address)
        sock.connect(sockaddr)
        sock.settimeout(None if timeout is socket._GLOBAL_DEFAULT_TIMEOUT else timeout)
        return sock
    except OSError:
        sock.close()
        raise


def create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, socket_options=None,
                      getaddrinfo=None, make_socket=None, clock=time.monotonic):
    """Fresh addresses first; when every address of an answer failed, ask DNS again (round robin: the
    box's resolver hands out the blocked pair and the working pair in turn), RESOLVE_TRIES answers at most."""
    host, port = address
    if host.startswith("["):
        host = host.strip("[]")
    last, tried = None, set()
    for _ in range(RESOLVE_TRIES):
        infos = (getaddrinfo or socket.getaddrinfo)(host, port, state["family"], socket.SOCK_STREAM)
        if not infos:
            raise OSError("getaddrinfo returns an empty list")
        fresh = [info for info in _ordered(infos, clock()) if info[4][0] not in tried]
        if not fresh:
            continue
        for info in fresh:
            tried.add(info[4][0])
            try:
                return _connect_one(info, timeout, source_address, socket_options, make_socket)
            except OSError as error:
                last = error
                mark_dead(info[4][0], clock())
    if last is None:
        raise OSError("no address left to try for %s" % host)
    raise last


def install(mode="auto") -> int:
    """Patch urllib3 (requests' transport) in THIS process. Returns the family used."""
    state["family"] = choose_family(mode)
    import urllib3.util.connection as connection      # urllib3.connection calls it through this module
    connection.create_connection = create_connection
    state["installed"] = True
    return state["family"]
