import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts

Dialog {
    id: panel
    title: "Settings"
    modal: true
    standardButtons: Dialog.Close
    property var importAction
    property var correctionAction
    property var importCorrectionsAction
    property var exportCorrectionsAction

    component Heading: Label {
        Layout.fillWidth: true
        Layout.topMargin: 12
        font.pixelSize: 17
        font.weight: Font.DemiBold
    }
    component Toggle: CheckBox {
        required property string key
        checked: app.preferences[key]
        onToggled: app.setting(key, checked)
    }
    component Choice: RowLayout {
        id: field
        required property string label
        required property string key
        required property var choices
        property var labels: choices
        Layout.fillWidth: true
        Label { text: field.label; Layout.fillWidth: true }
        ComboBox {
            Layout.preferredWidth: 190
            model: field.labels
            currentIndex: Math.max(0, field.choices.indexOf(app.preferences[field.key]))
            onActivated: app.setting(field.key, field.choices[currentIndex])
            Accessible.name: field.label
        }
    }
    component NumberSetting: RowLayout {
        id: number
        required property string label
        required property string key
        required property int minimum
        required property int maximum
        Layout.fillWidth: true
        Label { text: number.label; Layout.fillWidth: true; wrapMode: Text.Wrap }
        SpinBox {
            from: number.minimum
            to: number.maximum
            value: app.preferences[number.key]
            editable: true
            onValueModified: app.setting(number.key, value)
            Accessible.name: number.label
        }
    }
    component Scale: RowLayout {
        id: scale
        required property string label
        required property string key
        property real minimum: 0
        Layout.fillWidth: true
        Label { text: scale.label; Layout.fillWidth: true }
        Slider {
            from: scale.minimum
            to: 1
            stepSize: 0.05
            value: app.preferences[scale.key]
            onMoved: app.setting(scale.key, value)
            Accessible.name: scale.label
        }
    }

    ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            width: parent.width
            spacing: 5

            Heading { text: "Playback" }
            RowLayout {
                Layout.fillWidth: true
                Label { text: "MPRIS player"; Layout.fillWidth: true }
                ComboBox {
                    Layout.preferredWidth: 190
                    model: app.availablePlayers
                    textRole: "label"
                    valueRole: "service"
                    currentIndex: Math.max(0, indexOfValue(app.preferences.player))
                    onActivated: app.setting("player", currentValue)
                    Accessible.name: "MPRIS player"
                }
            }
            NumberSetting { label: "Reconcile position every (seconds)"; key: "reconcile"; minimum: 2; maximum: 30 }
            Button { text: "Resync player position"; onClicked: app.control("Resync") }

            Heading { text: "Lyrics" }
            Toggle { text: "Fetch lyrics automatically"; key: "automatic" }
            Label { text: "Additional LRCLIB-compatible servers"; Layout.fillWidth: true }
            TextArea {
                id: lyricServers
                Layout.fillWidth: true
                text: app.preferences.lyricsServers
                placeholderText: "One authorized server URL per line"
                wrapMode: TextEdit.WrapAnywhere
                selectByMouse: true
                Accessible.name: "Additional lyrics server URLs"
            }
            Button {
                text: "Apply lyric servers"
                enabled: lyricServers.text.trim() !== app.preferences.lyricsServers
                onClicked: app.setting("lyricsServers", lyricServers.text)
            }
            NumberSetting { label: "Global offset (ms)"; key: "globalOffset"; minimum: -30000; maximum: 30000 }
            RowLayout {
                Layout.fillWidth: true
                Label { text: "Track offset (ms)"; Layout.fillWidth: true }
                Button { text: "Reset"; enabled: !!app.song.title; onClicked: app.songOffset(0) }
                SpinBox {
                    from: -30000
                    to: 30000
                    editable: true
                    value: app.trackOffset
                    enabled: !!app.song.title
                    onValueModified: app.songOffset(value)
                    Accessible.name: "Track offset in milliseconds"
                }
            }
            Label {
                Layout.fillWidth: true
                text: "Positive offsets advance lyrics. Negative offsets delay them."
                wrapMode: Text.Wrap
            }
            Flow {
                Layout.fillWidth: true
                spacing: 9
                Button { text: "Import lyrics for this track"; enabled: !!app.song.title; onClicked: panel.importAction() }
                Button { text: "Use automatic match"; enabled: !!app.song.title; onClicked: app.useAutomatic() }
            }

            Heading { text: "Languages" }
            Choice {
                label: "Reading language"
                key: "language"
                choices: ["auto", "ja", "ko", "zh", "yue", "ar", "fa", "he", "ru", "uk", "el", "th", "ta", "ml",
                          "hi", "bn", "te", "kn", "pa", "gu", "mr", "si", "ne", "my", "km", "lo", "ka", "hy", "am"]
                labels: ["Automatic", "Japanese", "Korean", "Mandarin", "Cantonese", "Arabic", "Persian", "Hebrew",
                         "Russian", "Ukrainian", "Greek", "Thai", "Tamil", "Malayalam", "Hindi", "Bengali", "Telugu",
                         "Kannada", "Punjabi", "Gujarati", "Marathi", "Sinhala", "Nepali", "Burmese", "Khmer",
                         "Lao", "Georgian", "Armenian", "Amharic"]
            }
            Choice { label: "Pronunciation style"; key: "style"; choices: ["natural", "standard", "ascii"];
                     labels: ["Natural", "Standardized", "Simplified ASCII"] }
            Toggle { text: "Keep diacritics"; key: "diacritics" }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: "Readings depend on context. Arabic and Hebrew may omit short vowels. English display is available when lyrics include a translation."
            }
            Button { text: "Edit track romanization"; enabled: app.lines.length > 0; onClicked: panel.correctionAction() }
            Flow {
                Layout.fillWidth: true
                spacing: 9
                Button { text: "Import corrections"; onClicked: panel.importCorrectionsAction() }
                Button { text: "Export corrections"; onClicked: panel.exportCorrectionsAction() }
            }

            Heading { text: "Appearance" }
            Choice { label: "Theme"; key: "theme"; choices: ["Dark", "Light"] }
            Choice { label: "Lyrics alignment"; key: "alignment"; choices: ["Center", "Left"] }
            NumberSetting { label: "Lyrics size (px)"; key: "fontSize"; minimum: 18; maximum: 76 }
            Toggle { text: "Underline current word in synced lyrics"; key: "wordUnderline" }
            RowLayout {
                Layout.fillWidth: true
                Button { text: app.preferences.font || "Choose font"; onClicked: fontDialog.open() }
                Button { text: "System font"; enabled: !!app.preferences.font; onClicked: app.setting("font", "") }
                Button { text: "Accent color"; onClicked: accentDialog.open() }
            }
            Choice {
                label: "Background effect"
                key: "backgroundEffect"
                choices: ["Gradient", "CAVA bars", "Spectrum wave", "Solid"]
            }
            Label {
                Layout.fillWidth: true
                text: app.spectrum.status
                visible: !!text
                wrapMode: Text.Wrap
            }
            Scale { label: "Background intensity"; key: "background" }
            Scale { label: "Gradient softness"; key: "blur" }
            Toggle { text: "Animate backgrounds and transitions"; key: "motion" }

            Heading { text: "Desktop" }
            Toggle { text: "Keep main window on top"; key: "onTop" }
            Toggle { text: "Start minimized"; key: "startMinimized" }
            Toggle { text: "Show tray icon when available"; key: "tray" }
            CheckBox {
                text: "Start automatically at login"
                checked: app.autostartEnabled
                onToggled: {
                    app.autostart(checked)
                    checked = Qt.binding(function() { return app.autostartEnabled })
                }
            }
            NumberSetting { label: "Overlay font size (px)"; key: "overlayFont"; minimum: 14; maximum: 58 }
            Scale { label: "Overlay opacity"; key: "overlayOpacity"; minimum: 0.25 }
            Toggle { text: "Click-through overlay (X11)"; key: "clickThrough"; enabled: windowSystem === "xcb" }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: "Use the window manager to move the overlay. On X11, use the tray icon or window switcher to recover a click-through overlay."
            }

            Heading { text: "Diagnostics and cache" }
            Label { Layout.fillWidth: true; text: app.diagnostics; wrapMode: Text.Wrap }
            Label { Layout.fillWidth: true; text: app.status; wrapMode: Text.Wrap }
            Label { text: "Sources: LRCLIB, AMLL TTML, Better Lyrics, nearby files, and configured servers  ·  Logs: lyricflow --debug"; Layout.fillWidth: true; wrapMode: Text.Wrap }
            Button { text: "Clear fetched lyrics cache"; onClicked: app.clearCache() }
        }
    }
    FontDialog {
        id: fontDialog
        onAccepted: app.setting("font", selectedFont.family)
    }
    ColorDialog {
        id: accentDialog
        selectedColor: app.preferences.accent
        onAccepted: app.setting("accent", selectedColor.toString())
    }
}
