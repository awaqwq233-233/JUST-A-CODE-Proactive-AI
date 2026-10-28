"""任务中心：独立于语音连接的进度、恢复控制与资料补充。"""
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QPlainTextEdit, QCheckBox,
                               QDialogButtonBox, QFileDialog)

LABELS = {'queued': '排队', 'running': '执行中', 'pausing': '正在暂停', 'cancelling': '正在取消',
          'paused': '已暂停', 'needs_input': '等待补充', 'completed': '完成', 'partial': '部分完成',
          'cancelled': '已取消', 'failed': '失败', 'deferred': '等待云端接入', 'corrupt': '状态损坏',
          'publishing': '正在交付'}


class TaskCenter(QDialog):
    """轻量列表轮询内存状态，文件解析和模型执行始终在管理器后台线程。"""

    def __init__(self, manager, parent=None):
        """绑定任务管理器并创建可恢复任务控制界面。"""
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle('J.A.C. 任务中心')
        self.resize(960, 650)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('停止语音后任务继续；关闭程序时保存进度，下次打开恢复。'))
        self.items = QTreeWidget()
        self.items.setHeaderLabels(['任务', '状态', '工具调用', '执行时间'])
        self.items.setColumnWidth(0, 430)
        self.items.itemSelectionChanged.connect(self.show_detail)
        layout.addWidget(self.items)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(180)
        layout.addWidget(self.detail)
        row = QHBoxLayout()
        for title, action in [('暂停', 'pause'), ('继续', 'resume'), ('取消', 'cancel'),
                              ('移到队首', 'prioritize'), ('补充资料', 'supplement'),
                              ('增加30分钟/120次', 'extend'), ('打开结果', 'open'), ('删除记录', 'delete')]:
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, value=action: self.act(value))
            row.addWidget(button)
        layout.addLayout(row)
        self.message = QLabel('删除记录会移除本地恢复资料，已交付文件保留在结果目录。')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)
        self.refresh()

    def selected(self):
        """读取所选稳定任务编号，不把界面行号当执行标识。"""
        item = self.items.currentItem()
        return item.data(0, Qt.UserRole) if item else None

    def refresh(self):
        """更新轻量状态并保持选择，不读取任何资料文件正文。"""
        selected = self.selected()
        self.items.blockSignals(True)
        self.items.clear()
        for record in self.manager.list():
            budget = record.get('budget', {})
            item = QTreeWidgetItem([record.get('request', '')[:75], LABELS.get(record['status'], record['status']),
                                   f"{budget.get('used_calls', 0)}/{budget.get('calls', 0)}",
                                   f"{budget.get('used_seconds', 0):.0f}/{budget.get('seconds', 0)} 秒"])
            item.setData(0, Qt.UserRole, record['task_id'])
            self.items.addTopLevelItem(item)
            if record['task_id'] == selected:
                self.items.setCurrentItem(item)
        self.items.blockSignals(False)
        self.show_detail()

    def show_detail(self):
        """显示目标、计划与当前障碍，不编造完成百分比。"""
        record = next((r for r in self.manager.list() if r['task_id'] == self.selected()), None)
        if record:
            lines = [record['request'], record['task_id'], record.get('message', ''),
                     '阶段：' + str(record.get('stage', 0))]
            lines += [f'{i}. {step}' for i, step in enumerate(record.get('plan', []), 1)]
            if record.get('report'):
                lines.append('结果：' + record['report'])
            self.detail.setPlainText('\n'.join(filter(None, lines)))

    def supplement(self, identifier):
        """一次补充说明和明确选择的文件，刷新旧资料须由用户勾选。"""
        dialog = QDialog(self)
        dialog.setWindowTitle('补充资料并继续同一任务')
        dialog.resize(620, 400)
        layout = QVBoxLayout(dialog)
        note = QPlainTextEdit()
        note.setPlaceholderText('补充任务要求、缺失字段或更正信息')
        layout.addWidget(note)
        files, label = [], QLabel('尚未选择新文件')
        choose = QPushButton('选择补充文件')
        def select_files():
            """将用户选择的精确路径加入补充授权，不自动扫描其他目录。"""
            selected, _ = QFileDialog.getOpenFileNames(dialog, '选择补充文件')
            files[:] = selected
            label.setText('已选择 ' + str(len(files)) + ' 份文件')
        choose.clicked.connect(select_files)
        layout.addWidget(choose)
        layout.addWidget(label)
        refresh = QCheckBox('确认使用已修改文件的当前版本，并重新计算受影响结果')
        layout.addWidget(refresh)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            self.manager.resume(identifier, inputs={'files': files}, note=note.toPlainText(), refresh=refresh.isChecked())

    def act(self, action):
        """向管理器提交精确控制，失败原因留在界面，不影响其他任务。"""
        identifier = self.selected()
        if not identifier:
            self.message.setText('bo s s，请先选择任务。')
            return
        try:
            if action in {'pause', 'cancel'}:
                self.manager.control(identifier, action)
            elif action == 'resume':
                self.manager.resume(identifier)
            elif action == 'extend':
                self.manager.resume(identifier, extra_seconds=1800, extra_calls=120)
            elif action == 'supplement':
                self.supplement(identifier)
            elif action == 'prioritize':
                self.manager.prioritize(identifier)
            elif action == 'delete':
                self.manager.delete(identifier)
            elif action == 'open':
                record = next(r for r in self.manager.list() if r['task_id'] == identifier)
                if not record.get('report') or not Path(record['report']).is_file():
                    raise ValueError('该任务尚未交付结果文件')
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(record['report']).parent)))
            self.message.setText('操作已处理。')
        except Exception as error:
            self.message.setText('bo s s，' + str(error))
        self.refresh()
