"""Read CAVA's raw spectrum stream without blocking the Qt event loop."""

from pathlib import Path
from shutil import which
from tempfile import TemporaryDirectory

from ..qt import Property, QtCore, Signal, Slot

BANDS = 48
CONFIG = """[general]
bars = 48
framerate = 30
[input]
method = pulse
source = auto
[output]
method = raw
raw_target = /dev/stdout
data_format = ascii
ascii_max_range = 255
channels = mono
"""


class Spectrum(QtCore.QObject):
    changed = Signal()
    statusChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._levels = []
        self._status = ""
        self._active = False
        self._stopping = False
        self._pending = bytearray()
        self._directory = None
        self._process = QtCore.QProcess(self)
        self._process.readyReadStandardOutput.connect(self._read)
        self._process.setStandardErrorFile(QtCore.QProcess.nullDevice())
        self._process.finished.connect(self._finished)
        self._process.errorOccurred.connect(self._error)
        QtCore.QCoreApplication.instance().aboutToQuit.connect(self.close)

    @Property("QVariantList", notify=changed)
    def levels(self):
        return self._levels

    @Property(str, notify=statusChanged)
    def status(self):
        return self._status

    def _set_status(self, value):
        self._status = value
        self.statusChanged.emit()

    @Slot(bool)
    def setActive(self, active):
        if active == self._active:
            return
        self._active = active
        if active:
            if self._process.state() == QtCore.QProcess.ProcessState.NotRunning:
                self._start()
        else:
            self._set_status("")
            self._clear()
            self._stopping = self._process.state() != QtCore.QProcess.ProcessState.NotRunning
            if self._stopping:
                self._process.kill()

    def _start(self):
        executable = which("cava")
        if not executable:
            self._set_status("Install CAVA to use audio-reactive backgrounds.")
            return
        try:
            self._directory = TemporaryDirectory(prefix="lyricflow-cava-")
            config = Path(self._directory.name) / "config"
            config.write_text(CONFIG, encoding="utf-8")
        except OSError:
            self._cleanup()
            self._set_status("Could not create the CAVA configuration.")
            return
        self._set_status("")
        self._process.start(executable, ["-p", str(config)])

    def _read(self):
        self._pending.extend(bytes(self._process.readAllStandardOutput()))
        lines = self._pending.split(b"\n")
        self._pending = lines.pop()
        if not self._active:
            return
        for line in reversed(lines):
            bands = line.rstrip(b";").split(b";")
            if len(bands) != BANDS or not all(band.isdigit() for band in bands):
                continue
            self._levels = [min(255, int(band)) / 255 for band in bands]
            self.changed.emit()
            break

    def _clear(self):
        self._pending.clear()
        if self._levels:
            self._levels = []
            self.changed.emit()

    def _cleanup(self):
        if self._directory:
            self._directory.cleanup()
            self._directory = None

    def _finished(self, *_):
        stopped = self._stopping
        self._stopping = False
        self._clear()
        self._cleanup()
        if self._active:
            if stopped:
                self._start()
            else:
                self._set_status("CAVA stopped. Check your system audio output.")

    def _error(self, error):
        if error == QtCore.QProcess.ProcessError.FailedToStart:
            self._cleanup()
            self._set_status("Could not start CAVA.")

    def close(self):
        self._active = False
        if self._process.state() != QtCore.QProcess.ProcessState.NotRunning:
            self._process.kill()
            self._process.waitForFinished(1000)
        self._cleanup()
