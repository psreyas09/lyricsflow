import hashlib
import shutil
import subprocess
import unicodedata
from dataclasses import replace
from functools import lru_cache
from threading import local

import regex

from .korean import hangul

SCRIPT = regex.compile(
    r"[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}ー]+"
    r"|[\p{Script=Hangul}]+"
    r"|[\p{Tamil}\u200c\u200d]+|[\p{Malayalam}\u200c\u200d]+|[\p{Devanagari}\u200c\u200d]+"
    r"|(?:(?![\p{Latin}\p{Tamil}\p{Malayalam}\p{Devanagari}])\p{L}\p{M}*)+"
)
SOUTH_INDIAN = regex.compile(
    r"[\p{Tamil}\u200c\u200d]+|[\p{Malayalam}\u200c\u200d]+"
)
TAMIL = regex.compile(r"\p{Tamil}")
HINDI_RUN = regex.compile(r"[\p{Devanagari}\u200c\u200d]+")
HINDI_CONSONANT = regex.compile(r"[क-हक़-य़]़?$")
HINDI_MARKS = str.maketrans({
    "ā": "aa", "ī": "i", "ū": "oo", "ē": "e", "ō": "o",
    "ṅ": "ng", "ñ": "ny", "ṛ": "r", "ṝ": "ri", "ḻ": "l", "ś": "sh", "ṣ": "sh",
    "ʿ": None, "ʾ": None, "'": None, "\u200c": None, "\u200d": None,
})
JAPANESE = regex.compile(r"[\p{Hiragana}\p{Katakana}]")


ENGINES = local()


def icu_engine():
    if not hasattr(ENGINES, "icu"):
        try:
            from icu import Transliterator
            ENGINES.icu = Transliterator.createInstance("Any-Latin")
        except ImportError:
            ENGINES.icu = None
    return ENGINES.icu


def japanese_engine():
    if not hasattr(ENGINES, "kanji"):
        from pykakasi import kakasi
        ENGINES.kanji = kakasi()
    return ENGINES.kanji


@lru_cache(maxsize=8192)
def convert(segment, language, standard):
    if regex.fullmatch(r"\p{Hangul}+", segment):
        if result := hangul(segment):
            return result
    try:
        if JAPANESE.search(segment) or language == "ja" and regex.search(r"\p{Han}", segment):
            field = "kunrei" if standard else "hepburn"
            return "".join(part[field] for part in japanese_engine().convert(segment))
        if regex.fullmatch(r"\p{Han}+", segment):
            if language in {"yue", "zh-hk"}:
                from pycantonese import characters_to_jyutping
                return " ".join(pronunciation or chars for chars, pronunciation in characters_to_jyutping(segment))
            from pypinyin import Style, lazy_pinyin
            return " ".join(lazy_pinyin(segment, style=Style.TONE if standard else Style.NORMAL))
    except ImportError:
        if JAPANESE.search(segment) or language == "ja" and regex.search(r"\p{Han}", segment):
            # ICU's default Han reading is Mandarin. Preserve ambiguous Japanese
            # kanji when the dictionary is missing rather than giving that reading.
            return regex.sub(r"[\p{Hiragana}\p{Katakana}ー]+",
                             lambda run: icu_convert(run[0], "Hiragana-Latin"), segment)
    return icu_convert(segment)


@lru_cache(maxsize=8192)
def icu_convert(segment, transform="Any-Latin"):
    engine = icu_engine()
    if engine and transform != "Any-Latin":
        from icu import Transliterator
        engine = Transliterator.createInstance(transform)
    if engine:
        return engine.transliterate(segment)
    executable = shutil.which("uconv")
    if executable:
        try:
            return subprocess.run([executable, "-x", transform], input=segment, text=True,
                                  capture_output=True, check=True, timeout=2).stdout
        except (OSError, subprocess.SubprocessError):
            pass
    return segment


def language_hint(text, override=""):
    if override and override != "auto":
        return override.lower().replace("_", "-")
    return "ja" if JAPANESE.search(text) else ""


def readable_south_indian(reading, tamil):
    reading = unicodedata.normalize("NFC", reading).translate(str.maketrans({
        "ā": "aa", "ī": "ee", "ū": "oo", "ṅ": "ng", "ḻ": "zh",
        "ñ": "ny" if tamil else "nj", "ṛ": "ri", "ṟ": "r",
        "\u200c": None, "\u200d": None,
    }))
    if tamil:
        if reading.startswith("ē"):
            reading = "y" + reading
        reading = regex.sub(r"t{1,2}", "th", reading)
    else:
        # ICU can emit two vowels for Malayalam's two-part O sign.
        reading = reading.replace("ēaa", "o").replace("eaa", "o").replace("r̥", "ri").replace("ṇṭ", "nd")
    reading = unicodedata.normalize("NFD", reading)
    return "".join(char for char in reading if not unicodedata.category(char).startswith("M"))


def hindi_reading(reading, original=""):
    reading = unicodedata.normalize("NFC", reading)
    reading = reading.replace("ḵẖ", "kh").replace("m̐", "ṁ").replace("r̥", "ri")
    reading = regex.sub(r"[ṃṁ](?=[bmp])", "m", reading)
    reading = regex.sub(r"[ṃṁ]", "n", reading)
    reading = regex.sub(r"ch|c", lambda match: "ch" + ("h" if match[0] == "ch" else ""), reading)
    if original and HINDI_CONSONANT.search(original) and reading.endswith("a"):
        reading = reading[:-1]
    if original:
        # Drop an unstressed internal a before a stressed syllable, while
        # retaining a in clusters such as the one in namaste.
        reading = regex.sub(
            r"([aeiouāēīōū][^aeiouāēīōū])a([^aeiouāēīōū])(?=[āēīōūi])",
            r"\1\2", reading,
        )
    reading = reading.translate(HINDI_MARKS)
    return "".join(char for char in unicodedata.normalize("NFD", reading)
                   if not unicodedata.category(char).startswith("M"))


def romanize(text, hint="", style="natural", diacritics=True):
    language = language_hint(text, hint)
    if language not in {"zh-hk"}:
        language = language.split("-")[0]

    def transform(match):
        original = match[0]
        result = convert(original, language, style == "standard")
        if style != "standard" and result != original and SOUTH_INDIAN.fullmatch(original):
            result = readable_south_indian(result, bool(TAMIL.search(original)))
        if (style != "standard" and result != original and language in {"", "hi"}
                and HINDI_RUN.fullmatch(original)):
            result = hindi_reading(result, original)
        if not diacritics or style == "ascii":
            result = "".join(c for c in unicodedata.normalize("NFKD", result) if not unicodedata.combining(c))
        if style == "ascii":
            result = result.translate(str.maketrans({"ʿ": "'", "ʾ": "'", "ʻ": "'", "ı": "i", "ł": "l"}))
            return result if result.isascii() else original
        return unicodedata.normalize("NFC", result)

    result = SCRIPT.sub(transform, text)
    if style == "ascii":
        translations = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "..."})
        result = regex.sub(r"\p{Latin}\p{M}*", lambda match: "".join(
            c for c in unicodedata.normalize("NFKD", match[0]) if not unicodedata.combining(c)), result)
        result = result.translate(translations)
    return result


def preferred_reading(original, supplied, hint, style, diacritics):
    if not supplied:
        return romanize(original, hint, style, diacritics)
    if (style != "standard" and hint.split("-")[0] in {"", "hi"}
            and HINDI_RUN.search(original)):
        # Keep source-aligned spellings and only simplify marked Latin words.
        return regex.sub(
            r"[\p{Latin}\p{M}]+", lambda match: hindi_reading(match[0])
            if not match[0].isascii() else match[0], supplied,
        )
    return supplied


def correction_key(line, index):
    return hashlib.sha256(f"{index}\0{line.start_ms}\0{line.original}".encode()).hexdigest()


def apply_romanization(lyrics, override="auto", style="natural", diacritics=True, corrections=None):
    hint = language_hint("\n".join(line.original for line in lyrics.lines),
                         lyrics.language if override == "auto" else override)
    corrections = corrections or {}
    lines = []
    for index, line in enumerate(lyrics.lines):
        full = preferred_reading(line.original, line.romanized, hint, style, diacritics)
        words = tuple(replace(word, romanized=preferred_reading(
            word.original, word.romanized, hint, style, diacritics)) for word in line.words)
        if words and not line.romanized:
            joined = "".join(word.romanized for word in words).strip()
            if joined != full.strip():
                # Keep line context when the source has separate CJK spans
                # and the reading differs only in where it inserts spaces.
                if "".join(full.split()) == "".join(joined.split()):
                    cursor = 0
                    aligned = []
                    for word in words:
                        width = len("".join(word.romanized.split()))
                        start = cursor
                        while cursor < len(full) and width:
                            width -= not full[cursor].isspace()
                            cursor += 1
                        aligned.append(replace(word, romanized=full[start:cursor]))
                    words = tuple(aligned)
                else:
                    full = joined
        # The line reading may differ from its isolated word readings. A full-line
        # correction can carry word highlighting only when each span is mapped.
        edit = corrections.get(correction_key(line, index))
        if edit is not None:
            full = edit
            if len(words) == 1:
                words = (replace(words[0], romanized=edit),)
            elif words and " | " in edit and len(edit.split(" | ")) == len(words):
                parts = edit.split(" | ")
                full = "".join(parts[i] + (" " if w.original[-1:].isspace() else "")
                               for i, w in enumerate(words)).strip()
                words = tuple(replace(w, romanized=parts[i] + (" " if w.original[-1:].isspace() else ""))
                              for i, w in enumerate(words))
        lines.append(replace(line, romanized=full, words=words))
    return replace(lyrics, lines=tuple(lines))
