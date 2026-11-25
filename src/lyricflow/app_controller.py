import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import subprocess
from dataclasses import asdict
from pathlib import Path
from threading import Thread

import httpx
from .qt import QtCore, Property, Signal, Slot

QObject = QtCore.QObject
QSettings = QtCore.QSettings
QUrl = QtCore.QUrl

from .demo import DEMO_ALBUM, DEMO_ARTIST, DEMO_DURATION, DEMO_TITLE, demo_lyrics
from .languages.romanizer import SCRIPT, apply_romanization, correction_key
from .lyrics.models import Lyrics, Timeline
from .lyrics.parsers import MAX_BYTES, import_file
from .lyrics.providers import Amll, BetterLyrics, LrcLib, lyrics_language, provider_urls
from .mpris.clock import PlaybackClock
from .mpris.player import PREFIX, Mpris, Track, metadata, same_track
from .storage import LyricsManager, Store, restore, xdg
from .ui.artwork import Cover, CoverArchive, TARGET_EDGE, load_artwork, youtube_music_cover_url

from .ui.spectrum import Spectrum

LOG = logging.getLogger(__name__)
DEFAULTS = {
    "mode": "Both", "language": "auto", "style": "natural", "diacritics": True,
    "theme": "Dark", "accent": "#ed9f77", "fontSize": 40, "font": "", "wordUnderline": True,
    "motion": True, "backgroundEffect": "Gradient", "contentView": "Everything",
    "blur": 0.75, "background": 0.24, "alignment": "Center",
    "automatic": True, "lyricsServers": "", "globalOffset": 0, "player": "", "reconcile": 5,
    "onTop": False, "overlayOpacity": 0.9, "overlayFont": 27, "tray": False,
    "startMinimized": False, "clickThrough": False,
}


def desktop_motion():
    if "GNOME" not in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
        return True
    try:
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "enable-animations"],
            capture_output=True, text=True, timeout=.4,
        )
        return result.stdout.strip() != "false"
    except (OSError, subprocess.SubprocessError):
        return True


class Worker:
    def __init__(self, bridge, demo):
        self.bridge, self.demo = bridge, demo
        self.loop = asyncio.new_event_loop()
        self.ready = asyncio.Event()
        self.thread = Thread(target=self.run, name="LyricFlow I/O", daemon=True)
        self.thread.start()

    def run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.serve())
        self.loop.run_until_complete(self.loop.shutdown_default_executor())
        self.loop.close()

    async def serve(self):
        self.stop = asyncio.Event()
        self.bus = Mpris(self.bridge.player_update.emit)
        self.track_task = self.search_task = self.art_task = None
        try:
            self.store = Store(xdg("XDG_DATA_HOME", ".local/share") / "lyricflow" / "library.sqlite3")
            self.cache_available = True
        except (OSError, sqlite3.Error) as exc:
            LOG.error("Could not open lyric database: %s", exc)
            self.cache_available = False
            self.store = Store(Path(":memory:"))
        agent = os.environ.get("LYRICFLOW_USER_AGENT", "LyricFlow/0.1.8 (urn:desktop:io.github.lyricflow.LyricFlow)")
        headers = {"User-Agent": agent}
        app_id = os.environ.get("LYRICFLOW_LRCLIB_APP_ID")
        public_headers = {"Lrclib-App-Id": app_id} if app_id else None
        async with (
            httpx.AsyncClient(timeout=httpx.Timeout(6, connect=3), headers=headers) as client,
            httpx.AsyncClient(timeout=httpx.Timeout(5, connect=3), headers=headers) as self.art_client,
        ):
            self.lyric_client = client
            self.cover_archive = CoverArchive(self.art_client)
            self.public_provider = LrcLib(client, headers=public_headers)
            self.amll_provider = Amll(client)
            self.better_provider = BetterLyrics(client)
            self.manager = LyricsManager(self.store, [self.public_provider, self.amll_provider, self.better_provider])
            task = asyncio.create_task(self.bus.run()) if not self.demo else None
            self.ready.set()
            await self.stop.wait()
            for pending in (task, self.track_task, self.search_task, self.art_task):
                if pending:
                    pending.cancel()
            await asyncio.gather(*(p for p in (task, self.track_task, self.search_task, self.art_task) if p),
                                 return_exceptions=True)
        self.store.db.close()

    def call(self, callback, *args):
        async def dispatch():
            await self.ready.wait()
            callback(*args)

        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(lambda: asyncio.create_task(dispatch()))

    def close(self):
        if self.thread.is_alive():
            self.call(lambda: self.stop.set())
            self.thread.join(timeout=2)

    def save_offset(self, key, value):
        self.store.put("offset", key, value)

    def clear_cache(self):
        self.store.clear_cache()

    def preference(self, player, interval):
        self.bus.preferred, self.bus.interval = player, interval
        if not self.demo:
            self.bus.emit()
            asyncio.create_task(self.bus.poll(True))

    def lyric_sources(self, urls):
        previous = {source.base: source for source in self.manager.providers}
        self.manager.providers = [
            self.public_provider, self.amll_provider, self.better_provider,
            *(previous[url] if url in previous else LrcLib(self.lyric_client, url) for url in urls),
        ]

    def artwork(self, track):
        if self.art_task:
            self.art_task.cancel()

        async def fetch():
            original = await load_artwork(track.art, self.art_client)
            LOG.debug("Player cover edge: %d px", original.edge)
            self.bridge.art_update.emit(track.key, track.art, original)
            if original.edge >= TARGET_EDGE:
                return
            larger_url = youtube_music_cover_url(track.art)
            if larger_url:
                larger = await load_artwork(larger_url, self.art_client)
                LOG.debug("YouTube Music cover edge: %d px", larger.edge)
                if larger.edge > original.edge:
                    original = larger
                    self.bridge.art_update.emit(track.key, track.art, larger)
                if original.edge >= TARGET_EDGE:
                    return
            if not track.title:
                return
            saved = self.store.get("cover-hd", track.key, 7 * 86400)
            if saved == "" and self.store.get("cover-hd", track.key, 2700) == "":
                return
            cover, source = await self.cover_archive.larger_cover(track, original, saved)
            self.store.put("cover-hd", track.key, source)
            if source:
                self.bridge.art_update.emit(track.key, track.art, cover)

        self.art_task = asyncio.create_task(fetch())

    def load(self, track, settings, override=None, offline=False):
        if self.track_task:
            self.track_task.cancel()
        self.track_task = asyncio.create_task(self.load_async(track, settings, override, offline))

    async def load_async(self, track, settings, override, offline):
        try:
            if not track.title:
                return
            if override:
                lyrics, message = override, ""
            elif self.demo:
                saved = self.store.get("manual", track.key)
                lyrics, message = (restore(saved) if saved else demo_lyrics()), ""
            else:
                lyrics, message = await self.manager.resolve(track, settings["automatic"] and not offline)
            offset = self.store.get("offset", track.key) or 0
            if lyrics:
                corrections = self.store.get("corrections", track.key) or {}
                fingerprint = hashlib.sha256(json.dumps([
                    "romanization-v3", asdict(lyrics), corrections, settings["language"],
                    settings["style"], settings["diacritics"],
                ], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                cached = self.store.get("romanized", fingerprint)
                if cached:
                    display = restore(cached)
                else:
                    display = await asyncio.to_thread(
                        apply_romanization, lyrics, settings["language"], settings["style"],
                        settings["diacritics"], corrections,
                    )
                    self.store.put("romanized", fingerprint, asdict(display))
            else:
                display = None
            self.bridge.lyrics_update.emit(track.key, display, message, offset)
        except Exception:
            LOG.exception("Could not load lyrics")
            self.bridge.lyrics_update.emit(track.key, None, "Could not load lyrics. See diagnostics.", 0)

    def search(self, track, query):
        if self.search_task:
            self.search_task.cancel()
        self.search_task = asyncio.create_task(self.search_async(track, query))

    async def search_async(self, track, query):
        try:
            candidates, error = await self.manager.search(track, query)
            self.bridge.search_update.emit(track.key, candidates, error)
        except Exception:
            LOG.exception("Could not search lyrics")
            self.bridge.search_update.emit(track.key, [], "Lyrics search failed")

    def use_lyrics(self, track, settings, lyrics):
        self.store.put("manual", track.key, asdict(lyrics))
        self.load(track, settings, lyrics)

    def use_automatic(self, track, settings):
        self.store.remove("manual", track.key)
        self.load(track, settings)

    def import_lyrics(self, track, settings, path):
        try:
            self.use_lyrics(track, settings, import_file(path))
            self.bridge.notice_update.emit("Lyrics imported")
        except Exception as exc:
            self.bridge.notice_update.emit(f"Import failed: {exc}")

    def edit(self, track, settings, key, text):
        corrections = self.store.get("corrections", track.key) or {}
        if text:
            corrections[key] = text
        else:
            corrections.pop(key, None)
        self.store.put("corrections", track.key, corrections)
        self.load(track, settings, offline=True)

    def transfer(self, track, settings, path, exporting):
        try:
            if exporting:
                with path.open("x", encoding="utf-8") as stream:
                    json.dump(self.store.corrections(), stream, ensure_ascii=False, indent=2)
            else:
                with path.open("rb") as stream:
                    data = stream.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise ValueError("Correction file exceeds the size limit")
                self.store.import_corrections(json.loads(data))
                self.load(track, settings, offline=True)
            self.bridge.notice_update.emit("Corrections exported" if exporting else "Corrections imported")
        except Exception as exc:
            self.bridge.notice_update.emit(f"Corrections could not be transferred: {exc}")


class Controller(QObject):
    player_update = Signal(str, object, object, object, bool, str)
    lyrics_update = Signal(str, object, str, int)
    search_update = Signal(str, object, str)
    notice_update = Signal(str)
    art_update = Signal(str, str, object)
    trackChanged = Signal()
    lyricsChanged = Signal()
    statusChanged = Signal()
    settingsChanged = Signal()
    searchChanged = Signal()
    noticeChanged = Signal()
    frameChanged = Signal()
    focusChanged = Signal()
    jump = Signal()

    def __init__(self, demo=False):
        super().__init__()
        self.demo = demo
        self._spectrum = Spectrum(self)
        self.settings = QSettings()
        self.options = {k: self.settings.value(k, v, type=type(v)) for k, v in DEFAULTS.items()}
        if not self.settings.contains("motion"):
            self.options["motion"] = desktop_motion()
        self.track = Track()
        self.lyric_track = self.track
        self.service = ""
        self.cover = Cover()
        self.players = ()
        self.mpris_error = ""
        self.playback_status = "Stopped"
        self.can_seek = False
        self.clock = PlaybackClock()
        self.position = self.lyric_position = 0.0
        self.focus = -1
        self.active = []
        self.gap = ()
        self.offset = 0
        self.rows = []
        self.timeline = Timeline(Lyrics((), ""))
        self.document = None
        self.message = ""
        self.notice = ""
        self.candidates = []
        self.results = []
        self.search_status = ""
        self.player_update.connect(self.on_player)
        self.lyrics_update.connect(self.on_lyrics)
        self.search_update.connect(self.on_search)
        self.notice_update.connect(self.on_notice)
        self.art_update.connect(self.on_art)
        self.worker = Worker(self, demo)
        try:
            sources = provider_urls(self.options["lyricsServers"])
        except ValueError:
            sources = []
        self.worker.call(self.worker.lyric_sources, sources)
        self.worker.call(self.worker.preference, self.options["player"], self.options["reconcile"])
        if demo:
            self.on_player("Demo", {"Metadata": {"xesam:title": DEMO_TITLE, "xesam:artist": [DEMO_ARTIST],
                           "xesam:album": DEMO_ALBUM, "mpris:length": DEMO_DURATION * 1000,
                           "mpris:trackid": "/demo/one"},
                           "PlaybackStatus": "Playing", "CanSeek": True}, (), 0, True, "")

    @Property(QObject, constant=True)
    def spectrum(self):
        return self._spectrum

    @Slot(str, object, object, object, bool, str)
    def on_player(self, service, values, players, position, immediate, error):
        track = metadata(values.get("Metadata") or {})
        metadata_changed = track.key != self.track.key
        same_playing_track = same_track(self.track, track)
        changed = track.key != self.lyric_track.key
        # Keep the lyric document and its storage key together until the song changes.
        if (changed and self.rows and service == self.service and same_playing_track
                and same_track(self.lyric_track, track)):
            changed = False
        switched = service != self.service or track.path != self.track.path
        reset_clock = switched or (metadata_changed and not same_playing_track)
        if reset_clock:
            self.clock.reset()
        playing = values.get("PlaybackStatus") == "Playing"
        try:
            rate = float(values.get("Rate", 1))
            if not -32 <= rate <= 32 or not rate:
                rate = 1.0
        except (TypeError, ValueError):
            rate = 1.0
        if (playing, rate, track.duration) != (self.clock.playing, self.clock.rate, self.clock.duration):
            self.clock.state(playing, rate, track.duration)
        if position is not None:
            self.clock.reconcile(position, immediate=immediate or reset_clock)
        if (metadata_changed or track.art != self.track.art) and not self.demo:
            if not track.title:
                self.cover = Cover()
            self.worker.call(self.worker.artwork, track)
        self.track, self.service, self.players = track, service, players
        self.mpris_error = error
        self.playback_status = values.get("PlaybackStatus", "Stopped")
        self.can_seek = bool(values.get("CanSeek")) and (self.demo or track.path.startswith("/") and
                                                        not track.path.endswith("/NoTrack"))
        if changed:
            self.lyric_track = track
            self.document = None
            self.rows = []
            self.offset = 0
            self.timeline = Timeline(Lyrics((), ""))
            self.candidates, self.results = [], []
            self.search_status = ""
            self.searchChanged.emit()
            self.message = "Finding lyrics…" if track.title else ""
            self.worker.call(self.worker.load, track, dict(self.options))
            self.lyricsChanged.emit()
        self.trackChanged.emit()
        self.statusChanged.emit()
        self.tick()
        if immediate or changed or reset_clock:
            self.jump.emit()

    @Slot(str, str, object)
    def on_art(self, key, original, cover):
        if self.track.key == key and self.track.art == original:
            self.cover = cover
            self.trackChanged.emit()

    @Slot(str, object, str, int)
    def on_lyrics(self, key, lyrics, message, offset):
        if key != self.lyric_track.key:
            return
        self.document, self.message, self.offset = lyrics, message, offset
        self.timeline = Timeline(lyrics or Lyrics((), ""))
        self.rows = [asdict(line) for line in lyrics.lines] if lyrics else []
        for row in self.rows:
            row["words"] = list(row["words"])
        self.lyricsChanged.emit()
        self.statusChanged.emit()
        self.tick()
        self.jump.emit()

    @Slot(str, object, str)
    def on_search(self, key, candidates, error):
        if key != self.lyric_track.key:
            return
        self.candidates = candidates
        self.results = [{"title": c.title, "artist": c.artist, "album": c.album,
                         "seconds": round((c.duration or 0) / 1000), "quality": c.lyrics.quality,
                         "source": c.lyrics.source, "language": lyrics_language(c.lyrics).upper()}
                        for c in candidates]
        self.search_status = error or ("" if candidates else "No results")
        self.searchChanged.emit()

    @Slot(str)
    def on_notice(self, message):
        self.notice = message
        self.noticeChanged.emit()

    @Slot()
    def tick(self):
        position = self.clock.position()
        if self.demo and position >= DEMO_DURATION and self.clock.playing:
            self.clock.reconcile(0, True)
            position = 0
            self.jump.emit()
        lyric_position = position + self.offset + self.options["globalOffset"]
        active = self.timeline.active(lyric_position)
        focus = active[0] if active else self.timeline.anchor(lyric_position)
        gap = self.timeline.gap(lyric_position) if not active else ()
        if (self.position, self.lyric_position) != (position, lyric_position):
            self.position, self.lyric_position = position, lyric_position
            self.frameChanged.emit()
        if (focus, active, gap) != (self.focus, self.active, self.gap):
            self.focus, self.active, self.gap = focus, active, gap
            self.focusChanged.emit()

    @Slot(float)
    def seek(self, position):
        if not self.can_seek:
            return
        position = max(0, min(self.track.duration or position, position))
        if self.demo:
            self.clock.reconcile(position, True)
            self.tick()
            self.jump.emit()
        else:
            self.worker.call(lambda: asyncio.create_task(self.worker.bus.control("SetPosition", position)))

    @Slot(float)
    def seekLine(self, position):
        self.seek(position - self.offset - self.options["globalOffset"])

    @Slot(str)
    def control(self, action):
        if action not in {"PlayPause", "Next", "Previous", "Resync"}:
            return
        if self.demo:
            if action == "PlayPause":
                self.clock.state(not self.clock.playing, 1, DEMO_DURATION)
                self.statusChanged.emit()
            if action in {"Next", "Previous"}:
                self.seek(0)
        elif action == "Resync":
            self.worker.call(lambda: asyncio.create_task(self.worker.bus.poll(True)))
        else:
            self.worker.call(lambda: asyncio.create_task(self.worker.bus.control(action)))

    @Slot(str, "QVariant")
    def setting(self, key, value):
        if key not in DEFAULTS:
            return
        value = type(DEFAULTS[key])(value)
        if key == "lyricsServers":
            try:
                urls = provider_urls(value)
            except ValueError as exc:
                self.on_notice(str(exc))
                return
            value = "\n".join(urls)
            if value != self.options[key]:
                self.worker.call(self.worker.lyric_sources, urls)
        if self.options[key] == value:
            return
        self.options[key] = value
        self.settings.setValue(key, value)
        self.settingsChanged.emit()
        self.tick()
        if key in {"player", "reconcile"}:
            self.worker.call(self.worker.preference, self.options["player"], self.options["reconcile"])
        if key in {"language", "style", "diacritics", "automatic", "lyricsServers"}:
            self.worker.call(self.worker.load, self.lyric_track, dict(self.options))

    @Slot(int)
    def songOffset(self, value):
        if not self.track.title:
            return
        self.offset = max(-30000, min(30000, value))
        self.worker.call(self.worker.save_offset, self.lyric_track.key, self.offset)
        self.statusChanged.emit()
        self.tick()
        self.jump.emit()

    @Slot(str)
    def search(self, query):
        if not self.track.title:
            return
        self.search_status = "Searching lyrics sources…"
        self.results, self.candidates = [], []
        self.searchChanged.emit()
        self.worker.call(self.worker.search, self.lyric_track, query.strip())

    @Slot(int)
    def choose(self, index):
        if 0 <= index < len(self.candidates):
            self.worker.call(self.worker.use_lyrics, self.lyric_track, dict(self.options), self.candidates[index].lyrics)

    @Slot()
    def useAutomatic(self):
        if self.track.title:
            self.worker.call(self.worker.use_automatic, self.lyric_track, dict(self.options))

    @Slot(QUrl)
    def importLyrics(self, url):
        if self.track.title and url.isLocalFile():
            self.worker.call(self.worker.import_lyrics, self.lyric_track, dict(self.options), Path(url.toLocalFile()))

    @Slot(int, str)
    def edit(self, index, text):
        if self.document and 0 <= index < len(self.document.lines) and len(text) <= 4000:
            key = correction_key(self.document.lines[index], index)
            self.worker.call(self.worker.edit, self.lyric_track, dict(self.options), key, text.strip())

    @Slot(QUrl, bool)
    def corrections(self, url, exporting):
        if url.isLocalFile():
            self.worker.call(self.worker.transfer, self.lyric_track, dict(self.options), Path(url.toLocalFile()), exporting)

    @Slot()
    def clearCache(self):
        self.worker.call(self.worker.clear_cache)
        self.on_notice("Provider and romanization cache cleared")

    @Slot(bool)
    def autostart(self, enabled):
        path = xdg("XDG_CONFIG_HOME", ".config") / "autostart" / "io.github.lyricflow.LyricFlow.desktop"
        try:
            if enabled:
                launcher = xdg("XDG_DATA_HOME", ".local/share") / "applications" / path.name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(launcher.read_text(), encoding="utf-8")
            else:
                path.unlink(missing_ok=True)
            self.settingsChanged.emit()
        except OSError as exc:
            self.on_notice(f"Autostart requires an installed launcher: {exc}")

    @Property("QVariantMap", notify=settingsChanged)
    def preferences(self):
        return self.options

    @Property(bool, notify=settingsChanged)
    def autostartEnabled(self):
        return (xdg("XDG_CONFIG_HOME", ".config") / "autostart" /
                "io.github.lyricflow.LyricFlow.desktop").exists()

    @Property("QVariantMap", notify=trackChanged)
    def song(self):
        cover = self.cover
        if self.demo:
            cover = Cover(QUrl.fromLocalFile(str(Path(__file__).parent / "ui" / "night.svg")).toString(),
                          ("#936077", "#c47866", "#453759"), 640)
        return {"title": self.track.title, "artist": self.track.artist, "album": self.track.album,
                "duration": self.track.duration or 0, "art": cover.url,
                "artPalette": list(cover.palette), "artEdge": cover.edge}

    @Property("QVariantList", notify=lyricsChanged)
    def lines(self):
        return self.rows

    @Property("QVariantList", notify=searchChanged)
    def matches(self):
        return self.results

    @Property(str, notify=searchChanged)
    def searchStatus(self):
        return self.search_status

    @Property(str, notify=noticeChanged)
    def notification(self):
        return self.notice

    @Property(str, notify=statusChanged)
    def status(self):
        if self.track.title:
            return self.message
        if self.mpris_error:
            return self.mpris_error
        if self.options["player"] and not self.service:
            return "The selected player is offline. Choose Last active player in Settings."
        if self.service:
            return "Start a song in the connected player."
        return "Play a song in a music app that supports MPRIS."

    @Property(str, notify=lyricsChanged)
    def quality(self):
        return self.document.quality if self.document else ""

    @Property(str, notify=lyricsChanged)
    def source(self):
        return self.document.source if self.document else ""

    @Property(bool, notify=lyricsChanged)
    def hasTranslation(self):
        return bool(self.document and any(l.translation for l in self.document.lines))

    @Property(bool, notify=lyricsChanged)
    def hasUnromanized(self):
        return bool(self.document and any(SCRIPT.search(l.romanized) for l in self.document.lines))

    @Property(bool, notify=statusChanged)
    def playing(self):
        return self.clock.playing

    @Property(bool, notify=statusChanged)
    def seekable(self):
        return self.can_seek

    @Property(float, notify=frameChanged)
    def elapsed(self):
        return self.position

    @Property(float, notify=frameChanged)
    def lyricTime(self):
        return self.lyric_position

    @Property(int, notify=focusChanged)
    def currentLine(self):
        return self.focus

    @Property("QVariantList", notify=focusChanged)
    def activeLines(self):
        return self.active

    @Property("QVariantList", notify=focusChanged)
    def lyricGap(self):
        return list(self.gap)

    @Property(int, notify=statusChanged)
    def trackOffset(self):
        return self.offset

    @Property("QVariantList", notify=statusChanged)
    def availablePlayers(self):
        names = sorted(set(self.players) | ({self.options["player"]} if self.options["player"] else set()))
        return [{"label": "Last active player", "service": ""}] + [
            {"label": name.removeprefix(PREFIX) + (" (offline)" if name not in self.players else ""),
             "service": name} for name in names]

    @Property(str, notify=statusChanged)
    def diagnostics(self):
        return f"{self.service or self.mpris_error or 'No active MPRIS service'} · {len(self.players)} discovered"
