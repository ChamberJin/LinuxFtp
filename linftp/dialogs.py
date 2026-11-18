"""连接对话框：填写主机、账号、协议，测试并保存站点。"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .backend import SessionConfig, SessionError, create_session
from .theme import Palette


class _ProbeThread(QThread):
    """在后台线程里试连，避免对话框卡死。"""

    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, cfg: SessionConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg

    def run(self) -> None:
        try:
            with create_session(self.cfg) as sess:
                listing = sess.listdir(sess.cwd())
            self.finished_ok.emit(f"连接成功，当前目录 {len(listing)} 个条目")
        except SessionError as e:
            self.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}")


class ConnectDialog(QDialog):
    def __init__(self, cfg: SessionConfig | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("连接到服务器")
        self.setMinimumWidth(470)
        self._probe: _ProbeThread | None = None

        self.protocol = QComboBox()
        self.protocol.addItems(["ftp", "sftp"])
        self.protocol.setToolTip("ftp 默认 21 端口；sftp 默认 22 端口，支持密码或私钥")
        self._proto_default = 21      # 当前协议对应的默认端口，用于切换时跟随
        self.protocol.currentTextChanged.connect(self._on_protocol_changed)

        self.host = QLineEdit()
        self.host.setPlaceholderText("192.0.2.10 或 ftp.example.com")

        self.port = QSpinBox()
        self.port.setRange(0, 65535)
        self.port.setValue(21)
        self.port.setSpecialValueText("默认")

        self.username = QLineEdit("anonymous")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)

        self.passive = QCheckBox("被动模式（PASV）")
        self.passive.setChecked(True)
        self.passive.setToolTip("NAT / 防火墙环境下基本都需要开启")

        self.encoding = QComboBox()
        self.encoding.addItems(["utf-8", "gbk", "big5", "latin-1"])

        # 私钥认证（仅 SFTP）：可留空走密码 / ssh-agent
        self.keyfile = QLineEdit()
        self.keyfile.setPlaceholderText("可留空；密码登录时不填")
        self.keyfile.setToolTip("OpenSSH 私钥文件，如 ~/.ssh/id_ed25519")
        btn_key = QPushButton("浏览…")
        btn_key.setMinimumWidth(64)
        btn_key.clicked.connect(self._pick_keyfile)
        self._key_row = QWidget()
        key_layout = QHBoxLayout(self._key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(6)
        key_layout.addWidget(self.keyfile, 1)
        key_layout.addWidget(btn_key)

        self.timeout = QSpinBox()
        self.timeout.setRange(3, 120)
        self.timeout.setValue(15)
        self.timeout.setSuffix(" 秒")

        self.remember = QCheckBox("保存为站点")

        self._form = QFormLayout()
        form = self._form
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(9)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.addRow("协议", self.protocol)
        form.addRow("主机", self.host)
        form.addRow("端口", self.port)
        form.addRow("用户名", self.username)
        form.addRow("密码", self.password)
        form.addRow("私钥文件", self._key_row)
        form.addRow("", self.passive)
        form.addRow("文件名编码", self.encoding)
        form.addRow("超时", self.timeout)
        form.addRow("", self.remember)
        self._sync_proto_fields(self.protocol.currentText())

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(18)
        self.status.setStyleSheet(
            f"color: {Palette.TEXT_MUTED}; font-size: 12px;")

        self.btn_test = QPushButton("测试连接")
        self.btn_test.clicked.connect(self.on_test)

        self.btn_ok = QPushButton("连接")
        self.btn_ok.setProperty("primary", True)
        self.btn_ok.setMinimumWidth(88)
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self.on_accept)

        btn_cancel = QPushButton("取消")
        btn_cancel.setMinimumWidth(80)
        btn_cancel.clicked.connect(self.reject)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addWidget(self.btn_test)
        bottom.addStretch(1)
        bottom.addWidget(btn_cancel)
        bottom.addWidget(self.btn_ok)

        title = QLabel("连接到服务器")
        f = QFont()
        f.setPixelSize(17)
        f.setWeight(QFont.DemiBold)
        title.setFont(f)
        title.setStyleSheet(f"color: {Palette.TEXT};")

        hint = QLabel("填写主机与账号信息；可以先测试连通性再连接")
        hint.setStyleSheet(f"color: {Palette.TEXT_FAINT}; font-size: 12px;")

        head = QVBoxLayout()
        head.setSpacing(3)
        head.addWidget(title)
        head.addWidget(hint)

        body = QWidget()
        body.setLayout(form)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)
        root.addLayout(head)
        root.addWidget(body)
        root.addWidget(self.status)
        root.addLayout(bottom)

        if cfg:
            self.apply(cfg)
        theme.inherit_app_font(self)

    # ------------------------------------------------------------ 协议联动
    def _on_protocol_changed(self, proto: str) -> None:
        default = 22 if proto in ("sftp", "scp") else 21
        # 只有端口还是"上一个协议的默认值"时才跟着换，用户手填的端口不覆盖
        if self.port.value() in (0, self._proto_default):
            self.port.setValue(default)
        self._proto_default = default
        # 默认用户名跟着协议走；用户改过就尊重用户
        current = self.username.text().strip()
        if proto == "sftp" and current in ("", "anonymous"):
            self.username.setText("root")
        elif proto == "ftp" and current == "root":
            self.username.setText("anonymous")
        self._sync_proto_fields(proto)

    def _sync_proto_fields(self, proto: str) -> None:
        is_ftp = proto == "ftp"
        # SFTP 没有被动模式，文件名也固定 UTF-8，这两行藏起来更省心
        self._form.setRowVisible(self.passive, is_ftp)
        self._form.setRowVisible(self.encoding, is_ftp)
        self._form.setRowVisible(self._key_row, proto == "sftp")

    def _pick_keyfile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择私钥文件", os.path.expanduser("~/.ssh"),
            "所有文件 (*)")
        if path:
            self.keyfile.setText(path)

    def apply(self, cfg: SessionConfig) -> None:
        idx = self.protocol.findText(cfg.protocol)
        if idx >= 0:
            self.protocol.setCurrentIndex(idx)   # 会触发协议联动
        self.host.setText(cfg.host)
        self.port.setValue(cfg.port or 0)        # 显式端口放在联动之后覆盖
        self.username.setText(cfg.username)
        self.password.setText(cfg.password)
        self.keyfile.setText(cfg.keyfile)
        self.passive.setChecked(cfg.passive)
        e = self.encoding.findText(cfg.encoding)
        if e >= 0:
            self.encoding.setCurrentIndex(e)
        self.timeout.setValue(cfg.timeout)

    def config(self) -> SessionConfig:
        return SessionConfig(
            protocol=self.protocol.currentText(),
            host=self.host.text().strip(),
            port=self.port.value() or None,
            username=self.username.text().strip() or "anonymous",
            password=self.password.text(),
            keyfile=self.keyfile.text().strip(),
            passive=self.passive.isChecked(),
            encoding=self.encoding.currentText(),
            timeout=self.timeout.value(),
        )

    def wants_save(self) -> bool:
        return self.remember.isChecked()

    def on_test(self) -> None:
        cfg = self.config()
        if not cfg.host:
            QMessageBox.warning(self, "提示", "请填写主机地址")
            return
        if self._probe and self._probe.isRunning():
            return
        self.btn_test.setEnabled(False)
        self.status.setText("正在连接…")
        self._probe = _ProbeThread(cfg, self)
        self._probe.finished_ok.connect(self._on_probe_ok)
        self._probe.failed.connect(self._on_probe_fail)
        self._probe.start()

    def _on_probe_ok(self, msg: str) -> None:
        self.btn_test.setEnabled(True)
        self.status.setStyleSheet(f"color: {Palette.ACCENT}; font-size: 12px;")
        self.status.setText("✓ " + msg)

    def _on_probe_fail(self, msg: str) -> None:
        self.btn_test.setEnabled(True)
        self.status.setStyleSheet(f"color: {Palette.DANGER}; font-size: 12px;")
        self.status.setText("✗ " + msg)

    def on_accept(self) -> None:
        if not self.host.text().strip():
            QMessageBox.warning(self, "提示", "请填写主机地址")
            return
        self.accept()
