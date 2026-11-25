import QtQuick

Item {
    id: ambient
    objectName: "ambientGradient"
    property color background
    property var coverColors: []
    property bool light: false
    property bool motion: true
    property bool drift: false
    property real intensity: 0.24
    property real softness: 0.75
    property real phase: 0

    property color first: coverColors[0] || background
    property color second: coverColors[1] || first
    property color third: coverColors[2] || second
    Behavior on first { ColorAnimation { duration: ambient.motion ? 650 : 0 } }
    Behavior on second { ColorAnimation { duration: ambient.motion ? 650 : 0 } }
    Behavior on third { ColorAnimation { duration: ambient.motion ? 650 : 0 } }

    Rectangle {
        anchors.fill: parent
        color: ambient.background
    }

    Canvas {
        id: field
        width: 384
        height: Math.round(Math.max(144, Math.min(384, ambient.height * width / Math.max(1, ambient.width))))
        visible: ambient.intensity > 0 && ambient.coverColors.length > 0
        antialiasing: true
        transform: Scale {
            xScale: ambient.width / field.width
            yScale: ambient.height / field.height
        }

        function bloom(ctx, tint, x, y, radius, strength) {
            const gradient = ctx.createRadialGradient(x, y, 0, x, y, radius)
            gradient.addColorStop(0, Qt.rgba(tint.r, tint.g, tint.b, strength))
            gradient.addColorStop(0.44, Qt.rgba(tint.r, tint.g, tint.b, strength * 0.52))
            gradient.addColorStop(1, Qt.rgba(tint.r, tint.g, tint.b, 0))
            ctx.fillStyle = gradient
            ctx.fillRect(0, 0, width, height)
        }

        onPaint: {
            const ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)
            const angle = ambient.phase
            const strength = Math.min(1, ambient.intensity * (ambient.light ? 0.66 : 1.08))
            const scale = width * (0.59 + ambient.softness * 0.24)
            bloom(ctx, ambient.first, width * (0.16 + 0.13 * Math.sin(angle)),
                  height * (0.29 + 0.17 * Math.cos(angle)), scale, strength)
            bloom(ctx, ambient.second, width * (0.82 + 0.15 * Math.cos(angle)),
                  height * (0.81 + 0.21 * Math.sin(angle)), scale * 0.82, strength * 0.67)
            bloom(ctx, ambient.third, width * (0.58 - 0.19 * Math.sin(angle)),
                  height * (-0.12 + 0.14 * Math.cos(angle)), scale * 0.71, strength * 0.57)
        }
        onHeightChanged: requestPaint()
        onVisibleChanged: if (visible) requestPaint()
    }

    onFirstChanged: field.requestPaint()
    onSecondChanged: field.requestPaint()
    onThirdChanged: field.requestPaint()
    onIntensityChanged: field.requestPaint()
    onSoftnessChanged: field.requestPaint()
    onLightChanged: field.requestPaint()
    onPhaseChanged: field.requestPaint()

    Timer {
        interval: 55
        repeat: true
        running: ambient.drift && ambient.motion && field.visible
        onTriggered: ambient.phase = (ambient.phase + Math.PI * 2 * interval / 22000) % (Math.PI * 2)
    }
}
