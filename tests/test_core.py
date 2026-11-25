import asyncio
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
from dbus_fast import Message, MessageType, Variant

from lyricflow.demo import demo_lyrics
from lyricflow.languages.romanizer import apply_romanization, correction_key, romanize
from lyricflow.lyrics.models import Line, Lyrics, Timeline, Word, word_fill
from lyricflow.lyrics.parsers import lrc, lyricsfile, structured, ttml
from lyricflow.lyrics.providers import Amll, BetterLyrics, LrcLib, best_match, lrclib_record, provider_urls, similarity
from lyricflow.mpris.clock import PlaybackClock
from lyricflow.mpris.mock import MockPlayer
from lyricflow.mpris.player import BUS, PATH, PLAYER, PREFIX, PROPS, Mpris, Track, metadata, select
from lyricflow.storage import LyricsManager, Store, restore


def fixture(**extra):
    return {"id": 1, "trackName": "Signal", "artistName": "Night", "albumName": "City",
            "duration": 42, "syncedLyrics": "[00:01.000] Signal\n[00:02] Return", **extra}


WORD_FILE = """
version: '1.0'
metadata:
  language: ko
lines:
  - text: '사랑해 baby'
    start_ms: 1000
    end_ms: 3100
    words:
      - {text: '사랑해 ', start_ms: 1000}
      - {text: baby, start_ms: 2200}
"""


class PlaybackTests(unittest.TestCase):
    def test_metadata_and_selection(self):
        track = metadata({"mpris:length": Variant("x", 42123456),
                          "xesam:artist": Variant("as", ["A", "B"]),
                          "xesam:title": Variant("s", "曲")})
        self.assertEqual((track.duration, track.artist, track.title), (42123, "A, B", "曲"))
        self.assertIsNone(metadata({}).duration)
        self.assertNotEqual(Track("Signal", "A").key, Track("Signal", "B").key)
        players = {PREFIX + "ytmdesktop": {"PlaybackStatus": "Playing"},
                   PREFIX + "vlc": {"PlaybackStatus": "Playing"}}
        self.assertEqual(select(players), PREFIX + "vlc")
        self.assertEqual(select(players, activity={PREFIX + "ytmdesktop": 4}), PREFIX + "ytmdesktop")
        self.assertEqual(select(players, PREFIX + "vlc"), PREFIX + "vlc")
        players[PREFIX + "ytmdesktop"]["PlaybackStatus"] = "Paused"
        self.assertEqual(select(players), PREFIX + "vlc")

    def test_clock_play_pause_rate_seek_and_slew(self):
        now = [0]
        clock = PlaybackClock(lambda: now[0])
        clock.state(True, 1, 8000)
        now[0] = 1
        self.assertEqual(clock.position(), 1000)
        clock.reconcile(1200)
        self.assertEqual(clock.position(), 1000)
        now[0] = 1.5
        self.assertEqual(clock.position(), 1600)
        clock.state(False, 1, 8000)
        now[0] = 3
        self.assertEqual(clock.position(), 1600)
        clock.state(True, 1.5, 8000)
        now[0] = 4
        self.assertEqual(clock.position(), 3100)
        clock.reconcile(7900)
        self.assertEqual(clock.position(), 7900)
        now[0] = 5
        self.assertEqual(clock.position(), 8000)
        clock.reconcile(10, True)
        self.assertEqual(clock.position(), 10)
        clock.reset()
        self.assertEqual(clock.position(), 0)

    def test_mock_player(self):
        player = MockPlayer(lambda: 0)
        player.PlayPause()
        self.assertEqual(player.PlaybackStatus, "Paused")
        player.SetPosition(player.Metadata["mpris:trackid"].value, 13_500_000)
        self.assertEqual(player.Position, 13_500_000)
        player.Next()
        self.assertEqual(player.Position, 0)
        self.assertEqual(player.Metadata["xesam:title"].value, "Daybreak Signal")


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_discover_seek_track_change_and_disconnect(self):
        snapshots = []
        player = Mpris(lambda *args: snapshots.append(args))
        name = PREFIX + "ytmdesktop"
        player.daemon = AsyncMock(side_effect=[[[name, "org.kde.Other"]], [":1.49"]])
        player.call = AsyncMock(return_value=[{"PlaybackStatus": Variant("s", "Playing"),
                                               "Position": Variant("x", 1_200_000)}])
        await player.scan()
        self.assertEqual(snapshots[-1][:4], (name, player.players[name], (name,), 1200))
        seek = Message(path=PATH, interface=PLAYER, member="Seeked", sender=":1.49",
                       message_type=MessageType.SIGNAL, signature="x", body=[12_350_000])
        player.message(seek)
        self.assertEqual(snapshots[-1][3:5], (12350, True))
        player.poll = AsyncMock()
        changed = Message(path=PATH, interface=PROPS, member="PropertiesChanged", sender=":1.49",
                          message_type=MessageType.SIGNAL, signature="sa{sv}as",
                          body=[PLAYER, {"Metadata": Variant("a{sv}", {"xesam:title": Variant("s", "Next")})}, []])
        player.message(changed)
        await asyncio.sleep(0)
        self.assertEqual(metadata(snapshots[-1][1]["Metadata"]).title, "Next")
        self.assertIsNone(snapshots[-1][3])
        self.assertTrue(snapshots[-1][4])
        self.assertNotIn("Position", player.players[name])
        player.poll.assert_awaited_once()
        player.message(Message(path="/org/freedesktop/DBus", interface=BUS, member="NameOwnerChanged",
                               message_type=MessageType.SIGNAL, signature="sss", body=[name, ":1.49", ""]))
        self.assertEqual(snapshots[-1][0], "")


    async def test_last_player_wins_and_stays_selected_when_paused(self):
        player = Mpris(lambda *args: None)
        old, new = PREFIX + "ytmdesktop", PREFIX + "vlc"
        player.players = {old: {"PlaybackStatus": "Playing"}, new: {"PlaybackStatus": "Paused"}}
        player.owners = {old: ":1.41", new: ":1.42"}
        player.poll = AsyncMock()
        player.emit()
        self.assertEqual(player.active, old)

        def property_change(name, **values):
            player.message(Message(path=PATH, interface=PROPS, member="PropertiesChanged",
                                   sender=player.owners[name], message_type=MessageType.SIGNAL,
                                   signature="sa{sv}as", body=[PLAYER, values, []]))

        property_change(new, PlaybackStatus=Variant("s", "Playing"))
        self.assertEqual(player.active, new)
        property_change(old, Volume=Variant("d", .5))
        property_change(new, PlaybackStatus=Variant("s", "Paused"))
        self.assertEqual(player.active, new)
        property_change(old, PlaybackStatus=Variant("s", "Paused"))
        property_change(old, PlaybackStatus=Variant("s", "Playing"))
        self.assertEqual(player.active, old)
        await asyncio.sleep(0)

    async def test_stale_position_is_ignored_until_manual_resync(self):
        snapshots = []
        player = Mpris(lambda *args: snapshots.append(args))
        name = PREFIX + "test"
        player.players[name] = {"PlaybackStatus": "Playing"}
        player.owners[name] = ":1.12"
        player.emit()
        player.call = AsyncMock(return_value=[Variant("x", 20_000_000)])
        await player.poll()
        self.assertEqual(snapshots[-1][3], 20_000)
        await player.poll()
        self.assertEqual(len(snapshots), 2)
        await player.poll(True)
        self.assertEqual(snapshots[-1][3:5], (20_000, True))


class ParserTests(unittest.TestCase):
    def test_lrc_offset_repeat_and_silence(self):
        lyrics = lrc("[offset:+50]\n[00:01.5][00:05.000] 夜\n[00:06]", "local")
        self.assertEqual(lyrics.quality, "Line sync")
        self.assertEqual([(line.start_ms, line.end_ms) for line in lyrics.lines], [(1550, 5050), (5050, 6050)])
        self.assertEqual(Timeline(lyrics).active(6100), [])

    def test_enhanced_lrc_word_timing(self):
        lyrics = lrc("[00:01.000]<00:01.000>夜 <00:01.25>sun<00:03.100>\n[00:03.50] rest", "local")
        self.assertEqual(lyrics.quality, "Word sync")
        self.assertEqual(lyrics.lines[0].words[1], Word("sun", 1250, 3100))
        self.assertEqual(word_fill(lyrics.lines[0].words[1], 2175), .5)

    def test_ttml_timed_spans_and_parent_timing(self):
        text = """<tt xmlns="http://www.w3.org/ns/ttml" xml:lang="ja"><body begin="1s"><div>
        <p begin="00:00:01.000" end="4s"><span begin="0s" dur="200ms">君</span> <span begin="400ms" end="1s">空</span></p>
        </div></body></tt>"""
        result = ttml(text, "local")
        self.assertEqual(result.language, "ja")
        self.assertEqual(result.lines[0].words, (Word("君 ", 2000, 2200), Word("空", 2400, 3000)))
        self.assertEqual(result.lines[0].end_ms, 5000)

    def test_lyricsfile_structured_and_overlap(self):
        lyrics = lyricsfile(WORD_FILE, "local")
        self.assertEqual(lyrics.lines[0].words, (Word("사랑해 ", 1000, 2200), Word("baby", 2200, 3100)))
        self.assertEqual(restore(asdict(lyrics)), lyrics)
        timeline = Timeline(Lyrics((Line("a", 1, 8), Line("b", 3, 6)), "local"))
        self.assertEqual(timeline.active(4), [1, 0])
        self.assertEqual(timeline.active(6), [0])
        self.assertEqual(timeline.active(8), [])
        with self.assertRaises(ValueError):
            structured({"lines": [{"text": "x", "start_ms": -1}]}, "invalid")

    def test_word_timestamp_absent_end(self):
        result = lrc("[00:01]<00:01>word", "local")
        self.assertIsNone(result.lines[0].words[0].end_ms)
        self.assertEqual(word_fill(result.lines[0].words[0], 1000), 1)


class RomanizationTests(unittest.TestCase):
    def test_korean_and_mixed(self):
        self.assertEqual(romanize("사랑해"), "saranghae")
        self.assertEqual(romanize("한국어"), "hangugeo")
        self.assertIn(" baby saranghae", romanize("君が好き baby 사랑해"))

    def test_japanese_dictionary(self):
        try:
            import pykakasi  # noqa: F401
        except ImportError:
            self.skipTest("Japanese reading dictionary unavailable")
        self.assertEqual(romanize("ありがとう"), "arigatou")
        self.assertEqual(romanize("東京", "ja"), "toukyou")

    def test_mandarin(self):
        self.assertEqual(romanize("我爱你", "zh", "ascii"), "wo ai ni")

    def test_cantonese(self):
        try:
            import pycantonese  # noqa: F401
        except ImportError:
            self.skipTest("Cantonese dictionary unavailable")
        self.assertIn("gwong2", romanize("廣東話", "yue"))

    def test_icu_arabic_indic_and_cyrillic(self):
        for source in ("أحبك", "नमस्ते", "നമസ്കാരം", "நான்", "Я тебя люблю", "Καλημέρα",
                       "გამარჯობა", "שלום", "สวัสดี", "ሰላም"):
            with self.subTest(source=source):
                self.assertNotEqual(romanize(source), source)
        self.assertEqual(romanize("Café mañana"), "Café mañana")
        self.assertEqual(romanize("Café mañana", style="ascii"), "Cafe manana")

    def test_natural_tamil_and_malayalam_readings_keep_timed_spans(self):
        self.assertEqual(romanize("நான் உன்னை நினைக்கிறேன்"), "naan unnai ninaikkiren")
        self.assertEqual(romanize("ഞാൻ നിന്നെ ഓർക്കുന്നു"), "njaan ninne orkkunnu")
        self.assertEqual(romanize("പുഴയോരം കണ്ടു"), "puzhayoram kandu")
        self.assertIn("ā", romanize("நான்", style="standard"))
        word = Word("നിന്നെ", 1000, 2120)
        source = Lyrics((Line(word.original, 1000, 2120, (word,)),), "fixture")
        reading = apply_romanization(source).lines[0].words[0]
        self.assertEqual(reading, Word(word.original, 1000, 2120, "ninne"))

    def test_natural_hindi_removes_marks_without_changing_timing(self):
        self.assertEqual(romanize("तुम ही हो"), "tum hi ho")
        self.assertEqual(romanize("मैं तुम्हें प्यार करता हूँ", "hi"), "main tumhen pyaar kartaa hoon")
        self.assertEqual(romanize("ज़िंदगी ख़ूबसूरत है", "hi"), "zindagi khoobsoorat hai")
        self.assertEqual(romanize("चलो baby चलो", "hi"), "chalo baby chalo")
        self.assertIn("ī", romanize("ही", "hi", "standard"))
        word = Word("चाँद ", 1200, 2200)
        supplied = Word("सितारे", 2300, 3150, "sitārē")
        source = Lyrics((Line("चाँद सितारे", 1200, 3150, (word, supplied), "cām̐d sitārē"),),
                        "fixture", "hi")
        line = apply_romanization(source).lines[0]
        self.assertEqual(line.romanized, "chaand sitaare")
        self.assertEqual(line.words, (Word("चाँद ", 1200, 2200, "chaand "),
                                      Word("सितारे", 2300, 3150, "sitaare")))

    def test_source_timing_and_mapped_correction(self):
        original = lyricsfile(WORD_FILE, "local")
        line = original.lines[0]
        mapped = apply_romanization(original, corrections={correction_key(line, 0): "sa rang hae | baby"})
        self.assertEqual(mapped.lines[0].words[0], Word("사랑해 ", 1000, 2200, "sa rang hae "))
        for before, after in zip(demo_lyrics().lines, apply_romanization(demo_lyrics()).lines):
            self.assertEqual([(w.original, w.start_ms, w.end_ms) for w in before.words],
                             [(w.original, w.start_ms, w.end_ms) for w in after.words])


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_match_and_quality(self):
        track = Track("Signal (Official Video)", "Night", "City", 42000)
        timed = lrclib_record(fixture(lyricsfile=WORD_FILE, hasWordSync=True))
        ordinary = lrclib_record(fixture())
        self.assertEqual(timed.lyrics.quality, "Word sync")
        self.assertIs(best_match(track, [ordinary, timed]), timed)
        tagged = Track('Signal (From "City")', "Night - Topic", "City", 42000)
        self.assertIs(best_match(tagged, [ordinary, timed]), timed)
        self.assertIsNone(best_match(track, [lrclib_record(fixture(duration=65))]))
        self.assertIsNone(best_match(track, [lrclib_record(fixture(trackName="Signal (Live)"))]))
        self.assertIsNone(best_match(track, [lrclib_record(fixture(artistName="Somebody"))]))
        self.assertIsNone(lrclib_record(fixture(instrumental=True)))
        self.assertGreater(similarity("사랑해", "Saranghae"), .9)
        self.assertEqual(lrclib_record(fixture(lyricsfile="*missing")).lyrics.quality, "Line sync")

    async def test_aval_rejects_cached_english_and_selects_tamil_recording(self):
        track = Track("Aval (Trending Version)", "Santhosh Narayanan, Pradeep Kumar, & Priya Hemesh",
                      "Aval (Trending Version)", 90021)
        english = Lyrics((Line("An English version", 1000, 3000,
                               (Word("An English version", 1000, 3000),)),), "LRCLIB", "en")
        tamil = Lyrics((Line("தமிழ் பாடல்", 900, 2800),), "LRCLIB")
        class Catalog:
            name = "LRCLIB"
        wrong = lrclib_record({"id": 12, "trackName": track.title, "artistName": track.artist,
                               "albumName": track.album, "duration": 90,
                               "plainLyrics": "Lyrics in English"})
        candidate = lrclib_record({"id": 13, "trackName": track.title, "artistName": track.artist,
                                   "albumName": track.album, "duration": 90,
                                   "syncedLyrics": "[00:00.900] தமிழ் பாடல்"})
        self.assertIsNone(best_match(track, [wrong]))
        self.assertIs(best_match(track, [wrong, candidate]), candidate)
        self.assertIsNone(best_match(track, [replace(candidate, duration=250_000)]))

        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "language.sqlite3")
            store.put("auto", "LRCLIB:" + track.key, asdict(english))
            manager = LyricsManager(store, [Catalog()])
            self.assertIsNone((await manager.resolve(track, automatic=False))[0])
            manager.search = AsyncMock(return_value=([wrong], ""))
            self.assertEqual(await manager.resolve(track), (None, "No original-language lyrics found for this recording."))
            manager.search = AsyncMock(return_value=([wrong, replace(candidate, lyrics=tamil)], ""))
            self.assertEqual(await manager.resolve(track), (tamil, ""))
            store.db.close()

    async def test_signature_lookup_can_upgrade_line_timing(self):
        track = Track("Signal", "Night", "City", 42000)

        def serve(request):
            if request.url.path == "/api/get":
                self.assertEqual(dict(request.url.params), {
                    "track_name": "Signal", "artist_name": "Night", "album_name": "City", "duration": "42",
                })
                return httpx.Response(200, json=fixture(lyricsfile=WORD_FILE, hasWordSync=True))
            return httpx.Response(200, json=[fixture()])

        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
            rows = await LrcLib(client).search(track)
        self.assertEqual(best_match(track, rows).lyrics.quality, "Word sync")

    async def test_amll_native_words_win_over_lrclib_line_timing(self):
        raw = """<tt xmlns="http://www.w3.org/ns/ttml"
          xmlns:ttm="http://www.w3.org/ns/ttml#metadata"><body><div>
          <p begin="00:01.000" end="3.100">
            <span begin="1.000" end="00:01.500">Signal </span>
            <span begin="1.600s" end="2.000">home</span>
            <span ttm:role="x-translation" xml:lang="en">Come home</span>
            <span ttm:role="x-bg" begin="2.500" end="3.100">
              <span begin="2.500" end="3.100">(echo)</span>
            </span>
          </p></div></body></tt>"""
        song = {"id": 72, "musicNames": ["Signal"], "artistNames": ["Night"], "albumNames": ["City"]}
        track = Track("Signal", "Night", "City", 42000)

        def serve(request):
            if request.url.host == "lrclib.net":
                return httpx.Response(200, json=[fixture()] if request.url.path.endswith("search") else fixture())
            if request.url.path == "/v1/lyrics/search":
                self.assertEqual(request.url.params["musicName"], "Signal")
                return httpx.Response(200, json={"status": 200, "data": {"items": [song]}})
            self.assertEqual(request.url.path, "/v1/lyrics/get")
            self.assertEqual(request.url.params["id"], "72")
            return httpx.Response(200, json={"status": 200, "data": dict(song, lyrics=raw)})

        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "catalog.sqlite3")
            async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
                result, error = await LyricsManager(store, [LrcLib(client), Amll(client)]).resolve(track)
            self.assertEqual(error, "")
            self.assertEqual(result.source, "AMLL TTML")
            self.assertEqual(result.lines[0].original, "Signal home")
            self.assertEqual(result.lines[0].words, (Word("Signal ", 1000, 1500), Word("home", 1600, 2000)))
            self.assertEqual(result.lines[0].translation, "Come home")
            self.assertEqual(result.lines[1].words, (Word("(echo)", 2500, 3100),))
            store.db.close()

    async def test_better_lyrics_falls_through_to_qq_with_real_word_gaps(self):
        track = Track("Signal", "Night", "City", 42000)
        calls = []
        qrc = """<QrcInfos><LyricInfo><Lyric_1 LyricContent="[ti:Signal]
        [ar:Night]
        [al:City]
        [1000,2300]Signal (1100,400)home(1700,550)
        [3400,700]Return(3500,400)"/></LyricInfo></QrcInfos>"""

        def serve(request):
            calls.append((request.url.host, request.url.path))
            if request.url.host == "lrclib.net":
                return httpx.Response(404)
            self.assertEqual(dict(request.url.params), {"s": "Signal", "a": "Night", "al": "City", "d": "42"})
            if request.url.path == "/getLyrics":
                return httpx.Response(401)
            return httpx.Response(200, json={"provider": "qq", "lyrics": qrc})

        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "fallback.sqlite3")
            async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
                manager = LyricsManager(store, [LrcLib(client), BetterLyrics(client)])
                result, message = await manager.resolve(track)
                self.assertEqual(message, "")
                self.assertEqual(result.source, "Better Lyrics · QQ")
                self.assertEqual(result.lines[0].words, (Word("Signal ", 1100, 1500), Word("home", 1700, 2250)))
                self.assertEqual([path for host, path in calls if host == "api.betterlyrics.org"],
                                 ["/getLyrics", "/qq/getLyrics"])
                self.assertEqual((await manager.resolve(track, automatic=False))[0], result)
            store.db.close()

    async def test_better_lyrics_falls_through_to_kugou_and_rejects_wrong_artist(self):
        track = Track("Signal", "Night", "City", 42000)
        raw = """<QrcInfos><LyricInfo><Lyric_1
          LyricContent="[ar:Another Artist][1000,1000]Wrong(1100,300)"/></LyricInfo></QrcInfos>"""

        def serve(request):
            paths = {
                "/getLyrics": httpx.Response(404),
                "/qq/getLyrics": httpx.Response(200, json={"lyrics": raw}),
                "/kugou/getLyrics": httpx.Response(200, json={"lyrics": "[00:01.20]Signal"}),
            }
            return paths[request.url.path]

        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
            rows = await BetterLyrics(client).search(track)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].lyrics.source, "Better Lyrics · Kugou")
        self.assertEqual(rows[0].lyrics.quality, "Line sync")

    async def test_previous_catalog_cache_survives_adding_fallback(self):
        track = Track("Signal", "Night", "City", 42000)
        expected = lrclib_record(fixture()).lyrics

        def network(request):
            self.fail("Offline mode should not make any requests")

        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "upgrade.sqlite3")
            store.put("auto", "LRCLIB|AMLL TTML:" + track.key, asdict(expected))
            async with httpx.AsyncClient(transport=httpx.MockTransport(network)) as client:
                manager = LyricsManager(store, [LrcLib(client), Amll(client), BetterLyrics(client)])
                result, _ = await manager.resolve(track, automatic=False)
            self.assertEqual(expected, result)
            store.db.close()

    async def test_good_primary_word_match_skips_fallback(self):
        track = Track("Signal", "Night", "City", 42000)

        def serve(request):
            self.assertEqual(request.url.host, "lrclib.net")
            return httpx.Response(200, json=[fixture(lyricsfile=WORD_FILE, hasWordSync=True)])

        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "main.sqlite3")
            async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
                manager = LyricsManager(store, [LrcLib(client), BetterLyrics(client)])
                result, _ = await manager.resolve(track)
            self.assertEqual(result.source, "LRCLIB")
            store.db.close()

    async def test_configured_provider_and_sidecar(self):
        self.assertEqual(provider_urls("https://lrclib.net/ https://my-lyrics.example/"),
                         ["https://my-lyrics.example"])
        with self.assertRaises(ValueError):
            provider_urls("http://my-lyrics.example")

        def serve(request):
            if request.url.host == "my-lyrics.example":
                self.assertNotIn("Lrclib-App-Id", request.headers)
                return httpx.Response(200, json=[fixture(lyricsfile=WORD_FILE, hasWordSync=True)])
            return httpx.Response(403)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "signal.flac"
            audio.touch()
            track = Track("Signal", "Night", "City", 42000, url=audio.as_uri())
            store = Store(root / "cache.db")
            async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
                manager = LyricsManager(store, [LrcLib(client, headers={"Lrclib-App-Id": "example"}),
                                                LrcLib(client, "https://my-lyrics.example")])
                lyrics, _ = await manager.resolve(track)
                self.assertEqual((lyrics.source, lyrics.quality), ("my-lyrics.example", "Word sync"))
                audio.with_suffix(".lyricsfile.yaml").write_text(WORD_FILE)
                lyrics, _ = await manager.resolve(track, automatic=False)
                self.assertTrue(lyrics.source.startswith("Beside audio"))
                self.assertEqual(lyrics.quality, "Word sync")
            store.db.close()

    async def test_offline_and_provider_errors(self):
        track = Track("Signal", "Night", "City", 42000)
        calls = []
        def serve(request):
            calls.append(request)
            self.assertIn(request.url.path, {"/api/search", "/api/get"})
            self.assertEqual(request.url.params["track_name"], "Signal")
            if len(calls) == 1:
                return httpx.Response(200, json=[fixture()])
            return httpx.Response(404 if len(calls) <= 3 else 403)

        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "library.sqlite3")
            async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
                manager = LyricsManager(store, [LrcLib(client)])
                lyrics, _ = await manager.resolve(track)
                self.assertEqual(lyrics.quality, "Line sync")
                self.assertEqual((await manager.resolve(track, automatic=False))[0], lyrics)
                self.assertEqual(len(calls), 3)
                rows, error = await manager.search(track, refresh=True)
                self.assertEqual(rows[0].lyrics, lyrics)
                self.assertIn("unavailable", error)
                store.put("manual", track.key, asdict(lyrics))
                store.put("offset", track.key, 123)
                store.clear_cache()
                self.assertEqual(store.get("offset", track.key), 123)
                store.db.close()
                reopened = Store(Path(directory) / "library.sqlite3")
                self.assertEqual(restore(reopened.get("manual", track.key)), lyrics)
                reopened.db.close()
