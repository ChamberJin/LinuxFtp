"""SFTP 后端测试。

测试自带一个最小 SFTP 服务（paramiko 实现，只映射到一个临时目录），
不依赖外部服务，也不往系统里写配置：

    .venv/bin/python tools/test_sftp.py

覆盖：连接/认证、列目录排序与属性、上传下载、双向续传、目录增删改、
chmod、中文文件名、进度回调、取消、错误映射、协议工厂。
"""
from __future__ import annotations

import os
import posixpath
import socket
import sys
import tempfile
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import paramiko
except ImportError:                      # pragma: no cover
    print("需要 paramiko：.venv/bin/pip install paramiko")
    raise SystemExit(1)

import logging  # noqa: E402

logging.getLogger("paramiko").setLevel(logging.CRITICAL)

from linftp.backend import (  # noqa: E402
    SessionConfig,
    SessionError,
    TransferCancelled,
    create_session,
)
from linftp.backend.sftp import SftpBackend  # noqa: E402

USER, PWD = "test", "test123"
passed = failed = 0
_server: "_SftpFixture | None" = None
_root = ""
_app_state: dict = {}


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


# ---------------------------------------------------------------- 测试用 SFTP 服务
class _Auth(paramiko.ServerInterface):
    USER_KEY = None            # 由 fixture 注入，用于验证私钥登录

    def check_auth_password(self, username, password):
        ok = (username, password) == (USER, PWD)
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        ok = (username == USER and self.USER_KEY is not None
              and key.asbytes() == self.USER_KEY.asbytes())
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password,publickey"

    def check_channel_request(self, kind, chanid):
        return (paramiko.OPEN_SUCCEEDED if kind == "session"
                else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED)


class _Fs(paramiko.SFTPServerInterface):
    """把 SFTP 路径映射到 ROOT 下的本地目录。"""

    ROOT = ""

    def _real(self, path: str) -> str:
        clean = posixpath.normpath("/" + (path or "/").lstrip("/"))
        return os.path.join(self.ROOT, clean.lstrip("/"))

    def list_folder(self, path):
        real = self._real(path)
        try:
            names = os.listdir(real)
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        out = []
        for name in names:
            try:
                st = os.stat(os.path.join(real, name))
            except OSError:
                continue
            attrs = paramiko.SFTPAttributes.from_stat(st)
            attrs.filename = name
            out.append(attrs)
        return out

    def stat(self, path):
        try:
            return paramiko.SFTPAttributes.from_stat(os.stat(self._real(path)))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)

    def lstat(self, path):
        try:
            return paramiko.SFTPAttributes.from_stat(os.lstat(self._real(path)))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)

    def open(self, path, flags, attr):
        real = self._real(path)
        try:
            fd = os.open(real, flags, getattr(attr, "st_mode", None) or 0o666)
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        if flags & os.O_WRONLY:
            mode = "wb"
        elif flags & os.O_RDWR:
            mode = "r+b"
        else:
            mode = "rb"
        try:
            fh = os.fdopen(fd, mode)
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        handle = paramiko.SFTPHandle(flags)
        handle.filename = real
        handle.readfile = fh
        handle.writefile = fh
        return handle

    def remove(self, path):
        try:
            os.remove(self._real(path))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        return paramiko.SFTP_OK

    def rename(self, oldpath, newpath):
        try:
            os.rename(self._real(oldpath), self._real(newpath))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        return paramiko.SFTP_OK

    def mkdir(self, path, attr):
        try:
            os.mkdir(self._real(path))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        return paramiko.SFTP_OK

    def rmdir(self, path):
        try:
            os.rmdir(self._real(path))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        return paramiko.SFTP_OK

    def chattr(self, path, attr):
        real = self._real(path)
        try:
            mode = getattr(attr, "st_mode", None)
            if mode is not None:
                os.chmod(real, mode)
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        return paramiko.SFTP_OK

    def canonicalize(self, path):
        return posixpath.normpath("/" + (path or "/").lstrip("/"))


class _SftpFixture:
    def __init__(self, root: str):
        _Fs.ROOT = root
        self.key = paramiko.RSAKey.generate(2048)
        self.user_key = paramiko.RSAKey.generate(2048)
        _Auth.USER_KEY = self.user_key
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn) -> None:
        transport = paramiko.Transport(conn)
        transport.add_server_key(self.key)
        transport.set_subsystem_handler("sftp", paramiko.SFTPServer, _Fs)
        try:
            transport.start_server(server=_Auth())
            chan = transport.accept(15)
            while chan is not None and transport.is_active():
                time.sleep(0.05)
        except Exception:  # noqa: BLE001 - 客户端主动断开是常态
            pass
        finally:
            try:
                transport.close()
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> None:
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass


def _cfg(port: int = 0, **kw) -> SessionConfig:
    base = dict(protocol="sftp", host="127.0.0.1",
                port=port or (_server.port if _server else 0),
                username=USER, password=PWD, timeout=8)
    base.update(kw)
    return SessionConfig(**base)


def _connect(**kw) -> SftpBackend:
    sess = create_session(_cfg(**kw))
    sess.connect()
    return sess


def _write(name: str, data: bytes) -> str:
    path = os.path.join(_root, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


# ---------------------------------------------------------------- 用例
def test_connect_and_cwd() -> None:
    sess = _connect()
    try:
        assert isinstance(sess, SftpBackend), sess
        assert sess.is_connected() is True
        assert sess.cwd() == "/", sess.cwd()
        assert sess.host_key_fingerprint.startswith("SHA256:"), \
            sess.host_key_fingerprint
    finally:
        sess.close()
    assert sess.is_connected() is False


def test_list_dir_sorted_and_attrs() -> None:
    os.makedirs(os.path.join(_root, "zeta_dir"), exist_ok=True)
    _write("b.txt", b"hello")
    sess = _connect()
    try:
        entries = sess.listdir("/")
        names = [e.name for e in entries]
        assert names[0] == "zeta_dir", f"目录应排在最前：{names}"
        assert "b.txt" in names, names
        # 目录在前 + 名字升序
        dirs = [e.name for e in entries if e.is_dir]
        files = [e.name for e in entries if not e.is_dir]
        assert dirs == sorted(dirs, key=str.lower), dirs
        assert files == sorted(files, key=str.lower), files

        f = next(e for e in entries if e.name == "b.txt")
        assert f.size == 5, f.size
        assert f.path == "/b.txt", f.path
        perm = (f.mode or 0) & 0o777
        assert perm & 0o600 == 0o600, f"普通文件至少属主可读写：{oct(perm)}"
        assert f.mtime > 0, f.mtime
        assert f.mode_text.startswith("-rw-"), f.mode_text
        d = next(e for e in entries if e.name == "zeta_dir")
        assert d.is_dir and d.mode_text.startswith("drw"), d.mode_text
    finally:
        sess.close()


def test_stat_and_exists() -> None:
    _write("stat_me.txt", b"abc")
    sess = _connect()
    try:
        info = sess.stat("/stat_me.txt")
        assert info.name == "stat_me.txt" and info.size == 3, info
        assert sess.exists("/stat_me.txt") is True
        assert sess.exists("/nope.txt") is False
        try:
            sess.stat("/nope.txt")
        except SessionError:
            pass
        else:
            raise AssertionError("不存在的路径应抛 SessionError")
    finally:
        sess.close()


def test_upload_download_roundtrip() -> None:
    tmp = tempfile.mkdtemp(dir=_root)
    src = os.path.join(tmp, "src.bin")
    payload = bytes(range(256)) * 40        # 10 KB，含全字节值
    with open(src, "wb") as fh:
        fh.write(payload)
    local_back = os.path.join(tmp, "back.bin")

    seen = []
    sess = _connect()
    try:
        sess.put(src, "/上传 测试.bin",
                 progress=lambda d, t: seen.append((d, t)))
        assert seen and seen[-1][0] == len(payload), seen[-3:]
        assert sess.stat("/上传 测试.bin").size == len(payload)

        sess.get("/上传 测试.bin", local_back)
        with open(local_back, "rb") as fh:
            assert fh.read() == payload, "下载内容与上传不一致"

        # 中文名在列表里应原样返回
        names = [e.name for e in sess.listdir("/")]
        assert "上传 测试.bin" in names, names
    finally:
        sess.close()


def test_resume_both_directions() -> None:
    tmp = tempfile.mkdtemp(dir=_root)
    src = os.path.join(tmp, "resume_src.bin")
    payload = b"0123456789" * 6          # 60 字节
    with open(src, "wb") as fh:
        fh.write(payload)

    # 上传续传：远端先有前 20 字节
    _write("resume_up.bin", payload[:20])
    sess = _connect()
    try:
        sess.put(src, "/resume_up.bin", offset=20)
        with open(os.path.join(_root, "resume_up.bin"), "rb") as fh:
            assert fh.read() == payload, "续传上传结果不对"

        # 下载续传：本地先有前 25 字节
        _write("resume_dn.bin", payload)
        local = os.path.join(tmp, "resume_dn.bin")
        with open(local, "wb") as fh:
            fh.write(payload[:25])
        sess.get("/resume_dn.bin", local, offset=25)
        with open(local, "rb") as fh:
            assert fh.read() == payload, "续传下载结果不对"
    finally:
        sess.close()


def test_mkdir_rename_remove() -> None:
    sess = _connect()
    try:
        sess.mkdir("/新目录")
        assert sess.stat("/新目录").is_dir
        _write("待改名.txt", b"x")
        sess.rename("/待改名.txt", "/已改名.txt")
        assert sess.exists("/已改名.txt") and not sess.exists("/待改名.txt")
        sess.remove("/已改名.txt")
        assert not sess.exists("/已改名.txt")
        sess.remove("/新目录", is_dir=True)
        assert not sess.exists("/新目录")
        try:
            sess.mkdir("/新目录")            # 父目录缺失以外的错误也要映射成 SessionError
            sess.mkdir("/新目录")            # 第二次应失败
        except SessionError:
            pass
        finally:
            if sess.exists("/新目录"):
                sess.remove("/新目录", is_dir=True)
    finally:
        sess.close()


def test_chmod() -> None:
    _write("perm.txt", b"p")
    sess = _connect()
    try:
        sess.chmod("/perm.txt", 0o600)
        assert (sess.stat("/perm.txt").mode & 0o777) == 0o600, \
            oct(sess.stat("/perm.txt").mode & 0o777)
    finally:
        sess.close()


def test_progress_and_cancel() -> None:
    tmp = tempfile.mkdtemp(dir=_root)
    big = os.path.join(tmp, "big.bin")
    with open(big, "wb") as fh:
        fh.write(os.urandom(1024 * 1024))     # 1 MB，超过 CHUNK

    sess = _connect()
    try:
        sess.put(big, "/big.bin")
        calls = {"n": 0}

        def cancel() -> bool:
            calls["n"] += 1
            return calls["n"] > 2

        out = os.path.join(tmp, "big_back.bin")
        try:
            sess.get("/big.bin", out, cancel_flag=cancel)
        except TransferCancelled:
            pass
        else:
            raise AssertionError("取消后应抛 TransferCancelled")
        assert calls["n"] > 2, calls
        assert os.path.getsize(out) < 1024 * 1024, "取消后不应写完整个文件"
    finally:
        sess.close()


def test_bad_password() -> None:
    sess = create_session(_cfg(password="wrong-password"))
    try:
        sess.connect()
    except SessionError as e:
        assert "认证失败" in str(e), e
    else:
        raise AssertionError("密码错误应抛 SessionError")
    finally:
        sess.close()


def test_publickey_auth() -> None:
    """私钥登录：不填密码、只给 keyfile 也能连上。"""
    key_path = os.path.join(tempfile.mkdtemp(dir=_root), "id_rsa")
    _server.user_key.write_private_key_file(key_path)

    sess = create_session(_cfg(password="", keyfile=key_path))
    try:
        sess.connect()
        assert sess.is_connected() is True
        assert sess.listdir("/") is not None
    finally:
        sess.close()

    # 换一把不相干的私钥 → 认证失败
    other = os.path.join(tempfile.mkdtemp(dir=_root), "wrong_key")
    paramiko.RSAKey.generate(2048).write_private_key_file(other)
    bad = create_session(_cfg(password="", keyfile=other))
    try:
        try:
            bad.connect()
        except SessionError as e:
            assert "认证失败" in str(e), e
        else:
            raise AssertionError("错误私钥应认证失败")
    finally:
        bad.close()


def test_unreachable_port() -> None:
    # 占一个端口再关掉，保证这个端口上是"连接被拒绝"而不是"卡住"
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()

    sess = create_session(_cfg(port=dead_port, timeout=3))
    try:
        sess.connect()
    except SessionError as e:
        assert "连不上" in str(e) or "超时" in str(e), e
    else:
        raise AssertionError("端口不通应抛 SessionError")
    finally:
        sess.close()


def test_factory_and_config() -> None:
    cfg = _cfg()
    sess = create_session(cfg)
    assert isinstance(sess, SftpBackend)
    assert cfg.default_port() == _server.port
    # 默认端口按协议取 22
    assert SessionConfig(protocol="sftp", host="h").default_port() == 22
    assert SessionConfig(protocol="ftp", host="h").default_port() == 21
    # 不支持的协议给出可读错误
    try:
        create_session(SessionConfig(protocol="scp", host="h"))
    except SessionError as e:
        assert "暂不支持" in str(e), e
    else:
        raise AssertionError("scp 尚未实现，应抛 SessionError")


def test_relative_path_and_cd() -> None:
    _write("sub/inner.txt", b"i")
    sess = _connect()
    try:
        assert sess.change_dir("/sub") == "/sub"
        assert sess.cwd() == "/sub"
        assert sess.listdir("").pop().path == "/sub/inner.txt"
        entries = sess.listdir(".")
        assert any(e.name == "inner.txt" for e in entries), entries
        try:
            sess.change_dir("/no-such-dir")
        except SessionError:
            pass
        else:
            raise AssertionError("进不存在的目录应抛 SessionError")
        assert sess.cwd() == "/sub", "失败后 cwd 不应被改掉"
    finally:
        sess.close()


def main() -> int:
    global _server, _root
    _root = tempfile.mkdtemp(prefix="linftp-sftp-")
    _server = _SftpFixture(_root)
    print(f"内嵌 SFTP 服务：127.0.0.1:{_server.port}  root={_root}")
    try:
        check("连接与初始目录", test_connect_and_cwd)
        check("列目录排序与属性", test_list_dir_sorted_and_attrs)
        check("stat / exists / 错误映射", test_stat_and_exists)
        check("上传下载往返（含中文名）", test_upload_download_roundtrip)
        check("双向断点续传", test_resume_both_directions)
        check("新建 / 改名 / 删除", test_mkdir_rename_remove)
        check("chmod", test_chmod)
        check("进度回调与取消", test_progress_and_cancel)
        check("私钥登录（含错误私钥）", test_publickey_auth)
        check("密码错误映射", test_bad_password)
        check("端口不通映射", test_unreachable_port)
        check("协议工厂与默认端口", test_factory_and_config)
        check("相对路径与 cd", test_relative_path_and_cd)
    finally:
        _server.stop()

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
