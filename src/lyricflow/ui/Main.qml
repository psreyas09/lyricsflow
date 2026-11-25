import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts

ApplicationWindow {
    id: window
    width: 1160
    height: 760
    minimumWidth: floating ? 340 : 440
    minimumHeight: floating ? 138 : 540
    visible: false
    title: "LyricFlow"
    property string viewMode: "Main"
    readonly property bool overlay: viewMode === "Overlay"
    readonly property bool floating: viewMode === "Compact" || overlay
    readonly property bool immersive: viewMode === "Full screen"
    readonly property bool wide: width >= 850 && !floating
    readonly property string contentView: app.preferences.contentView
    readonly property bool detailsOnly: contentView === "Song details"
    readonly property bool showArtwork: contentView !== "Song name and lyrics"
    readonly property bool artworkOnTop: contentView === "Everything" && !floating && !wide
                                         && height >= 780 && !!app.song.art
    readonly property string backgroundEffect: app.preferences.backgroundEffect
    readonly property bool audioEffect: backgroundEffect === "CAVA bars" || backgroundEffect === "Spectrum wave"
    readonly property bool audioActive: audioEffect && visible && visibility !== Window.Minimized
                                        && app.playing && app.preferences.motion && app.preferences.background > 0
    onAudioActiveChanged: app.spectrum.setActive(audioActive)
    readonly property bool light: app.preferences.theme === "Light"
    readonly property color ink: light ? "#241b26" : "#fff1e7"
    readonly property color dim: light ? "#6f5d6e" : "#aa9ba9"
    readonly property color surface: light ? "#e7d7dd" : "#221b2a"
    readonly property color canvas: light ? "#f8ece8" : "#15121b"
    readonly property color accent: light && app.preferences.accent === "#ed9f77" ? "#9b4431" : app.preferences.accent
    readonly property real backgroundIntensity: Math.max(0, Math.min(1, app.preferences.background))
    readonly property real bloomOpacity: Math.min(1, backgroundIntensity * (light ? 0.66 : 1.08))
    // Bound the combined opacity of the three blooms, including while their colors animate.
    readonly property real effectOpacity: backgroundEffect === "Gradient" && app.song.artPalette.length > 0
        ? 1 - (1 - bloomOpacity) * (1 - 0.67 * bloomOpacity) * (1 - 0.57 * bloomOpacity)
        : audioEffect ? backgroundIntensity : 0
    readonly property real lyricEffectOpacity: Math.min(effectOpacity, 0.30)
    readonly property real lyricScrimOpacity: effectOpacity > lyricEffectOpacity
        ? 1 - lyricEffectOpacity / effectOpacity : 0
    readonly property color lyricTint: app.song.artPalette[0] || accent
    readonly property color lyricFallbackTint: light ? "#a33b11" : "#ff9d58"
    readonly property color lyricAccent: chooseLyricColor(lyricTint)
    readonly property bool x11: windowSystem === "xcb"
    property int mainWidth: 1160
    property int mainHeight: 760

    color: canvas
    opacity: overlay ? app.preferences.overlayOpacity : 1
    flags: Qt.Window | ((floating || app.preferences.onTop) ? Qt.WindowStaysOnTopHint : 0)
           | (overlay && x11 && app.preferences.clickThrough ? Qt.WindowTransparentForInput : 0)
    palette.window: canvas
    palette.windowText: ink
    palette.text: ink
    palette.buttonText: ink
    palette.base: surface
    palette.button: surface
    palette.highlight: accent
    palette.highlightedText: canvas

    function restoreMain() {
        mode("Main")
        showNormal()
        requestActivate()
    }

    function mode(next) {
        if (next === viewMode)
            return
        let wasFloating = floating
        if (viewMode === "Main") {
            mainWidth = width
            mainHeight = height
        }
        if (immersive)
            showNormal()
        viewMode = next
        if (immersive) {
            if (wasFloating) {
                width = mainWidth
                height = mainHeight
            }
            showFullScreen()
        } else if (floating) {
            width = 540
            height = overlay ? 168 : 204
        } else {
            width = mainWidth
            height = mainHeight
        }
    }

    function clock(ms) {
        if (ms < 0)
            return "—"
        const seconds = Math.floor(ms / 1000)
        const mins = Math.floor(seconds / 60)
        return mins + ":" + String(seconds % 60).padStart(2, "0")
    }

    function luminance(color) {
        function linear(channel) {
            return channel <= 0.04045 ? channel / 12.92 : Math.pow((channel + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * linear(color.r) + 0.7152 * linear(color.g) + 0.0722 * linear(color.b)
    }

    function chooseLyricColor(tint: color): color {
        const highlight = readableLyricColor(tint)
        const chroma = Math.max(highlight.r, highlight.g, highlight.b)
                     - Math.min(highlight.r, highlight.g, highlight.b)
        const distance = Math.max(Math.abs(highlight.r - ink.r),
                                  Math.abs(highlight.g - ink.g),
                                  Math.abs(highlight.b - ink.b))
        return chroma < 0.10 || distance < 0.16
            ? readableLyricColor(lyricFallbackTint) : highlight
    }

    function readableLyricColor(tint: color): color {
        // Compare against the brightest possible dark background or darkest light background.
        const extreme = light ? 0 : 1
        const blend = lyricEffectOpacity
        const background = luminance(Qt.rgba(canvas.r * (1 - blend) + extreme * blend,
                                            canvas.g * (1 - blend) + extreme * blend,
                                            canvas.b * (1 - blend) + extreme * blend, 1))
        const limit = light ? (background + 0.05) / 4.5 - 0.05 : 4.5 * (background + 0.05) - 0.05
        const target = extreme
        let low = 0
        let high = 1
        let result = Qt.rgba(target, target, target, 1)
        for (let i = 0; i < 12; ++i) {
            const amount = i === 0 ? 0 : (low + high) / 2
            const color = Qt.rgba(tint.r * (1 - amount) + target * amount,
                                  tint.g * (1 - amount) + target * amount,
                                  tint.b * (1 - amount) + target * amount, 1)
            if (light ? luminance(color) <= limit : luminance(color) >= limit) {
                if (amount === 0)
                    return color
                result = color
                high = amount
            } else {
                low = amount
            }
        }
        return result
    }

    Shortcut { sequence: "Escape"; onActivated: if (window.viewMode !== "Main") window.mode("Main") }
    Shortcut { sequence: "F11"; onActivated: window.mode(window.immersive ? "Main" : "Full screen") }
    Shortcut { sequence: "Ctrl+,"; onActivated: preferences.open() }
    Shortcut { sequence: "Ctrl+F"; enabled: !!app.song.title; onActivated: search.open() }
    readonly property bool playbackVisible: visible && visibility !== Window.Minimized && app.playing
    readonly property bool animateLyrics: playbackVisible && lyrics.visible
                                          && (app.quality === "Word sync"
                                              || (app.preferences.motion && app.lyricGap.length === 2))
    FrameAnimation {
        running: window.animateLyrics
        onTriggered: app.advance()
    }
    Timer {
        interval: lyrics.visible && app.quality === "Line sync" ? 32 : 100
        repeat: true
        running: window.playbackVisible && !window.animateLyrics
        onTriggered: app.advance()
    }

    AmbientGradient {
        anchors.fill: parent
        background: window.canvas
        coverColors: app.song.artPalette
        light: window.light
        motion: app.preferences.motion
        drift: window.visible && window.visibility !== Window.Minimized && app.playing
        intensity: window.backgroundEffect === "Gradient" ? window.backgroundIntensity : 0
        softness: app.preferences.blur
    }

    Loader {
        anchors.fill: parent
        active: window.audioActive
        sourceComponent: AudioVisualizer {
            effect: window.backgroundEffect
            levels: app.spectrum.levels
            tint: app.song.artPalette[0] || window.accent
            intensity: window.backgroundIntensity
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: window.floating ? 12 : window.wide ? 32 : 20
        anchors.topMargin: moveGrip.visible ? Math.max(24, anchors.margins) : anchors.margins
        spacing: window.floating ? 2 : 12
        RowLayout {
            Layout.fillWidth: true
            visible: !window.immersive
            spacing: 8
            Image {
                source: "icon.svg"
                Layout.preferredWidth: 28
                Layout.preferredHeight: 28
                visible: !window.floating
            }
            Artwork {
                objectName: "floatingArtwork"
                visible: window.floating && !window.detailsOnly && window.showArtwork && !!app.song.art
                Layout.preferredWidth: 28
                Layout.preferredHeight: 28
                source: visible ? app.song.art : ""
                surface: window.surface
                radius: 5
                motion: app.preferences.motion
            }
            Label {
                text: window.floating && app.song.title && !window.detailsOnly ? app.song.title : "LyricFlow"
                color: window.ink
                font.pixelSize: window.floating ? 13 : 18
                font.weight: Font.DemiBold
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
            ComboBox {
                id: display
                visible: !window.floating && !window.detailsOnly
                model: app.hasTranslation ? ["Original", "Romanized", "Both", "English", "Romanized + English"]
                                           : ["Original", "Romanized", "Both"]
                currentIndex: Math.max(0, model.indexOf(app.preferences.mode))
                onActivated: app.setting("mode", currentText)
                Accessible.name: "Lyrics languages"
            }
            ToolButton {
                text: "Search"
                visible: !window.floating && !window.detailsOnly
                enabled: !!app.song.title
                onClicked: search.open()
            }
            ToolButton {
                text: window.floating ? "Restore" : "View"
                onClicked: window.floating ? window.mode("Main") : views.open()
                Menu {
                    id: views
                    y: parent.height
                    palette.window: window.canvas
                    palette.windowText: window.ink
                    palette.light: window.surface
                    palette.midlight: window.surface
                    ButtonGroup { id: contentChoices }
                    MenuItem {
                        text: "Everything"
                        checkable: true
                        ButtonGroup.group: contentChoices
                        checked: window.contentView === text
                        onTriggered: app.setting("contentView", text)
                    }
                    MenuItem {
                        text: "Song details"
                        checkable: true
                        ButtonGroup.group: contentChoices
                        checked: window.detailsOnly
                        onTriggered: app.setting("contentView", text)
                    }
                    MenuItem {
                        text: "Song name and lyrics"
                        checkable: true
                        ButtonGroup.group: contentChoices
                        checked: window.contentView === text
                        onTriggered: app.setting("contentView", text)
                    }
                    MenuSeparator {}
                    MenuItem { text: "Compact"; onTriggered: window.mode("Compact") }
                    MenuItem { text: "Overlay"; onTriggered: window.mode("Overlay") }
                    MenuItem { text: "Full screen"; onTriggered: window.mode("Full screen") }
                }
            }
            ToolButton { text: "Settings"; visible: !window.floating; onClicked: preferences.open() }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Column {
                id: songPanel
                objectName: "songPanel"
                visible: window.detailsOnly || (window.wide && window.showArtwork)
                width: Math.min(420, parent.width * (window.detailsOnly ? 1 : 0.38))
                x: window.detailsOnly ? (parent.width - width) / 2 : 0
                anchors.verticalCenter: parent.verticalCenter
                spacing: window.floating ? 4 : 12
                Artwork {
                    objectName: "panelArtwork"
                    x: window.detailsOnly ? (songPanel.width - width) / 2 : 0
                    width: Math.max(0, Math.min(songPanel.width,
                                               app.song.artEdge > 0 ? app.song.artEdge : songPanel.width,
                                               songPanel.parent.height - trackTitle.height - songPanel.spacing
                                               - (trackDetails.visible ? trackDetails.height + songPanel.spacing : 0)))
                    height: width
                    source: songPanel.visible ? app.song.art : ""
                    surface: window.surface
                    motion: app.preferences.motion
                }
                Label {
                    id: trackTitle
                    width: parent.width
                    font.pixelSize: window.floating ? 20 : 31
                    font.weight: Font.DemiBold
                    color: window.ink
                    text: app.song.title || "No track playing"
                    horizontalAlignment: window.detailsOnly ? Text.AlignHCenter : Text.AlignLeft
                    wrapMode: Text.Wrap
                    maximumLineCount: window.floating ? 1 : 3
                    elide: Text.ElideRight
                }
                Label {
                    id: trackDetails
                    width: parent.width
                    horizontalAlignment: window.detailsOnly ? Text.AlignHCenter : Text.AlignLeft
                    font.pixelSize: window.floating ? 13 : 16
                    color: window.dim
                    text: [app.song.artist, app.song.album].filter(Boolean).join("  ·  ")
                    elide: Text.ElideRight
                    visible: !!text
                }
            }

            GridLayout {
                id: songHeader
                visible: !songPanel.visible && !window.floating && !!app.song.title
                width: parent.width
                columns: window.artworkOnTop ? 1 : 2
                columnSpacing: 12
                rowSpacing: 12
                Artwork {
                    objectName: "headerArtwork"
                    readonly property real targetSize: window.artworkOnTop ? Math.min(220, songHeader.parent.height * 0.32) : 88
                    visible: window.showArtwork && !!app.song.art
                    Layout.alignment: Qt.AlignHCenter
                    Layout.preferredWidth: Math.min(targetSize, app.song.artEdge || targetSize)
                    Layout.preferredHeight: Layout.preferredWidth
                    source: visible ? app.song.art : ""
                    surface: window.surface
                    motion: app.preferences.motion
                }
                Column {
                    Layout.fillWidth: true
                    spacing: 4
                    Label {
                        width: parent.width
                        text: app.song.title
                        color: window.ink
                        font.pixelSize: 24
                        font.weight: Font.DemiBold
                        horizontalAlignment: window.artworkOnTop ? Text.AlignHCenter : Text.AlignLeft
                        elide: Text.ElideRight
                    }
                    Label {
                        width: parent.width
                        visible: window.showArtwork && !!text
                        text: [app.song.artist, app.song.album].filter(Boolean).join("  ·  ")
                        color: window.dim
                        horizontalAlignment: window.artworkOnTop ? Text.AlignHCenter : Text.AlignLeft
                        elide: Text.ElideRight
                    }
                }
            }

            LyricPane {
                id: lyrics
                anchors.top: songHeader.visible ? songHeader.bottom : parent.top
                anchors.topMargin: songHeader.visible ? 6 : 0
                anchors.bottom: parent.bottom
                anchors.left: songPanel.visible && !window.detailsOnly ? songPanel.right : parent.left
                anchors.leftMargin: songPanel.visible && !window.detailsOnly ? 42 : 0
                anchors.right: parent.right
                visible: !window.detailsOnly && app.hasLyrics
                ink: window.ink
                dim: window.dim
                accent: window.lyricAccent
                progressUnderline: app.preferences.wordUnderline
                scrim: window.canvas
                scrimOpacity: window.lyricScrimOpacity
                mode: !app.hasTranslation && app.preferences.mode.includes("English") ? "Both" : app.preferences.mode
                fontSize: window.floating ? app.preferences.overlayFont :
                          Math.round(app.preferences.fontSize * (window.immersive ? 1.25 : window.wide ? 1 : 0.82))
                compact: window.floating
                interactive: !(window.overlay && window.x11 && app.preferences.clickThrough)
            }
            Column {
                anchors.centerIn: lyrics
                width: Math.max(1, lyrics.width - 40)
                spacing: 13
                visible: !window.detailsOnly && !lyrics.visible
                Label {
                    width: parent.width
                    text: app.song.title ? (app.status || "Lyrics unavailable") :
                          window.floating ? "Waiting for music" : app.status
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    font.pixelSize: window.floating ? 16 : 23
                    color: window.ink
                }
                Label {
                    width: parent.width
                    visible: !app.song.title && !window.floating
                    text: "Check that your player exposes media controls to the desktop."
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    font.pixelSize: 13
                    color: window.dim
                }
                Button {
                    anchors.horizontalCenter: parent.horizontalCenter
                    visible: !!app.song.title && !window.floating
                    text: "Import lyrics"
                    onClicked: importDialog.open()
                }
            }
        }

        Label {
            Layout.fillWidth: true
            visible: !window.detailsOnly && !window.floating && !window.immersive && !!app.quality
            text: [app.source, app.quality, app.status, app.hasUnromanized ? "Some readings unavailable" : ""]
                  .filter(Boolean).join("  ·  ")
            color: window.dim
            elide: Text.ElideRight
            font.pixelSize: 12
            HoverHandler { id: syncHelp }
            ToolTip.visible: syncHelp.hovered && app.quality !== "Word sync"
            ToolTip.text: app.quality === "Line sync"
                          ? "This match includes line timestamps. Word-by-word playback needs a source with word timestamps."
                          : "This match includes lyric text only."
        }
        RowLayout {
            Layout.fillWidth: true
            visible: !window.floating
            opacity: !window.immersive || controls.hovered || controls.activeFocus ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: app.preferences.motion ? 160 : 0 } }
            spacing: 12
            id: controls
            property bool hovered: controlsHover.hovered
            HoverHandler { id: controlsHover }
            ToolButton { text: "‹"; Accessible.name: "Previous track"; enabled: !!app.song.title; onClicked: app.control("Previous") }
            ToolButton {
                text: app.playing ? "Ⅱ" : "▶"
                Accessible.name: app.playing ? "Pause" : "Play"
                enabled: !!app.song.title
                onClicked: app.control("PlayPause")
            }
            ToolButton { text: "›"; Accessible.name: "Next track"; enabled: !!app.song.title; onClicked: app.control("Next") }
            Label {
                text: window.clock(app.elapsed)
                color: window.dim
                font.pixelSize: 13
                font.family: "monospace"
            }
            Slider {
                id: progress
                Layout.fillWidth: true
                from: 0
                to: Math.max(1, app.song.duration)
                enabled: app.seekable && app.song.duration > 0
                value: app.elapsed
                Accessible.name: "Playback position"
                onPressedChanged: if (!pressed && enabled) app.seek(value)
                Keys.onLeftPressed: event => { app.seek(app.elapsed - 5000); event.accepted = true }
                Keys.onRightPressed: event => { app.seek(app.elapsed + 5000); event.accepted = true }
                background: Rectangle {
                    x: progress.leftPadding
                    y: (progress.height - height) / 2
                    width: progress.availableWidth
                    height: 4
                    radius: 2
                    color: window.dim
                    Rectangle {
                        width: parent.width * progress.visualPosition
                        height: parent.height
                        radius: 2
                        color: window.accent
                    }
                }
                handle: Rectangle {
                    x: progress.leftPadding + progress.visualPosition * (progress.availableWidth - width)
                    y: (progress.height - height) / 2
                    width: 11
                    height: 11
                    radius: 6
                    color: window.accent
                    visible: progress.enabled
                }
            }
            Label {
                text: window.clock(app.song.duration > 0 ? app.song.duration : -1)
                color: window.dim
                font.pixelSize: 13
                font.family: "monospace"
            }
        }
    }

    Preferences {
        id: preferences
        objectName: "preferencesDialog"
        anchors.centerIn: Overlay.overlay
        width: Math.min(window.width - 32, 640)
        height: Math.min(window.height - 36, 680)
        importAction: function() { importDialog.open() }
        correctionAction: function() { editor.open() }
        importCorrectionsAction: function() { importCorrections.open() }
        exportCorrectionsAction: function() { exportCorrections.open() }
    }

    Dialog {
        id: search
        title: "Find lyrics"
        anchors.centerIn: Overlay.overlay
        width: Math.min(window.width - 30, 620)
        height: Math.min(window.height - 40, 520)
        modal: true
        standardButtons: Dialog.Close
        onOpened: {
            query.text = [app.song.title, app.song.artist].filter(Boolean).join(" ")
            query.forceActiveFocus()
            app.search("")
        }
        ColumnLayout {
            anchors.fill: parent
            RowLayout {
                TextField {
                    id: query
                    Layout.fillWidth: true
                    placeholderText: "Title, artist, or album"
                    onAccepted: app.search(text)
                }
                Button { text: "Search"; onClicked: app.search(query.text) }
            }
            Label { text: app.searchStatus; visible: !!text }
            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: app.matches
                delegate: ItemDelegate {
                    required property var modelData
                    required property int index
                    width: ListView.view.width
                    text: modelData.title + "  ·  " + modelData.artist + "\n"
                          + [modelData.album, modelData.source, modelData.language, modelData.quality, window.clock(modelData.seconds ? modelData.seconds * 1000 : -1)]
                            .filter(Boolean).join("  ·  ")
                    onClicked: { app.choose(index); search.close() }
                }
            }
        }
    }
    Dialog {
        id: editor
        title: "Edit romanization"
        anchors.centerIn: Overlay.overlay
        width: Math.min(window.width - 32, 620)
        modal: true
        standardButtons: Dialog.Save | Dialog.Cancel
        onOpened: {
            editLine.currentIndex = Math.max(0, app.currentLine)
            editText.text = app.lyricModel.reading(editLine.currentIndex)
        }
        onAccepted: app.edit(editLine.currentIndex, editText.text)
        ColumnLayout {
            anchors.fill: parent
            ComboBox {
                id: editLine
                Layout.fillWidth: true
                textRole: "original"
                model: app.lyricModel
                onActivated: editText.text = app.lyricModel.reading(currentIndex)
            }
            TextField { id: editText; Layout.fillWidth: true; selectByMouse: true }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: "Separate readings with space, |, space to map one reading to each timed span. Clear the field to restore the generated reading."
            }
        }
    }

    MouseArea {
        id: moveGrip
        anchors.top: parent.top
        anchors.topMargin: resizeHandles.grip
        anchors.horizontalCenter: parent.horizontalCenter
        width: 88
        height: 14
        z: 1
        visible: resizeHandles.visible
        hoverEnabled: true
        cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
        onPressed: window.startSystemMove()

        Rectangle {
            anchors.centerIn: parent
            width: 36
            height: 3
            radius: height / 2
            color: window.dim
            opacity: moveGrip.containsMouse || moveGrip.pressed ? 0.85 : 0.45
        }
    }

    Item {
        id: resizeHandles
        anchors.fill: parent
        z: 1
        visible: window.visibility === Window.Windowed && !window.immersive
                 && !(window.overlay && window.x11 && app.preferences.clickThrough)
        readonly property int grip: 8

        Repeater {
            model: [
                { edges: Qt.TopEdge, cursor: Qt.SizeVerCursor },
                { edges: Qt.BottomEdge, cursor: Qt.SizeVerCursor },
                { edges: Qt.LeftEdge, cursor: Qt.SizeHorCursor },
                { edges: Qt.RightEdge, cursor: Qt.SizeHorCursor },
                { edges: Qt.TopEdge | Qt.LeftEdge, cursor: Qt.SizeFDiagCursor },
                { edges: Qt.BottomEdge | Qt.RightEdge, cursor: Qt.SizeFDiagCursor },
                { edges: Qt.TopEdge | Qt.RightEdge, cursor: Qt.SizeBDiagCursor },
                { edges: Qt.BottomEdge | Qt.LeftEdge, cursor: Qt.SizeBDiagCursor }
            ]
            delegate: MouseArea {
                required property var modelData
                readonly property int edges: modelData.edges
                readonly property bool horizontalEdge: !!(edges & (Qt.TopEdge | Qt.BottomEdge))
                readonly property bool verticalEdge: !!(edges & (Qt.LeftEdge | Qt.RightEdge))
                width: verticalEdge ? resizeHandles.grip : resizeHandles.width - 2 * resizeHandles.grip
                height: horizontalEdge ? resizeHandles.grip : resizeHandles.height - 2 * resizeHandles.grip
                x: edges & Qt.LeftEdge ? 0 : edges & Qt.RightEdge ? resizeHandles.width - width : resizeHandles.grip
                y: edges & Qt.TopEdge ? 0 : edges & Qt.BottomEdge ? resizeHandles.height - height : resizeHandles.grip
                cursorShape: modelData.cursor
                onPressed: window.startSystemResize(edges)
            }
        }
    }

    FileDialog {
        id: importDialog
        title: "Import lyrics for this track"
        nameFilters: ["Lyrics (*.lrc *.ttml *.json *.yaml *.yml)", "All files (*)"]
        onAccepted: app.importLyrics(selectedFile)
    }
    FileDialog {
        id: importCorrections
        title: "Import romanization corrections"
        nameFilters: ["JSON (*.json)"]
        onAccepted: app.corrections(selectedFile, false)
    }
    FileDialog {
        id: exportCorrections
        title: "Export romanization corrections to a new file"
        nameFilters: ["JSON (*.json)"]
        defaultSuffix: "json"
        fileMode: FileDialog.SaveFile
        onAccepted: app.corrections(selectedFile, true)
    }
    Popup {
        id: notification
        x: Math.max(8, (window.width - width) / 2)
        y: window.height - height - 66
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        Label { text: app.notification; wrapMode: Text.Wrap; width: Math.min(460, window.width - 60) }
        Timer { id: hideNotice; interval: 4400; onTriggered: notification.close() }
    }
    Connections {
        target: app
        function onNoticeChanged() { notification.open(); hideNotice.restart() }
    }
}
