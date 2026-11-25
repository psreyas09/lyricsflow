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
    readonly property int decodeSize: Math.min(1536, Math.max(640, Math.ceil(width * Screen.devicePixelRatio)))
    readonly property bool useMask: radius > 0 && !software

    function setImage() {
        const nextIndex = 1 - front
        const next = nextIndex === 0 ? first : second
        next.source = source
        if (!source.toString() || next.status === Image.Ready)
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
            sourceSize.width: artwork.decodeSize
            sourceSize.height: artwork.decodeSize
            smooth: true
            mipmap: !artwork.software
            opacity: artwork.front === 0 ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: artwork.motion ? 380 : 0 } }
            onStatusChanged: if (String(source) === String(artwork.source) &&
                                 (status === Image.Ready || status === Image.Error)) artwork.front = 0
        }
        Image {
            id: second
            anchors.fill: parent
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
            sourceSize.width: artwork.decodeSize
            sourceSize.height: artwork.decodeSize
            smooth: true
            mipmap: !artwork.software
            opacity: artwork.front === 1 ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: artwork.motion ? 380 : 0 } }
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
