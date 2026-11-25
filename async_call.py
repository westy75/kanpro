"""Run blocking work off the GUI thread; callbacks come back on the reactor (GUI) thread.

FIX27 (box log 2026-11-19 20:04): an answer that arrives after its Screen closed must never run. Enigma2's
Screen.doClose clears the Screen's __dict__, so a late `self.closed` check itself raised AttributeError. The
owner of a callback (a bound method's object, or the `self` a lambda/nested function closes over) is looked
up when the answer arrives; a closed Screen's callbacks are dropped.
"""
from twisted.internet.threads import deferToThread


def owner_of(fn):
    """The object a callback belongs to: a bound method's __self__, else the `self` it closes over."""
    owner = getattr(fn, "__self__", None)
    if owner is not None:
        return owner
    code, cells = getattr(fn, "__code__", None), getattr(fn, "__closure__", None) or ()
    if code is None or "self" not in code.co_freevars:
        return None
    try:
        return cells[code.co_freevars.index("self")].cell_contents
    except (IndexError, ValueError):             # an empty cell: the closure was never bound
        return None


def _is_screen(owner):
    return any(cls.__name__ == "Screen" for cls in type(owner).__mro__)


def owner_closed(owner):
    """True for a Screen that is gone: Enigma2 cleared its __dict__, or it set closed = True itself."""
    if owner is None or not _is_screen(owner):
        return False
    state = getattr(owner, "__dict__", None)
    if state is None:
        return False
    return not state or state.get("closed") is True


def guard(fn):
    """fn, except that it does nothing once its owning Screen has closed."""
    def call(value):
        if owner_closed(owner_of(fn)):
            return None
        return fn(value)
    return call


def run(fn, *args, on_done, on_error):
    deferred = deferToThread(fn, *args)
    done, error = guard(on_done), guard(on_error)
    deferred.addCallbacks(done, lambda failure: error(failure.value))
    return deferred


def on_gui(fn):
    """fn() soon on the GUI (reactor) thread, from any thread (084: api.Client's 409 profile_required signal
    fires inside a worker); dropped when its owning Screen has closed meanwhile."""
    from twisted.internet import reactor
    owner = owner_of(fn)

    def call():
        if not owner_closed(owner):
            fn()
    reactor.callFromThread(call)
