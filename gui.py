"""J.A.C.Prototype —— PySide6 现代化桌面界面。

布局：左侧视觉解析画面 + 控制条 / 中间控制台（日志 + 手动输入框 + 发送）/
最右侧可折叠开关选项面板。
- 黑色主题 + 圆角矩形 + 高 DPI 适配。
- 「显示分辨率」「显示缩放」只改变程序在桌面的显示尺寸，不动摄像头采集分辨率，
  也不影响送入大模型的帧。
- 所有开关/滑块仅在程序未运行时可调节，运行中锁定。
"""
import os
import sys
import queue
import logging
import threading

import numpy as np
import main  # 复用 main.context 作为唯一共享上下文

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPlainTextEdit, QPushButton, QFrame, QSplitter,
    QToolButton, QComboBox, QSlider, QSizePolicy, QCheckBox,
    QProgressBar, QDoubleSpinBox,
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import (
    QImage, QPixmap, QFont, QPainter, QPainterPath, QTextCursor,
)

from src.utils.config import Config
from src.runtime import JACRuntime


# ----------------------------- 暗色圆角主题 -----------------------------
DARK_QSS = """
QWidget {
    background: #0d0d0f;
    color: #e6e6e6;
    font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    font-size: 13px;
}
QMainWindow, QWidget#central { background: #0d0d0f; }

QFrame, QPlainTextEdit, QPushButton, QComboBox, QSlider, QLabel, QToolButton {
    background: #16161a;
    border: 1px solid #2a2a30;
    border-radius: 12px;
    padding: 6px;
}
QPushButton {
    background: #1c1c22;
    border: 1px solid #34343c;
    padding: 8px 16px;
}
QPushButton:hover { background: #26262e; border: 1px solid #4f8cff; }
QPushButton:pressed { background: #2f2f3a; }
QPushButton:disabled { background: #101012; color: #555; border: 1px solid #1c1c20; }

QPlainTextEdit#console { background: #0a0a0c; border-radius: 12px; }
QPlainTextEdit#input  { background: #111114; border-radius: 12px; }

QLabel#video {
    background: #000000;
    border: 1px solid #2a2a30;
    border-radius: 14px;
}

QComboBox { padding: 4px 8px; }
QComboBox QAbstractItemView {
    background: #16161a;
    selection-background-color: #2a2a35;
    border-radius: 8px;
}

QSlider::groove:horizontal {
    background: #222228;
    height: 6px;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #4f8cff;
    width: 16px;
    height: 16px;
    margin-top: -5px;
    border-radius: 8px;
}
QSlider::handle:horizontal:hover { background: #6fa0ff; }

QScrollBar:vertical, QScrollBar:horizontal {
    background: #111114;
    border-radius: 6px;
}
QCheckBox { background: transparent; border: none; padding: 4px; }
QToolButton { padding: 6px; }

QStatusBar {
    background: #16181d;
    color: #c8ccd4;
    border-top: 1px solid #2a2e36;
    padding: 2px 8px;
}
QStatusBar QLabel { color: #c8ccd4; }
QStatusBar::item { border: none; }
"""


# ----------------------------- 圆角视频标签 -----------------------------
class RoundedVideoLabel(QLabel):
    """把 pixmap 裁剪为圆角绘制，配合 #video 的圆角边框。
    关键：摄像头画面保持原始比例居中绘制，绝不拉伸填满标签矩形。"""
    def paintEvent(self, event):
        """绘制事件：圆角裁剪后等比绘制视频帧。

        关键防御：pix 可能是 None，也可能是 isNull() 的空 QPixmap——
        PySide6 在 QLabel 未设置 pixmap 时会返回空 QPixmap 而非 None。对空 pixmap
        调用 scaled() 会打印 "QPixmap::scaled: Pixmap is a null pixmap" 并触发 macOS
        Metal 后端断言崩溃(abort)。因此必须同时判 None 与 isNull()。
        """
        pix = self.pixmap()
        if pix is None or pix.isNull():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        r = 14
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), r, r)
        painter.setClipPath(path)
        # 手动等比缩放绘制，避免调用 QPixmap.scaled() —— 该方法在 macOS
        # Metal 后端会触发 "_status < MTLCommandBufferStatusCommitted" 断言崩溃(abort)。
        pw, ph = pix.width(), pix.height()
        lw, lh = self.width(), self.height()
        if pw <= 0 or ph <= 0:
            super().paintEvent(event)
            return
        scale = min(lw / pw, lh / ph)
        dw, dh = int(pw * scale), int(ph * scale)
        x = (lw - dw) // 2
        y = (lh - dh) // 2
        painter.drawPixmap(x, y, dw, dh, pix)


# ----------------------------- 多行输入框 -----------------------------
class InputBox(QPlainTextEdit):
    """Enter 换行；Ctrl+Enter 触发发送。"""
    sendRequested = Signal()

    def keyPressEvent(self, event):
        """键按键事件"""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and \
                (event.modifiers() & Qt.ControlModifier):
            self.sendRequested.emit()
            return
        super().keyPressEvent(event)


# ----------------------------- 日志重定向 -----------------------------
class _GuiStream:
    """替换 sys.stdout / sys.stderr，把文本推入线程安全队列。"""
    def __init__(self, q: queue.Queue):
        """初始化实例"""
        self.q = q

    def write(self, s):
        """写入"""
        if s:
            self.q.put(s)
        return len(s)

    def flush(self):
        """刷新"""
        pass

    def isatty(self):
        # GUI 模式下不是真实终端，状态行不应打印到 stdout（交给状态栏）
        """终端判断"""
        return False


class _QtLogHandler(logging.Handler):
    """把 logging 记录推入同一队列，供 GUI 控制台显示。"""
    def __init__(self, q: queue.Queue):
        """初始化实例"""
        super().__init__()
        self.q = q
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        """发出"""
        try:
            self.q.put(self.format(record) + "\n")
        except Exception:
            pass


# ----------------------------- 主窗口 -----------------------------
class MainWindow(QMainWindow):
    def __init__(self, config: Config):
        """初始化实例"""
        super().__init__()
        self.config = config
        self.context = main.context
        self.runtime = JACRuntime(
            context=self.context,
            on_state_change=self._on_state_change,
        )
        self.panel_collapsed = False
        self.zoom = 1.0
        self._stop_requested = False  # 启动过程中若用户点「停止」，用于中止刚拉起的运行时

        self.setWindowTitle("J.A.C.Prototype")
        self.resize(1280, 720)

        self._build_ui()
        self._setup_timers()
        self._redirect_logging()

    # ----------------------------------------------------- UI 构建
    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # ============ 左：视觉解析画面 + 控制 ============
        left = QVBoxLayout()
        left.setSpacing(10)

        self.video_label = RoundedVideoLabel()
        self.video_label.setObjectName("video")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_label.setMinimumSize(640, 360)
        self.video_label.setText("未启动 · 摄像头画面将显示在这里")
        left.addWidget(self.video_label, 1)

        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)

        self.start_btn = QPushButton("启动")
        self.start_btn.setMinimumHeight(40)
        self.start_btn.clicked.connect(self._toggle_run)
        ctrl.addWidget(self.start_btn)

        ctrl.addWidget(QLabel("显示分辨率"))
        self.res_combo = QComboBox()
        self.res_combo.addItems(["1280×720", "1600×900", "1920×1080", "自适应"])
        self.res_combo.setCurrentText("1280×720")
        self.res_combo.currentTextChanged.connect(self._apply_resolution)
        ctrl.addWidget(self.res_combo)

        ctrl.addWidget(QLabel("缩放"))
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(50, 200)
        self.zoom_slider.setValue(100)
        self.zoom_slider.setMinimumWidth(120)
        self.zoom_slider.valueChanged.connect(self._apply_zoom)
        ctrl.addWidget(self.zoom_slider)
        self.zoom_label = QLabel("100%")
        ctrl.addWidget(self.zoom_label)

        left.addLayout(ctrl)

        left_w = QWidget()
        left_w.setLayout(left)
        root.addWidget(left_w, 3)

        # ============ 中：控制台 ============
        mid = QVBoxLayout()
        mid.setSpacing(10)

        self.console = QPlainTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        # 显式声明可选中/可复制：macOS + Fusion 样式下只读控件偶发选择被禁用，
        # 这里强制开启鼠标与键盘选择，保证控制台日志随时可被 Cmd+C 复制（用于 debug）。
        self.console.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        )
        self.console.setMinimumWidth(320)
        self.console.setMaximumBlockCount(4000)  # 限制缓冲，防止无限增长卡顿
        self.console.setFont(QFont("Consolas", 11))
        self.console.appendPlainText("J.A.C.Prototype 控制台已就绪。点击「启动」开始。")
        mid.addWidget(self.console, 3)

        self.input_box = InputBox()
        self.input_box.setObjectName("input")
        self.input_box.setPlaceholderText(
            "在此输入指令（Enter 换行，Ctrl+Enter 或点「发送」提交）…"
        )
        self.input_box.setMaximumHeight(90)
        self.input_box.sendRequested.connect(self._send)
        mid.addWidget(self.input_box, 1)

        send_row = QHBoxLayout()
        send_row.addStretch(1)
        self.send_btn = QPushButton("发送")
        self.send_btn.setMinimumHeight(36)
        self.send_btn.clicked.connect(self._send)
        send_row.addWidget(self.send_btn)
        mid.addLayout(send_row)

        mid_w = QWidget()
        mid_w.setLayout(mid)
        root.addWidget(mid_w, 2)

        # ============ 右：折叠开关选项面板 ============
        self.option_panel = QFrame()
        self.option_panel.setObjectName("options")
        self.option_panel.setMinimumWidth(260)
        op = QVBoxLayout(self.option_panel)
        op.setContentsMargins(14, 14, 14, 14)
        op.setSpacing(14)

        self.collapse_btn = QToolButton()
        self.collapse_btn.setText("« 收起选项")
        self.collapse_btn.clicked.connect(self._toggle_panel)
        op.addWidget(self.collapse_btn)

        self.judge_chk = QCheckBox("前置判断模型（主动感知）")
        self.judge_chk.setChecked(False)  # bo s s 偏好：GUI 默认不勾选判断模型
        op.addWidget(self.judge_chk)

        self.tts_chk = QCheckBox("Qwen3-TTS 语音合成")
        self.tts_chk.setChecked(self.config.use_qwen_tts)
        op.addWidget(self.tts_chk)

        # 工具功能（Function Calling）开关：与主动模型/TTS 一致，属于启动前配置
        self.tools_chk = QCheckBox("工具功能（Function Calling）")
        self.tools_chk.setChecked(self.config.tools_enabled)
        op.addWidget(self.tools_chk)

        # MiniCPM-o-4_5 全双工开关：勾选后由 omni 直接接管 TTS + 判断引擎（OMNI 模式）
        self.omni_chk = QCheckBox("MiniCPM-o-4_5 全双工（接管 TTS + 判断）")
        self.omni_chk.setChecked(True)  # bo s s 偏好：GUI 默认进 OMNI 全双工
        op.addWidget(self.omni_chk)
        # OMNI 模式下传统 judge/TTS/tools 与 omni 架构互斥，勾选 OMNI 时灰掉它们并提示
        self.omni_chk.toggled.connect(self._on_omni_toggled)

        # 麦克风增益（OMNI 全双工：内建麦离嘴远、能量不足时调高，便于触发服务端 VAD）
        gain_row = QHBoxLayout()
        gain_row.addWidget(QLabel("麦克风增益 (OMNI)"))
        self.mic_gain_spin = QDoubleSpinBox()
        self.mic_gain_spin.setRange(1.0, 20.0)
        self.mic_gain_spin.setSingleStep(0.5)
        self.mic_gain_spin.setValue(float(getattr(self.config, "omni_mic_gain", 1.0)))
        gain_row.addWidget(self.mic_gain_spin)
        op.addLayout(gain_row)

        # Listen 采样系数（OMNI 全双工：<1 压低 listen 逼回复，>1 增 listen；默认 0.5）
        lps_row = QHBoxLayout()
        lps_row.addWidget(QLabel("Listen 概率系数 (OMNI)"))
        self.listen_prob_scale_spin = QDoubleSpinBox()
        self.listen_prob_scale_spin.setRange(0.1, 1.0)
        self.listen_prob_scale_spin.setSingleStep(0.05)
        self.listen_prob_scale_spin.setValue(float(getattr(self.config, "omni_listen_prob_scale", 0.5)))
        lps_row.addWidget(self.listen_prob_scale_spin)
        op.addLayout(lps_row)

        # ---- OMNI 实时诊断区（音量条 + 实时回复文字）----
        self.omni_live = QFrame()
        self.omni_live.setObjectName("omniLive")
        live = QVBoxLayout(self.omni_live)
        live.setContentsMargins(0, 0, 0, 0)
        live.setSpacing(6)
        live.addWidget(QLabel("麦克风音量 (OMNI)"))
        self.mic_bar = QProgressBar()
        self.mic_bar.setRange(0, 100)
        self.mic_bar.setValue(0)
        live.addWidget(self.mic_bar)
        live.addWidget(QLabel("OMNI 实时回复"))
        self.omni_reply = QPlainTextEdit()
        self.omni_reply.setReadOnly(True)
        self.omni_reply.setMaximumHeight(150)
        self.omni_reply.setObjectName("omniReply")
        live.addWidget(self.omni_reply)
        op.addWidget(self.omni_live)
        self._last_reply_shown = ""

        op.addWidget(self._labeled_slider(
            "判断间隔（秒）", self._make_interval_slider(), self.interval_label,
        ))
        op.addWidget(self._labeled_slider(
            "判断请求超时（秒）", self._make_timeout_slider(), self.timeout_label,
        ))

        op.addStretch(1)
        root.addWidget(self.option_panel, 1)

        # 折叠后显示的细条
        self.expand_btn = QToolButton()
        self.expand_btn.setText("»")
        self.expand_btn.setFixedWidth(22)
        self.expand_btn.clicked.connect(self._toggle_panel)
        self.expand_btn.hide()
        root.addWidget(self.expand_btn)

        self._init_status_bar()

    def _init_status_bar(self):
        """初始化状态栏"""
        self.status_listen = QLabel("就绪")
        self.status_listen.setObjectName("statusListen")
        self.status_omni = QLabel("○ OMNI 未启用")
        self.status_omni.setObjectName("statusOmni")
        self.status_sys = QLabel("● 已停止")
        self.status_sys.setObjectName("statusSys")
        bar = self.statusBar()
        bar.addWidget(self.status_listen)
        bar.addWidget(self.status_omni)
        bar.addPermanentWidget(self.status_sys)

    def _make_interval_slider(self):
        """生成间隔滑块"""
        s = QSlider(Qt.Horizontal)
        s.setRange(2, 40)              # 半秒步进 -> 1.0s ~ 20.0s
        s.setValue(int(self.config.judgment_interval * 2))
        self.interval_label = QLabel(f"{self.config.judgment_interval:.1f}s")
        s.valueChanged.connect(lambda v: self.interval_label.setText(f"{v / 2:.1f}s"))
        self.interval_slider = s
        return s

    def _make_timeout_slider(self):
        """生成超时滑块"""
        s = QSlider(Qt.Horizontal)
        s.setRange(6, 120)             # 半秒步进 -> 3.0s ~ 60.0s
        s.setValue(int(self.config.judgment_timeout * 2))
        self.timeout_label = QLabel(f"{self.config.judgment_timeout:.1f}s")
        s.valueChanged.connect(lambda v: self.timeout_label.setText(f"{v / 2:.1f}s"))
        self.timeout_slider = s
        return s

    def _labeled_slider(self, title, slider, label):
        """带标签滑块"""
        box = QVBoxLayout()
        box.setSpacing(4)
        box.addWidget(QLabel(title))
        box.addWidget(slider)
        box.addWidget(label)
        w = QWidget()
        w.setLayout(box)
        return w

    # ----------------------------------------------------- 计时器
    def _setup_timers(self):
        self.frame_timer = QTimer(self)
        self.frame_timer.timeout.connect(self._pull_frame)
        self.frame_timer.start(33)

        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._update_status)
        self.status_timer.start(250)

    # ----------------------------------------------------- 日志重定向
    def _redirect_logging(self):
        self.log_q = queue.Queue()
        sys.stdout = _GuiStream(self.log_q)
        sys.stderr = _GuiStream(self.log_q)
        self._log_handler = _QtLogHandler(self.log_q)
        logging.getLogger().addHandler(self._log_handler)
        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self._pull_logs)
        self.log_timer.start(50)

    # ----------------------------------------------------- 取帧
    def _pull_frame(self):
        """从共享上下文取标注帧并绘制到视频标签（带空帧/空 pixmap 防御）。

        macOS Metal 后端对空 QPixmap 做 scaled() 会断言崩溃(abort)，故任何一
        环拿到空对象都直接跳过本次绘制，绝不把空 pixmap 交出去渲染。
        """
        # 运行时已停止（点了「停止」或正在退出窗口）则不再绘制：避免在底层资源
        # 释放/窗口销毁过程中仍向 Metal 渲染管线提交帧，触发断言崩溃（闪退）。
        if not self.runtime.running:
            return
        # OMNI 模式：画面来自 omni 客户端的摄像头帧（传统模式来自 context 标注帧）
        omni_client = getattr(self.runtime, "omni_client", None)
        if getattr(self.runtime, "omni_mode", False) and omni_client is not None:
            f = omni_client.get_latest_frame()
        else:
            f = self.context.get_annotated_frame()
        if f is None:
            return
        try:
            h, w, ch = f.shape
        except Exception:
            return
        if h <= 0 or w <= 0 or ch <= 0:
            return
        # cv2 帧需内存连续，否则 QImage 绑定到错位缓冲会得到损坏/空的 pixmap
        try:
            _contiguous = bool(f.flags["C_CONTIGUOUS"])
        except Exception:
            _contiguous = False
        if not _contiguous:
            f = np.ascontiguousarray(f)
        img = QImage(f.data, w, h, ch * w, QImage.Format_BGR888)
        if img.isNull():
            return
        pix = QPixmap.fromImage(img)
        if pix.isNull():
            return
        # 不做 scaled：缩放交给 RoundedVideoLabel.paintEvent 用 drawPixmap 等比绘制，
        # 避免在 Metal 后端对 QPixmap 二次 scaled 触发断言崩溃(abort)。
        self.video_label.setPixmap(pix)

    # ----------------------------------------------------- 取日志
    def _pull_logs(self):
        # 仅当用户已滚动到底部且未在选中文本时才自动跟随，否则保留其阅读/复制位置
        sb = self.console.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 2
        has_selection = self.console.textCursor().hasSelection()
        while True:
            try:
                msg = self.log_q.get_nowait()
            except queue.Empty:
                break
            # 把 stdout 的 \r（原地覆盖）净化成换行，避免把状态行当成新行疯狂堆叠
            clean = msg.replace("\r", "\n").rstrip("\n")
            if clean:
                self.console.appendPlainText(clean)
        # 用户正在选中复制（有选区）时不打断其选型；否则在底部时自动跟随最新日志
        if at_bottom and not has_selection:
            self.console.moveCursor(QTextCursor.End)

    # ----------------------------------------------------- 状态栏
    def _update_status(self):
        s = self.context.get_listening_status()
        self.status_listen.setText(s or "就绪")
        if getattr(self.runtime, "omni_mode", False):
            self.status_omni.setText("● OMNI 全双工")
        else:
            self.status_omni.setText("○ OMNI 未启用")
        self.status_sys.setText("● 运行中" if self.runtime.running else "● 已停止")
        # OMNI 实时诊断：麦克风音量条 + 实时回复文字区（仅在 OMNI 模式且客户端就绪时刷新）
        omni_client = getattr(self.runtime, "omni_client", None)
        if getattr(self.runtime, "omni_mode", False) and omni_client is not None:
            level = omni_client.get_latest_mic_level()
            self.mic_bar.setValue(int(min(1.0, level / 0.15) * 100))
            txt = omni_client.get_reply_text()
            if txt != self._last_reply_shown:
                self.omni_reply.setPlainText(txt)
                self._last_reply_shown = txt
        else:
            if self.mic_bar.value() != 0:
                self.mic_bar.setValue(0)

    # ----------------------------------------------------- 启动/停止
    def _toggle_run(self):
        if not self.runtime.running:
            self._stop_requested = False  # 开始新启动，清除上一次的「停止」意图
            cfg = self._collect_config()
            # 把重活放到后台线程：摄像头/YOLO/Whisper/Qwen3-TTS/记忆加载
            # 全在主线程同步执行会长时间阻塞事件循环，macOS 会判为「未响应」，
            # 并在阻塞期间任何绘制请求下放大 Metal 崩溃概率。后台跑可保 GUI 流畅。
            self.start_btn.setEnabled(False)
            self.start_btn.setText("启动中…")

            def _do_start():
                try:
                    self.runtime.start(cfg)
                except Exception:
                    # 打印完整 traceback（而非仅异常消息），便于真机验收时直接定位
                    # 缺失依赖 / 导入错误等根因，避免反复来回。
                    import traceback as _tb
                    self.console.appendPlainText(
                        "[GUI] 启动失败:\n" + "".join(_tb.format_exception(*sys.exc_info()))
                    )
                # 边界：若用户在启动过程中点了「停止」，立即停掉刚拉起的运行时
                if self._stop_requested and self.runtime.running:
                    self._safe_stop_runtime()
                    return
                # 成功时 _on_state_change 已把按钮置为「停止」；
                # 失败时需在此恢复按钮可交互，否则会卡在「启动中…」。
                if not self.runtime.running:
                    self.console.appendPlainText(
                        "[GUI] 启动未完成（摄像头/模型未就绪），请检查设备与 LM Studio。"
                    )
                    # 跨线程回主线程恢复 UI
                    QTimer.singleShot(0, lambda: (
                        self.start_btn.setEnabled(True),
                        self.start_btn.setText("启动"),
                        self._set_options_enabled(True),
                    ))

            threading.Thread(target=_do_start, daemon=True, name="gui-start").start()
        else:
            # 点「停止」：只停运行时，GUI 窗口保持打开（便于查看/复制控制台日志 debug）
            self._stop_requested = True
            self._safe_stop_runtime()

    def _safe_stop_runtime(self):
        """安全地停止 J.A.C. 运行时，但**不关闭 GUI 窗口**。

        顺序很关键：先停帧定时器 + 清空视频画面（释放 Metal 渲染资源），再释放
        底层资源（摄像头/线程），避免在资源销毁过程中仍在向窗口提交帧，触发
        macOS Metal 断言崩溃（abort/闪退）。停止后 GUI 保持打开，控制台日志完整
        保留，便于调试（debug）。
        """
        if not self.runtime.running:
            return
        try:
            self.frame_timer.stop()       # 停止视频绘制，避免 Metal 崩溃
            self.video_label.clear()      # 清空画面 pixmap，释放渲染资源
            self.runtime.stop()           # 释放摄像头/线程等底层资源
        except Exception as e:
            print(f"[GUI] 停止运行时异常（已忽略）: {e}")
        # 状态回调 _on_state_change(False) 已把按钮恢复为「启动」并解锁选项

    def _on_state_change(self, running):
        """当状态变化

        关键修复：启动时 _toggle_run 会把按钮 setEnabled(False) 防重复点击，
        启动成功后必须在此重新 setEnabled(True)，否则按钮虽显示「停止」却仍是
        禁用态（灰色），用户点不动、无法停止程序。
        """
        self.start_btn.setText("停止" if running else "启动")
        self.start_btn.setEnabled(True)  # 运行/停止两种状态都必须可点击
        self._set_options_enabled(not running)

    def _set_options_enabled(self, en):
        """设置选项已启用"""
        for w in (self.judge_chk, self.tts_chk, self.tools_chk, self.omni_chk,
                  self.interval_slider, self.timeout_slider):
            w.setEnabled(en)
        # OMNI 模式下传统 judge/TTS/tools 与 omni 架构互斥，仍保持灰掉状态
        if en and self.omni_chk.isChecked():
            self._on_omni_toggled(True)

    def _on_omni_toggled(self, checked):
        """OMNI 与传统 judge/TTS/tools 架构互斥：勾选 OMNI 时灰掉三者并提示。

        OMNI 模式下 omni 直接接管「看 + 听 + 说 + 判断」，传统链路（MiniCPM-v 判断引擎 /
        Qwen3-TTS / Function Calling 工具）不生效，避免用户误以为开启。
        """
        for w in (self.judge_chk, self.tts_chk, self.tools_chk):
            w.setEnabled(not checked)
            if checked:
                w.setToolTip("OMNI 模式下不生效（架构互斥）")
            else:
                w.setToolTip("")

    def _collect_config(self):
        """收集配置"""
        return Config(
            judgment_engine_enabled=self.judge_chk.isChecked(),
            judgment_interval=self.interval_slider.value() / 2.0,
            judgment_timeout=self.timeout_slider.value() / 2.0,
            use_qwen_tts=self.tts_chk.isChecked(),
            tools_enabled=self.tools_chk.isChecked(),
            omni_enabled=self.omni_chk.isChecked(),
            omni_server_url=self.config.omni_server_url,
            omni_server_bin=self.config.omni_server_bin,
            omni_model_dir=self.config.omni_model_dir,
            omni_host=self.config.omni_host,
            omni_port=self.config.omni_port,
            omni_quant=self.config.omni_quant,
            omni_ref_audio=self.config.omni_ref_audio,
            omni_fps=self.config.omni_fps,
            omni_mic_gain=self.mic_gain_spin.value(),
            omni_listen_prob_scale=self.listen_prob_scale_spin.value(),
            omni_duplex=self.config.omni_duplex,
            omni_auto_launch=self.config.omni_auto_launch,
            brain_backend=self.config.brain_backend,
            awake_timeout=self.config.awake_timeout,
            memory_enabled=self.config.memory_enabled,
            memory_capture_person_id=self.config.memory_capture_person_id,
            judgment_model_name=self.config.judgment_model_name,
            wake_words=self.config.wake_words,
            camera_width=self.config.camera_width,
            camera_height=self.config.camera_height,
        )

    # ----------------------------------------------------- 手动发送
    def _send(self):
        txt = self.input_box.toPlainText().strip()
        if not txt:
            return
        self.runtime.manual_input(txt)
        self.input_box.clear()

    # ----------------------------------------------------- 分辨率/缩放（仅显示）
    def _apply_resolution(self, text):
        if text == "自适应":
            self.showMaximized()
        else:
            self.showNormal()
            try:
                w, h = text.split("×")
                self.resize(int(w), int(h))
            except Exception:
                pass

    def _apply_zoom(self, value):
        """应用缩放"""
        self.zoom_label.setText(f"{value}%")
        z = value / 100.0
        # 改变视频面板的最小尺寸，让画面整体放大/缩小（不影响采集/模型）
        self.video_label.setMinimumSize(int(640 * z), int(360 * z))

    # ----------------------------------------------------- 折叠面板
    def _toggle_panel(self):
        self.panel_collapsed = not self.panel_collapsed
        if self.panel_collapsed:
            self.option_panel.hide()
            self.expand_btn.show()
        else:
            self.option_panel.show()
            self.expand_btn.hide()

    # ----------------------------------------------------- 关闭
    def closeEvent(self, event):
        # 点窗口 X：真正退出程序。先安全停止运行时（停帧定时器 + 清空画面防 Metal
        # 崩溃），再无论如何都关闭窗口。用 try/finally 兜底：即使停止过程抛异常，
        # 窗口也能正常关闭，不会闪退。
        try:
            self._safe_stop_runtime()
        finally:
            super().closeEvent(event)


# ----------------------------- 入口 -----------------------------
def run_gui(config: Config):
    # 高 DPI（必须在 QApplication 实例化之前设置）
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_QSS)

    w = MainWindow(config)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run_gui(Config.load())
