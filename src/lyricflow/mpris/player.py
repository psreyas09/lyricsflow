import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass

from dbus_fast import Message, MessageType
from dbus_fast.aio import MessageBus

LOG = logging.getLogger(__name__)
PREFIX = "org.mpris.MediaPlayer2."
PATH = "/org/mpris/MediaPlayer2"
PLAYER = "org.mpris.MediaPlayer2.Player"
PROPS = "org.freedesktop.DBus.Properties"
BUS = "org.freedesktop.DBus"


def unwrap(value):
    return getattr(value, "value", value)


@dataclass(frozen=True)
class Track:
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: int | None = None
    path: str = ""
    url: str = ""
    art: str = ""
    recording: str = ""

    @property
    def key(self):
        fields = (self.recording or self.url, self.title, self.artist, self.album, self.duration)
        return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()


def metadata(values):
    def string(key):
        value = unwrap(values.get(key))
        return value if isinstance(value, str) else ""

    artists = unwrap(values.get("xesam:artist"))
    duration = unwrap(values.get("mpris:length"))
    return Track(
        string("xesam:title"),
        ", ".join(a for a in artists if isinstance(a, str)) if isinstance(artists, list) else "",
        string("xesam:album"),
        duration // 1000 if type(duration) is int and duration > 0 else None,
        string("mpris:trackid"), string("xesam:url"), string("mpris:artUrl"),
        string("xesam:musicBrainzTrackID"),
    )


def select(players, preferred="", activity=None, current=""):
    if preferred:
        return preferred if preferred in players else ""
    activity = activity or {}
    return min(players, key=lambda name: (
        -activity.get(name, 0),
        players[name].get("PlaybackStatus") != "Playing",
        name != current,
        name,
    ), default="")


def track_changed(before, after):
    old, new = metadata(before), metadata(after)
    return old.path != new.path or (old.key != new.key and not same_track(old, new))


def same_track(old, new):
    """Recognize metadata updates for a track that is already playing."""
    if not old.title or old.title != new.title or old.path != new.path:
        return False
    if any(before and after and before != after for before, after in (
        (old.artist, new.artist), (old.album, new.album),
        (old.recording, new.recording), (old.url, new.url),
    )):
        return False
    if old.duration and new.duration and abs(old.duration - new.duration) > 4500:
        return False
    # Without an ID, require the artist to match before overlooking a duration update.
    return bool((old.path and not old.path.endswith("/NoTrack"))
                or (old.recording and new.recording) or (old.url and new.url)
                or (old.artist and old.artist == new.artist))


class Mpris:
    def __init__(self, publish):
        self.publish = publish
        self.bus = None
        self.players = {}
        self.owners = {}
        self.activity = {}
        self.event_order = 0
        self.preferred = ""
        self.active = ""
        self.revision = 0
        self.poll_serial = 0
        self.last_sample = None
        self.interval = 5.0
        self.error = ""

    async def call(self, destination, path, interface, member, signature="", body=None):
        reply = await asyncio.wait_for(self.bus.call(Message(
            destination=destination, path=path, interface=interface, member=member,
            signature=signature, body=body or [],
        )), 3)
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(reply.error_name)
        return reply.body

    async def daemon(self, member, signature="", body=None):
        return await self.call(BUS, "/org/freedesktop/DBus", BUS, member, signature, body)

    def touch(self, name):
        self.event_order += 1
        self.activity[name] = self.event_order

    def emit(self, position=None, immediate=False):
        chosen = select(self.players, self.preferred, self.activity, self.active)
        if chosen != self.active:
            self.active = chosen
            self.revision += 1
            self.last_sample = None
            position = self.players.get(chosen, {}).get("Position")
            immediate = True
            if (chosen and not self.preferred and not self.activity.get(chosen)
                    and self.players[chosen].get("PlaybackStatus") == "Playing"):
                self.touch(chosen)
        self.publish(chosen, dict(self.players.get(chosen, {})), tuple(sorted(self.players)),
                     position / 1000 if type(position) is int and position >= 0 else None,
                     immediate, self.error)

    async def discover(self, name, owner, appeared=False):
        if not owner or self.owners.get(name) == owner:
            return
        self.owners[name] = owner
        try:
            values, = await self.call(owner, PATH, PROPS, "GetAll", "s", [PLAYER])
            if self.owners.get(name) == owner:
                player = self.players.setdefault(name, {})
                for key, value in values.items():
                    player.setdefault(key, unwrap(value))
                if appeared and player.get("PlaybackStatus") == "Playing" and name not in self.activity:
                    self.touch(name)
                if appeared:
                    self.emit()
        except (RuntimeError, TimeoutError):
            if self.owners.get(name) == owner and name not in self.players:
                self.owners.pop(name, None)
            LOG.debug("Unable to read %s", name, exc_info=True)

    async def scan(self):
        names, = await self.daemon("ListNames")
        async def resolve(name):
            try:
                owner, = await self.daemon("GetNameOwner", "s", [name])
                await self.discover(name, owner)
            except (RuntimeError, TimeoutError):
                LOG.debug("Player disappeared during discovery: %s", name)
        await asyncio.gather(*(resolve(name) for name in names if name.startswith(PREFIX)))
        self.emit()

    def message(self, msg):
        if msg.message_type != MessageType.SIGNAL:
            return
        if msg.interface == BUS and msg.member == "NameOwnerChanged":
            name, _, owner = msg.body
            if name.startswith(PREFIX):
                self.owners.pop(name, None)
                self.players.pop(name, None)
                self.activity.pop(name, None)
                self.emit()
                if owner:
                    asyncio.create_task(self.discover(name, owner, appeared=True))
            return
        if msg.path != PATH:
            return
        name = next((name for name, owner in self.owners.items() if owner == msg.sender), "")
        if not name:
            return
        if msg.interface == PROPS and msg.member == "PropertiesChanged" and msg.body[0] == PLAYER:
            values = self.players.setdefault(name, {})
            changed = {key: unwrap(value) for key, value in msg.body[1].items()}
            new_track = "Metadata" in changed and track_changed(
                values.get("Metadata") or {}, changed["Metadata"] or {})
            prior_status = values.get("PlaybackStatus")
            if new_track:
                values.pop("Position", None)
            values.update(changed)
            if values.get("PlaybackStatus") == "Playing" and (new_track or prior_status != "Playing"):
                self.touch(name)
            previous = self.active
            if name == self.active:
                self.revision += 1
                if new_track or "PlaybackStatus" in changed:
                    self.last_sample = None
            self.emit(changed.get("Position") if name == self.active else None,
                      new_track and name == self.active)
            if msg.body[2]:
                asyncio.create_task(self.refresh(name))
            elif self.active != previous or name == self.active and (
                new_track or "PlaybackStatus" in changed or "Rate" in changed
            ):
                asyncio.create_task(self.poll())
        elif msg.interface == PLAYER and msg.member == "Seeked" and name in self.players:
            player = self.players[name]
            player["Position"] = msg.body[0]
            if player.get("PlaybackStatus") == "Playing":
                self.touch(name)
            if select(self.players, self.preferred, self.activity, self.active) == name:
                self.revision += 1
                self.last_sample = msg.body[0]
                self.emit(msg.body[0], True)

    async def refresh(self, name):
        revision, owner = self.revision, self.owners.get(name)
        try:
            values, = await self.call(owner, PATH, PROPS, "GetAll", "s", [PLAYER])
            if owner == self.owners.get(name) and revision == self.revision:
                player = self.players[name]
                updated = {k: unwrap(v) for k, v in values.items()}
                if updated.get("PlaybackStatus", player.get("PlaybackStatus")) == "Playing" and (
                    player.get("PlaybackStatus") != "Playing"
                    or "Metadata" in updated and track_changed(
                        player.get("Metadata") or {}, updated["Metadata"] or {})
                ):
                    self.touch(name)
                player.update(updated)
                self.emit(player.get("Position") if name == self.active else None)
        except (RuntimeError, TimeoutError, KeyError):
            LOG.debug("MPRIS properties unavailable for %s", name)

    async def poll(self, immediate=False):
        name, revision = self.active, self.revision
        if not name:
            return
        self.poll_serial += 1
        serial = self.poll_serial
        try:
            value, = await self.call(self.owners[name], PATH, PROPS, "Get", "ss", [PLAYER, "Position"])
            if name == self.active and revision == self.revision and serial == self.poll_serial:
                position = unwrap(value)
                if type(position) is not int or position < 0:
                    return
                if not immediate and self.players[name].get("PlaybackStatus") == "Playing" and position == self.last_sample:
                    return
                self.last_sample = self.players[name]["Position"] = position
                self.emit(position, immediate)
        except (KeyError, RuntimeError, TimeoutError):
            LOG.debug("Position unavailable for %s", name)

    async def control(self, member, position=0):
        values = self.players.get(self.active, {})
        if not self.active:
            return
        track = metadata(values.get("Metadata", {}))
        try:
            if member == "SetPosition":
                if not values.get("CanSeek") or not track.path.startswith("/") or track.path.endswith("/NoTrack"):
                    return
                await self.call(self.owners[self.active], PATH, PLAYER, member, "ox",
                                [track.path, int(position * 1000)])
                await self.poll(True)
            else:
                await self.call(self.owners[self.active], PATH, PLAYER, member)
        except (KeyError, RuntimeError, TimeoutError):
            LOG.info("Player rejected %s", member)

    async def run(self):
        while True:
            try:
                self.bus = await asyncio.wait_for(MessageBus().connect(), 4)
                self.bus.add_message_handler(self.message)
                for rule in (
                    "type='signal',sender='org.freedesktop.DBus',member='NameOwnerChanged',arg0namespace='org.mpris.MediaPlayer2'",
                    "type='signal',interface='org.freedesktop.DBus.Properties',path='/org/mpris/MediaPlayer2'",
                    "type='signal',interface='org.mpris.MediaPlayer2.Player',member='Seeked'",
                ):
                    await self.daemon("AddMatch", "s", [rule])
                self.error = ""
                await self.scan()
                while self.bus.connected:
                    await self.poll()
                    await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.error = f"Session D-Bus unavailable ({type(exc).__name__})"
                LOG.debug("MPRIS connection unavailable", exc_info=True)
            finally:
                if self.bus and self.bus.connected:
                    self.bus.disconnect()
                self.owners.clear()
                self.players.clear()
                self.activity.clear()
                self.emit()
            await asyncio.sleep(4)
