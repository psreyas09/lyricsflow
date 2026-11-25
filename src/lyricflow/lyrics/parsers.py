import json
import re
from dataclasses import replace
from decimal import Decimal

import yaml
from defusedxml import ElementTree

from .models import Line, Lyrics, Word

STAMP = r"(\d+):([0-5]\d)(?:[.:](\d{1,3}))?"
LRC_LINE = re.compile(r"\[" + STAMP + r"\]")
LRC_WORD = re.compile("<" + STAMP + ">")
MAX_BYTES = 2_000_000


def milliseconds(match):
    minutes, seconds, fraction = match.groups()
    return int(minutes) * 60000 + int(seconds) * 1000 + int((fraction or "").ljust(3, "0"))


def plain(text, source):
    return Lyrics(tuple(Line(row) for row in text.splitlines() if row.strip()), source)


def lrc(text, source, duration=None):
    offset = re.findall(r"(?im)^\s*\[offset:\s*([+-]?\d+)\s*\]", text)
    adjustment = int(offset[-1]) if offset else 0
    rows = []
    for row in text.splitlines():
        tags = []
        cursor = 0
        row = row.lstrip()
        while tag := LRC_LINE.match(row, cursor):
            tags.append(milliseconds(tag))
            cursor = tag.end()
        if not tags:
            continue
        body = row[cursor:].lstrip()
        markers = list(LRC_WORD.finditer(body))
        original = LRC_WORD.sub("", body)
        for start in tags:
            shift = start - tags[0] + adjustment
            words = []
            if markers and not body[:markers[0].start()].strip():
                for index, marker in enumerate(markers):
                    following = markers[index + 1] if index + 1 < len(markers) else None
                    token = body[marker.end():following.start() if following else None]
                    if token.strip():
                        begin = max(0, milliseconds(marker) + shift)
                        end = max(0, milliseconds(following) + shift) if following else None
                        words.append(Word(token, begin, end))
            rows.append(Line(original, max(0, start + adjustment), words=tuple(words)))
    rows.sort(key=lambda line: line.start_ms)
    result = []
    for index, line in enumerate(rows):
        end = next((other.start_ms for other in rows[index + 1:] if other.start_ms > line.start_ms),
                   duration if duration and duration > line.start_ms else None)
        if line.original.strip():
            result.append(replace(line, end_ms=end, words=complete_words(line.words, end)))
    return Lyrics(tuple(result), source)


def complete_words(words, line_end):
    return tuple(
        replace(word, end_ms=next((other.start_ms for other in words[index + 1:]
                                  if other.start_ms > word.start_ms),
                                 line_end if line_end and line_end > word.start_ms else None))
        if word.end_ms is None else word
        for index, word in enumerate(words)
    )


def stamp(value):
    if value is None or type(value) is int and value >= 0:
        return value
    raise ValueError("Expected a nonnegative integer timestamp in milliseconds")


def optional_text(value):
    if value is not None and not isinstance(value, str):
        raise ValueError("Expected lyrics text")
    return value or ""


def structured(data, source, *, from_cache=False):
    if not isinstance(data, dict) or not isinstance(data.get("lines", []), (list, tuple)):
        raise ValueError("Expected a lyrics document with a lines array")
    meta = data.get("metadata") or {}
    language = optional_text(data.get("language") or meta.get("language"))
    rows = []
    for row in data.get("lines", []):
        original = row.get("original", row.get("text", ""))
        start, end = stamp(row.get("start_ms")), stamp(row.get("end_ms"))
        words = []
        for token in row.get("words") or ():
            begin, finish = stamp(token.get("start_ms")), stamp(token.get("end_ms"))
            text = token.get("original", token.get("text"))
            if not isinstance(text, str) or begin is None or finish is not None and finish <= begin:
                raise ValueError("Invalid word text or timing interval")
            if words and begin < words[-1].start_ms:
                raise ValueError("Words must occur in timestamp order")
            words.append(Word(text, begin, finish, optional_text(token.get("romanized"))))
        if not isinstance(original, str):
            raise ValueError("Invalid line text")
        original = original or "".join(word.original for word in words)
        if words and original.strip() != "".join(word.original for word in words).strip():
            # JSON may store whitespace between lexical tokens rather than in each token.
            if " ".join(word.original for word in words) == original:
                words = [replace(word, original=word.original + (" " if i + 1 < len(words) else ""))
                         for i, word in enumerate(words)]
            else:
                words = []
        if start is None and words:
            start = words[0].start_ms
        if end is not None and (start is None or end <= start):
            raise ValueError("Invalid line interval")
        if words and (words[0].start_ms < start or
                      end is not None and any(w.start_ms >= end or (w.end_ms or 0) > end for w in words)):
            raise ValueError("Word outside line interval")
        if original.strip():
            rows.append(Line(original, start, end, tuple(words),
                             optional_text(row.get("romanized")), optional_text(row.get("translation"))))
    if not rows:
        return replace(plain(data.get("plain") or "", source), language=language)
    if from_cache:
        return Lyrics(tuple(rows), source, language)
    starts = sorted({row.start_ms for row in rows if row.start_ms is not None})
    next_start = dict(zip(starts, starts[1:]))
    result = []
    for row in rows:
        boundary = row.end_ms or next_start.get(row.start_ms) or stamp(meta.get("duration_ms"))
        result.append(replace(row, words=complete_words(row.words, boundary)))
    return Lyrics(tuple(result), source, language)


def lyricsfile(text, source):
    if len(text.encode()) > MAX_BYTES or any(
        isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)) for token in yaml.scan(text)
    ):
        raise ValueError("Lyricsfile is too large or uses YAML references")
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or str(data.get("version")) != "1.0":
        raise ValueError("Unsupported Lyricsfile version")
    return structured(data, source)


def ttml_time(value, amll=False):
    if amll and re.fullmatch(r"\d+:[0-5]\d(?:\.\d+)?", value):
        minutes, seconds = value.split(":")
        return int((int(minutes) * 60 + Decimal(seconds)) * 1000)
    if amll and re.fullmatch(r"\d+(?:\.\d+)?", value):
        return int(Decimal(value) * 1000)
    if re.fullmatch(r"\d+:\d{2}:\d{2}(\.\d+)?", value):
        hours, minutes, seconds = value.split(":")
        return int((int(hours) * 3600 + int(minutes) * 60 + Decimal(seconds)) * 1000)
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h)", value)
    if not match:
        raise ValueError(f"Unsupported TTML time expression {value}")
    number, unit = match.groups()
    return int(Decimal(number) * {"ms": 1, "s": 1000, "m": 60000, "h": 3600000}[unit])


def ttml(text, source, *, amll=False):
    root = ElementTree.fromstring(text, forbid_dtd=True)
    rows = []

    def name(node):
        return node.tag.rsplit("}", 1)[-1]

    def tail(node):
        value = node.tail or ""
        return "" if amll and not value.strip() and "\n" in value else value

    def lyric_text(node):
        value = node.text or ""
        pieces = ["" if amll and not value.strip() and "\n" in value else value]
        for child in node:
            role = child.get("{http://www.w3.org/ns/ttml#metadata}role")
            if role not in {"x-bg", "x-translation", "x-roman"}:
                pieces.append(lyric_text(child))
            pieces.append(tail(child))
        return "".join(pieces)

    def visit(node, parent_start=0, parent_end=None, row=None):
        origin = 0 if amll else parent_start
        start = origin + ttml_time(node.attrib["begin"], amll) if "begin" in node.attrib else parent_start
        end = origin + ttml_time(node.attrib["end"], amll) if "end" in node.attrib else parent_end
        if "dur" in node.attrib:
            duration_end = start + ttml_time(node.attrib["dur"], amll)
            end = min(end, duration_end) if end is not None else duration_end
        if parent_end is not None:
            end = min(end, parent_end) if end is not None else parent_end
        role = node.get("{http://www.w3.org/ns/ttml#metadata}role") if amll else None
        if role == "x-translation" or role == "x-roman":
            lang = node.get("{http://www.w3.org/XML/1998/namespace}lang", "")
            if row is not None and role == "x-translation" and lang.lower().split("-")[0] == "en":
                row["translation"] = lyric_text(node).strip()
            elif row is not None and role == "x-roman":
                row["romanized"] = lyric_text(node).strip()
            return
        if name(node) == "p" or role == "x-bg":
            row = {"text": lyric_text(node) if amll else "".join(node.itertext()),
                   "start_ms": start, "end_ms": end, "words": []}
            rows.append(row)
        elif name(node) == "span" and row is not None and "begin" in node.attrib:
            row["words"].append({"text": (lyric_text(node) if amll else "".join(node.itertext())) + tail(node),
                                 "start_ms": start,
                                 "end_ms": end if "end" in node.attrib or "dur" in node.attrib else None})
            return
        if node.get("timeContainer") == "seq":
            raise ValueError("Sequential TTML containers are unsupported")
        for child in node:
            visit(child, start, end, row)

    visit(root)
    return structured({"lines": rows, "language": root.get("{http://www.w3.org/XML/1998/namespace}lang")}, source)


def import_file(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Lyrics file is too large")
    text = raw.decode("utf-8-sig")
    readers = {".lrc": lrc, ".ttml": ttml, ".json": lambda value, source: structured(json.loads(value), source),
               ".yaml": lyricsfile, ".yml": lyricsfile}
    reader = readers.get(path.suffix.lower())
    if reader is None:
        raise ValueError("Choose an LRC, TTML, JSON or Lyricsfile document")
    lyrics = reader(text, "Local file")
    if not lyrics.lines:
        raise ValueError("The file contains no readable lyrics")
    return lyrics


QRC_LINE = re.compile(r"\[(\d+),(\d+)\]")
QRC_WORD = re.compile(r"\((\d+),(\d+)\)")
QRC_CREDIT = re.compile(
    r"^\s*(?:lyrics?\s+by|composed\s+by|arranged\s+by|producer|作[词詞曲]|編曲|编曲|制作人|製作人)\s*[:：]",
    re.I,
)


def qrc(text, source, duration=None):
    """Read QQ's XML envelope and its millisecond word spans."""
    root = ElementTree.fromstring(text, forbid_dtd=True)
    body = root.findtext("./LyricInfo/Lyric_1") or ""
    element = root.find("./LyricInfo/Lyric_1")
    if element is not None:
        body = element.get("LyricContent", body)
    headers = {key.lower(): value.strip() for key, value in re.findall(r"\[([A-Za-z]+):([^\]]*)\]", body)}
    lines = list(QRC_LINE.finditer(body))
    rows = []
    for index, stamp in enumerate(lines):
        start, length = map(int, stamp.groups())
        following = lines[index + 1] if index + 1 < len(lines) else None
        end = start + length if length else int(following[1]) if following else duration
        if end is not None and end <= start:
            continue
        raw = body[stamp.end():following.start() if following else None].strip()
        position, words = 0, []
        for mark in QRC_WORD.finditer(raw):
            token = raw[position:mark.start()]
            begin, span = map(int, mark.groups())
            if token:
                words.append(Word(token, begin, begin + span if span else None))
            position = mark.end()
        original = QRC_WORD.sub("", raw)
        if not original.strip() or QRC_CREDIT.match(original):
            continue
        if (position != len(raw) or any(
            word.start_ms < start or (end is not None and (word.end_ms or word.start_ms) > end)
            for word in words
        ) or any(a.start_ms > b.start_ms for a, b in zip(words, words[1:]))):
            words = []
        rows.append(Line(original, start, end, complete_words(tuple(words), end)))
    return Lyrics(tuple(rows), source), headers
