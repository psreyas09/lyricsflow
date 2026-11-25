"""Shape each complete line, then color its timed grapheme spans."""

import regex
from ..qt import QtCore, QtGui, QtQuick, Property, Signal

QPointF = QtCore.QPointF
QRectF = QtCore.QRectF
Qt = QtCore.Qt
QColor = QtGui.QColor
QFont = QtGui.QFont
QPainter = QtGui.QPainter
QPainterPath = QtGui.QPainterPath
QTextLayout = QtGui.QTextLayout
QTextLine = QtGui.QTextLine
QTextOption = QtGui.QTextOption
QQuickPaintedItem = QtQuick.QQuickPaintedItem

from ..lyrics.models import Word, word_fill


def reactive(kind, name, layout=False):
    def read(item):
        return item._values[name]

    def write(item, value):
        if item._values[name] != value:
            item._values[name] = value
            if layout:
                item._rebuild()
            else:
                item.update()

    return Property(kind, read, write)


def utf16_length(text):
    return len(text.encode("utf-16-le")) // 2


class KaraokeText(QQuickPaintedItem):
    measuredChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values = dict(content="", spans=[], variant="original", family="", pixelSize=38, malayalam=False,
                            alignment="Center", base=QColor("#a6a2ac"), accent=QColor("#f5ab85"),
                            position=0.0, active=False, progressUnderline=False)
        self.layout = QTextLayout()
        self._height = 1.0
        self.fragments = []
        self.setAntialiasing(True)
        self.widthChanged.connect(self._rebuild)

    content = reactive(str, "content", True)
    spans = reactive("QVariantList", "spans", True)
    variant = reactive(str, "variant", True)
    family = reactive(str, "family", True)
    pixelSize = reactive(int, "pixelSize", True)
    malayalam = reactive(bool, "malayalam", True)
    alignment = reactive(str, "alignment", True)
    base = reactive(QColor, "base")
    accent = reactive(QColor, "accent")
    position = reactive(float, "position")
    active = reactive(bool, "active")
    progressUnderline = reactive(bool, "progressUnderline")

    @Property(float, notify=measuredChanged)
    def measured(self):
        return self._height

    def _rebuild(self):
        font = QFont(self.family) if self.family else QFont()
        if self.malayalam:
            preferred = ["Noto Sans Malayalam", "Noto Serif Malayalam"]
            font.setFamilies(([self.family] if self.family else []) + preferred + font.families())
        font.setPixelSize(max(1, self.pixelSize))
        weight = QFont.Weight.DemiBold
        if self.malayalam:
            weight = QFont.Weight.Normal if self.variant == "romanized" else QFont.Weight.Bold
        font.setWeight(weight)
        text = self.content
        self.layout = QTextLayout(text, font)
        options = QTextOption()
        options.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        options.setAlignment(Qt.AlignmentFlag.AlignHCenter if self.alignment == "Center"
                             else Qt.AlignmentFlag.AlignLeft)
        self.layout.setTextOption(options)
        self.layout.setCacheEnabled(True)
        self.layout.beginLayout()
        height = 0.0
        while (line := self.layout.createLine()).isValid():
            line.setLineWidth(max(1.0, self.width()))
            line.setPosition(QPointF(0, height))
            height += line.height()
        self.layout.endLayout()
        self.fragments = self._fragments(text)
        if self._height != height:
            self._height = height
            self.measuredChanged.emit()
        self.update()

    def _fragments(self, text):
        if not self.spans:
            return []
        parts = [word.get(self.variant) or word["original"] for word in self.spans]
        joined = "".join(parts)
        if joined.strip() != text.strip():
            return []
        chars = list(regex.finditer(r"\X", joined))
        fragments = []
        start = 0
        removed = len(joined) - len(joined.lstrip()) if not text[:1].isspace() else 0
        for token, body in zip(self.spans, parts):
            end = start + len(body)
            # Adjacent graphemes with compatible visual direction form one clip
            # so a word fills continuously even when shaped as a ligature.
            groups = []
            for cluster in chars:
                if not start <= cluster.start() < end or cluster.start() < removed:
                    continue
                cursor = utf16_length(joined[removed:cluster.start()])
                line = self.layout.lineForTextPosition(cursor)
                if not line.isValid() or cursor >= utf16_length(text):
                    continue
                lead, _ = line.cursorToX(cursor, QTextLine.Edge.Leading)
                tail, _ = line.cursorToX(cursor, QTextLine.Edge.Trailing)
                if abs(tail - lead) < .01:
                    continue
                y, height = line.y(), line.height()
                if groups and groups[-1][2] == y and abs(groups[-1][1] - lead) < 1.5:
                    first, _, _, _ = groups[-1]
                    groups[-1] = (first, tail, y, height)
                else:
                    groups.append((lead, tail, y, height))
            fragments.append((Word(body, token["start_ms"], token.get("end_ms")), groups))
            start = end
        return fragments

    def paint(self, painter):
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setPen(self.accent if self.active and not self.fragments else self.base)
        self.layout.draw(painter, QPointF())
        if not self.active or not self.fragments:
            return
        done, current = QPainterPath(), QPainterPath()
        underline = QPainterPath()
        underline_height = max(2.0, self.pixelSize * .055)
        done.setFillRule(Qt.FillRule.WindingFill)
        current.setFillRule(Qt.FillRule.WindingFill)
        for word, groups in self.fragments:
            fill = word_fill(word, self.position)
            path = done if word.end_ms is not None and self.position >= word.end_ms else current
            remaining = fill * sum(abs(b - a) for a, b, _, _ in groups)
            for start, end, y, height in groups:
                extent = min(remaining, abs(end - start))
                if extent <= 0:
                    break
                left = start if end > start else start - extent
                path.addRect(QRectF(left, y, extent, height))
                if self.progressUnderline and path is current:
                    underline.addRect(QRectF(left, y + height - underline_height, extent, underline_height))
                remaining -= extent
        dimmed = QColor.fromRgbF(
            *(getattr(self.accent, channel)() * .72 + getattr(self.base, channel)() * .28
              for channel in ("redF", "greenF", "blueF")),
            .72,
        )
        for path, color in ((done, dimmed), (current, self.accent)):
            if path.isEmpty():
                continue
            painter.save()
            painter.setClipPath(path)
            painter.setPen(color)
            self.layout.draw(painter, QPointF())
            painter.restore()
        if not underline.isEmpty():
            painter.fillPath(underline, self.accent)
