"""记忆管理界面；Qt 只读快照，写入由后台服务执行。"""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QPlainTextEdit, QPushButton, QCheckBox, QMessageBox


class MemoryWindow(QDialog):
    """展示来源并提供精确更正、删除与自动记忆设置。"""

    def __init__(self, service, parent=None):
        """构造控件并定时读取轻量快照；不等待磁盘或模型。"""
        super().__init__(parent)
        self.service, self.facts, self.pending, self.version = service, [], None, None
        self.setWindowTitle('长期记忆')
        self.resize(720, 620)
        layout = QVBoxLayout(self)
        self.status = QLabel('正在读取本机记忆…')
        layout.addWidget(self.status)
        controls = QHBoxLayout()
        self.automatic = QCheckBox('日常对话自动记忆')
        self.automatic.clicked.connect(lambda checked: self.send('settings', automatic=checked))
        self.pause = QCheckBox('本次对话不记忆')
        self.pause.clicked.connect(lambda checked: self.send('pause', value=checked))
        controls.addWidget(self.automatic)
        controls.addWidget(self.pause)
        layout.addLayout(controls)
        self.items = QListWidget()
        self.items.currentRowChanged.connect(self.selected)
        layout.addWidget(self.items)
        self.source = QLabel('选中条目可查看来源并更正。')
        self.source.setWordWrap(True)
        layout.addWidget(self.source)
        self.editor = QPlainTextEdit()
        self.editor.setMaximumHeight(130)
        layout.addWidget(self.editor)
        buttons = QHBoxLayout()
        for title, callback in [('保存更正', self.edit), ('删除选中记忆', self.delete), ('清空全部记忆', self.clear_all),
                                ('重建索引', lambda: self.send('rebuild_index'))]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.pending_items, self.pending_version = [], None
        layout.addWidget(QLabel('待整理内容（连续失败可重试或丢弃）'))
        self.candidates = QListWidget()
        self.candidates.setMaximumHeight(85)
        layout.addWidget(self.candidates)
        pending_buttons = QHBoxLayout()
        for title, retry in [('重试选中内容', True), ('丢弃选中内容', False)]:
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, retry=retry: self.resolve(retry))
            pending_buttons.addWidget(button)
        layout.addLayout(pending_buttons)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)
        self.refresh()

    def send(self, op, **values):
        """单次变更提交到后台，有未完成操作时不覆盖其回执。"""
        if self.pending and not self.pending.done():
            self.status.setText('上一项变更正在保存，请稍候。')
            return
        self.pending = self.service.request(op, **values)
        self.status.setText('正在保存…')

    def refresh(self):
        """只在版本改变时更新列表，保留用户正在输入的更正文本。"""
        if self.pending and self.pending.done():
            try:
                self.pending.result()
                self.status.setText('变更已保存。')
            except Exception as error:
                self.status.setText(str(error))
            self.pending = None
        state = self.service.view()
        self.automatic.setChecked(state.get('automatic', True))
        self.pause.setChecked(state.get('paused', False))
        if not state.get('ready'):
            self.status.setText(state.get('error') or '正在读取本机记忆…')
            return
        self.setWindowTitle(f"长期记忆 · {len(state['facts'])}条 · 待整理{state['pending']}条")
        if self.status.text() == '正在读取本机记忆…':
            self.status.setText('本机记忆已就绪。')
        pending_items = state.get('pending_items', [])
        if pending_items != self.pending_version:
            self.pending_version = self.pending_items = pending_items
            self.candidates.clear()
            self.candidates.addItems([('待处理：' if item['needs_input'] else '整理中：') + item['text'] for item in pending_items])
        if self.version != state.get('revision'):
            self.version, self.facts = state.get('revision'), state['facts'] + state.get('candidate_facts', [])
            self.items.clear()
            self.items.addItems([('[待核对] ' if fact.get('status') == 'candidate' else '') + fact['content'] for fact in self.facts])

    def selected(self, row):
        """展示真实原话来源与更新时间。"""
        if not 0 <= row < len(self.facts):
            self.editor.clear()
            return
        fact = self.facts[row]
        self.editor.setPlainText(fact['content'])
        self.source.setText(f"来源：{fact['source_kind']}　更新：{fact['updated_at']}\n原话：{fact['evidence']}")

    def edit(self):
        """用选中事实的编号与版本提交更正，冲突时由存储拒绝。"""
        row = self.items.currentRow()
        if 0 <= row < len(self.facts):
            fact = self.facts[row]
            self.send('edit', id=fact['id'], revision=fact['revision'], content=self.editor.toPlainText())

    def delete(self):
        """仅删除当前选中事实。"""
        row = self.items.currentRow()
        if 0 <= row < len(self.facts):
            self.send('delete', ids=[self.facts[row]['id']])

    def clear_all(self):
        """确认清空记忆与待整理数据，范围不包含任务原文件。"""
        if QMessageBox.question(self, '清空长期记忆', '删除全部长期记忆和待整理内容？任务原文件与外部导出不会删除。') == QMessageBox.StandardButton.Yes:
            self.send('delete')

    def resolve(self, retry):
        """按精确事件编号重试或丢弃，不把未提炼原话当作事实。"""
        row = self.candidates.currentRow()
        if 0 <= row < len(self.pending_items):
            self.send('resolve_pending', id=self.pending_items[row]['id'], retry=retry)
