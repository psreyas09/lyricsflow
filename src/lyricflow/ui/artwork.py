"""Sample cover colors and find larger artwork for confirmed releases."""
import asyncio
import hashlib
import logging
import re
from colorsys import rgb_to_hsv
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import monotonic
from urllib.parse import unquote, urlparse
from uuid import UUID

import httpx

from ..lyrics.providers import clean, similarity
from ..qt import QtCore, QtGui
from ..storage import xdg

LOG = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_DIMENSION = 8192
TARGET_EDGE = 840
RASTER = {b"jpeg", b"jpg", b"png", b"webp", b"gif", b"avif"}


@dataclass(frozen=True)
class Cover:
    url: str = ""
    palette: tuple[str, ...] = ()
    edge: int = 0


def sample(path, url):
    reader = QtGui.QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if not size.isValid() or max(size.width(), size.height()) > MAX_DIMENSION:
        return Cover()
    reader.setScaledSize(QtCore.QSize(36, 36))
    image = reader.read()
    if image.isNull():
        return Cover()
    bins = {}
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() < 128:
                continue
            rgb = (pixel.red(), pixel.green(), pixel.blue())
            hue, saturation, brightness = rgb_to_hsv(*(channel / 255 for channel in rgb))
            if brightness < .13 or brightness > .93 and saturation < .15:
                continue
            key = int(hue * 12) if saturation >= .16 else 12
            bucket = bins.setdefault(key, [])
            bucket.append(rgb)
    ranked = sorted(bins.values(), key=len, reverse=True)[:3]
    palette = []
    for bucket in ranked:
        rgb = [round(sum(channel) / len(bucket)) for channel in zip(*bucket)]
        palette.append(QtGui.QColor(*rgb).name())
    return Cover(url, tuple(palette), min(size.width(), size.height()))


def youtube_music_cover_url(url):
    """Request a larger rendition of the same YouTube Music cover."""
    if not url.startswith("https://"):
        return ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    if parsed.scheme != "https" or parsed.netloc not in {"lh3.googleusercontent.com", "lh3.ggpht.com"}:
        return ""
    path = re.sub(r"=w[0-9]+-h[0-9]+(?=-|$)", "=w1200-h1200", parsed.path)
    path = re.sub(r"=s[0-9]+(?=-|$)", "=s1200", path)
    return parsed._replace(path=path).geturl() if path != parsed.path else ""


async def load_artwork(url, client):
    try:
        parsed = urlparse(url)
        if parsed.scheme == "file" and parsed.netloc in {"", "localhost"}:
            return sample(Path(unquote(parsed.path)), url)
        if parsed.scheme not in {"http", "https"}:
            return Cover()
        cache = xdg("XDG_CACHE_HOME", ".cache") / "lyricflow" / "artwork"
        cache.mkdir(parents=True, exist_ok=True)
        path = cache / hashlib.sha256(url.encode()).hexdigest()
        local_url = QtCore.QUrl.fromLocalFile(str(path)).toString()
        if path.exists():
            return sample(path, local_url)
        temporary = None
        try:
            async with client.stream("GET", url, follow_redirects=True) as response:
                response.raise_for_status()
                if not response.headers.get("Content-Type", "").lower().startswith("image/"):
                    return Cover()
                size = 0
                with NamedTemporaryFile(dir=cache, delete=False) as output:
                    temporary = Path(output.name)
                    async for block in response.aiter_bytes():
                        size += len(block)
                        if size > MAX_IMAGE_BYTES:
                            return Cover()
                        output.write(block)
            if bytes(QtGui.QImageReader(str(temporary)).format()).lower() not in RASTER:
                return Cover()
            artwork = sample(temporary, local_url)
            if not artwork.url:
                return artwork
            temporary.replace(path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

        old = sorted((file for file in cache.iterdir() if len(file.name) == 64),
                     key=lambda file: file.stat().st_mtime, reverse=True)
        for file in old[64:]:
            file.unlink(missing_ok=True)
        return artwork
    except (OSError, ValueError, httpx.HTTPError):
        LOG.debug("Artwork unavailable", exc_info=False)
        return Cover()


class CoverArchive:
    def __init__(self, client):
        self.client = client
        self.lock = asyncio.Lock()
        self.next_request = 0.0

    async def release_paths(self, track):
        if not track.title or not track.artist or not track.album:
            return []
        artist = track.artist.split(",")[0].split(" & ")[0].strip()
        title = clean(track.title)

        def quoted(value):
            return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'

        params = {"query": f"recording:{quoted(title)} AND artist:{quoted(artist)} "
                           f"AND release:{quoted(track.album)}",
                  "fmt": "json", "limit": 6}
        async with self.lock:
            await asyncio.sleep(max(0, self.next_request - monotonic()))
            try:
                result = await self.client.get("https://musicbrainz.org/ws/2/recording", params=params)
                result.raise_for_status()
                data = result.json()
            finally:
                self.next_request = monotonic() + 1.1
        releases = []
        groups = []
        for recording in data.get("recordings", []):
            if similarity(title, recording.get("title", "")) < .95:
                continue
            duration = recording.get("length")
            if track.duration and duration and abs(track.duration - duration) > 4500:
                continue
            names = [credit.get("name", "") for credit in recording.get("artist-credit", [])]
            if not any(similarity(artist, name) >= .93 for name in names):
                continue
            for release in recording.get("releases", []):
                if similarity(track.album, release.get("title", "")) < .95:
                    continue
                for kind, entity, paths in (
                    ("release", release, releases),
                    ("release-group", release.get("release-group") or {}, groups),
                ):
                    try:
                        identifier = str(UUID(entity["id"]))
                    except (AttributeError, KeyError, TypeError, ValueError):
                        continue
                    path = f"{kind}/{identifier}"
                    if path not in paths:
                        paths.append(path)
        return releases[:2] + groups[:1]

    async def larger_cover(self, track, original, previous=None):
        best, source = original, ""
        if previous:
            cached = await load_artwork(previous, self.client)
            if cached.edge > max(original.edge, 240):
                return cached, previous

        try:
            for path in await self.release_paths(track):
                front = f"https://coverartarchive.org/{path}/front"
                for url in (f"{front}-1200", front):
                    cover = await load_artwork(url, self.client)
                    if cover.edge > max(best.edge, 240):
                        best, source = cover, url
                        if best.edge >= TARGET_EDGE:
                            return best, source
        except (httpx.HTTPError, OSError, TypeError, ValueError):
            LOG.debug("Larger release artwork unavailable")
        return best, source
