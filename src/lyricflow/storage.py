import asyncio
import json
import os
import sqlite3
from dataclasses import asdict
from pathlib import Path
from time import time

from .lyrics.parsers import structured
from .lyrics.providers import QUALITY, Candidate, best_match, language_compatible, local_candidates, track_language


def xdg(variable, fallback):
    path = Path(os.environ.get(variable) or Path.home() / fallback)
    return path if path.is_absolute() else Path.home() / fallback


class Store:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS entries (
            namespace TEXT, key TEXT, updated REAL NOT NULL, value TEXT NOT NULL,
            PRIMARY KEY (namespace, key))""")

    def put(self, namespace, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO entries VALUES (?, ?, ?, ?)",
                            (namespace, key, time(), json.dumps(value, ensure_ascii=False)))

    def get(self, namespace, key, max_age=None):
        row = self.db.execute("SELECT updated, value FROM entries WHERE namespace=? AND key=?",
                              (namespace, key)).fetchone()
        if row and (max_age is None or time() - row[0] <= max_age):
            return json.loads(row[1])
        return None

    def remove(self, namespace, key):
        with self.db:
            self.db.execute("DELETE FROM entries WHERE namespace=? AND key=?", (namespace, key))

    def clear_cache(self):
        with self.db:
            self.db.execute("DELETE FROM entries WHERE namespace IN ('auto', 'search', 'romanized', 'track')")

    def corrections(self):
        return {key: json.loads(value) for key, value in self.db.execute(
            "SELECT key, value FROM entries WHERE namespace='corrections'")}

    def import_corrections(self, data):
        if not isinstance(data, dict):
            raise ValueError("Expected an object containing track corrections")
        records = []
        for track, lines in data.items():
            if len(track) != 64 or set(track) - set("0123456789abcdef") or not isinstance(lines, dict):
                raise ValueError("Invalid track correction")
            for key, text in lines.items():
                if not isinstance(text, str) or len(text) > 4000 or len(key) != 64:
                    raise ValueError("Invalid corrected lyric")
            records.append(("corrections", track, time(), json.dumps(
                {**(self.get("corrections", track) or {}), **lines}, ensure_ascii=False)))
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO entries VALUES (?, ?, ?, ?)", records)


def restore(data):
    return structured(data, data["source"], from_cache=True)


def restore_candidate(data):
    return Candidate(**{**data, "lyrics": restore(data["lyrics"])})


class LyricsManager:
    def __init__(self, store, providers):
        self.store, self.providers = store, providers

    async def search(self, track, query="", refresh=False, include_local=True):
        from .lyrics.providers import LOG
        import httpx

        local = local_candidates(track) if include_local and not query else []

        async def lookup(provider):
            key = f"v2:{provider.name}:{track.key}:{query}"
            cached = self.store.get("search", key, 3600)
            if cached is not None and not refresh:
                return [restore_candidate(item) for item in cached], ""
            try:
                candidates = await provider.search(track, query)
            except (httpx.HTTPError, ValueError) as exc:
                LOG.warning("%s lookup failed: %s", provider.name, type(exc).__name__)
                stale = self.store.get("search", key) or []
                return [restore_candidate(item) for item in stale], f"{provider.name} unavailable"
            self.store.put("search", key, [asdict(item) for item in candidates])
            return candidates, ""

        providers = tuple(self.providers)
        primary = [provider for provider in providers if not getattr(provider, "fallback", False)]
        fallback = [provider for provider in providers if getattr(provider, "fallback", False)]
        results = await asyncio.gather(*(lookup(provider) for provider in primary))
        rows = local + [item for items, _ in results for item in items]
        matched = best_match(track, rows)
        if not query and not (matched and matched.lyrics.quality == "Word sync"):
            additional = await asyncio.gather(*(lookup(provider) for provider in fallback))
            results.extend(additional)
            rows.extend(item for items, _ in additional for item in items)
        rows.sort(key=lambda item: QUALITY[item.lyrics.quality], reverse=True)
        return rows, "; ".join(error for _, error in results if error)

    async def resolve(self, track, automatic=True):
        self.store.put("track", track.key, asdict(track))
        manual = self.store.get("manual", track.key)
        if manual:
            return restore(manual), ""
        local = local_candidates(track)
        nearby = local[0].lyrics if local else None
        if nearby and nearby.quality == "Word sync":
            return nearby, ""

        key = "|".join(provider.name for provider in self.providers) + ":" + track.key
        saved = self.store.get("auto", key)
        old_key = "|".join(provider.name for provider in self.providers
                           if not getattr(provider, "fallback", False)) + ":" + track.key
        old = saved or self.store.get("auto", old_key) or self.store.get("auto", track.key)
        fresh = self.store.get("auto", key, 86400)
        previous = restore(old) if old else None
        if previous and not language_compatible(track, previous):
            previous, fresh = None, None
        fallback = max((item for item in (nearby, previous) if item),
                       key=lambda item: QUALITY[item.quality], default=None)
        if not automatic or fresh:
            if fallback:
                return fallback, "" if fallback is nearby else "Cached lyrics"
            return None, "Automatic lyrics lookup is off"

        rows, error = await self.search(track, include_local=False)
        selected = best_match(track, rows)
        if selected:
            selected_lyrics = selected.lyrics
            if not fallback or QUALITY[selected_lyrics.quality] >= QUALITY[fallback.quality]:
                self.store.put("auto", key, asdict(selected_lyrics))
                return selected_lyrics, ""
        if fallback:
            return fallback, "" if fallback is nearby else error or "Previously cached lyrics"
        if track_language(track) and any(not language_compatible(track, row.lyrics) for row in rows):
            return None, "No original-language lyrics found for this recording."
        return None, error or ("Choose a match from Search" if rows else "No lyrics found")
