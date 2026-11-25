"""Original offline demonstration written for LyricFlow."""
from .lyrics.models import Line, Lyrics, Word

DEMO_DURATION = 42000
DEMO_ARTIST = "LyricFlow Demo"
DEMO_TITLE = "Signal After Midnight"
DEMO_ALBUM = "Night Frequencies"


def demo_lyrics():
    rows = [
        (1000, 5000, [(1000, 1840, "City "), (1840, 2780, "lights "), (2880, 3400, "begin "), (3500, 4890, "to glow")]),
        (5000, 9900, [(5000, 5700, "君が "), (5800, 6800, "好き "), (6900, 7580, "baby "), (7800, 9640, "사랑해")]),
        (10000, 15300, [(10000, 11200, "Follow "), (11300, 11900, "the "), (12000, 13400, "signal "), (13700, 15200, "home")]),
        (15600, 20500, [(15600, 16750, "நான் "), (16900, 18500, "உன்னை "), (18600, 20300, "நினைக்கிறேன்")]),
        (21000, 25600, [(21000, 22000, "Under "), (22100, 22700, "the "), (22800, 23900, "midnight "), (24000, 25400, "sky")]),
        (26000, 31600, [(26000, 27200, "我爱你"), (27500, 28600, "，"), (28700, 29900, "别"), (30000, 31500, "离开")]),
        (32000, 38000, [(32000, 33600, "Carry "), (33700, 34500, "the "), (34600, 36100, "music "), (36200, 37800, "home")]),
    ]
    return Lyrics(tuple(Line("".join(w[2] for w in words), start, end,
                             tuple(Word(text, begin, close) for begin, close, text in words))
                        for start, end, words in rows), "Offline demo")
