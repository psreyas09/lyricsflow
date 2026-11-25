from time import monotonic


class PlaybackClock:
    """Interpolate milliseconds and slew small corrections over one second."""

    def __init__(self, now=monotonic):
        self.now = now
        self.reset()

    def reset(self):
        self.base = self.drift = 0.0
        self.anchor = self.now()
        self.rate = 1.0
        self.playing = False
        self.duration = None

    def position(self):
        elapsed = max(0, self.now() - self.anchor)
        position = self.base
        if self.playing:
            position += elapsed * 1000 * self.rate + self.drift * min(1, elapsed)
        return max(0, min(self.duration, position) if self.duration else position)

    def state(self, playing, rate, duration):
        self.base = self.position()
        self.anchor = self.now()
        self.drift = 0
        self.playing, self.rate, self.duration = playing, rate, duration

    def reconcile(self, position, immediate=False):
        current = self.position()
        error = position - current
        smooth = self.playing and self.rate > 0 and abs(error) < min(400, self.rate * 750)
        self.base = current if smooth and not immediate else position
        self.anchor = self.now()
        self.drift = error if smooth and not immediate else 0
