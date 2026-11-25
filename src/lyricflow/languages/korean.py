"""Read modern Hangul syllables using Revised Romanization spellings."""

ONSETS = ("g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "",
          "j", "jj", "ch", "k", "t", "p", "h")
VOWELS = ("a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae",
          "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i")
CODAS = ("", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l",
         "p", "l", "m", "p", "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t")
# When the next onset is silent, a simple final consonant generally links to it.
LINK = {1: "g", 2: "kk", 4: "n", 7: "d", 8: "r", 16: "m", 17: "b", 19: "s",
        20: "ss", 22: "j", 23: "ch", 24: "k", 25: "t", 26: "p", 27: ""}


def hangul(text):
    syllables = []
    for char in text:
        code = ord(char) - 0xAC00
        if not 0 <= code < 11172:
            return None
        onset, remainder = divmod(code, 588)
        vowel, coda = divmod(remainder, 28)
        syllables.append((onset, vowel, coda))
    parts = []
    linked = None
    for index, (onset, vowel, coda) in enumerate(syllables):
        initial = ONSETS[onset] if linked is None else linked
        if index and onset == 5 and syllables[index - 1][2] == 8:
            initial = "l"
        linked = None
        final = CODAS[coda]
        if index + 1 < len(syllables):
            following, vowel_after, _ = syllables[index + 1]
            if following == 11 and coda in LINK:
                final, linked = "", LINK[coda]
                if vowel_after == 20 and coda in {7, 25}:
                    linked = "j" if coda == 7 else "ch"
            elif following in {2, 6}:
                final = {"k": "ng", "t": "n", "p": "m"}.get(final, final)
        parts.append(initial + VOWELS[vowel] + final)
    return "".join(parts)
