"""Serialize Enigma2 service changes without importing Enigma2 (and keep it unit-testable)."""


class ServiceFlow:
    """Stop, yield to the GUI loop, then start only the newest still-valid service request."""

    def __init__(self, nav, timer, valid, delay_ms=400):
        self.nav, self.timer, self.valid, self.delay_ms = nav, timer, valid, delay_ms
        self.pending, self.in_flight, self.serial = None, False, 0

    def stop(self):
        """Cancel a deferred start before stopping the current service."""
        self.serial += 1
        self.pending = None
        self.timer.stop()
        self.nav.stopService()

    def switch(self, ref, generation, after=None):
        """Replace any deferred request; never call playService in the stopService stack."""
        self.serial += 1
        request = (self.serial, ref, generation, after)
        self.pending = request
        self.timer.stop()
        if self.in_flight:
            return
        self.nav.stopService()
        self.timer.start(self.delay_ms, True)

    def ready(self):
        request, self.pending = self.pending, None
        if request is None:
            return
        serial, ref, generation, after = request
        if serial != self.serial or not self.valid(generation):
            return
        if self.in_flight:                       # defensive: a re-entrant timer callback
            self.pending = request
            self.timer.start(self.delay_ms, True)
            return
        self.in_flight = True
        try:
            self.nav.playService(ref)
            if after is not None and serial == self.serial and self.valid(generation):
                after()
        finally:
            self.in_flight = False
        # A request made re-entrantly by playService/after supersedes this start. Drain it first.
        if self.pending is not None:
            self.nav.stopService()
            self.timer.start(self.delay_ms, True)
