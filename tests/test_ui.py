import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

try:
    from lyricflow.qt import QtCore, QtQml, QtWidgets
except ImportError:
    QtCore = None


@unittest.skipIf(QtCore is None, "Qt bindings are unavailable")
class QmlTests(unittest.TestCase):
    maxDiff = None
    def test_demo_loads_paints_and_switches_display_modes(self):
        from lyricflow.app_controller import Controller
        from lyricflow.ui.karaoke import KaraokeText

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["lyricflow-test", "-platform", "offscreen"])
        app.setOrganizationName("lyricflow-test")
        app.setApplicationName("lyricflow-test")
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        old_data = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = directory.name
        QtCore.QSettings.setDefaultFormat(QtCore.QSettings.Format.IniFormat)
        QtCore.QSettings.setPath(QtCore.QSettings.Format.IniFormat, QtCore.QSettings.Scope.UserScope, directory.name)

        def reset_environment():
            if old_data is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = old_data

        self.addCleanup(reset_environment)
        worker_loop = asyncio.new_event_loop()
        # The test runner can be sandboxed with socketpair writes denied.
        # A short test-only timer lets its event loop drain cross-thread work.
        def wake():
            worker_loop.call_later(.03, wake)
        wake()
        with patch("lyricflow.app_controller.asyncio.new_event_loop", return_value=worker_loop):
            controller = Controller(demo=True)
        self.addCleanup(controller.worker.close)
        QtQml.qmlRegisterType(KaraokeText, "LyricFlow", 1, 0, "KaraokeText")
        engine = QtQml.QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda messages: warnings.extend(message.toString() for message in messages))
        context = engine.rootContext()
        context.setContextProperty("app", controller)
        context.setContextProperty("windowSystem", app.platformName())
        engine.load(QtCore.QUrl.fromLocalFile(
            str(Path(__file__).resolve().parents[1] / "src/lyricflow/ui/Main.qml")))
        self.assertTrue(engine.rootObjects(), warnings)
        window = engine.rootObjects()[0]
        window.show()

        def wait(milliseconds):
            loop = QtCore.QEventLoop()
            QtCore.QTimer.singleShot(milliseconds, loop.quit)
            loop.exec()

        for _ in range(30):
            if controller.quality:
                break
            wait(100)
        self.assertEqual(controller.quality, "Word sync")
        ambient = window.findChild(QtCore.QObject, "ambientGradient")
        controller.setting("motion", True)
        initial_phase = ambient.property("phase")
        wait(180)
        self.assertGreater(ambient.property("phase"), initial_phase)
        controller.setting("motion", False)
        stopped_phase = ambient.property("phase")
        wait(140)
        self.assertEqual(ambient.property("phase"), stopped_phase)
        for effect in ("CAVA bars", "Spectrum wave", "Solid", "Gradient"):
            controller.setting("backgroundEffect", effect)
            wait(30)
            self.assertEqual(window.property("backgroundEffect"), effect)
            self.assertFalse(window.property("audioActive"))
        controller.setting("motion", True)
        controller.control("PlayPause")
        controller.seek(3900)
        wait(550)
        lyric_list = window.findChild(QtCore.QObject, "lyricList")
        before = lyric_list.property("contentY")
        controller.clock.reconcile(8200, True)
        controller.tick()
        wait(165)
        during = lyric_list.property("contentY")
        wait(400)
        after = lyric_list.property("contentY")
        self.assertLess(before, during)
        self.assertLess(during, after)
        self.assertEqual(controller.currentLine, 1)
        self.assertEqual(controller.elapsed, 8200)
        def visible_karaoke(item):
            if isinstance(item, KaraokeText) and item.isVisible() and item.active and item.content:
                yield item
            for child in item.childItems():
                yield from visible_karaoke(child)

        highlights = {item.variant: item.position for item in visible_karaoke(window.contentItem())}
        self.assertEqual(highlights, {"original": 8200, "romanized": 8200})
        from lyricflow.languages.romanizer import apply_romanization
        from lyricflow.lyrics.models import Line, Lyrics, Word
        from lyricflow.qt import QtGui

        def luminance(channels):
            linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
                      for c in channels]
            return sum(channel * weight for channel, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

        cover_color = QtGui.QColor(controller.song["artPalette"][0])
        controller.setting("backgroundEffect", "Solid")
        self.assertEqual(window.property("lyricTint"), cover_color)
        for theme in ("Dark", "Light"):
            controller.setting("theme", theme)
            for effect in ("Gradient", "CAVA bars", "Spectrum wave"):
                controller.setting("backgroundEffect", effect)
                controller.setting("background", 1.0)
                highlight = window.property("lyricAccent")
                gray = QtGui.QColor("#bbbbbb")
                neutral_highlight = QtCore.QMetaObject.invokeMethod(
                    window, "chooseLyricColor", QtCore.Q_RETURN_ARG(QtGui.QColor),
                    QtCore.Q_ARG(QtGui.QColor, gray))
                self.assertAlmostEqual(neutral_highlight.hsvHueF(),
                                       window.property("lyricFallbackTint").hsvHueF(), places=3)
                canvas = window.property("canvas")
                opacity = window.property("lyricEffectOpacity")
                extreme = int(theme == "Dark")
                worst_background = [(1 - opacity) * c + opacity * extreme for c in canvas.getRgbF()[:3]]
                for color in (highlight, neutral_highlight):
                    levels = sorted((luminance(color.getRgbF()[:3]), luminance(worst_background)))
                    self.assertGreaterEqual((levels[1] + 0.05) / (levels[0] + 0.05), 4.5)
                self.assertGreater(window.property("lyricScrimOpacity"), 0)
        for item in visible_karaoke(window.contentItem()):
            self.assertEqual(item.accent, window.property("lyricAccent"))
        controller.setting("theme", "Dark")
        controller.setting("background", 0.24)
        controller.setting("backgroundEffect", "Gradient")

        line = KaraokeText()
        line.setWidth(240)
        line.content = "Timing"
        line.base = QtGui.QColor("#ffffff")
        line.accent = cover_color
        line.active = True
        image = QtGui.QImage(240, round(line.measured), QtGui.QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QtGui.QPainter(image)
        line.paint(painter)
        painter.end()
        self.assertIn(cover_color.rgba(), {image.pixel(x, y) for x in range(image.width())
                                           for y in range(image.height())})

        line.accent = line.base
        line.spans = [{"original": line.content, "start_ms": 1000, "end_ms": 2000}]
        line.progressUnderline = True

        def underline_width(position):
            line.position = position
            image.fill(0)
            painter = QtGui.QPainter(image)
            line.paint(painter)
            painter.end()
            return sum(image.pixelColor(x, image.height() - 1).alpha() > 0
                       for x in range(image.width()))

        self.assertEqual(underline_width(900), 0)
        early = underline_width(1250)
        self.assertGreater(early, 0)
        self.assertGreater(underline_width(1750), early)
        self.assertEqual(underline_width(2000), 0)

        text = "ഞാൻ നിന്നെ ഓർക്കുന്നു"
        malayalam = apply_romanization(Lyrics((Line(text, 1000, 4000, (
            Word("ഞാൻ ", 1100, 1700), Word("നിന്നെ ", 1800, 2400), Word("ഓർക്കുന്നു", 2500, 3800),
        )),), "Typography fixture"))
        original_demo = controller.document
        controller.on_lyrics(controller.track.key, malayalam, "", 0)
        controller.seek(2000)
        wait(300)
        pair = {item.variant: item for item in visible_karaoke(window.contentItem())}
        self.assertEqual(set(pair), {"original", "romanized"})
        self.assertEqual(pair["original"].pixelSize, pair["romanized"].pixelSize)
        self.assertEqual(pair["original"].layout.font().weight(), QtGui.QFont.Weight.Bold)
        self.assertEqual(pair["romanized"].layout.font().weight(), QtGui.QFont.Weight.Normal)
        for item in pair.values():
            self.assertEqual(item.layout.font().families()[0], "Noto Sans Malayalam")
            self.assertEqual(item.position, 2000)
        if path := os.environ.get("LYRICFLOW_MALAYALAM_SCREENSHOT"):
            self.assertTrue(window.grabWindow().save(path))
        controller.on_lyrics(controller.track.key, original_demo, "", 0)
        controller.seek(8200)
        wait(300)
        settings = window.findChild(QtCore.QObject, "preferencesDialog")
        QtCore.QMetaObject.invokeMethod(settings, "open")
        wait(70)
        self.assertTrue(settings.property("opened"))
        QtCore.QMetaObject.invokeMethod(settings, "close")
        wait(70)
        image = window.grabWindow()
        self.assertFalse(image.isNull())
        self.assertGreater(len({image.pixel(x, y) for x in range(0, image.width(), 30)
                                for y in range(0, image.height(), 30)}), 12)
        if path := os.environ.get("LYRICFLOW_TEST_SCREENSHOT"):
            self.assertTrue(image.save(path))
        for width in (510, 880, 1160):
            window.setWidth(width)
            wait(30)
        for mode in ("Romanized", "Original", "Both"):
            controller.setting("mode", mode)
            wait(30)
        song_panel = window.findChild(QtCore.QObject, "songPanel")
        controller.setting("motion", False)
        cover_url = QtCore.QUrl(controller.song["art"])

        def assert_cover(name):
            artwork = window.findChild(QtCore.QObject, name)
            self.assertIsNotNone(artwork, name)
            self.assertTrue(artwork.property("visible"), name)
            for _ in range(30):
                if any(image.property("source") == cover_url
                       and image.property("progress") == 1
                       and image.property("opacity") == 1
                       for image in artwork.findChildren(QtCore.QObject)):
                    return
                wait(20)
            self.fail(f"{name} did not display the current cover")

        for content, art_visible, lyrics_visible in (
            ("Song details", True, False),
            ("Song name and lyrics", False, True),
            ("Everything", True, True),
        ):
            controller.setting("contentView", content)
            wait(30)
            self.assertEqual(song_panel.property("visible"), art_visible)
            self.assertEqual(lyric_list.property("visible"), lyrics_visible)
            if art_visible:
                assert_cover("panelArtwork")
        for width, height, stacked in (
            (440, 760, False), (440, 820, True), (849, 820, True),
            (850, 820, False), (1160, 820, False),
        ):
            window.resize(width, height)
            wait(30)
            self.assertEqual(window.property("artworkOnTop"), stacked)
            self.assertTrue(lyric_list.property("visible"))
            assert_cover("headerArtwork" if width < 850 else "panelArtwork")
        window.resize(440, 820)
        controller.setting("contentView", "Song name and lyrics")
        self.assertFalse(window.property("artworkOnTop"))
        controller.setting("contentView", "Everything")
        window.resize(1160, 760)
        for mode in ("Compact", "Overlay", "Main", "Full screen", "Main"):
            QtCore.QMetaObject.invokeMethod(window, "mode", QtCore.Q_ARG("QVariant", mode))
            self.assertEqual(window.property("viewMode"), mode)
            wait(35)
            if mode in ("Compact", "Overlay"):
                assert_cover("floatingArtwork")
            else:
                assert_cover("panelArtwork" if window.property("wide") else "headerArtwork")

        track = controller.track
        lyrics = controller.document
        pauses = Lyrics((Line("First", 1000, 2000), Line("Second", 6000, 8000)), "Gap fixture")
        controller.on_lyrics(track.key, pauses, "", 0)
        indicator = window.findChild(QtCore.QObject, "lyricGapIndicator")
        for position, bounds in ((500, [0, 1000]), (1500, []), (3000, [2000, 6000]),
                                 (6500, []), (9000, [])):
            controller.seek(position)
            self.assertEqual(controller.lyricGap, bounds)
            self.assertEqual(indicator.property("visible"), bool(bounds))
            if bounds:
                self.assertAlmostEqual(indicator.property("progress"),
                                       (position - bounds[0]) / (bounds[1] - bounds[0]), delta=0.02)
        controller.on_lyrics(track.key, lyrics, "", 0)
        values = {"Metadata": {"xesam:title": track.title, "xesam:artist": [track.artist],
                               "mpris:length": (track.duration + 300) * 1000,
                               "mpris:trackid": track.path, "xesam:url": "file:///demo"},
                  "PlaybackStatus": "Playing", "CanSeek": True}
        with patch.object(controller.worker, "call") as dispatch:
            for position in (None, 8400):
                controller.on_player(controller.service, values, (), position, False, "")
                self.assertIs(controller.document, lyrics)
                self.assertEqual(controller.song["duration"], track.duration + 300)
                self.assertEqual(controller.lyric_track.key, track.key)
                self.assertTrue(lyric_list.property("visible"))
            dispatch.assert_not_called()
            values["Metadata"].update({"xesam:title": "Next song", "mpris:trackid": "/demo/two"})
            controller.on_player(controller.service, values, (), 0, True, "")
            self.assertIsNone(controller.document)
            self.assertFalse(lyric_list.property("visible"))
            dispatch.assert_called_once_with(controller.worker.load, controller.track, controller.options)
        self.assertEqual(warnings, [])
        window.close()
        del engine
