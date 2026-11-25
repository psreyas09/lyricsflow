"""Expose lyric rows to Qt Quick without duplicating the full document."""

from ..qt import QtCore, Slot


class LyricModel(QtCore.QAbstractListModel):
    Original, Romanized, Translation, StartMs, Words = range(
        QtCore.Qt.ItemDataRole.UserRole + 1, QtCore.Qt.ItemDataRole.UserRole + 6
    )

    def __init__(self, parent):
        super().__init__(parent)
        self.lines = ()

    def set_lines(self, lines):
        self.beginResetModel()
        self.lines = lines
        self.endResetModel()

    def roleNames(self):
        return {
            self.Original: b"original", self.Romanized: b"romanized",
            self.Translation: b"translation", self.StartMs: b"startMs", self.Words: b"words",
        }

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.lines)

    def data(self, index, role=QtCore.Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.lines):
            return None
        line = self.lines[index.row()]
        if role in (self.Original, QtCore.Qt.ItemDataRole.DisplayRole):
            return line.original
        if role == self.Romanized:
            return line.romanized
        if role == self.Translation:
            return line.translation
        if role == self.StartMs:
            return line.start_ms
        if role == self.Words:
            return [
                {"original": word.original, "romanized": word.romanized,
                 "start_ms": word.start_ms, "end_ms": word.end_ms}
                for word in line.words
            ]
        return None

    @Slot(int, result=str)
    def reading(self, row):
        return self.lines[row].romanized if 0 <= row < len(self.lines) else ""
