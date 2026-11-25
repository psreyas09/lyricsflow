import QtQuick

Canvas {
    id: visualizer
    property string effect
    property var levels: []
    property color tint
    property real intensity: 0.24

    onLevelsChanged: requestPaint()
    onEffectChanged: requestPaint()
    onTintChanged: requestPaint()
    onIntensityChanged: requestPaint()

    onPaint: {
        const ctx = getContext("2d")
        ctx.clearRect(0, 0, width, height)
        if (!levels.length)
            return

        const step = width / levels.length
        const baseline = height * 0.94
        const range = height * 0.48
        const gradient = ctx.createLinearGradient(0, baseline - range, 0, height)
        gradient.addColorStop(0, Qt.rgba(tint.r, tint.g, tint.b, intensity))
        gradient.addColorStop(1, Qt.rgba(tint.r, tint.g, tint.b, intensity * 0.16))
        ctx.fillStyle = gradient

        if (effect === "CAVA bars") {
            for (let i = 0; i < levels.length; ++i) {
                const bar = levels[i] * range
                ctx.fillRect((i + 0.14) * step, baseline - bar, step * 0.72, bar)
            }
        } else {
            ctx.beginPath()
            ctx.moveTo(0, baseline)
            for (let i = 0; i < levels.length; ++i) {
                const x = (i + 0.5) * step
                const y = baseline - levels[i] * range
                ctx.lineTo(x, y)
            }
            ctx.lineTo(width, baseline)
            ctx.closePath()
            ctx.fill()
        }
    }
}
