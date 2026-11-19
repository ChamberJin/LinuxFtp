"""文本预览测试：编码探测、二进制判定、截断、read_head、预览窗口。

    .venv/bin/python tools/test_preview.py

远端 read_head 用内嵌 SFTP 服务（无需外部依赖）；FTP 那条只在本地
2121 端口有测试服务时才跑，否则跳过。
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication  # noqa: E402

from linftp import preview, theme  # noqa: E402
from linftp.backend import SessionConfig, create_session  # noqa: E402
from linftp.preview import (  # noqa: E402
    MAX_PREVIEW,
    PreviewDialog,
    decode_text,
    guess_text_kind,
    looks_binary,
)

passed = failed = skipped = 0
_app = None
_tmp = tempfile.mkdtemp(prefix="linftp-preview-")
_sftp = None


class SkipTest(Exception):
    """环境不具备时跳过（不算失败）。"""


def app() -> QApplication:
    global _app
    if _app is None:
        _app = QApplication([])
        theme.apply(_app)
        _app.setQuitOnLastWindowClosed(False)
    return _app


def check(name: str, fn) -> None:
    global passed, failed, skipped
    try:
        fn()
        print(f"  PASS  {name}")
        passed += 1
    except SkipTest as e:
        print(f"  SKIP  {name}: {e}")
        skipped += 1
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")
        traceback.print_exc()
        failed += 1


def _write(name: str, data) -> str:
    path = os.path.join(_tmp, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def _wait(dlg: PreviewDialog, timeout: float = 15.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if not dlg.hint.text().startswith("正在读取"):
            QApplication.processEvents()
            return
        time.sleep(0.02)
    raise AssertionError("预览加载超时")


def _preview_local(path: str, name: str = "") -> PreviewDialog:
    dlg = PreviewDialog()
    dlg.preview(name=name or os.path.basename(path), path=path,
                size=os.path.getsize(path), mtime=os.path.getmtime(path),
                local_path=path)
    _wait(dlg)
    return dlg


# ---------------------------------------------------------------- 类型判断
def test_guess_text_kind() -> None:
    cases = {
        "app.log": "text", "readme.md": "text", "conf/nginx.conf": "text",
        "Main.java": "text", "a.PNG": "binary", "pkg.tar.gz": "binary",
        "报告.docx": "binary", "data.unknownext": "unknown",
        "Makefile": "text", "dockerfile": "text", ".gitignore": "text",
        "noext": "unknown",
    }
    for name, want in cases.items():
        got = guess_text_kind(name)
        assert got == want, f"{name}: {got} != {want}"


def test_looks_binary() -> None:
    assert looks_binary(b"hello\nworld\n") is False
    assert looks_binary(b"") is False
    # 带 ANSI 颜色的日志要当文本（ESC 放行）
    assert looks_binary(b"\x1b[31mERROR\x1b[0m something failed\n") is False
    # NUL 一律按二进制
    assert looks_binary(b"abc\x00def") is True
    # UTF-16 的 NUL 是正常的，BOM 优先
    assert looks_binary("中文".encode("utf-16")) is False
    # 大量控制字符
    assert looks_binary(bytes(range(1, 9)) * 100) is True


def test_decode_utf8_and_gbk() -> None:
    text = "第一行\n第二行\n"
    got, enc, lossy = decode_text(text.encode("utf-8"))
    assert (got, enc, lossy) == (text, "utf-8", False), (got[:10], enc, lossy)

    gbk = text.encode("gbk")
    got, enc, lossy = decode_text(gbk)
    assert got == text and enc == "gb18030" and not lossy, (got[:10], enc, lossy)
    # 手动指定编码也能解
    got, enc, _ = decode_text(gbk, "gb18030")
    assert got == text and enc == "gb18030"


def test_decode_utf16_bom() -> None:
    text = "UTF-16 文本"
    got, enc, _ = decode_text(text.encode("utf-16"))
    assert got == text, repr(got)
    assert enc in ("utf-16", "utf-16-le"), enc


def test_decode_latin1_fallback() -> None:
    raw = b"caf\xe9 na\xefve\n"          # 不是合法 UTF-8，也不是合法 GBK 常用字
    got, enc, _ = decode_text(raw)
    assert got.startswith("caf"), repr(got)


def test_decode_truncated_multibyte() -> None:
    """截断点落在多字节字符中间时，不能把好的 UTF-8 误判成 GBK。"""
    raw = "中文".encode("utf-8")          # 6 字节
    cut = raw[:5]                         # 最后一个字符只剩 2 字节
    got, enc, _ = decode_text(cut, may_truncate=True)
    assert got == "中", (got, enc)
    assert enc == "utf-8", enc
    # 不开启截断容错时按原样解，会退到别的编码（所以这个参数是必要的）
    got2, enc2, _ = decode_text(cut)
    assert (got2, enc2) != ("中", "utf-8"), (got2, enc2)


# ---------------------------------------------------------------- read_head
def test_read_head_local_fixture() -> None:
    """本地不走 session，这里验证 PreviewLoader 的本地分支。"""
    path = _write("head.txt", "0123456789" * 100)
    loader = preview.PreviewLoader(path, None, limit=64)
    got = {}

    def _on(done: bytes, err: str) -> None:
        got["data"] = done
        got["err"] = err

    loader.done.connect(_on)
    loader.start()
    assert loader.wait(8000), "读取线程超时"
    QApplication.processEvents()
    assert got.get("err") == "", got.get("err")
    assert len(got["data"]) == 64, len(got["data"])


def _sftp_cfg() -> SessionConfig:
    global _sftp
    if _sftp is None:
        from test_sftp import _SftpFixture

        _sftp = _SftpFixture(_tmp)
    return SessionConfig(protocol="sftp", host="127.0.0.1", port=_sftp.port,
                         username="test", password="test123", timeout=10)


def test_read_head_sftp() -> None:
    """远端只读头部：不该把整份文件拉下来。"""
    name = "big.log"
    data = b"".join(b"line %06d\n" % i for i in range(20000))   # ~240KB
    path = _write(name, data)
    assert os.path.getsize(path) > 200_000

    with create_session(_sftp_cfg()) as sess:
        got = sess.read_head("/" + name, 1000)
    assert got == data[:1000], (len(got), got[:20])


def test_read_head_ftp() -> None:
    """FTP 没有部分读取命令，靠收到够数主动中断——顺带确认连接被收掉。"""
    try:
        with socket.create_connection(("127.0.0.1", 2121), timeout=2):
            pass
    except OSError:
        raise SkipTest("127.0.0.1:2121 没有测试 FTP 服务")

    # 测试服务的根是 <项目>/.tmp/ftproot，文件得写进那里才是 /ftphead/big.log
    ftp_root = os.path.join(os.path.dirname(HERE), ".tmp", "ftproot", "ftphead")
    os.makedirs(ftp_root, exist_ok=True)
    payload = b"".join(b"%06d abcdefghij\n" % i for i in range(20000))
    path = os.path.join(ftp_root, "big.log")
    with open(path, "wb") as f:
        f.write(payload)

    cfg = SessionConfig(protocol="ftp", host="127.0.0.1", port=2121,
                        username="test", password="test123", timeout=10)
    sess = create_session(cfg)
    sess.connect()
    try:
        got = sess.read_head("/ftphead/big.log", 777)
        assert got == payload[:777], (len(got), got[:20])
        # 中断传输后连接已主动关闭，避免留一条半途的控制连接
        assert sess.is_connected() is False
    finally:
        sess.close()
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------------------------------------------------------------- 预览窗口
def test_preview_text_file() -> None:
    path = _write("hello.txt", "第一行\n第二行\n第三行\n")
    dlg = _preview_local(path)
    assert "第一行" in dlg.text.toPlainText(), dlg.text.toPlainText()[:40]
    assert dlg.text.toPlainText().count("\n") >= 3
    assert "编码 utf-8" in dlg.hint.text(), dlg.hint.text()
    assert "3 行" in dlg.hint.text() or "4 行" in dlg.hint.text(), dlg.hint.text()
    assert not dlg.btn_system.isHidden()      # 本地文件给"用系统程序打开"


def test_preview_starts_at_top() -> None:
    """长文件不能停在末尾——setPlainText 默认把光标留在最后。"""
    path = _write("long.txt", "".join(f"line {i}\n" for i in range(5000)))
    dlg = _preview_local(path)
    assert dlg.text.textCursor().position() == 0, dlg.text.textCursor().position()
    assert dlg.text.verticalScrollBar().value() == 0


def test_preview_truncates_big_file() -> None:
    body = "x" * 1023 + "\n"
    path = _write("huge.log", body * 3000)          # ~3MB
    assert os.path.getsize(path) > MAX_PREVIEW
    dlg = _preview_local(path)
    assert "仅显示前" in dlg.hint.text(), dlg.hint.text()
    assert len(dlg.text.toPlainText()) <= MAX_PREVIEW + 10


def test_preview_binary_is_rejected() -> None:
    path = _write("icon.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 500)
    dlg = _preview_local(path)
    assert dlg.text.toPlainText() == ""
    assert "不是文本" in dlg.hint.text(), dlg.hint.text()


def test_preview_gbk_manual_switch() -> None:
    text = "中文配置项=值\n"
    path = _write("gbk.ini", text.encode("gbk"))
    dlg = _preview_local(path)
    # 自动探测应该已经认成 gb18030
    assert "中文配置项" in dlg.text.toPlainText(), dlg.text.toPlainText()[:40]
    # 手动切成 utf-8 会解成乱码，但不该崩
    dlg.enc_box.setCurrentText("utf-8")
    dlg._render()
    assert dlg.text.toPlainText() != ""


def test_preview_empty_file() -> None:
    path = _write("empty.txt", b"")
    dlg = _preview_local(path)
    assert "空" in dlg.hint.text(), dlg.hint.text()


def test_preview_missing_file() -> None:
    dlg = PreviewDialog()
    dlg.preview(name="nope.txt", path=os.path.join(_tmp, "nope.txt"),
                size=0, mtime=0, local_path=os.path.join(_tmp, "nope.txt"))
    _wait(dlg)
    assert "读取失败" in dlg.hint.text(), dlg.hint.text()


def test_preview_find() -> None:
    path = _write("find.txt", "alpha\nbeta\ngamma\nbeta\n")
    dlg = _preview_local(path)
    dlg.show_find()
    dlg.find_edit.setText("beta")
    text = dlg.text.toPlainText()
    cur = dlg.text.textCursor()
    assert cur.hasSelection(), "查找应当选中命中词"
    assert text[cur.selectionStart():cur.selectionEnd()] == "beta"


def test_preview_remote_sftp() -> None:
    """远端预览走 read_head，不整份下载，也不在本地落临时文件。"""
    payload = b"remote content line\n" * 5000
    _write("remote.txt", payload)
    cfg = _sftp_cfg()
    dlg = PreviewDialog()
    dlg.preview(name="remote.txt", path="/remote.txt", size=len(payload),
                mtime=time.time(), cfg=cfg)
    _wait(dlg)
    assert "读取失败" not in dlg.hint.text(), dlg.hint.text()
    assert dlg.text.toPlainText().startswith("remote content line")
    assert dlg.btn_system.isHidden()          # 远端文件没有本地路径


def main() -> int:
    app()          # 先建 QApplication：跨线程信号要靠它的事件循环分发
    print("文本预览测试")
    check("按扩展名判断类型", test_guess_text_kind)
    check("二进制判定", test_looks_binary)
    check("UTF-8 / GBK 解码", test_decode_utf8_and_gbk)
    check("UTF-16 BOM 解码", test_decode_utf16_bom)
    check("latin-1 兜底", test_decode_latin1_fallback)
    check("截断处多字节容错", test_decode_truncated_multibyte)
    check("本地读文件头", test_read_head_local_fixture)
    check("SFTP 只读文件头", test_read_head_sftp)
    check("FTP 只读文件头", test_read_head_ftp)
    check("预览文本文件", test_preview_text_file)
    check("预览从第一行开始", test_preview_starts_at_top)
    check("大文件截断提示", test_preview_truncates_big_file)
    check("二进制文件给提示", test_preview_binary_is_rejected)
    check("手动切换编码", test_preview_gbk_manual_switch)
    check("空文件提示", test_preview_empty_file)
    check("文件不存在", test_preview_missing_file)
    check("查找选中命中", test_preview_find)
    check("远端预览（SFTP）", test_preview_remote_sftp)

    print(f"\n结果：{passed} 通过 / {failed} 失败"
          + (f" / {skipped} 跳过" if skipped else ""))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
