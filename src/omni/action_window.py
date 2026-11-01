"""本次任务的目录、网站、应用与执行权限选择；不保存为全局默认授权。"""
import plistlib
from pathlib import Path

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QLineEdit, QCheckBox, QPlainTextEdit, QFileDialog,
                               QDialogButtonBox, QMessageBox)


class ActionScopeDialog(QDialog):
    """用户选择实际操作范围；模型无法访问或修改对话框中的授权值。"""

    def __init__(self, value=None, parent=None):
        """恢复本次尚未发布的选择，所有权限初始关闭。"""
        super().__init__(parent)
        selected = value or {}
        self.setWindowTitle('本次任务的电脑操作范围')
        self.resize(640, 560)
        layout = QVBoxLayout(self)
        hint = QLabel('允许 J.A.C. 按本次任务连续操作以下范围。\n密码请手动输入；发送、提交等具体操作可在任务中心确认。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        self.workspace = QLineEdit(selected.get('workspace', ''))
        self.workspace.setReadOnly(True)
        self.workspace.setPlaceholderText('选择具体工作目录')
        choose = QPushButton('选择工作目录')
        choose.clicked.connect(self.choose_workspace)
        row.addWidget(self.workspace)
        row.addWidget(choose)
        layout.addLayout(row)
        self.edit = QCheckBox('允许修改此目录中的文本和代码（保留恢复副本）')
        self.edit.setChecked(selected.get('edit', False))
        layout.addWidget(self.edit)
        self.python = QCheckBox('允许在隔离环境运行 Python 脚本')
        self.node = QCheckBox('允许在隔离环境运行 JavaScript / Node.js 脚本')
        for name, widget in [('python', self.python), ('node', self.node)]:
            widget.setChecked(name in selected.get('runtimes', []))
            layout.addWidget(widget)
        layout.addWidget(QLabel('允许操作的网站（每行一个完整网址）'))
        self.origins = QPlainTextEdit('\n'.join(selected.get('origins', [])))
        self.origins.setPlaceholderText('https://example.com')
        self.origins.setMaximumHeight(110)
        layout.addWidget(self.origins)
        self.apps = list(selected.get('apps', []))
        choose_app = QPushButton('选择允许操作的应用')
        choose_app.clicked.connect(self.choose_app)
        layout.addWidget(choose_app)
        self.app_label = QLabel('、'.join(self.apps) or '尚未授权应用')
        self.app_label.setWordWrap(True)
        layout.addWidget(self.app_label)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_workspace(self):
        """授权用户实际选择的目录；目录内容仍在后台按工具边界访问。"""
        path = QFileDialog.getExistingDirectory(self, '选择本次工作目录')
        if path:
            self.workspace.setText(path)

    def choose_app(self):
        """从应用包读取身份，用户不需要手工输入系统标识。"""
        path = QFileDialog.getExistingDirectory(self, '选择应用程序包', '/Applications',
                                               QFileDialog.ShowDirsOnly | QFileDialog.DontUseNativeDialog)
        if not path:
            return
        try:
            info = plistlib.loads((Path(path) / 'Contents/Info.plist').read_bytes())
            identifier = info['CFBundleIdentifier']
            if identifier not in self.apps:
                self.apps.append(identifier)
            self.app_label.setText('、'.join(self.apps))
        except (OSError, ValueError, KeyError):
            QMessageBox.warning(self, '无法选择', '请选择完整的 .app 应用程序包。')

    def value(self):
        """生成显式授权原始值，发布任务时再次冻结目录身份。"""
        return {'workspace': self.workspace.text(), 'edit': self.edit.isChecked(),
                'runtimes': [name for name, widget in [('python', self.python), ('node', self.node)] if widget.isChecked()],
                'origins': [line.strip() for line in self.origins.toPlainText().splitlines() if line.strip()], 'apps': self.apps}

    def validate(self):
        """在用户关闭对话框前解释无效范围；有效选择仍只用于当前任务。"""
        from src.brain.agent.action_policy import authorize
        try:
            authorize(self.value())
            self.accept()
        except Exception as error:
            QMessageBox.warning(self, '范围需要调整', str(error))
