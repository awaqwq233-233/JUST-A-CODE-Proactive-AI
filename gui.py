"""Gateway 语音工作台；深蓝渐变科技风格，异步管理客户端与固定后端。"""
import logging
import queue
import sys
import threading
from dataclasses import replace
from datetime import datetime

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal, Slot, QRectF, QUrl
from PySide6.QtGui import (QColor, QFont, QImage, QPixmap, QPainter, QPainterPath,
                          QLinearGradient, QRadialGradient, QPen, QTextCursor, QDesktopServices)
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout,
    QVBoxLayout, QLabel, QPlainTextEdit, QPushButton, QFrame, QComboBox,
    QSizePolicy, QCheckBox, QProgressBar, QDoubleSpinBox, QSpinBox,
    QScrollArea, QLineEdit, QFileDialog, QSplitter, QDialog, QDialogButtonBox)

from src.utils.config import Config
from src.omni.desktop_runtime import DesktopRuntime
from src.omni.backend_control import BackendController, load_profile, save_profile
from src.utils.context import SharedContext

TECH_QSS = """
QWidget { color: #e0ecff; font-size: 13px; background: transparent; }
QLabel { border: none; }
QLabel#brand { font-size: 25px; font-weight: 700; letter-spacing: 3px; color: #f1f7ff; }
QLabel#subtitle, QLabel#hint { color: #92adc9; font-size: 12px; }
QLabel#sectionTitle { font-size: 16px; font-weight: 600; color: #dceeff; }
QLabel#eyebrow { color: #50c6e4; font-size: 11px; letter-spacing: 2px; }
QLabel#logo { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
    stop:0 #0b7197, stop:1 #224582); color: #8be8ff; border: 1px solid #2aa4c8;
    border-radius: 14px; font-size: 22px; }
QLabel#statePill { border: 1px solid #264d73; border-radius: 17px;
    background: rgba(9,29,53,200); padding: 8px 16px; color: #8da9c5; }
QLabel#statePill[active="true"] { color: #6df2d6; background: rgba(11,69,72,180); border-color: #218b87; }
QPushButton { border: 1px solid #2b527a; border-radius: 12px;
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #183e66,stop:1 #102a4a); padding: 9px 16px; }
QPushButton:hover { background: #20517b; border-color: #48bdda; }
QPushButton:pressed { background: #113958; }
QPushButton:disabled { color: #576f89; border-color: #213951; background: #11233a; }
QPushButton#primary { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
    stop:0 #148eac,stop:1 #2464b6); color: #f5ffff; border-color: #3bb2da;
    font-weight: 600; padding: 10px 24px; }
QPushButton#primary:hover { background: #178eaf; border-color: #8cecff; }
QPushButton#primary:disabled { background: #22496a; border-color: #315472; color: #8ba7bc; }
QPushButton#backend { color: #a7e5ff; border-color: #3178a4; }
QPushButton#settingsToggle:checked { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
    stop:0 #176f91,stop:1 #204f89); color: #effcff; border: 1px solid #5dcfe8; }
QPushButton#settingsToggle:checked:hover { background: #1c7fa2; border-color: #99efff; }
QPushButton#settingsToggle:pressed { background: #0d405e; border-color: #a1f0ff; }
QPushButton#quiet { padding: 5px 10px; border-radius: 9px; font-size: 12px; }
QPlainTextEdit { border: none; background: transparent; padding: 4px; selection-background-color: #245982; }
QPlainTextEdit#console { font-size: 15px; }
QPlainTextEdit#diagnostics { background: #071a2e; border: 1px solid #1a3d5d;
    border-radius: 12px; font-size: 11px; padding: 10px; color: #8dbcd3; }
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { background: #0a203a;
    border: 1px solid #284b6d; border-radius: 9px; padding: 7px; min-height: 20px; }
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus { border-color: #37b5d5; }
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled { color: #57728e; background: #102338; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: #102a45; color: #e0ecff; selection-background-color: #205276; }
QCheckBox { spacing: 9px; padding: 4px 0; }
QCheckBox::indicator { width: 17px; height: 17px; border-radius: 5px;
    border: 1px solid #3b6384; background: #102b44; }
QCheckBox::indicator:checked { background: #23a8c8; border-color: #73d9eb; }
QCheckBox:disabled { color: #57728e; }
QProgressBar { border: none; border-radius: 4px; background: #193853; min-height: 8px; max-height: 8px; }
QProgressBar::chunk { border-radius: 4px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #227dbc,stop:1 #5fe0db); }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }
QScrollBar::handle:vertical { background: #2d5371; border-radius: 3px; min-height: 32px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QSplitter::handle { background: transparent; width: 12px; }
QLabel#separator { background: #203f5c; min-height: 1px; max-height: 1px; }
QDialog { background: #0c223b; }
"""


class TechBackground(QWidget):
    """静态深海蓝渐变与细网格背景，不增加实时媒体工作。"""

    def paintEvent(self, event):
        """绘制深蓝渐变、青色柔光与低对比科技网格。"""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#061224"))
        for x, y, radius, color in ((0.12, 0.12, .7, "#0d365a"),
                                   (.72, .14, .65, "#152d5e"),
                                   (.4, .95, .6, "#072f43"),
                                   (1., .9, .45, "#0a2140")):
            gradient = QRadialGradient(self.width()*x, self.height()*y, self.width()*radius)
            gradient.setColorAt(0, QColor(color))
            transparent = QColor(color)
            transparent.setAlpha(0)
            gradient.setColorAt(1, transparent)
            painter.fillRect(self.rect(), gradient)
        painter.setPen(QPen(QColor(75, 157, 206, 12), 1))
        for x in range(0, self.width(), 48):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), 48):
            painter.drawLine(0, y, self.width(), y)


class TechPanel(QFrame):
    """深蓝面板与青色边缘高光；跨平台保持相同布局。"""

    def paintEvent(self, event):
        """面板绘制静态渐变，不复制或模糊视频内容。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0, QColor(20, 48, 80, 235))
        gradient.setColorAt(.55, QColor(11, 32, 58, 225))
        gradient.setColorAt(1, QColor(11, 28, 51, 242))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor(64, 132, 176, 130), 1.2))
        painter.drawRoundedRect(rect, 24, 24)


class RoundedVideoLabel(QLabel):
    """只在原比例矩形内绘制完整摄像头画面，圆角外透明。"""

    def paintEvent(self, event):
        """覆盖上一帧并直接绘制 pixmap，避免 Metal 上二次 scaled。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 18, 18)
        painter.setClipPath(path)
        painter.fillRect(self.rect(), QColor("#0a1e34"))
        pix = self.pixmap()
        if pix is not None and not pix.isNull():
            painter.drawPixmap(self.rect(), pix)
        else:
            painter.setPen(QColor("#6789a8"))
            painter.drawText(self.rect(), Qt.AlignCenter, self.text() or "摄像头已暂停")


class AspectVideoContainer(QWidget):
    """容器可自由伸缩，内部视频控件严格跟随输入画面比例。"""

    def __init__(self, label):
        """默认采用固定采集的 4:3，收到帧后使用真实比例。"""
        super().__init__()
        self.label, self.ratio = label, 4 / 3
        label.setParent(self)
        self.setMinimumSize(280, 210)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_ratio(self, width, height):
        """只更新显示几何，不裁切、拉伸或改变摄像头采集设置。"""
        ratio = width / height
        if abs(ratio - self.ratio) > .0001:
            self.ratio = ratio
            self._fit_video()

    def resizeEvent(self, event):
        """窗口调整时重新计算原比例视频区域。"""
        self._fit_video()
        super().resizeEvent(event)

    def _fit_video(self):
        """把视频控件限制为等比矩形，余下区域属于透明布局而非黑边。"""
        width = min(self.width(), round(self.height() * self.ratio))
        height = round(width / self.ratio)
        self.label.setGeometry((self.width()-width)//2, (self.height()-height)//2, width, height)


class _GuiStream:
    """日志写入有界队列，避免后台线程访问 Qt 控件。"""

    def __init__(self, messages):
        """保存 GUI 日志队列。"""
        self.messages = messages

    def write(self, text):
        """队列满时淘汰最旧日志，不阻塞实时媒体线程。"""
        if text:
            try:
                self.messages.put_nowait(text)
            except queue.Full:
                try:
                    self.messages.get_nowait()
                    self.messages.put_nowait(text)
                except (queue.Empty, queue.Full):
                    pass
        return len(text)

    def flush(self):
        """GUI 定时读取日志，无需同步刷新。"""

    def isatty(self):
        """声明非终端，避免输出原地刷新的控制字符。"""
        return False


class _QtLogHandler(logging.Handler):
    """复用 GUI 的有界日志写入器。"""

    def __init__(self, messages):
        """配置日志文本格式。"""
        super().__init__()
        self.stream = _GuiStream(messages)
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        """只入队，不从日志线程绘制 GUI。"""
        self.stream.write(self.format(record) + "\n")


class MainWindow(QMainWindow):
    _runtime_state_changed = Signal(bool)
    _stop_runtime_requested = Signal()
    _startup_failed = Signal(str)
    _shutdown_finished = Signal(str)
    _reply_received = Signal(str)
    _reply_finished = Signal()
    _transcript_received = Signal(str)
    _task_received = Signal(str, object)

    def __init__(self, config):
        """建立 Gateway 工作台，初始化仅探测本机后端，不打开用户设备。"""
        super().__init__()
        self.config, self.context = replace(config, gateway_consent_devices=True), SharedContext()
        self._runtime_state_changed.connect(self._apply_runtime_state, Qt.QueuedConnection)
        self._stop_runtime_requested.connect(self._safe_stop_runtime, Qt.QueuedConnection)
        self._startup_failed.connect(self._handle_startup_failure, Qt.QueuedConnection)
        self._shutdown_finished.connect(self._finish_stop_runtime, Qt.QueuedConnection)
        self._reply_received.connect(self._append_reply, Qt.QueuedConnection)
        self._reply_finished.connect(self._end_reply, Qt.QueuedConnection)
        self._transcript_received.connect(self._append_transcript, Qt.QueuedConnection)
        self._task_received.connect(self._append_task, Qt.QueuedConnection)
        self.runtime = DesktopRuntime(context=self.context, on_state_change=self._on_state_change)
        self.runtime.text_callback = self._reply_received.emit
        self.runtime.reply_finished_callback = self._reply_finished.emit
        self.runtime.transcript_callback = self._transcript_received.emit
        self.runtime.task_callback = self._task_received.emit
        self._latest_report = None
        self._stop_requested = self._stopping = self._close_after_stop = False
        self._stop_error, self._start_thread = "", None
        self._reply_open = False
        self._stop_backend_after_client = False
        self.backend_profile = load_profile()
        self._controls = []
        self.setWindowTitle("J.A.C. · 本地语音工作台")
        self.resize(1440, 880)
        self.setMinimumSize(1100, 700)
        self.setStyleSheet(TECH_QSS)
        self._build_ui()
        self._setup_timers()
        self._redirect_logging()
        self.backend = BackendController(self)
        self.backend.changed.connect(self._on_backend_change)
        self.backend.log.connect(self._backend_log)
        self._update_status()

    def _label(self, text, name="", wrap=False):
        """创建统一文本层级，中文字体由应用级回退链提供。"""
        label = QLabel(text)
        label.setObjectName(name)
        label.setWordWrap(wrap)
        return label

    def _panel_layout(self, panel, margin=20):
        """设置科技面板留白和控件间距。"""
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(14)
        return layout

    def _build_ui(self):
        """布局为原比例相机、完整对话记录和可折叠调试设置。"""
        central = TechBackground()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 22, 24, 24)
        root.setSpacing(22)
        header = QHBoxLayout()
        logo = self._label("◌", "logo")
        logo.setFixedSize(48, 48)
        logo.setAlignment(Qt.AlignCenter)
        header.addWidget(logo)
        brand = QVBoxLayout()
        brand.setSpacing(2)
        brand.addWidget(self._label("J.A.C.", "brand"))
        brand.addWidget(self._label("本地语音工作台", "subtitle"))
        header.addLayout(brand)
        header.addStretch()
        self.state_pill = self._label("● 已停止", "statePill")
        header.addWidget(self.state_pill)
        self.backend_btn = QPushButton("启动后端")
        self.backend_btn.setObjectName("backend")
        self.backend_btn.clicked.connect(self._toggle_backend)
        header.addWidget(self.backend_btn)
        self.start_btn = QPushButton("启动语音")
        self.start_btn.setObjectName("primary")
        self.start_btn.setToolTip("开启已同意的摄像头、麦克风和语音播放")
        self.start_btn.clicked.connect(self._toggle_run)
        header.addWidget(self.start_btn)
        self.settings_btn = QPushButton("调节参数")
        self.settings_btn.setObjectName("settingsToggle")
        self.settings_btn.setToolTip("收起参数面板")
        self.settings_btn.setCheckable(True)
        self.settings_btn.setChecked(True)
        self.settings_btn.toggled.connect(self._toggle_panel)
        header.addWidget(self.settings_btn)
        root.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(16)
        self.content_splitter = QSplitter(Qt.Horizontal)
        self.content_splitter.setChildrenCollapsible(False)
        body.addWidget(self.content_splitter, 1)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(16)
        camera_panel = TechPanel()
        camera_layout = self._panel_layout(camera_panel)
        title = QHBoxLayout()
        title.addWidget(self._label("实时画面", "sectionTitle"))
        title.addStretch()
        title.addWidget(self._label("640 × 480", "hint"))
        camera_layout.addLayout(title)
        self.video_label = RoundedVideoLabel()
        self.video_label.setText("启动后，画面将在这里出现")
        self.video_container = AspectVideoContainer(self.video_label)
        camera_layout.addWidget(self.video_container, 1)
        self.camera_hint = self._label("画面保留原始比例 · 仅在本机处理", "hint")
        camera_layout.addWidget(self.camera_hint)
        left_layout.addWidget(camera_panel, 1)

        activity_panel = TechPanel()
        activity = self._panel_layout(activity_panel)
        activity.addWidget(self._label("VOICE / LOCAL", "eyebrow"))
        self.activity_label = self._label("从一句话开始", "sectionTitle")
        activity.addWidget(self.activity_label)
        activity.addWidget(self._label("戴好耳机，直接与 J.A.C. 交流。", "hint"))
        meter_title = QHBoxLayout()
        meter_title.addWidget(self._label("麦克风电平", "hint"))
        meter_title.addStretch()
        self.mic_level_label = self._label("0%", "hint")
        meter_title.addWidget(self.mic_level_label)
        activity.addLayout(meter_title)
        self.mic_bar = QProgressBar()
        self.mic_bar.setTextVisible(False)
        activity.addWidget(self.mic_bar)
        self.session_hint = self._label("尚未连接", "hint")
        activity.addWidget(self.session_hint)
        left_layout.addWidget(activity_panel)
        self.content_splitter.addWidget(left)

        conversation_panel = TechPanel()
        conversation = self._panel_layout(conversation_panel)
        title = QHBoxLayout()
        title.addWidget(self._label("对话记录", "sectionTitle"))
        title.addStretch()
        self.diagnostics_btn = QPushButton("连接日志")
        self.diagnostics_btn.setObjectName("quiet")
        self.diagnostics_btn.setCheckable(True)
        title.addWidget(self.diagnostics_btn)
        conversation.addLayout(title)
        conversation.addWidget(self._label("语音交流 · 用户转写与回复实时显示", "hint"))
        self.open_report_btn = QPushButton("打开最新报告")
        self.open_report_btn.setObjectName("quiet")
        self.open_report_btn.setEnabled(False)
        self.open_report_btn.clicked.connect(self._open_report)
        conversation.addWidget(self.open_report_btn)
        self.console = QPlainTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        self.console.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.console.setMaximumBlockCount(4000)
        self.console.setPlaceholderText("对话从这里开始。\n\n启动后，J.A.C. 的回复会实时出现在这里。")
        self.console.document().setDocumentMargin(8)
        conversation.addWidget(self.console, 1)
        self.diagnostics = QPlainTextEdit()
        self.diagnostics.setObjectName("diagnostics")
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setMaximumBlockCount(1200)
        self.diagnostics.setMaximumHeight(170)
        self.diagnostics.hide()
        self.diagnostics_btn.toggled.connect(self.diagnostics.setVisible)
        conversation.addWidget(self.diagnostics)
        conversation.addWidget(self._label("文字可选中复制 · 不保存原始音视频", "hint"))
        self.content_splitter.addWidget(conversation_panel)
        self.content_splitter.setSizes([570, 480])
        self.content_splitter.setStretchFactor(0, 5)
        self.content_splitter.setStretchFactor(1, 4)

        self.option_panel = TechPanel()
        self.option_panel.setFixedWidth(300)
        options = self._panel_layout(self.option_panel, 18)
        options.addWidget(self._label("调节参数", "sectionTitle"))
        options.addWidget(self._label("启动前调整，下次启动生效", "hint"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        options.addWidget(scroll, 1)
        content = QWidget()
        op = QVBoxLayout(content)
        op.setContentsMargins(0, 0, 6, 0)
        op.setSpacing(10)
        scroll.setWidget(content)
        self._build_options(op)
        body.addWidget(self.option_panel)
        root.addLayout(body, 1)

    def _section(self, layout, title):
        """用轻分隔线划分参数组。"""
        layout.addWidget(self._label("", "separator"))
        layout.addWidget(self._label(title, "sectionTitle"))

    def _field(self, layout, title, control, hint=""):
        """统一参数标题与帮助说明，并登记运行期间的锁定控件。"""
        layout.addWidget(self._label(title, "hint"))
        layout.addWidget(control)
        if hint:
            layout.addWidget(self._label(hint, "hint", True))
        self._controls.append(control)
        return control

    def _spin(self, low, high, value, suffix=""):
        """建立带上下界的整型参数控件。"""
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setValue(value)
        spin.setSuffix(suffix)
        return spin

    def _build_options(self, op):
        """只呈现固定 Gateway 客户端实际支持的设置。"""
        self._section(op, "采集")
        self.mic_gain_spin = QDoubleSpinBox()
        self.mic_gain_spin.setRange(.1, 8.)
        self.mic_gain_spin.setSingleStep(.1)
        self.mic_gain_spin.setDecimals(2)
        self.mic_gain_spin.setValue(self.config.omni_mic_gain)
        self.mic_gain_spin.setSuffix(" ×")
        self._field(op, "麦克风增益", self.mic_gain_spin)
        self.mic_gain_spin.setToolTip("声音较小时逐步提高；过高会削波。")
        self.video_enabled_chk = QCheckBox("启用摄像头")
        self.video_enabled_chk.setChecked(self.config.omni_video_enabled)
        self._controls.append(self.video_enabled_chk)
        op.addWidget(self.video_enabled_chk)
        self.fps_spin = self._field(op, "相机采集帧率", self._spin(5, 10, self.config.omni_fps, " fps"),
            "640 × 480 · 每秒上行最新 1 帧")
        self._section(op, "会话")
        self.session_spin = self._field(op, "会话轮换时长", self._spin(5, 240, self.config.gateway_session_seconds, " 秒"),
            "轮换时暂停采集，恢复有限上下文。")
        self.retry_spin = self._field(op, "异常重连次数", self._spin(0, 10, self.config.gateway_retry_limit))
        self.transcription_chk = QCheckBox("本地转写与系统任务")
        self.transcription_chk.setChecked(self.config.gateway_transcription_enabled)
        self.transcription_chk.setToolTip("CPU 转写；明确查询时间、电池、CPU、内存或生成系统报告。结果为文字/文件。")
        self._controls.append(self.transcription_chk)
        op.addWidget(self.transcription_chk)
        self.advanced_btn = QPushButton("设备、连接与音色  ▾")
        self.advanced_btn.setObjectName("quiet")
        self.advanced_btn.setCheckable(True)
        op.addWidget(self.advanced_btn)
        advanced_panel = QWidget()
        advanced = QVBoxLayout(advanced_panel)
        advanced.setContentsMargins(0, 0, 0, 0)
        advanced.setSpacing(10)
        self.advanced_btn.toggled.connect(advanced_panel.setVisible)
        advanced_panel.hide()
        op.addWidget(advanced_panel)
        self.whisper_dir_edit = self._field(advanced, "本地 Whisper 目录", QLineEdit(self.config.whisper_model_dir))
        self.whisper_choose_btn = QPushButton("选择 Whisper 目录")
        self.whisper_choose_btn.clicked.connect(self._choose_whisper_directory)
        self._controls.append(self.whisper_choose_btn)
        advanced.addWidget(self.whisper_choose_btn)
        self.brain_url_edit = self._field(advanced, "本地大脑地址", QLineEdit(self.config.gateway_brain_url))
        self.input_device_combo, self.output_device_combo = QComboBox(), QComboBox()
        for combo, device in ((self.input_device_combo, self.config.gateway_input_device),
                              (self.output_device_combo, self.config.gateway_output_device)):
            combo.addItem("系统默认", None)
            if device is not None:
                combo.addItem(f"设备 {device}", device)
                combo.setCurrentIndex(1)
        self._field(advanced, "麦克风", self.input_device_combo)
        self._field(advanced, "播放设备", self.output_device_combo)
        self.refresh_devices_btn = QPushButton("刷新音频设备")
        self.refresh_devices_btn.setObjectName("quiet")
        self.refresh_devices_btn.clicked.connect(self._refresh_devices)
        self._controls.append(self.refresh_devices_btn)
        advanced.addWidget(self.refresh_devices_btn)
        self.camera_spin = self._field(advanced, "摄像头编号", self._spin(0, 20, self.config.gateway_camera))
        self.url_edit = QLineEdit(self.config.gateway_url)
        self._field(advanced, "本机 Gateway", self.url_edit)
        self.voice_edit = QLineEdit(self.config.omni_ref_audio)
        self._field(advanced, "参考音频", self.voice_edit)
        self.voice_btn = QPushButton("选择参考 WAV")
        self.voice_btn.setObjectName("quiet")
        self.voice_btn.clicked.connect(self._choose_voice)
        self._controls.append(self.voice_btn)
        advanced.addWidget(self.voice_btn)
        self.backend_config_btn = QPushButton("后端路径设置")
        self.backend_config_btn.setObjectName("quiet")
        self.backend_config_btn.clicked.connect(self._edit_backend_profile)
        advanced.addWidget(self.backend_config_btn)
        op.addWidget(self._label("先启动后端，再启动语音。", "hint", True))
        op.addStretch()

    def _backend_log(self, text):
        """在连接日志中显示后端启动信息，并保留独立的对话区域。"""
        self._insert_text(self.diagnostics, text)

    def _on_backend_change(self, state, detail):
        """同步后端按钮；后端异常退出时先关闭正在运行的媒体会话。"""
        labels = {'starting': '取消后端启动', 'ready': '停止后端',
                  'stopping': '后端停止中…', 'external': '外部后端已就绪'}
        label = labels.get(state, '重试停止后端' if self.backend.owned else '启动后端')
        self.backend_btn.setText(label)
        self.backend_btn.setEnabled(state not in {'stopping', 'external'})
        self.backend_btn.setToolTip(detail)
        self.backend_config_btn.setEnabled(not self.backend.owned and not self.runtime.running)
        if state == 'error':
            self.console.appendPlainText(detail)
            self.diagnostics_btn.setChecked(True)
            if not self.backend.owned and self.runtime.running:
                self._safe_stop_runtime()
        if (self._close_after_stop and not self.backend.owned and not self._stopping
                and not self.runtime.running and self.runtime.omni_client is None):
            self.close()
        self._update_status()

    def _toggle_backend(self):
        """启动或停止本窗口拥有的后端，停止先完成媒体会话清理。"""
        if self.backend.owned:
            starter = self._start_thread and self._start_thread.is_alive()
            if self.runtime.running or self.runtime.omni_client is not None or starter or self._stopping:
                self._stop_backend_after_client = True
                self._safe_stop_runtime()
            else:
                self.backend.stop()
            return
        if self.backend.state == 'external':
            return
        if not all(self.backend_profile.get(key) for key in ('demo_dir', 'engine_dir', 'model_dir')):
            if not self._edit_backend_profile():
                return
        try:
            self.diagnostics_btn.setChecked(True)
            self.backend.start(self.backend_profile)
        except (ValueError, RuntimeError) as error:
            self.console.appendPlainText(str(error))

    def _edit_backend_profile(self):
        """用目录选择器设置本机固定后端路径，不在公开仓库保存机器路径。"""
        if self.backend.owned:
            return False
        dialog = QDialog(self)
        dialog.setWindowTitle('后端路径设置')
        dialog.resize(700, 370)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        layout.addWidget(self._label('选择固定版本后端与仓库外模型目录', 'sectionTitle'))
        fields = {}
        for key, title in (('demo_dir', 'MiniCPM-o-Demo 目录'),
                           ('engine_dir', 'llama.cpp-omni 引擎目录'), ('model_dir', 'GGUF 模型目录')):
            layout.addWidget(self._label(title, 'hint'))
            row = QHBoxLayout()
            edit = QLineEdit(str(self.backend_profile.get(key, '')))
            fields[key] = edit
            row.addWidget(edit, 1)
            button = QPushButton('选择目录')
            button.clicked.connect(lambda checked=False, field=edit: self._choose_backend_directory(field))
            row.addWidget(button)
            layout.addLayout(row)
        verify = QCheckBox('启动时校验模型 SHA256')
        verify.setChecked(self.backend_profile.get('verify_sha', True))
        layout.addWidget(verify)
        layout.addWidget(self._label('启动器会检查固定版本、完整模型和端口占用。', 'hint'))
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return False
        profile = {key: edit.text().strip() for key, edit in fields.items()}
        profile['verify_sha'] = verify.isChecked()
        try:
            save_profile(profile)
        except OSError as error:
            self.console.appendPlainText(f'保存后端设置失败：{error}')
            return False
        self.backend_profile = profile
        return True

    def _choose_backend_directory(self, field):
        """使用本地目录选择器填写一项路径。"""
        path = QFileDialog.getExistingDirectory(self, '选择后端目录', field.text())
        if path:
            field.setText(path)

    def _choose_voice(self):
        """选择本地参考 WAV，只有启动后才发送至本机 Gateway。"""
        path, _ = QFileDialog.getOpenFileName(self, "选择参考音频", self.voice_edit.text(), "WAV 音频 (*.wav)")
        if path:
            self.voice_edit.setText(path)

    def _choose_whisper_directory(self):
        """选择仓库外锁定转写模型，校验在后台启动时完成。"""
        path = QFileDialog.getExistingDirectory(self, "选择本地 Whisper 目录", self.whisper_dir_edit.text())
        if path:
            self.whisper_dir_edit.setText(path)

    @Slot(str)
    def _append_transcript(self, text):
        """独立显示用户原话，禁止将转写文本拼到助手增量中。"""
        if self._stopping or self._stop_requested:
            return
        self._end_reply()
        self._insert_text(self.console, "\n\nbo s s · " + datetime.now().strftime("%H:%M") + "\n" + text)

    @Slot(str, object)
    def _append_task(self, state, detail):
        """主线程显示任务状态，完成后才开放真实报告文件入口。"""
        if self._stopping or self._stop_requested:
            return
        labels = {"running": "大脑正在查询", "completed": "报告已生成", "error": "任务暂停或失败", "rejected": "任务未执行", "cancelled": "任务已取消"}
        reasons = {"brain_busy": "大脑忙，请完成后再说", "unclear_speech": "没有听清，请重复", "brain_failed": "大脑不可用或任务失败",
                   "audio_overflow": "转写积压，请停止后重启", "utterance_overflow": "转写积压，请停止后重启",
                   "whisper_error": "转写异常，请停止后重启", "vad_error": "切句异常，请停止后重启",
                   "session_changed": "会话重连，请重新发出指令"}
        message = labels.get(state, state)
        if detail.get("code"):
            message += "：" + reasons.get(detail["code"], "请查看连接状态")
        self._end_reply()
        self._insert_text(self.console, "\n\n大脑 · " + message)
        if state == "completed":
            from pathlib import Path
            self._latest_report = Path(detail["path"])
            self.open_report_btn.setEnabled(self._latest_report.is_file())
            self.open_report_btn.setToolTip(str(self._latest_report))
            self._insert_text(self.console, "\n" + detail["answer"] + "\n文件：" + self._latest_report.name)

    def _open_report(self):
        """仅用户点击后打开已经生成的本机报告，不执行模型给出的链接。"""
        if self._latest_report is not None and self._latest_report.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._latest_report)))

    def _refresh_devices(self):
        """查询设备名称，不开启采集或播放流；保留失联设备编号便于排障。"""
        import sounddevice as sd
        try:
            devices = sd.query_devices()
            for combo, key in ((self.input_device_combo, "max_input_channels"),
                               (self.output_device_combo, "max_output_channels")):
                chosen = combo.currentData()
                combo.clear()
                combo.addItem("系统默认", None)
                for index, device in enumerate(devices):
                    if device[key] > 0:
                        combo.addItem(f"{index} · {device['name']}", index)
                current = combo.findData(chosen)
                if current < 0 and chosen is not None:
                    combo.addItem(f"{chosen} · 未找到设备", chosen)
                    current = combo.count()-1
                combo.setCurrentIndex(max(0, current))
        except Exception as error:
            self.console.appendPlainText(f"无法查询设备：{error}")

    def _setup_timers(self):
        """只在运行时绘制画面，状态低频刷新。"""
        self.frame_timer = QTimer(self)
        self.frame_timer.timeout.connect(self._pull_frame)
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._update_status)
        self.status_timer.start(250)

    def _redirect_logging(self):
        """连接日志单独展示，并保存原输出流以便退出时恢复。"""
        self.log_q = queue.Queue(maxsize=2000)
        self._original_streams = sys.stdout, sys.stderr
        self._gui_stream = _GuiStream(self.log_q)
        sys.stdout = sys.stderr = self._gui_stream
        self._log_handler = _QtLogHandler(self.log_q)
        logging.getLogger().addHandler(self._log_handler)
        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self._pull_logs)
        self.log_timer.start(80)

    def _pull_frame(self):
        """只读取后台帧缓存，不在 Qt 线程采集或编码。"""
        if not self.runtime.running or self._stopping:
            return
        client = self.runtime.omni_client
        frame = client.get_latest_frame() if client else None
        if frame is None or frame.ndim != 3 or frame.shape[2] != 3:
            return
        height, width, _ = frame.shape
        if width <= 0 or height <= 0:
            return
        frame = np.ascontiguousarray(frame)
        image = QImage(frame.data, width, height, width*3, QImage.Format_BGR888)
        if image.isNull():
            return
        pix = QPixmap.fromImage(image)
        if not pix.isNull():
            self.video_container.set_ratio(width, height)
            self.video_label.setPixmap(pix)

    def _insert_text(self, editor, text):
        """流式连续插入，保持阅读位置及用户已有选区。"""
        scrollbar = editor.verticalScrollBar()
        follow = scrollbar.value() >= scrollbar.maximum()-2 and not editor.textCursor().hasSelection()
        cursor = QTextCursor(editor.document())
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        if follow:
            scrollbar.setValue(scrollbar.maximum())

    def _pull_logs(self):
        """每次限定读取数量，日志洪峰不能饿死画面绘制。"""
        parts = []
        for _ in range(200):
            try:
                parts.append(self.log_q.get_nowait().replace("\r", "\n"))
            except queue.Empty:
                break
        if parts:
            self._insert_text(self.diagnostics, "".join(parts))

    @Slot(str)
    def _append_reply(self, text):
        """将模型增量拼成自然段，避免逐块换行割裂中文字词。"""
        if self._stopping or self._stop_requested:
            return
        if not self._reply_open:
            prefix = "\n\n" if self.console.toPlainText() else ""
            self._insert_text(self.console, prefix + "J.A.C. · " + datetime.now().strftime("%H:%M") + "\n")
            self._reply_open = True
        self._insert_text(self.console, text)

    @Slot()
    def _end_reply(self):
        """模型回到聆听时结束当前显示段落，不依赖 response.done。"""
        self._reply_open = False

    def _update_status(self):
        """用单个状态胶囊表达启停、重连和听说阶段。"""
        state = getattr(self.runtime, "state", "ready" if self.runtime.running else "stopped")
        labels = {"connecting": "连接中", "reconnecting": "重连中 · 采集暂停", "ready": "聆听中",
                  "closed": "会话已关闭", "error": "连接异常", "stopped": "已停止"}
        if self._stopping:
            text = "停止中"
        elif self._stop_error:
            text = "停止未完成"
        elif self._start_thread and self._start_thread.is_alive() and not self.runtime.running:
            text = "启动中"
        elif self.runtime.running:
            text = labels.get(state, "聆听中")
            if state == "ready" and self.context.is_speaking:
                text = "正在回应"
            if state == "ready" and self.context.is_thinking:
                text = "大脑正在处理"
        else:
            text = "连接异常" if state == "error" else "语音已停止"
            backend = getattr(self, 'backend', None)
            if backend and backend.state == 'starting':
                text = '后端启动中'
            elif backend and backend.state == 'stopping':
                text = '后端停止中'
            elif backend and backend.state in {'ready', 'external'}:
                text = '后端就绪'

        self.state_pill.setText("● " + text)
        active = self.runtime.running and state == "ready" and not self._stopping
        if self.state_pill.property("active") != active:
            self.state_pill.setProperty("active", active)
            self.state_pill.style().unpolish(self.state_pill)
            self.state_pill.style().polish(self.state_pill)
        self.activity_label.setText(text if self.runtime.running or self._stopping else "从一句话开始")
        client = self.runtime.omni_client
        level = client.get_latest_mic_level() if client and active else 0
        percent = int(min(1., max(0., level)/.15)*100)
        self.mic_bar.setValue(percent)
        self.mic_level_label.setText(f"{percent}%")
        if client and hasattr(client, "stats"):
            stats = client.stats()
            self.session_hint.setText(f"已建立 {stats.get('sessions_started', 0)} 个会话 · 上行 {stats.get('chunks_sent', 0)} 秒")
        elif not self.runtime.running:
            self.session_hint.setText("尚未连接")
        if state in {"closed", "reconnecting", "error"}:
            self.video_label.clear()
        self.camera_hint.setText("摄像头已关闭" if not self.video_enabled_chk.isChecked() else "画面保留原始比例 · 仅在本机处理")

    def _toggle_run(self):
        """保留主线程权限申请和后台启动，关闭完成前禁止重启。"""
        if self._stopping:
            return
        if self._stop_error:
            self._safe_stop_runtime()
            return
        if self._start_thread and self._start_thread.is_alive():
            return
        if self.runtime.running:
            self._safe_stop_runtime()
            return
        if self.backend.state not in {'ready', 'external'}:
            self.console.appendPlainText('请先启动后端，并等待就绪后再启动语音。')
            return
        config = self._collect_config()
        try:
            from src.omni.realtime_protocol import gateway_url
            gateway_url(config.gateway_url, "video")
            from pathlib import Path
            from src.omni.gateway_client import ROOT
            voice = Path(config.omni_ref_audio)
            if not (voice if voice.is_absolute() else ROOT/voice).is_file():
                raise ValueError("参考音频文件不存在")
            if config.omni_video_enabled:
                from src.omni.media import request_camera_permission
                request_camera_permission(config.gateway_camera)
        except Exception as error:
            self.console.appendPlainText(f"无法启动：{error}")
            return
        self._stop_requested = False
        self._reply_open = False
        self.config = config
        self.start_btn.setEnabled(False)
        self.start_btn.setText("启动中…")
        self._set_options_enabled(False)

        def start_runtime():
            """后台启动出错时通过信号清理半启动设备并显示原因。"""
            try:
                self.runtime.start(config)
            except Exception:
                import traceback
                self._stop_runtime_requested.emit()
                self._startup_failed.emit("[GUI] 启动失败：\n" + traceback.format_exc())
                return
            if self._stop_requested and self.runtime.running:
                self._stop_runtime_requested.emit()
            elif not self.runtime.running:
                self._startup_failed.emit("启动未完成，请检查设备和 Gateway 后端。")

        self._start_thread = threading.Thread(target=start_runtime, daemon=True, name="gui-start")
        self._start_thread.start()

    @Slot()
    def _safe_stop_runtime(self):
        """安全地停止 J.A.C. 运行时，但**不关闭 GUI 窗口**。

        顺序很关键：先停帧定时器 + 清空视频画面（释放 Metal 渲染资源），再释放
        底层资源（摄像头/线程），避免在资源销毁过程中仍在向窗口提交帧，触发
        macOS Metal 断言崩溃（abort/闪退）。停止后 GUI 保持打开，控制台日志完整
        保留，便于调试（debug）。
        """
        self._stop_requested = True
        self.frame_timer.stop()
        self.video_label.clear()
        self.video_label.update()
        if self._stopping:
            return
        self._stopping = True
        self.start_btn.setEnabled(False)
        self.start_btn.setText("停止中…")
        self._set_options_enabled(False)

        def _do_stop():
            """在后台等待会话和设备释放，结束前不允许 GUI 再次启动。"""
            failure = ""
            try:
                self.runtime.stop()
                # 启动异常/关闭窗口可能与启动线程相交，确保它退出后不遗留新设备。
                starter = self._start_thread
                if starter is not None and starter is not threading.current_thread():
                    starter.join(timeout=5)
                    if starter.is_alive():
                        raise RuntimeError("启动线程仍在退出，请稍后重试停止")
                self.runtime.stop()
            except Exception as error:
                failure = f"{type(error).__name__}: {error}"
            self._shutdown_finished.emit(failure)

        threading.Thread(target=_do_stop, daemon=True, name="gui-stop").start()

    @Slot(str)
    def _finish_stop_runtime(self, failure):
        """仅在后台清理完成后恢复启动；清理超时则提供重试，禁止重叠会话。"""
        self._stopping = False
        self._stop_error = failure
        if failure:
            self.console.appendPlainText(f"[GUI] 停止未完成：{failure}")
            self.start_btn.setText("重试停止")
            self.start_btn.setEnabled(True)
            return
        self._apply_runtime_state(False)
        if self._stop_backend_after_client:
            self._stop_backend_after_client = False
            self.backend.stop()
            if self.backend.owned:
                return
        if self._close_after_stop:
            self.close()

    def _on_state_change(self, running):
        """将任意工作线程的状态通知排队交给 Qt 主线程，不直接修改控件。"""
        self._runtime_state_changed.emit(bool(running))

    @Slot(bool)
    def _apply_runtime_state(self, running):
        """在 Qt 主线程更新状态，并在每次成功启动后恢复持续预览刷新。"""
        # 上一轮停止通知可能晚于下一轮启动到达，避免旧通知停掉新预览。
        if bool(running) != bool(self.runtime.running):
            return
        if self._stopping or self._stop_error:
            return
        if not running and self.runtime.omni_client is not None:
            self.console.appendPlainText("连接已结束，正在清理会话；详情见连接日志。")
            self._safe_stop_runtime()
            return
        if running:
            if self._stop_requested:
                return
            self.frame_timer.start(33)
            self._pull_frame()
        else:
            self.frame_timer.stop()
            self.video_label.clear()
        if not running:
            self._end_reply()
        self.start_btn.setText("停止语音" if running else "启动语音")
        self.start_btn.setEnabled(True)  # 运行/停止两种状态都必须可点击
        self._set_options_enabled(not running)

    @Slot(str)
    def _handle_startup_failure(self, message):
        """在 Qt 主线程显示失败原因并恢复按钮，兼容工作线程没有 Qt 事件循环。"""
        self.console.appendPlainText(message)
        self._apply_runtime_state(False)

    def _set_options_enabled(self, enabled):
        """锁定所有会影响下一会话的控件，避免呈现无效的实时调节。"""
        for control in self._controls:
            control.setEnabled(enabled)
        if hasattr(self, 'backend'):
            self.backend_config_btn.setEnabled(enabled and not self.backend.owned)

    def _collect_config(self):
        """收集真实生效的 Gateway 参数，保留未在界面暴露的配置。"""
        return replace(self.config, omni_enabled=True, omni_backend="gateway",
            gateway_consent_devices=True,
            gateway_input_device=self.input_device_combo.currentData(),
            gateway_output_device=self.output_device_combo.currentData(),
            gateway_camera=self.camera_spin.value(), gateway_url=self.url_edit.text().strip(),
            gateway_session_seconds=self.session_spin.value(), gateway_retry_limit=self.retry_spin.value(),
            omni_mic_gain=self.mic_gain_spin.value(), omni_fps=self.fps_spin.value(),
            omni_video_enabled=self.video_enabled_chk.isChecked(), omni_ref_audio=self.voice_edit.text().strip(),
            gateway_transcription_enabled=self.transcription_chk.isChecked(),
            whisper_model_dir=self.whisper_dir_edit.text().strip(), gateway_brain_url=self.brain_url_edit.text().strip())

    def _toggle_panel(self, visible):
        """隐藏设置后将空间交给画面与对话记录。"""
        self.option_panel.setVisible(visible)
        self.settings_btn.setToolTip("收起参数面板" if visible else "展开参数面板")

    def closeEvent(self, event):
        """退出先等待会话关闭，然后恢复日志流并释放 Qt 定时器。"""
        starter_alive = self._start_thread is not None and self._start_thread.is_alive()
        if (self._stopping or self._stop_error or self.runtime.running
                or self.runtime.omni_client is not None or starter_alive):
            self._close_after_stop = True
            event.ignore()
            self._safe_stop_runtime()
            return
        if self.backend.owned:
            self._close_after_stop = True
            event.ignore()
            if self.backend.state != 'stopping':
                self.backend.stop()
            return
        self.backend.close()
        for timer in (self.frame_timer, self.status_timer, self.log_timer):
            timer.stop()
        self.video_label.clear()
        self._pull_logs()
        logging.getLogger().removeHandler(self._log_handler)
        if sys.stdout is self._gui_stream:
            sys.stdout = self._original_streams[0]
        if sys.stderr is self._gui_stream:
            sys.stderr = self._original_streams[1]
        super().closeEvent(event)


def run_gui(config):
    """采用系统中文字体和高 DPI，启动科技风格 Gateway 工作台。"""
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    font = QFont()
    font.setFamilies(["PingFang SC", "SF Pro Text", "Microsoft YaHei", "Noto Sans CJK SC", "sans-serif"])
    font.setPointSize(11)
    app.setFont(font)
    window = MainWindow(config)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run_gui(Config.load()))
