import QtQuick
import QtQuick.Effects

Item {
    id: artwork
    property url source
    property bool motion: true
    property color surface: "#241d2b"
    property real radius: 16
    property int front: 0
    readonly property bool software: GraphicsInfo.api === GraphicsInfo.Software
    // Reuse the same decode size across small window resizes.
    readonly property int decodeSize: Math.min(1536, 32 * Math.max(1, Math.ceil(width * Screen.devicePixelRatio / 32)))
    readonly property bool useMask: radius > 0 && !software

    function setImage() {
        if (!source.toString()) {
            first.source = ""
            second.source = ""
            return
        }
        const current = front === 0 ? first : second
        if (String(current.source) === String(source) && current.status === Image.Ready)
            return
        const nextIndex = 1 - front
        const next = nextIndex === 0 ? first : second
        next.source = source
        if (next.status === Image.Ready || next.status === Image.Error)
            front = nextIndex
    }
    onSourceChanged: setImage()
    Rectangle {
        anchors.fill: parent
        color: artwork.surface
        radius: artwork.radius
        Image {
            anchors.centerIn: parent
            width: Math.min(parent.width * 0.36, 96)
            height: width
            source: "icon.svg"
            opacity: 0.75
        }
    }
    Rectangle {
        id: roundedMask
        anchors.fill: parent
        radius: artwork.radius
        visible: false
        layer.enabled: artwork.useMask
    }
    Item {
        id: imageLayer
        anchors.fill: parent
        layer.enabled: !artwork.software
        layer.smooth: true
        layer.effect: artwork.useMask ? rounding : null
        Image {
            id: first
            anchors.fill: parent
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
            cache: false
            sourceSize.width: artwork.decodeSize
            sourceSize.height: artwork.decodeSize
            smooth: true
            mipmap: !artwork.software
            opacity: artwork.front === 0 ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: artwork.motion ? 380 : 0 } }
            onOpacityChanged: if (opacity === 0 && String(source) !== String(artwork.source)) source = ""
            onStatusChanged: if (String(source) === String(artwork.source) &&
                                 (status === Image.Ready || status === Image.Error)) artwork.front = 0
        }
        Image {
            id: second
            anchors.fill: parent
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
            cache: false
            sourceSize.width: artwork.decodeSize
            sourceSize.height: artwork.decodeSize
            smooth: true
            mipmap: !artwork.software
            opacity: artwork.front === 1 ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: artwork.motion ? 380 : 0 } }
            onOpacityChanged: if (opacity === 0 && String(source) !== String(artwork.source)) source = ""
            onStatusChanged: if (String(source) === String(artwork.source) &&
                                 (status === Image.Ready || status === Image.Error)) artwork.front = 1
        }
    }
    Component {
        id: rounding
        MultiEffect {
            maskEnabled: true
            maskSource: roundedMask
            autoPaddingEnabled: false
        }
    }
}
