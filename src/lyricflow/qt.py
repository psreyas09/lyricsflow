"""Use the packaged Qt binding, or Fedora's system PyQt6 for local development."""
try:
    from PySide6 import QtCore, QtGui, QtQml, QtQuick, QtWidgets
    from PySide6.QtCore import Property, Signal, Slot
except ModuleNotFoundError as exc:
    if exc.name != "PySide6":
        raise
    from PyQt6 import QtCore, QtGui, QtQml, QtQuick, QtWidgets
    from PyQt6.QtCore import pyqtProperty as Property, pyqtSignal as Signal, pyqtSlot as Slot
