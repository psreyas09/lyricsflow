import asyncio
import logging
import random
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from difflib import SequenceMatcher
from time import monotonic
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlsplit

import httpx
import regex
import yaml
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError

from ..languages.romanizer import SCRIPT, romanize
from .models import Lyrics
from .parsers import MAX_BYTES, import_file, lyricsfile, lrc, plain, qrc, ttml

LOG = logging.getLogger(__name__)
VERSION = re.compile(r"\b(live|remix|acoustic|instrumental|karaoke|sped up|slowed|demo)\b", re.I)
SUFFIX = re.compile(
    r"\s*[\[(](?:official(?: music)? (?:video|audio)|lyrics?(?: video)?|"
    r"(?:(?:\d{4} )?remaster(?:ed)?(?: \d{4})?)|(?:feat\.?|ft\.?) .+?)[\])]\s*$"
    r"|\s+[-–—]\s+(?:official (?:video|audio)|lyrics?(?: video)?|(?:\d{4} )?remaster(?:ed)?(?: \d{4})?)$",
    re.I,
)
QUALITY = {"Word sync": 3, "Line sync": 2, "Plain lyrics": 1}


RELEASE_SUFFIX = re.compile(r"""\s*\(\s*from\s+["“‘'][^()]+["”’']\s*\)\s*$""", re.I)
TOPIC_SUFFIX = re.compile(r"\s+[-–—]\s+Topic$", re.I)


def clean_artist(name):
    return TOPIC_SUFFIX.sub("", name.strip())


def clean(title):
    while True:
        result = RELEASE_SUFFIX.sub("", SUFFIX.sub("", title)).strip()
        if result == title:
            return result
        title = result


def normalize(text):
    parts = []
    latin = False
    for char in unicodedata.normalize("NFKD", text.casefold()):
        if char.isalnum():
            parts.append(char)
            latin = "LATIN" in unicodedata.name(char, "")
        elif unicodedata.category(char).startswith("M"):
            if not latin and parts and parts[-1] != " ":
                parts.append(char)
        else:
            parts.append(" ")
            latin = False
    return " ".join("".join(parts).split())


LANGUAGE_SCRIPTS = {
    "ta": regex.compile(r"\p{Script=Tamil}"),
    "ml": regex.compile(r"\p{Script=Malayalam}"),
    "ko": regex.compile(r"\p{Script=Hangul}"),
    "ja": regex.compile(r"[\p{Script=Hiragana}\p{Script=Katakana}]"),
    "te": regex.compile(r"\p{Script=Telugu}"),
    "kn": regex.compile(r"\p{Script=Kannada}"),
}


def track_language(track):
    for language, script in LANGUAGE_SCRIPTS.items():
        if script.search(track.title):
            return language
    # Apple Music catalog 1682432906 identifies this 90-second edition
    # as Tamil. Several otherwise exact LRCLIB records contain English
    # renderings of the same recording, even with no language tag.
    if (normalize(track.title) == "aval trending version"
            and track.duration and abs(track.duration - 90_000) <= 1500
            and {"santhosh narayanan", "pradeep kumar"} <= {
                normalize(artist) for artist in re.split(r"[,;&]", track.artist)
            }):
        return "ta"
    return ""


@lru_cache(maxsize=512)
def lyrics_language(lyrics):
    text = "\n".join(line.original for line in lyrics.lines)
    letters = sum(char.isalpha() for char in text)
    if letters:
        minimum = max(1, letters * .20)
        for language, script in LANGUAGE_SCRIPTS.items():
            if len(script.findall(text)) >= minimum:
                return language
    return lyrics.language.lower().replace("_", "-").split("-")[0]


def language_compatible(track, lyrics):
    expected = track_language(track)
    return not expected or lyrics_language(lyrics) == expected


def similarity(left, right):
    a, b = normalize(left), normalize(right)
    if not a or not b:
        return 0.0
    score = SequenceMatcher(None, a, b, autojunk=False).ratio()
    if score < .87 and bool(SCRIPT.search(left)) != bool(SCRIPT.search(right)):
        a, b = (normalize(romanize(text, style="ascii")).replace(" ", "") for text in (left, right))
        if a and b:
            reading = SequenceMatcher(None, a, b, autojunk=False).ratio()
            score = max(score, reading * .96)
    return score


@dataclass(frozen=True)
class Candidate:
    identifier: str
    title: str
    artist: str
    album: str
    duration: int | None
    lyrics: Lyrics
    recording_match: bool = False


class Provider(Protocol):
    name: str

    async def search(self, track, query="") -> list[Candidate]: ...


def confidence(track, candidate):
    if candidate.lyrics.lines and not language_compatible(track, candidate.lyrics):
        return 0.0
    if track.duration and any(
        (line.end_ms or line.start_ms or 0) > track.duration + 1500 for line in candidate.lyrics.lines
    ):
        return 0.0
    if candidate.recording_match:
        return 1.0
    title = similarity(clean(track.title), clean(candidate.title))
    artist = max(similarity(clean_artist(a), clean_artist(b))
                 for a in re.split(r",| & | feat\.? ", track.artist, flags=re.I)
                 for b in re.split(r",| & | feat\.? ", candidate.artist, flags=re.I))
    if title < .87 or artist < .83:
        return 0.0
    if {v.lower() for v in VERSION.findall(track.title)} != {v.lower() for v in VERSION.findall(candidate.title)}:
        return 0.0
    difference = abs(track.duration - candidate.duration) if track.duration and candidate.duration else None
    if difference is not None and difference > min(7500, max(3000, track.duration * .025)):
        return 0.0
    if difference is None and (title < .99 or artist < .99):
        return 0.0
    return .53 * title + .30 * artist + (.1 * (1 - difference / 8000) if difference is not None else .05) + (
        .07 * similarity(track.album, candidate.album) if track.album and candidate.album else .03
    )


def best_match(track, candidates):
    eligible = [(confidence(track, candidate), candidate) for candidate in candidates]
    eligible = [(score, candidate) for score, candidate in eligible if score >= .88]
    if not eligible:
        return None
    best = max(score for score, _ in eligible)
    close = [(score, candidate) for score, candidate in eligible if score >= best - .055]
    if not track.duration and len({round(c.duration / 5000) for _, c in close if c.duration}) > 1:
        return None
    return max(close, key=lambda pair: (QUALITY[pair[1].lyrics.quality], pair[0]))[1]


def lrclib_record(data, source="LRCLIB"):
    if not isinstance(data, dict) or data.get("instrumental"):
        return None
    duration = data.get("duration")
    duration = round(duration * 1000) if type(duration) in (int, float) and 0 < duration < 86400 else None
    document = data.get("lyricsfile")
    synced = data.get("syncedLyrics")
    raw = data.get("plainLyrics")
    options = []
    if isinstance(document, str) and document:
        try:
            options.append(lyricsfile(document, source))
        except (ValueError, TypeError, AttributeError, RecursionError, yaml.YAMLError):
            LOG.debug("Invalid LRCLIB Lyricsfile record %s", data.get("id"))
    if isinstance(synced, str):
        options.append(lrc(synced, source, duration))
    if isinstance(raw, str):
        options.append(plain(raw, source))
    options = [item for item in options if item.lines]
    if not options:
        return None
    return Candidate(str(data.get("id", "")), str(data.get("trackName") or data.get("name") or ""),
                     str(data.get("artistName") or ""), str(data.get("albumName") or ""),
                     duration, max(options, key=lambda item: QUALITY[item.quality]))


class HttpLyrics:
    def __init__(self, client: httpx.AsyncClient, base, headers=None):
        self.client, self.base = client, base.rstrip("/")
        self.headers = headers
        self.lock = asyncio.Lock()
        self.next_request = 0.0

    async def request(self, path, params, *, missing=(404,)):
        async with self.lock:
            for attempt in range(2):
                await asyncio.sleep(max(0, self.next_request - monotonic()))
                try:
                    response = await self.client.get(self.base + path, params=params, headers=self.headers)
                except httpx.TransportError:
                    self.next_request = monotonic() + .5
                    if attempt:
                        raise
                    continue
                self.next_request = monotonic() + .4
                if response.status_code in missing:
                    return None
                if response.status_code == 429 or response.status_code >= 500:
                    try:
                        delay = max(1, int(response.headers.get("Retry-After", "1")))
                    except ValueError:
                        delay = 2
                    self.next_request = monotonic() + delay + random.uniform(0, min(delay * .2, 2))
                    if not attempt and delay <= 5:
                        continue
                response.raise_for_status()
                return response.json()


class LrcLib(HttpLyrics):
    def __init__(self, client, base="https://lrclib.net", headers=None):
        super().__init__(client, base, headers)
        uri = urlsplit(self.base)
        self.name = "LRCLIB" if self.base == "https://lrclib.net" else uri.netloc + uri.path

    def candidates(self, rows):
        return [candidate for row in (rows if isinstance(rows, list) else [])
                if (candidate := lrclib_record(row, self.name)) is not None]

    async def search(self, track, query=""):
        if query:
            return self.candidates(await self.request("/api/search", {"q": query}))

        params = {"track_name": clean(track.title)}
        if track.artist:
            params["artist_name"] = clean_artist(track.artist)
        candidates = self.candidates(await self.request("/api/search", params))
        selected = best_match(track, candidates)
        if selected and selected.lyrics.quality == "Word sync":
            return candidates

        # The recording-signature endpoint can surface a different edition
        # from search. Keep the original results if the extra lookup fails.
        if track.artist and track.duration and 1000 <= track.duration <= 3_600_000:
            signature = {**params, "duration": round(track.duration / 1000)}
            if track.album:
                signature["album_name"] = track.album
            try:
                exact = lrclib_record(await self.request("/api/get", signature), self.name)
                if exact and exact not in candidates:
                    candidates.append(exact)
            except (httpx.HTTPError, ValueError):
                if not candidates:
                    raise
        chosen = best_match(track, candidates)
        if track.artist and (not chosen or chosen.lyrics.quality != "Word sync"):
            try:
                for candidate in self.candidates(await self.request("/api/search", {"track_name": params["track_name"]})):
                    if candidate not in candidates:
                        candidates.append(candidate)
            except (httpx.HTTPError, ValueError):
                if not candidates:
                    raise
        return candidates


class Amll(HttpLyrics):
    name = "AMLL TTML"

    def __init__(self, client):
        super().__init__(client, "https://api.amll.dev")

    @staticmethod
    def spotify_id(url):
        match = re.fullmatch(r"spotify:track:([A-Za-z0-9]{22})", url)
        if not match:
            match = re.match(r"https://open\.spotify\.com/track/([A-Za-z0-9]{22})(?:[?#]|$)", url)
        return match[1] if match else ""

    @staticmethod
    def unwrap(payload):
        data = payload.get("data") if isinstance(payload, dict) and payload.get("status") == 200 else None
        return data if isinstance(data, dict) else {}

    @staticmethod
    def names(row, field):
        names = row.get(field)
        return [name for name in names if isinstance(name, str) and name.strip()] if isinstance(names, list) else []

    def metadata_candidate(self, track, row, lyrics, spotify=""):
        title = max(self.names(row, "musicNames"), key=lambda name: similarity(clean(track.title), clean(name)),
                    default="")
        album = max(self.names(row, "albumNames"), key=lambda name: similarity(track.album, name), default="")
        artists = ", ".join(self.names(row, "artistNames"))
        same_recording = bool(spotify and spotify in self.names(row, "spotifyIds"))
        # AMLL's timestamps stop with the last lyric. They are not audio duration.
        return Candidate(str(row.get("id", "")), title, artists, album, None, lyrics, same_recording)

    def record(self, track, row, spotify=""):
        raw = row.get("lyrics")
        if not isinstance(raw, str) or len(raw.encode()) > MAX_BYTES:
            return None
        try:
            lyrics = ttml(raw, self.name, amll=True)
        except (ValueError, ParseError, DefusedXmlException, RecursionError):
            LOG.debug("AMLL TTML record %s could not be parsed", row.get("id"))
            return None
        if not lyrics.lines:
            return None
        return self.metadata_candidate(track, row, lyrics, spotify)

    async def search(self, track, query=""):
        if not query and (spotify := self.spotify_id(track.url)):
            row = self.unwrap(await self.request("/v1/lyrics/get", {"spotifyId": spotify}))
            if ((candidate := self.record(track, row, spotify)) and candidate.recording_match
                    and confidence(track, candidate) > 0):
                return [candidate]
        params = {"q": query} if query else {"musicName": clean(track.title)}
        if not query and track.artist:
            # Artist lists from MPRIS are comma-separated. The API searches
            # for a substring of an individual artist name.
            params["artistName"] = clean_artist(track.artist.split(",")[0])
        data = self.unwrap(await self.request("/v1/lyrics/search", {**params, "pageSize": 12}))
        rows = data.get("items", [])
        if not isinstance(rows, list):
            return []
        summaries = [
            row for row in rows
            if isinstance(row, dict) and type(row.get("id")) is int and
            (query or confidence(track, self.metadata_candidate(track, row, Lyrics((), self.name))) >= .88)
        ]
        candidates = []
        for row in summaries[:4]:
            details = self.unwrap(await self.request("/v1/lyrics/get", {"id": row["id"]}))
            candidate = self.record(track, details)
            if candidate:
                candidates.append(candidate)
                if not query and candidate.lyrics.quality == "Word sync" and confidence(track, candidate) >= .88:
                    break
        return candidates


class BetterLyrics(HttpLyrics):
    name = "Better Lyrics"
    fallback = True

    def __init__(self, client):
        super().__init__(client, "https://api.betterlyrics.org")

    async def search(self, track, query=""):
        if query or not track.title or not track.artist:
            return []
        params = {"s": clean(track.title), "a": clean_artist(track.artist), "al": track.album.strip()}
        if track.duration:
            params["d"] = max(1, round(track.duration / 1000))
        candidates = []
        errors = []
        for path, field, source, parser in (
            ("/getLyrics", "ttml", "Better Lyrics · TTML", self.parse_ttml),
            ("/qq/getLyrics", "lyrics", "Better Lyrics · QQ", qrc),
            ("/kugou/getLyrics", "lyrics", "Better Lyrics · Kugou", self.parse_lrc),
        ):
            try:
                body = await self.request(path, params, missing=(401, 404))
                candidate = self.record(track, body, field, source, parser)
            except (httpx.HTTPError, ValueError, ParseError, DefusedXmlException, RecursionError) as exc:
                errors.append(exc)
                # Retry-After applies across paths on this API.
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                    break
                continue
            if candidate:
                candidates.append(candidate)
                if candidate.lyrics.quality == "Word sync":
                    break
        if not candidates and errors:
            error = errors[-1]
            if isinstance(error, httpx.HTTPError):
                raise error
            raise ValueError("Better Lyrics returned invalid timing data") from error
        return candidates

    @staticmethod
    def parse_ttml(raw, source, duration):
        return ttml(raw, source, amll=True), {}

    @staticmethod
    def parse_lrc(raw, source, duration):
        tags = dict(re.findall(r"(?im)^\s*\[(ti|ar|al):([^\]]*)\]", raw))
        return lrc(raw, source, duration), {key.lower(): val.strip() for key, val in tags.items()}

    @staticmethod
    def record(track, body, field, source, parser):
        raw = body.get(field) if isinstance(body, dict) else None
        if not isinstance(raw, str) or len(raw.encode()) > MAX_BYTES:
            return None
        score = body.get("score")
        if type(score) in (int, float) and not .88 <= score <= 1:
            return None
        lyrics, tags = parser(raw, source, track.duration)
        if not lyrics.lines or not (
            track.duration or tags.get("ti") and tags.get("ar") or type(score) in (int, float)
        ):
            return None
        # The API matches the full request signature. Use any returned title,
        # artist, or album to independently reject mismatched results.
        candidate = Candidate(source, tags.get("ti") or track.title, tags.get("ar") or track.artist,
                              tags.get("al") or track.album, None, lyrics)
        if confidence(track, candidate) < .88:
            return None
        return candidate


def provider_urls(value):
    urls = []
    for entry in re.split(r"[\s,]+", value.strip()):
        if not entry:
            continue
        try:
            parsed = urlsplit(entry)
            parsed.port
        except ValueError:
            raise ValueError("Enter a valid lyrics server URL.") from None
        if (not parsed.hostname or parsed.username is not None or parsed.query or parsed.fragment
                or parsed.scheme != "https" and not (
                    parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                )):
            raise ValueError("Enter HTTPS provider URLs or loopback HTTP URLs, without credentials.")
        try:
            url = str(httpx.URL(entry)).rstrip("/")
        except httpx.InvalidURL:
            raise ValueError("Enter a valid lyrics server URL.") from None
        if url != "https://lrclib.net" and url not in urls:
            urls.append(url)
    return urls


def local_candidates(track):
    try:
        parts = urlsplit(track.url)
    except ValueError:
        return []
    if parts.scheme != "file" or parts.netloc not in {"", "localhost"}:
        return []
    media = Path(unquote(parts.path))
    if not media.is_absolute() or not media.is_file():
        return []
    candidates = []
    for suffix in (".lrc", ".ttml", ".lyrics.json", ".lyricsfile.yaml"):
        path = media.with_suffix(suffix)
        if not path.is_file():
            continue
        try:
            lyrics = import_file(path)
        except Exception:
            LOG.debug("Could not read nearby %s lyrics", suffix, exc_info=True)
            continue
        lyrics = Lyrics(lyrics.lines, f"Beside audio · {suffix.lstrip('.')}", lyrics.language)
        candidates.append(Candidate(suffix, track.title, track.artist, track.album, track.duration, lyrics))
    return sorted(candidates, key=lambda candidate: QUALITY[candidate.lyrics.quality], reverse=True)
