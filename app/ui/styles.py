"""Dark-mode QSS for the Digitalinos-branded app.

The Digitalinos palette leans on a deep navy background with a teal/cyan
accent (#1BB5C4) for primary actions, and a warm amber (#F59E0B) for
secondary highlights. This reads as premium software while staying
friendly to long editing sessions.
"""

BRAND_NAME = "Digitalinos"
PRODUCT_NAME = "Video Batch Pro"

# Accent / state colours (kept in one place so buttons / sliders / progress
# bars all stay in sync).
ACCENT = "#1BB5C4"         # teal — primary
ACCENT_HOVER = "#16A3B0"
ACCENT_DIM = "#0F6E77"
ACCENT_DISABLED = "#2E6E74"

SUCCESS = "#22C55E"
SUCCESS_HOVER = "#1E9F4E"

DANGER = "#EF4444"
DANGER_HOVER = "#B91C1C"

WARN = "#F59E0B"


DARK_QSS = f"""
* {{
    font-family: "Segoe UI", "San Francisco", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
    color: #E6E6E6;
}}

QMainWindow, QWidget {{
    background-color: #0F1419;
}}

QFrame#card {{
    background-color: #1A1F25;
    border: 1px solid #2A2F36;
    border-radius: 10px;
}}

QLabel#brand {{
    font-size: 12px;
    font-weight: 700;
    color: {ACCENT};
    letter-spacing: 2px;
}}

QLabel#h1 {{
    font-size: 22px;
    font-weight: 700;
    color: #FFFFFF;
    letter-spacing: 0.5px;
}}

QLabel#h2 {{
    font-size: 14px;
    font-weight: 600;
    color: #FFFFFF;
    padding: 4px 0;
}}

QLabel#muted {{
    color: #8A95A2;
}}

QLabel#status_good {{
    color: {ACCENT};
    font-weight: 600;
}}
QLabel#status_warn {{
    color: {WARN};
    font-weight: 600;
}}
QLabel#status_bad {{
    color: {DANGER};
    font-weight: 600;
}}

QPushButton {{
    background-color: #252B33;
    border: 1px solid #353C45;
    border-radius: 6px;
    padding: 7px 14px;
    min-height: 22px;
    color: #FFFFFF;
}}
QPushButton:hover {{
    background-color: #303740;
}}
QPushButton:pressed {{
    background-color: #1A1F25;
}}
QPushButton:disabled {{
    background-color: #1A1F25;
    color: #5A6370;
    border-color: #252B33;
}}

QPushButton#primary {{
    background-color: {ACCENT};
    border: 1px solid {ACCENT};
    color: #0F1419;
    font-weight: 700;
}}
QPushButton#primary:hover {{
    background-color: {ACCENT_HOVER};
}}
QPushButton#primary:disabled {{
    background-color: {ACCENT_DISABLED};
    color: #7FA8AE;
    border-color: {ACCENT_DISABLED};
}}

QPushButton#success {{
    background-color: {SUCCESS};
    border: 1px solid {SUCCESS};
    color: #0F1419;
    font-weight: 700;
}}
QPushButton#success:hover {{
    background-color: {SUCCESS_HOVER};
}}
QPushButton#success:disabled {{
    background-color: #1E4530;
    color: #6FA780;
    border-color: #1E4530;
}}

QPushButton#danger {{
    background-color: {DANGER};
    border: 1px solid {DANGER};
    color: #FFFFFF;
    font-weight: 600;
}}
QPushButton#danger:hover {{
    background-color: {DANGER_HOVER};
}}
QPushButton#danger:disabled {{
    background-color: #3B1E1E;
    color: #8A5555;
    border-color: #3B1E1E;
}}

QListWidget, QTreeWidget, QTableWidget {{
    background-color: #131820;
    border: 1px solid #2A2F36;
    border-radius: 8px;
    padding: 4px;
}}
QListWidget::item, QTreeWidget::item {{
    padding: 6px;
    border-radius: 4px;
}}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background-color: {ACCENT_DIM};
    color: #FFFFFF;
}}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {{
    background-color: #131820;
    border: 1px solid #2A2F36;
    border-radius: 6px;
    padding: 6px 10px;
    color: #FFFFFF;
    selection-background-color: {ACCENT_DIM};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {ACCENT};
}}

QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox QAbstractItemView {{
    background-color: #1A1F25;
    border: 1px solid #2A2F36;
    selection-background-color: {ACCENT_DIM};
}}

QSlider::groove:horizontal {{
    height: 6px;
    background: #2A2F36;
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {ACCENT};
    width: 16px;
    margin: -5px 0;
    border-radius: 8px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 3px;
}}

QProgressBar {{
    background-color: #131820;
    border: 1px solid #2A2F36;
    border-radius: 6px;
    text-align: center;
    color: #FFFFFF;
    height: 20px;
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 5px;
}}

QGroupBox {{
    border: 1px solid #2A2F36;
    border-radius: 8px;
    margin-top: 10px;
    padding-top: 18px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #FFFFFF;
    font-weight: 600;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid #353C45;
    border-radius: 4px;
    background: #131820;
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}

QScrollBar:vertical {{
    background: #0F1419;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #2A2F36;
    border-radius: 5px;
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QTabBar::tab {{
    background-color: #1A1F25;
    border: 1px solid #2A2F36;
    padding: 7px 14px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    color: #8A95A2;
}}
QTabBar::tab:selected {{
    background-color: {ACCENT_DIM};
    color: #FFFFFF;
}}
QTabBar::tab:hover {{
    color: #FFFFFF;
}}

QToolTip {{
    background-color: #1A1F25;
    border: 1px solid {ACCENT};
    color: #FFFFFF;
    padding: 4px 6px;
}}
"""
