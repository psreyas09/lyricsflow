from bisect import bisect_right
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Word:
    original: str
    start_ms: int
    end_ms: int | None = None
    romanized: str = ""


@dataclass(frozen=True, slots=True)
class Line:
    original: str
    start_ms: int | None = None
    end_ms: int | None = None
    words: tuple[Word, ...] = ()
    romanized: str = ""
    translation: str = ""


@dataclass(frozen=True)
class Lyrics:
    lines: tuple[Line, ...]
    source: str
    language: str = ""

    @property
    def quality(self):
        if any(line.words for line in self.lines):
            return "Word sync"
        if any(line.start_ms is not None for line in self.lines):
            return "Line sync"
        return "Plain lyrics"


class Timeline:
    def __init__(self, lyrics):
        pairs = sorted((line.start_ms, index) for index, line in enumerate(lyrics.lines)
                       if line.start_ms is not None)
        self.starts = [start for start, _ in pairs]
        self.indices = [index for _, index in pairs]
        self.ends = []
        self.latest_end = []
        for slot, index in enumerate(self.indices):
            end = lyrics.lines[index].end_ms
            if end is None:
                later = bisect_right(self.starts, self.starts[slot])
                end = self.starts[later] if later < len(self.starts) else float("inf")
            self.ends.append(end)
            self.latest_end.append(max(end, self.latest_end[-1] if self.latest_end else end))

    def anchor(self, position):
        if not self.indices:
            return -1
        return self.indices[max(0, bisect_right(self.starts, position) - 1)]

    def active(self, position):
        last = bisect_right(self.starts, position)
        active = []
        for slot in range(last - 1, -1, -1):
            if position >= self.latest_end[slot]:
                break
            if position < self.ends[slot]:
                active.append(self.indices[slot])
        return active

    def gap(self, position):
        """Return the bounds of a pause with an upcoming lyric."""
        slot = bisect_right(self.starts, position)
        if slot == len(self.starts):
            return ()
        start = max(0, self.latest_end[slot - 1]) if slot else 0
        end = self.starts[slot]
        minimum = 1500 if slot else 1000
        return (start, end) if position >= start and end - start >= minimum else ()


def word_fill(word, position):
    if position < word.start_ms:
        return 0.0
    if word.end_ms is None or word.end_ms <= word.start_ms:
        return 1.0
    return min(1.0, (position - word.start_ms) / (word.end_ms - word.start_ms))
