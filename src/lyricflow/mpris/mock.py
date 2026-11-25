"""Interactive MPRIS fixture with two original, locally generated tracks."""
import argparse
import asyncio
import json
import logging
from dataclasses import asdict
from pathlib import Path
from time import monotonic

from dbus_fast import PropertyAccess, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, dbus_property, method, signal

from ..demo import DEMO_ALBUM, DEMO_ARTIST, DEMO_DURATION, DEMO_TITLE, demo_lyrics
from .clock import PlaybackClock
from .player import PATH, PLAYER, PREFIX

NAMES = [DEMO_TITLE, "Daybreak Signal"]


class Root(ServiceInterface):
    def __init__(self):
        super().__init__("org.mpris.MediaPlayer2")

    @dbus_property(access=PropertyAccess.READ)
    def Identity(self) -> "s":
        return "LyricFlow Test Player"


class MockPlayer(ServiceInterface):
    def __init__(self, now=monotonic):
        super().__init__(PLAYER)
        self.index = 0
        self.clock = PlaybackClock(now)
        self.clock.state(True, 1, DEMO_DURATION)

    @dbus_property(access=PropertyAccess.READ)
    def Metadata(self) -> "a{sv}":
        return {
            "mpris:trackid": Variant("o", "/lyricflow/track/" + str(self.index)),
            "mpris:length": Variant("x", DEMO_DURATION * 1000),
            "xesam:title": Variant("s", NAMES[self.index]),
            "xesam:artist": Variant("as", [DEMO_ARTIST]),
            "xesam:album": Variant("s", DEMO_ALBUM),
        }

    @dbus_property(access=PropertyAccess.READ)
    def PlaybackStatus(self) -> "s":
        return "Playing" if self.clock.playing else "Paused"

    @dbus_property(access=PropertyAccess.READ)
    def Position(self) -> "x":
        return int(self.clock.position() * 1000)

    @dbus_property(access=PropertyAccess.READ)
    def Rate(self) -> "d":
        return float(self.clock.rate)

    @dbus_property(access=PropertyAccess.READ)
    def CanSeek(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanControl(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanPlay(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanPause(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanGoNext(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanGoPrevious(self) -> "b":
        return True

    @signal()
    def Seeked(self, position) -> "x":
        return position

    @method()
    def PlayPause(self):
        self.clock.state(not self.clock.playing, self.clock.rate, DEMO_DURATION)
        self.emit_properties_changed({"PlaybackStatus": self.PlaybackStatus})

    @method()
    def Play(self):
        if not self.clock.playing:
            self.PlayPause()

    @method()
    def Pause(self):
        if self.clock.playing:
            self.PlayPause()

    @method()
    def Seek(self, amount: "x"):
        self.SetPosition(self.Metadata["mpris:trackid"].value, self.Position + amount)

    @method()
    def SetPosition(self, track: "o", position: "x"):
        if track == self.Metadata["mpris:trackid"].value:
            self.clock.reconcile(max(0, min(DEMO_DURATION, position / 1000)), True)
            self.Seeked(self.Position)

    @method()
    def Next(self):
        self.index = (self.index + 1) % len(NAMES)
        self.clock.reconcile(0, True)
        self.emit_properties_changed({"Metadata": self.Metadata})

    @method()
    def Previous(self):
        self.Next()


async def run():
    bus = await MessageBus().connect()
    bus.export(PATH, Root())
    bus.export(PATH, MockPlayer())
    name = PREFIX + "lyricflow_mock"
    await bus.request_name(name)
    print(f"Mock player available as {name}", flush=True)
    await bus.wait_for_disconnect()


def main():
    parser = argparse.ArgumentParser(description="Run the LyricFlow MPRIS fixture")
    parser.add_argument("--write-demo", type=Path, metavar="FILE", help="Export the original demo timing fixture")
    args = parser.parse_args()
    if args.write_demo:
        try:
            with args.write_demo.open("x", encoding="utf-8") as stream:
                json.dump(asdict(demo_lyrics()), stream, ensure_ascii=False, indent=2)
        except OSError as error:
            parser.error(str(error))
        return 0
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        return 0
    except OSError as error:
        logging.error("Could not connect to session D-Bus: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
