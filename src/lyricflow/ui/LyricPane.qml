import QtQuick
import QtQuick.Controls
import LyricFlow 1.0

Item {
    id: pane
    property color ink
    property color dim
    property color accent
    property bool progressUnderline: false
    property color scrim: "transparent"
    property real scrimOpacity: 0
    property string mode: app.preferences.mode
    property int fontSize: app.preferences.fontSize
    property bool motion: app.preferences.motion
    property bool interactive: true
    property bool compact: false

    function recenter(animate) {
        if (app.currentLine < 0 || view.count === 0 || view.moving || view.height <= 0)
            return
        scroll.stop()
        let item = view.itemAtIndex(app.currentLine)
        let nearby = item !== null && item !== undefined
        if (!nearby) {
            view.positionViewAtIndex(app.currentLine, ListView.Contain)
            item = view.itemAtIndex(app.currentLine)
            if (!item)
                return
        }
        const previous = view.contentY
        const center = pane.compact ? item.y : item.y + item.height / 2 - view.height * 0.45
        const lower = view.originY - view.topMargin
        const upper = Math.max(lower, view.originY + view.contentHeight + view.bottomMargin - view.height)
        const target = Math.max(lower, Math.min(center, upper))
        if (animate && nearby && pane.motion && Math.abs(target - previous) <= view.height) {
            scroll.from = previous
            scroll.to = target
            scroll.start()
        } else {
            view.contentY = target
        }
    }
    Connections {
        target: app
        function onFocusChanged() { pane.recenter(true) }
        function onJump() { Qt.callLater(function() { pane.recenter(false) }) }
        function onLyricsChanged() {
            if (app.currentLine < 0)
                view.positionViewAtBeginning()
        }
    }
    NumberAnimation {
        id: scroll
        target: view
        property: "contentY"
        duration: 420
        easing.type: Easing.InOutCubic
    }
    Rectangle {
        anchors.fill: parent
        color: pane.scrim
        opacity: pane.scrimOpacity
        visible: opacity > 0
    }
    Item {
        id: gapIndicator
        objectName: "lyricGapIndicator"
        anchors.top: parent.top
        width: parent.width
        height: visible ? (pane.compact ? 18 : 30) : 0
        readonly property var timing: app.lyricGap
        visible: timing.length === 2
        readonly property real progress: visible
            ? Math.max(0, Math.min(1, (app.lyricTime - timing[0]) / (timing[1] - timing[0]))) : 0
        Accessible.role: Accessible.StaticText
        Accessible.name: qsTr("Instrumental break")

        Row {
            anchors.centerIn: parent
            spacing: pane.compact ? 5 : 7
            Repeater {
                model: 3
                Rectangle {
                    required property int index
                    width: pane.compact ? 5 : 8
                    height: width
                    radius: width / 2
                    color: pane.accent
                    opacity: pane.motion ? 0.45 + 0.55 * Math.max(0, Math.min(1, gapIndicator.progress * 3 - index)) : 1
                }
            }
        }
    }
    ListView {
        id: view
        objectName: "lyricList"
        anchors.top: gapIndicator.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        clip: true
        topMargin: pane.compact ? 0 : Math.round(height * 0.32)
        bottomMargin: pane.compact ? height * 0.65 : Math.round(height * 0.56)
        cacheBuffer: Math.max(0, height)
        interactive: pane.interactive
        boundsBehavior: Flickable.StopAtBounds
        onMovementStarted: scroll.stop()
        onHeightChanged: Qt.callLater(function() { pane.recenter(false) })
        model: app.lines
        ScrollBar.vertical: ScrollBar { policy: pane.compact ? ScrollBar.AlwaysOff : ScrollBar.AsNeeded }
        delegate: Item {
            id: lyric
            required property var modelData
            required property int index
            property bool active: app.activeLines.indexOf(index) !== -1
            property bool plain: app.quality === "Plain lyrics"
            property color textColor: active || plain ? pane.ink : pane.dim
            Behavior on textColor {
                ColorAnimation { duration: pane.motion ? 270 : 0; easing.type: Easing.OutCubic }
            }
            property bool showOriginal: pane.mode === "Original" || pane.mode === "Both"
            property bool showRomanized: pane.mode === "Romanized" || pane.mode === "Both"
                                          || pane.mode === "Romanized + English"
            property bool showTranslation: pane.mode === "English" || pane.mode === "Romanized + English"
            property bool malayalamPair: /[\u0d00-\u0d7f]/.test(modelData.original)
            property bool romanDiffers: modelData.romanized !== modelData.original
            property bool primaryRoman: showRomanized && !showOriginal
            property string primary: showOriginal ? modelData.original :
                (primaryRoman ? (modelData.romanized || modelData.original) :
                (modelData.translation || "—"))
            width: view.width
            height: content.implicitHeight + (pane.compact ? 15 : 24)
            Column {
                id: content
                width: Math.max(1, parent.width - (pane.compact ? 12 : 52))
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: 5
                opacity: lyric.active || lyric.plain ? 1 : 0.78
                Behavior on opacity {
                    NumberAnimation { duration: pane.motion ? 270 : 0; easing.type: Easing.OutCubic }
                }
                transform: Translate {
                    y: lyric.active || lyric.plain ? 0 : (pane.compact ? 2 : 5)
                    Behavior on y {
                        NumberAnimation { duration: pane.motion ? 300 : 0; easing.type: Easing.OutCubic }
                    }
                }
                KaraokeText {
                    width: parent.width
                    height: measured
                    content: lyric.primary
                    spans: lyric.showOriginal || lyric.primaryRoman ? lyric.modelData.words : []
                    variant: lyric.primaryRoman ? "romanized" : "original"
                    alignment: app.preferences.alignment
                    family: app.preferences.font
                    pixelSize: pane.fontSize
                    malayalam: lyric.malayalamPair && (lyric.showOriginal || lyric.primaryRoman)
                    active: lyric.active && !lyric.plain
                    position: lyric.active && lyric.modelData.words.length ? app.lyricTime : 0
                    base: lyric.textColor
                    accent: pane.accent
                    progressUnderline: pane.progressUnderline
                }
                KaraokeText {
                    visible: lyric.showOriginal && lyric.showRomanized && lyric.romanDiffers
                    width: parent.width
                    height: visible ? measured : 0
                    content: visible ? lyric.modelData.romanized : ""
                    spans: visible ? lyric.modelData.words : []
                    variant: "romanized"
                    alignment: app.preferences.alignment
                    family: app.preferences.font
                    pixelSize: lyric.malayalamPair ? pane.fontSize : Math.round(pane.fontSize * 0.62)
                    malayalam: lyric.malayalamPair
                    active: visible && lyric.active && !lyric.plain
                    position: lyric.active && lyric.modelData.words.length ? app.lyricTime : 0
                    base: lyric.textColor
                    accent: pane.accent
                    progressUnderline: pane.progressUnderline
                }
                KaraokeText {
                    visible: lyric.showTranslation && lyric.primaryRoman && !!lyric.modelData.translation
                    width: parent.width
                    height: visible ? measured : 0
                    content: visible ? lyric.modelData.translation : ""
                    alignment: app.preferences.alignment
                    family: app.preferences.font
                    pixelSize: Math.round(pane.fontSize * 0.62)
                    active: visible && lyric.active && !lyric.plain
                    base: lyric.textColor
                    accent: pane.accent
                }
            }
            Accessible.role: Accessible.StaticText
            Accessible.name: primary
            TapHandler {
                enabled: pane.interactive && app.seekable &&
                         lyric.modelData.start_ms !== null && lyric.modelData.start_ms !== undefined
                onTapped: app.seekLine(lyric.modelData.start_ms)
            }
        }
    }
}
