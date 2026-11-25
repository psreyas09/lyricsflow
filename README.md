# LyricFlow

LyricFlow is a native Qt Quick karaoke companion for Linux. It follows the active MPRIS player, looks up lyrics on LRCLIB, AMLL TTML, and Better Lyrics, and displays the original text, Latin-script pronunciation, or both. It supports local timing files and works offline with a previously fetched or imported lyric library.

## Install pre-built packages

Pre-built packages for `v0.1.8` are available in the [`dist/`](dist/) directory (and on the Releases page):

- **Fedora / RHEL / openSUSE (`.rpm`)**

  ```bash
  sudo dnf install ./dist/lyricflow-0.1.8-3.noarch.rpm
  ```

- **Debian / Ubuntu / Linux Mint / Pop!_OS (`.deb`)**

  ```bash
  sudo apt install ./dist/lyricflow_0.1.8-3_all.deb
  ```

- **Arch Linux / Manjaro / EndeavourOS (`.pkg.tar.zst`)**

  ```bash
  sudo pacman -U ./dist/lyricflow-0.1.8-3-any.pkg.tar.zst
  ```

After installing, launch **LyricFlow** from your application menu or run `lyricflow` from a terminal (`lyricflow --demo` for the offline demo).

## Install from source 

Run these commands from the project directory. The installation creates a virtual environment and installs a launcher and icon for your account. Keep the project directory in the same location after installation.

```bash
sudo dnf install python3 python3-devel gcc-c++ pkgconf-pkg-config libicu-devel \
  libxkbcommon libxkbcommon-x11 libxcb xcb-util-cursor mesa-libEGL mesa-libGL \
  desktop-file-utils playerctl
./install.sh
./run.sh
```

PySide6 supplies Qt. Python 3.12 or later is required. If your architecture or system Python lacks a compatible PySide6 wheel, use another supported interpreter with its development headers, for example `LYRICFLOW_PYTHON=python3.12 ./install.sh`. Installing Noto fonts for CJK, Arabic, and Indic scripts can improve fallback coverage. Fedora’s GNOME launcher shows **LyricFlow** after installation. To force X11 through XWayland, use `QT_QPA_PLATFORM=xcb ./run.sh`.

```bash
./run.sh --demo     # Offline, looping demonstration with no audio
./run.sh --debug    # Playback and provider diagnostics
```

## Verify with YouTube Music Desktop

1. Start [YouTube Music Desktop](https://github.com/ytmdesktop/ytmdesktop) and play a track. If your version offers **Integrations → Enhanced media service**, enable it and restart the player if needed. [YTMDesktop releases](https://github.com/ytmdesktop/ytmdesktop/releases)
2. From a terminal in the same desktop session, list the exposed players and inspect the actual instance name.

   ```bash
   playerctl --list-all
   busctl --user list | grep org.mpris.MediaPlayer2
   playerctl --player PLAYER_NAME metadata
   playerctl --player PLAYER_NAME position
   ```

3. Launch LyricFlow. Automatic selection follows the last player observed starting or resuming playback, changing its playing track, or seeking during playback. It stays with that player on pause. At startup, if there is no event history, it selects a playing player with a stable service-name tie break. Use **Settings → Playback** to pin a particular player. The Settings diagnostics show the selected D-Bus service.
4. Confirm that song information appears. Play through a line, pause, resume, then seek backwards and forwards in the player. The lyrics should freeze on pause and follow seeks. Click a timed lyric to seek from LyricFlow when the player supports it.
5. Check the source and quality below the lyrics. **Word sync** uses supplied word boundaries. **Line sync** updates at line boundaries. **Plain lyrics** remain static. If the match or edition looks wrong, choose another via **Search**, or import a known timing file. Set a track-specific offset for a constant mismatch.

If D-Bus lists no YTMDesktop service, LyricFlow can use another MPRIS player or its offline demo. GNOME media controls are a quick indication that the player is exporting a service. Check the YTMDesktop version and integration setting before troubleshooting lyrics.

To exercise transport without YTMDesktop, install the project and run `.venv/bin/lyricflow-mock-player` in one terminal and `./run.sh` in another. Select `lyricflow_mock` in Settings. The mock alternates between two original fixtures and exposes pause, seek, and track changes. Export the original fixture with `.venv/bin/lyricflow-mock-player --write-demo demo.json` and import it for the mock’s first track to test playback and word highlighting together. Use `--demo` for a self-contained preview with bundled lyrics and cover art.

## Timed lyrics and pronunciation

Import UTF-8 LRC, enhanced LRC, TTML, Lyricsfile YAML, or JSON using **Settings → Lyrics**. The application preserves supplied intervals. When a format omits a word end, a later source boundary defines the fill where available. A word with no usable end activates at its start. Plain lyrics receive no synthetic timing. The TTML reader supports parallel containers with clock-time and wall-clock offset units, including nested offsets and timed spans. Frame/tick expressions and sequential containers are unsupported.

A JSON import can include source-aligned Latin readings and an optional English translation. Times are integer milliseconds. A phrase can occupy a single source timing span.

```json
{
  "language": "ko",
  "lines": [
    {
      "original": "사랑해",
      "start_ms": 12400,
      "end_ms": 12950,
      "translation": "I love you",
      "words": [
        {
          "original": "사랑해",
          "romanized": "saranghae",
          "start_ms": 12400,
          "end_ms": 12950
        }
      ]
    }
  ]
}
```

Japanese uses a kanji reading dictionary, Mandarin uses phrase-aware Pinyin, Cantonese can use Jyutping, and Hangul uses Revised Romanization spellings with common consonant changes. Natural Hindi, Tamil, and Malayalam readings use common Latin spellings without scholarly marks. Standardized mode retains ICU diacritics. ICU supplies broad script coverage including Cyrillic, Greek, Georgian, Armenian, Arabic script, and Indic scripts. Mixed-script text retains existing Latin text in the default mode. Select a reading language for ambiguous Han-only Japanese or Cantonese. Short vowels omitted in Arabic or Hebrew cannot always be reconstructed. Readings can also vary with Korean sound changes, uncommon names, dialects, and context. Unconverted characters remain visible.

A timed word and its reading share the original timing interval. A separately supplied full-line reading or a free-form correction gets word fill only if it aligns with the timed readings. To attach each edited reading to its existing span, use **Edit track romanization** and separate span readings with ` | `. Corrections can be exported and merged from JSON. Translation modes require English text supplied by the lyrics file. LyricFlow does not machine-translate.

## Window layouts and backgrounds

Choose what to display from the **View** menu. LyricFlow remembers your choice.

- **Everything** includes the cover, song title, artist, album, and lyrics. At 850 px wide or more, the cover sits on the left. In narrower windows at least 780 px tall, it sits above the lyrics. Shorter narrow windows show a small cover beside the title.
- **Song details** centers the cover and track information.
- **Song name and lyrics** shows the title and lyrics without artwork.

**Compact**, **Overlay**, and **Full screen** are also in the **View** menu. Press F11 to toggle full screen or Escape to return to the main view. To move a window on Niri or another compatible compositor, drag the small grip at the top center. Drag an edge or corner to resize. The overlay requests always-on-top, and Wayland compositors decide whether to honor it. Click-through is available on X11. A supported tray can restore the main window, and GNOME may require an AppIndicator or StatusNotifier extension.

In **Settings → Background effect**, select **Gradient**, **CAVA bars**, **Spectrum wave**, or **Solid**. Gradient uses colors sampled from the cover and drifts during playback. Adjust **Background intensity** for the gradient and visualizers, or **Gradient softness** for the gradient. LyricFlow follows GNOME’s animation preference at startup until you set **Animate backgrounds and transitions** yourself.

Both audio visualizers use CAVA to read system audio through the PulseAudio interface, including on Fedora’s PipeWire setup. Install it with `sudo dnf install cava` and start playback to see the visualization. The animation setting must be enabled.

## Privacy and offline storage

- LyricFlow searches LRCLIB and AMLL TTML first and prefers genuine word timings for a credible match. Without a credible word-timed match, it also checks Better Lyrics' public TTML cache, QQ word timings, and Kugou line timings. Better Lyrics receives the playing track’s title, artist, album, and duration for this lookup. An uncached TTML record may require provider authorization, and every catalog has coverage gaps. A line timestamp says when a line starts. It lacks the timing for each word, and a different recording of the same song may need different timings. [LRCLIB API](https://lrclib.net/docs) · [AMLL TTML API](https://amll.dev/reference/http-api/overview) · [Better Lyrics](https://better-lyrics.boidu.dev/) · [Better Lyrics API](https://lyrics-api-docs.boidu.dev/docs/providers)
- For a local audio file, LyricFlow also checks for a file with the same stem and an `.lrc`, `.ttml`, `.lyrics.json`, or `.lyricsfile.yaml` extension. In **Settings → Lyrics**, you can add authorized LRCLIB-compatible servers by base URL, one per line. They should implement `/api/search` and `/api/get` with standard records. HTTPS and loopback HTTP are supported.
- Online lookup sends track metadata or your search terms to the enabled catalogs, and a Spotify recording ID to AMLL when available. LyricFlow loads the cover supplied by the player. For supported YouTube Music artwork URLs, it requests a larger version of the same image. It can also send track and release metadata to MusicBrainz to look for a higher-resolution cover in the Cover Art Archive. When no sharper cover is available, small images stay at their source size. No telemetry is collected. Disabling automatic lookup still allows deliberate searches and nearby files. `LYRICFLOW_USER_AGENT` can identify a distribution with its project URL or contact, and `LYRICFLOW_LRCLIB_APP_ID` can supply an optional registered public app ID. [Cover Art Archive API](https://musicbrainz.org/doc/Cover_Art_Archive/API)
- Settings live under `$XDG_CONFIG_HOME/lyricflow`, the library under `$XDG_DATA_HOME/lyricflow`, and downloaded artwork under `$XDG_CACHE_HOME/lyricflow`. Defaults are `~/.config`, `~/.local/share`, and `~/.cache`. Online results revalidate after a day and remain available if lookup fails. Clearing the fetched-lyrics cache retains imports, corrections, and per-track offsets.

## Tests and packaging

```bash
./install.sh --dev
.venv/bin/python -m pytest -q
.venv/bin/python -m build
desktop-file-validate packaging/io.github.lyricflow.LyricFlow.desktop
```

The suite uses mocked HTTP, MPRIS messages, an offline demo, and a Qt offscreen render. It does not need an internet connection. Python’s built-in runner can also run the suite with `PYTHONPATH=src python3 -m unittest discover -s tests -v` when runtime dependencies are present. Dictionary tests skip when their optional development environment dependencies are missing. The project produces a Python wheel and provides XDG desktop integration via the installer.

## Uninstall

```bash
./install.sh --uninstall
```

This removes the launcher, icon, and login entry. Delete the project `.venv` to remove its installed Python packages. The lyric library and settings are retained so corrections survive a reinstall.
