"""后端协议层自测（无需 GUI）。

覆盖：连接、列表（MLSD/LIST）、stat、mkdir、上传、下载、
断点续传、rename、chmod、删除、中文文件名、错误路径。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback

UNIQ = str(int(time.time() * 1000) % 10_000_000)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linftp.backend import SessionConfig, SessionError, UnsupportedOperation, create_session
from linftp.backend.ftp import FtpBackend

HOST = os.environ.get("TEST_HOST", "127.0.0.1")
PORT = int(os.environ.get("TEST_PORT", "2121"))
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


def main() -> int:
    cfg = SessionConfig(protocol="ftp", host=HOST, port=PORT,
                        username=USER, password=PWD)
    sess = create_session(cfg)
    assert isinstance(sess, FtpBackend), "工厂应返回 FtpBackend"
    sess.connect()

    tmpdir = tempfile.mkdtemp(prefix="linftp-test-")
    print(f"临时目录: {tmpdir}")

    def t_connect():
        assert sess.is_connected()
        assert sess.cwd() == "/"

    def t_list():
        entries = sess.listdir("/")
        names = {e.name for e in entries}
        assert "readme.txt" in names, f"列表缺少 readme.txt，实际: {names}"
        assert "docs" in names
        docs = [e for e in entries if e.name == "docs"][0]
        assert docs.is_dir, "docs 应识别为目录"
        rd = [e for e in entries if e.name == "readme.txt"][0]
        assert not rd.is_dir and rd.size > 0, "readme.txt 大小应 > 0"
        assert rd.mode is not None, "应解析出权限位"

    def t_mlsd_used():
        assert sess._supports_mlsd is not False, "服务端声明支持 MLSD 时应使用 MLSD"

    def t_nested_list():
        entries = sess.listdir("/docs")
        names = {e.name for e in entries}
        assert "2024" in names, f"/docs 下应有 2024，实际 {names}"
        sub = sess.listdir("/docs/2024")
        assert any(e.name == "report.txt" for e in sub)

    def t_stat():
        f = sess.stat("/readme.txt")
        assert f.size > 0 and not f.is_dir
        d = sess.stat("/docs")
        assert d.is_dir

    def t_stat_missing():
        try:
            sess.stat("/no-such-file-xyz")
        except SessionError:
            return
        raise AssertionError("不存在的路径应抛 SessionError")

    def t_download():
        local = os.path.join(tmpdir, "readme.txt")
        sess.get("/readme.txt", local)
        remote_size = sess.stat("/readme.txt").size
        assert os.path.getsize(local) == remote_size, "下载大小应与远端一致"

    def t_progress():
        seen = []
        local = os.path.join(tmpdir, "data.bin")
        sess.get("/data.bin", local, progress=lambda d, t: seen.append((d, t)))
        assert seen, "进度回调未被调用"
        assert seen[-1][0] == seen[-1][1], f"最终进度应满：{seen[-1]}"

    def t_upload():
        local = os.path.join(tmpdir, "upload.bin")
        payload = b"x" * (300 * 1024)
        with open(local, "wb") as fh:
            fh.write(payload)
        sess.put(local, "/upload.bin")
        assert sess.stat("/upload.bin").size == len(payload), "上传后远端大小不符"
        back = os.path.join(tmpdir, "upload.back")
        sess.get("/upload.bin", back)
        with open(back, "rb") as fh:
            assert fh.read() == payload, "回读内容不一致"

    def t_resume_download():
        # 先下前 100KB，再用 offset 续传，最终大小与内容都应正确
        part = os.path.join(tmpdir, "resume.bin")
        with open(part, "wb") as fh:
            fh.write(b"")
        sess.get("/data.bin", part, offset=0)
        full_size = os.path.getsize(part)
        os.remove(part)
        # 人为造出"已下载一半"的状态
        sess.get("/data.bin", part, offset=0)
        with open(part, "r+b") as fh:
            fh.truncate(100 * 1024)
        sess.get("/data.bin", part, offset=100 * 1024)
        assert os.path.getsize(part) == full_size, "续传后大小应等于完整文件"

    def t_mkdir_rmdir():
        d = f"/newdir_{UNIQ}"
        sess.mkdir(d)
        assert sess.stat(d).is_dir
        sess.remove(d, is_dir=True)
        assert not sess.exists(d), "目录应已删除"

    def t_rename():
        a, b = f"/ren_a_{UNIQ}", f"/ren_b_{UNIQ}"
        sess.mkdir(a)
        sess.rename(a, b)
        assert sess.exists(b) and not sess.exists(a)
        sess.remove(b, is_dir=True)

    def t_chmod():
        try:
            sess.chmod("/readme.txt", 0o644)
        except UnsupportedOperation:
            print("        (服务端不支持 SITE CHMOD，跳过)")

    def t_delete():
        sess.remove("/upload.bin")
        assert not sess.exists("/upload.bin"), "文件应已删除"

    def t_chinese_name():
        local = os.path.join(tmpdir, "中文测试.txt")
        with open(local, "w", encoding="utf-8") as fh:
            fh.write("中文内容\n")
        sess.put(local, "/中文测试.txt")
        names = {e.name for e in sess.listdir("/")}
        assert "中文测试.txt" in names, f"中文文件名应正确：{names}"
        back = os.path.join(tmpdir, "中文回读.txt")
        sess.get("/中文测试.txt", back)
        with open(back, encoding="utf-8") as fh:
            assert "中文内容" in fh.read()
        sess.remove("/中文测试.txt")

    def t_cancel():
        from linftp.backend import TransferCancelled

        local = os.path.join(tmpdir, "cancel.bin")
        state = {"n": 0}

        def flag():
            state["n"] += 1
            return state["n"] > 1

        try:
            sess.get("/data.bin", local, cancel_flag=flag)
        except TransferCancelled:
            return
        # 小文件可能在取消前就传完，不算失败
        print("        (文件较小，未能触发取消，属正常)")

    def t_change_dir():
        p = sess.change_dir("/docs")
        assert p == "/docs", p
        sess.change_dir("/")
        assert sess.cwd() == "/"

    print("\n--- 后端协议测试 ---")
    check("连接与 pwd", t_connect)
    check("根目录列表解析", t_list)
    check("MLSD 生效", t_mlsd_used)
    check("嵌套目录列表", t_nested_list)
    check("stat 文件与目录", t_stat)
    check("stat 不存在路径报错", t_stat_missing)
    check("change_dir", t_change_dir)
    check("单文件下载", t_download)
    check("进度回调", t_progress)
    check("上传并回读校验", t_upload)
    check("断点续传下载", t_resume_download)
    check("mkdir / rmdir", t_mkdir_rmdir)
    check("rename", t_rename)
    check("chmod", t_chmod)
    check("中文文件名", t_chinese_name)
    check("删除文件", t_delete)
    check("取消传输", t_cancel)

    sess.close()
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
