import argparse
import logging
import sys
from pathlib import Path

from .qt import QtCore, QtGui, QtQml, QtWidgets

QUrl = QtCore.QUrl
QMetaObject = QtCore.QMetaObject
QIcon = QtGui.QIcon
QQmlApplicationEngine = QtQml.QQmlApplicationEngine
qmlRegisterType = QtQml.qmlRegisterType
QApplication = QtWidgets.QApplication
QMenu = QtWidgets.QMenu
QSystemTrayIcon = QtWidgets.QSystemTrayIcon

from .app_controller import Controller
from .ui.karaoke import KaraokeText


def main():
    parser = argparse.ArgumentParser(description="Live karaoke lyrics for Linux MPRIS players")
    parser.add_argument("--demo", action="store_true", help="Run the offline demonstration")
    parser.add_argument("--debug", action="store_true", help="Print playback and provider diagnostics")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # HTTP request logs can include track metadata in URL query parameters.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    app = QApplication([sys.argv[0]])
    app.setApplicationName("LyricFlow")
    app.setOrganizationName("lyricflow")
    app.setDesktopFileName("io.github.lyricflow.LyricFlow")
    ui = Path(__file__).parent / "ui"
    icon = QIcon(str(ui / "icon.svg"))
    app.setWindowIcon(icon)
    qmlRegisterType(KaraokeText, "LyricFlow", 1, 0, "KaraokeText")
    controller = Controller(args.demo)
    app.aboutToQuit.connect(controller.worker.close)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("app", controller)
    engine.rootContext().setContextProperty("windowSystem", app.platformName())
    engine.load(QUrl.fromLocalFile(str(ui / "Main.qml")))
    if not engine.rootObjects():
        controller.worker.close()
        return 1
    window = engine.rootObjects()[0]
    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip("LyricFlow")
    menu = QMenu()
    def restore():
        QMetaObject.invokeMethod(window, "restoreMain")

    menu.addAction("Show LyricFlow", restore)
    menu.addAction("Quit", app.quit)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: restore() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)

    def tray_preference():
        enabled = (controller.options["tray"] or controller.options["clickThrough"]) and QSystemTrayIcon.isSystemTrayAvailable()
        tray.setVisible(enabled)

    controller.settingsChanged.connect(tray_preference)
    tray_preference()
    if controller.options["startMinimized"] and not args.demo:
        window.showMinimized()
    else:
        window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
