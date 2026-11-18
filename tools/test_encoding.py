"""文件名编码测试（重点覆盖 GBK 服务器）。

背景：真实的国产化 FTP 服务器常用 GBK 存文件名，而 ftplib 默认按 utf-8
解码控制连接，会抛 UnicodeDecodeError 让整个目录打不开。
本测试验证：自动识别、显式指定、乱码兜底、以及中文名增删改查全链路。

用法：
    自备 GBK 文件名的本地 FTP 服务（默认 127.0.0.1:2122），然后：
    .venv/bin/python tools/test_encoding.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linftp.backend import SessionConfig, create_session
from linftp.backend.ftp import FtpBackend

GBK_HOST = os.environ.get("GBK_HOST", "127.0.0.1")
GBK_PORT = int(os.environ.get("GBK_PORT", "2122"))
USER = os.environ.get("TEST_USER", "test")
PWD = os.environ.get("TEST_PWD", "test123")

passed = failed = 0


def check(name: str, fn) -> None:
    global passed, failed
    try:
        fn()
        print(f"  PASS  {name}")
        passed += 1
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")
        traceback.print_exc()
        failed += 1


def _connect(encoding: str) -> FtpBackend:
    cfg = SessionConfig(protocol="ftp", host=GBK_HOST, port=GBK_PORT,
                        username=USER, password=PWD, encoding=encoding)
    s = create_session(cfg)
    assert isinstance(s, FtpBackend)
    s.connect()
    return s


def t_auto_detect() -> None:
    """用户选 utf-8，但服务端是 GBK —— 应自动纠正。"""
    s = _connect("utf-8")
    try:
        assert s.effective_encoding == "gbk", f"应识别为 gbk，实际 {s.effective_encoding}"
        assert s.encoding_autodetected is True
    finally:
        s.close()


def t_list_chinese() -> None:
    s = _connect("utf-8")
    try:
        names = {e.name for e in s.listdir("/")}
        assert "中文目录" in names, f"中文目录名解码错误：{names}"
        assert "中文测试文档" in names, f"中文文件名解码错误：{names}"
        item = [e for e in s.listdir("/") if e.name == "中文测试文档"][0]
        assert item.size == 4, f"文件大小应正确：{item.size}"
        assert not item.is_dir
    finally:
        s.close()


def t_explicit_gbk() -> None:
    """用户显式指定 gbk，应被尊重。"""
    s = _connect("gbk")
    try:
        assert s.effective_encoding == "gbk"
        names = {e.name for e in s.listdir("/")}
        assert "中文目录" in names, names
    finally:
        s.close()


def t_cd_chinese() -> None:
    s = _connect("utf-8")
    try:
        p = s.change_dir("/中文目录")
        assert p == "/中文目录", f"cwd 应为中文原样，实际 {p!r}"
        s.change_dir("/")
        assert s.cwd() == "/"
    finally:
        s.close()


def t_download_chinese() -> None:
    s = _connect("utf-8")
    tmp = os.path.join(tempfile.mkdtemp(), "out.bin")
    try:
        s.get("/中文测试文档", tmp)
        assert os.path.getsize(tmp) == 4, os.path.getsize(tmp)
    finally:
        s.close()


def t_roundtrip_chinese() -> None:
    """上传中文名 → 列表校验 → 重命名 → 回读 → 删除。"""
    s = _connect("utf-8")
    tmpdir = tempfile.mkdtemp()
    up = os.path.join(tmpdir, "本地文件.txt")
    with open(up, "w", encoding="utf-8") as fh:
        fh.write("编码往返测试")
    try:
        s.put(up, "/上传中文.txt")
        names = {e.name for e in s.listdir("/")}
        assert "上传中文.txt" in names, f"上传后应能看见中文名：{names}"

        s.mkdir("/中文新目录")
        names = {e.name for e in s.listdir("/")}
        assert "中文新目录" in names, names

        s.rename("/上传中文.txt", "/改名中文.txt")
        names = {e.name for e in s.listdir("/")}
        assert "改名中文.txt" in names, names

        back = os.path.join(tmpdir, "回读.txt")
        s.get("/改名中文.txt", back)
        with open(back, encoding="utf-8") as fh:
            assert fh.read() == "编码往返测试"

        s.remove("/改名中文.txt")
        s.remove("/中文新目录", is_dir=True)
        names = {e.name for e in s.listdir("/")}
        assert "改名中文.txt" not in names and "中文新目录" not in names
    finally:
        s.close()


def t_no_crash_on_bad_bytes() -> None:
    """服务端返回非法字节时，列表不能整体失败，只能局部乱码。"""
    s = _connect("utf-8")
    try:
        # 强行把编码设成一个不匹配的，模拟用户选错
        s.set_encoding("big5")
        entries = s.listdir("/")          # 不应抛异常
        assert entries, "即使编码不对，也应返回条目而不是崩溃"
    finally:
        s.close()


def t_switch_encoding_in_chinese_dir() -> None:
    """回归：中文目录下切换编码曾导致
    'error_reply: 200 Type set to A.'（控制连接响应错位）。

    根因是编码探测时用已解码的中文 cwd 拼命令，latin-1 发不出去，
    异常打断了 retrlines，残留响应被下一条命令读到。
    """
    s = _connect("utf-8")
    try:
        s.change_dir("/中文目录")
        assert s.cwd() == "/中文目录"
        for enc in ("gbk", "utf-8", "gbk"):
            s.set_encoding(enc)
            entries = s.listdir("/")          # 不应抛 error_reply
            assert entries, f"{enc} 下列表应非空"
            if enc == "gbk":
                assert "中文目录" in {e.name for e in entries}, \
                    "gbk 下中文名应正确解码"
    finally:
        s.close()


def t_switch_encoding_repeatedly() -> None:
    """反复切换编码，连接不能被搞坏。"""
    s = _connect("utf-8")
    try:
        for enc in ("gbk", "utf-8", "big5", "gbk", "utf-8"):
            s.set_encoding(enc)
            assert s.listdir("/"), f"{enc} 下列表应非空"
        # 连续刷新 20 次，验证响应流没有错位
        for _ in range(20):
            s.listdir("/")
    finally:
        s.close()


def t_wrong_encoding_gives_readable_error() -> None:
    """编码不支持该字符时，应给可读提示而不是底层异常。"""
    s = _connect("utf-8")
    try:
        s.set_encoding("latin-1")
        try:
            s.listdir("/中文目录")
        except Exception as e:
            msg = str(e)
            assert "编码" in msg, f"提示应说明编码问题，实际：{msg}"
        # 切回正确编码后必须立刻恢复可用
        s.set_encoding("gbk")
        assert s.listdir("/"), "切回正确编码后应恢复正常"
    finally:
        s.close()


def t_list_fallback_with_encoding() -> None:
    """强制走 LIST 回退（模拟不支持 MLSD 的老服务器）× 切换编码。"""
    s = _connect("utf-8")
    try:
        s._supports_mlsd = False
        s.set_encoding("gbk")
        entries = s.listdir("/")
        names = {e.name for e in entries}
        assert "中文目录" in names, f"LIST 回退下中文应正确：{names}"
        # 再切一次，确认回退路径不污染连接
        s.set_encoding("gbk")
        assert s.listdir("/"), "回退路径下再次刷新应正常"
    finally:
        s.close()



    """纯 ASCII 服务器不应被误判成其他编码。"""
    cfg = SessionConfig(protocol="ftp", host=os.environ.get("TEST_HOST", "127.0.0.1"),
                        port=int(os.environ.get("TEST_PORT", "2121")),
                        username=USER, password=PWD, encoding="utf-8")
    try:
        s = create_session(cfg)
        s.connect()
    except Exception:
        print("        (UTF-8 测试服务器未启动，跳过)")
        return
    try:
        assert s.effective_encoding == "utf-8", s.effective_encoding
        names = {e.name for e in s.listdir("/")}
        assert "readme.txt" in names, names
    finally:
        s.close()


def t_ascii_only_server() -> None:
    """纯 ASCII 服务器不应被误判成其他编码。"""
    cfg = SessionConfig(protocol="ftp", host=os.environ.get("TEST_HOST", "127.0.0.1"),
                        port=int(os.environ.get("TEST_PORT", "2121")),
                        username=USER, password=PWD, encoding="utf-8")
    try:
        s = create_session(cfg)
        s.connect()
    except Exception:
        print("        (UTF-8 测试服务器未启动，跳过)")
        return
    try:
        assert s.effective_encoding == "utf-8", s.effective_encoding
        names = {e.name for e in s.listdir("/")}
        assert "readme.txt" in names, names
    finally:
        s.close()


def main() -> int:
    print("--- 文件名编码测试（GBK 服务器）---")
    check("自动识别 GBK", t_auto_detect)
    check("中文文件名列表", t_list_chinese)
    check("显式指定 gbk", t_explicit_gbk)
    check("进入中文目录", t_cd_chinese)
    check("下载中文名文件", t_download_chinese)
    check("中文名上传/新建/改名/回读/删除", t_roundtrip_chinese)
    check("编码选错也不崩溃", t_no_crash_on_bad_bytes)
    check("中文目录下切换编码（回归 200 Type set to A.）",
          t_switch_encoding_in_chinese_dir)
    check("反复切换编码连接稳定", t_switch_encoding_repeatedly)
    check("编码不支持时给可读提示", t_wrong_encoding_gives_readable_error)
    check("LIST 回退 + 切换编码", t_list_fallback_with_encoding)
    check("ASCII 服务器不误判", t_ascii_only_server)
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
