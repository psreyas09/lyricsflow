import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

try:
    from lyricflow.qt import QtCore, QtGui
except ImportError:
    QtCore = None


@unittest.skipIf(QtCore is None, "Qt bindings are unavailable")
class CoverTests(unittest.IsolatedAsyncioTestCase):
    def test_youtube_music_cover_url_preserves_the_image_and_crop(self):
        from lyricflow.ui.artwork import youtube_music_cover_url

        url = "https://lh3.googleusercontent.com/album-cover=w120-h120-l90-rj?token=value"
        self.assertEqual(youtube_music_cover_url(url), url.replace("w120-h120", "w1200-h1200"))
        self.assertEqual(
            youtube_music_cover_url("https://lh3.ggpht.com/album-cover=s120-c"),
            "https://lh3.ggpht.com/album-cover=s1200-c",
        )
        self.assertEqual(youtube_music_cover_url(url.replace("lh3.googleusercontent.com", "example.com")), "")
        self.assertEqual(youtube_music_cover_url("https://lh3.googleusercontent.com/album-cover"), "")

    async def test_replaces_thumbnail_only_with_a_larger_matched_release(self):
        from lyricflow.mpris.player import Track
        from lyricflow.ui.artwork import CoverArchive, load_artwork

        identifier = "76df3287-6cda-33eb-8e9a-044b5e15ffdd"
        wrong = "528df55b-7636-4c9d-8d60-0a5f45431ac9"
        group = "c31a5e2b-0bf8-32e0-8aeb-ef4ba9973932"
        image_format = QtGui.QImage.Format.Format_RGB32

        def save_cover(path, edge):
            image = QtGui.QImage(edge, edge, image_format)
            painter = QtGui.QPainter(image)
            painter.fillRect(0, 0, edge // 2, edge, QtGui.QColor("#af4622"))
            painter.fillRect(edge // 2, 0, edge, edge, QtGui.QColor("#20709e"))
            painter.end()
            self.assertTrue(image.save(str(path)))

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"XDG_CACHE_HOME": directory}):
            root = Path(directory)
            save_cover(root / "tiny.png", 120)
            save_cover(root / "medium.png", 320)
            save_cover(root / "big.png", 1000)

            def request(req):
                if req.url.host == "musicbrainz.org":
                    self.assertIn('release:"Night Frequencies"', req.url.params["query"])
                    return httpx.Response(200, json={"recordings": [{
                        "title": "Signal", "length": 42000, "artist-credit": [{"name": "Night"}],
                        "releases": [{"title": "A compilation", "id": wrong},
                                     {"title": "Night Frequencies", "id": identifier,
                                      "release-group": {"id": group}}],
                    }]})
                if req.url.path == f"/release/{identifier}/front-1200":
                    return httpx.Response(200, content=(root / "medium.png").read_bytes(),
                                          headers={"Content-Type": "image/png"})
                if req.url.path in {f"/release/{identifier}/front", f"/release-group/{group}/front-1200"}:
                    return httpx.Response(404)
                self.assertEqual(req.url.path, f"/release-group/{group}/front")
                return httpx.Response(200, content=(root / "big.png").read_bytes(),
                                      headers={"Content-Type": "image/png"})

            async with httpx.AsyncClient(transport=httpx.MockTransport(request)) as client:
                original = await load_artwork((root / "tiny.png").as_uri(), client)
                self.assertEqual(original.edge, 120)
                self.assertEqual(len(original.palette), 2)
                track = Track("Signal", "Night", "Night Frequencies", 42000)
                cover, source = await CoverArchive(client).larger_cover(track, original)
                self.assertEqual(cover.edge, 1000)
                self.assertTrue(cover.url.startswith("file:"))
                self.assertIn(group, source)
                self.assertEqual(len(cover.palette), 2)
