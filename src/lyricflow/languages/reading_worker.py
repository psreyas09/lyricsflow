"""Run dictionary-backed pronunciation without keeping dictionaries in the GUI process."""

import asyncio
import json
import sys
from contextlib import suppress
from dataclasses import asdict

import regex

from ..lyrics.models import Line, Lyrics, Word
from .romanizer import apply_romanization

CJK = regex.compile(r"[\p{Han}\p{Hiragana}\p{Katakana}]")


def decode_lyrics(data):
    lines = tuple(Line(**{**line, "words": tuple(Word(**word) for word in line["words"])})
                  for line in data["lines"])
    return Lyrics(lines, data["source"], data["language"])


async def prepare_readings(lyrics, language, style, diacritics, corrections):
    needs_dictionary = any(
        not span.romanized and CJK.search(span.original)
        for line in lyrics.lines for span in (line, *line.words)
    )
    if not needs_dictionary:
        return await asyncio.to_thread(apply_romanization, lyrics, language, style, diacritics, corrections)

    payload = json.dumps([asdict(lyrics), language, style, diacritics, corrections]).encode()
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "lyricflow.languages.reading_worker",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
    )
    communication = asyncio.create_task(process.communicate(payload))
    try:
        output, _ = await asyncio.shield(communication)
    except asyncio.CancelledError:
        with suppress(ProcessLookupError):
            process.kill()
        await communication
        raise
    if process.returncode:
        raise RuntimeError(f"Pronunciation worker exited with status {process.returncode}")
    return decode_lyrics(json.loads(output))


if __name__ == "__main__":
    document, *settings = json.load(sys.stdin)
    json.dump(asdict(apply_romanization(decode_lyrics(document), *settings)), sys.stdout)
