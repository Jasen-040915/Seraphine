"""Render our real Qt badges over a local reference for layout QA."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.common.qt_runtime import configure_qt_plugins
configure_qt_plugins()
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QImage, QPainter, QFont, QColor
from app.overlay.scoreboard import EconomyPanel
from app.overlay.model import ScoreboardData


parser = argparse.ArgumentParser()
parser.add_argument('--image', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
app = QApplication([])
reference = QImage(args.image)
if reference.isNull():
    raise ValueError('Reference image could not be opened')
panel = EconomyPanel()
panel.resize(round(reference.width() * .8), reference.height())
panel.data = ScoreboardData(13712, 16633, (1308, 1200, 463, -450, 400), (('', ''),) * 5, False, own_on_left=False)
overlay = panel.grab().toImage()
p = QPainter(reference)
p.drawImage(round(reference.width() * .1), 0, overlay)
font = QFont('Microsoft YaHei UI')
font.setPixelSize(17)
p.setFont(font)
p.setPen(QColor('#e7cf89'))
p.drawText(25, 35, '样式预览 · 数字为示例')
p.end()
if not reference.save(args.output):
    raise ValueError('Preview could not be saved')
panel.close()
