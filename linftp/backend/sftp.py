"""SFTP 后端（paramiko 实现）。

与 FTP 后端的差异：

- 文件名固定 UTF-8，没有编码探测这回事（``set_encoding`` 是空操作）；
- 有真实的权限位与符号链接，``chmod`` 直接可用；
- 续传用文件句柄 seek，不依赖服务端 REST 支持；
- 认证除了密码，还可以指定私钥文件（``SessionConfig.keyfile``）。
"""
from __future__ import annotations

import base64
import hashlib
import os
import posixpath
import socket
import stat as stat_mod
from typing import List, Optional

import paramiko

from .base import (
    CHUNK,
    CancelCb,
    FtpSession,
    ProgressCb,
    RemoteFile,
    SessionConfig,
    SessionError,
    TransferCancelled,
)


def _sha256_fingerprint(key: "paramiko.PKey") -> str:
    """OpenSSH 风格的 SHA256 指纹。"""
    try:
        digest = hashlib.sha256(key.asbytes()).digest()
        return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")
    except Exception:  # noqa: BLE001 - 指纹只用于展示，失败不影响连接
        return ""


def _load_private_key(path: str) -> "paramiko.PKey":
    """自己加载私钥，而不是把路径丢给 SSHClient。

    SSHClient 的 key_filename 探测在 paramiko 5 上对传统 PEM
    （``-----BEGIN RSA PRIVATE KEY-----``）会报
    "encountered RSA key, expected OPENSSH key"；``PKey.from_path``
    对 PEM / OpenSSH 两种格式都能读，错误也能翻译成人话。
    """
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        raise SessionError(f"私钥文件不存在：{path}")
    try:
        return paramiko.PKey.from_path(path)
    except paramiko.PasswordRequiredException as e:
        raise SessionError(
            f"私钥 {path} 有口令，本版还不支持在界面里输入；"
            "可以先 ssh-add 到 ssh-agent，再留空私钥文件走 agent 登录") from e
    except (paramiko.SSHException, OSError, ValueError) as e:
        raise SessionError(
            f"读取私钥失败（{path}）：{e}（支持 PEM / OpenSSH 格式，"
            "PuTTY 的 .ppk 需要先转换）") from e


class SftpBackend(FtpSession):
    protocol = "sftp"

    def __init__(self, cfg: SessionConfig):
        super().__init__(cfg)
        self._client: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None
        self._cwd = "/"
        # 上层（编码下拉框）会读这两个属性，SFTP 恒定 UTF-8
        self.effective_encoding = "utf-8"
        self.encoding_autodetected = False
        self.host_key_fingerprint = ""

    # ---------------------------------------------------------------- 生命周期
    def connect(self) -> None:
        host = (self.cfg.host or "").strip()
        if not host:
            raise SessionError("请填写主机地址")
        port = self.cfg.default_port()

        # 私钥自己加载：格式兼容性更好，错误也能讲清楚
        pkey = _load_private_key(self.cfg.keyfile) if self.cfg.keyfile else None

        client = paramiko.SSHClient()
        try:
            client.load_system_host_keys()
        except Exception:  # noqa: BLE001 - 没有 known_hosts 也照常连
            pass
        # 首次连接自动信任（指纹存在 host_key_fingerprint 里，方便用户核对）
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            client.connect(
                hostname=host,
                port=port,
                username=self.cfg.username or "root",
                password=self.cfg.password or None,
                pkey=pkey,
                timeout=self.cfg.timeout,
                banner_timeout=self.cfg.timeout,
                auth_timeout=self.cfg.timeout,
                allow_agent=True,
                look_for_keys=not self.cfg.keyfile,
            )
        except paramiko.BadHostKeyException as e:
            client.close()
            raise SessionError(
                f"主机密钥与 known_hosts 记录不一致，可能被中间人劫持：{e}") from e
        except paramiko.AuthenticationException as e:
            client.close()
            hint = ("私钥无效、口令错误或服务端不接受该密钥"
                    if self.cfg.keyfile else "用户名或密码不正确")
            raise SessionError(f"SFTP 认证失败（{hint}）：{e}") from e
        except paramiko.SSHException as e:
            client.close()
            raise SessionError(f"SSH 协商失败：{e}") from e
        except (socket.timeout, TimeoutError) as e:
            client.close()
            raise SessionError(
                f"连接 {host}:{port} 超时（{self.cfg.timeout}s）") from e
        except OSError as e:
            client.close()
            raise SessionError(f"连不上 {host}:{port}：{e}") from e

        try:
            sftp = client.open_sftp()
        except Exception as e:  # noqa: BLE001
            client.close()
            raise SessionError(
                f"打开 SFTP 子系统失败（服务端可能没启用 sftp）：{e}") from e

        self._client = client
        self._sftp = sftp
        try:
            self._cwd = sftp.normalize(".") or "/"
        except Exception:  # noqa: BLE001
            self._cwd = "/"
        try:
            transport = client.get_transport()
            if transport is not None:
                self.host_key_fingerprint = _sha256_fingerprint(
                    transport.get_remote_server_key())
        except Exception:  # noqa: BLE001
            self.host_key_fingerprint = ""

    def close(self) -> None:
        if self._sftp is not None:
            try:
                self._sftp.close()
            except Exception:  # noqa: BLE001
                pass
            self._sftp = None
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    def is_connected(self) -> bool:
        if self._client is None:
            return False
        transport = self._client.get_transport()
        return bool(transport is not None and transport.is_active())

    def noop(self) -> None:
        """保活：stat 一下当前目录，比重新列目录轻。"""
        sftp = self._chk()
        try:
            sftp.stat(self._cwd or "/")
        except IOError as e:
            raise SessionError(f"保活失败：{e}") from e

    def _chk(self) -> "paramiko.SFTPClient":
        if self._sftp is None or not self.is_connected():
            raise SessionError("尚未连接")
        return self._sftp

    # ---------------------------------------------------------------- 路径
    def _abs(self, path: str | None) -> str:
        p = (path or "").strip()
        if not p:
            return self._cwd or "/"
        if not p.startswith("/"):
            p = posixpath.join(self._cwd or "/", p)
        return posixpath.normpath(p)

    def cwd(self) -> str:
        return self._cwd

    def change_dir(self, path: str) -> str:
        sftp = self._chk()
        target = self._abs(path)
        try:
            attrs = sftp.stat(target)
        except IOError as e:
            raise SessionError(f"无法进入 {target}：{e}") from e
        if not stat_mod.S_ISDIR(attrs.st_mode or 0):
            raise SessionError(f"不是目录：{target}")
        self._cwd = target
        return target

    def set_encoding(self, enc: str) -> None:
        """SFTP 的文件名一律 UTF-8，这里只是兼容上层菜单的调用。"""
        self.effective_encoding = "utf-8"

    # ---------------------------------------------------------------- 列表与属性
    @staticmethod
    def _to_entry(parent: str, attrs: "paramiko.SFTPAttributes") -> RemoteFile:
        mode = attrs.st_mode or 0
        return RemoteFile(
            name=attrs.filename,
            path=posixpath.join(parent, attrs.filename),
            size=attrs.st_size or 0,
            mtime=float(attrs.st_mtime or 0),
            is_dir=stat_mod.S_ISDIR(mode),
            is_link=stat_mod.S_ISLNK(mode),
            mode=mode or None,
        )

    def listdir(self, path: str) -> List[RemoteFile]:
        sftp = self._chk()
        target = self._abs(path)
        try:
            attrs_list = sftp.listdir_attr(target)
        except FileNotFoundError as e:
            raise SessionError(f"目录不存在：{target}") from e
        except PermissionError as e:
            raise SessionError(f"没有权限读取 {target}") from e
        except IOError as e:
            raise SessionError(f"列目录失败 {target}：{e}") from e

        entries = [self._to_entry(target, a) for a in attrs_list]
        # 与 FTP 后端保持一致的排序：目录在前，然后按名字
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return entries

    def stat(self, path: str) -> RemoteFile:
        sftp = self._chk()
        target = self._abs(path)
        try:
            attrs = sftp.stat(target)      # 跟随符号链接
        except IOError as e:
            raise SessionError(f"读取属性失败 {target}：{e}") from e
        attrs.filename = posixpath.basename(target) or target
        return self._to_entry(posixpath.dirname(target) or "/", attrs)

    # ---------------------------------------------------------------- 写操作
    def mkdir(self, path: str) -> None:
        sftp = self._chk()
        target = self._abs(path)
        try:
            sftp.mkdir(target)
        except IOError as e:
            raise SessionError(f"新建目录失败 {target}：{e}") from e

    def remove(self, path: str, is_dir: bool = False) -> None:
        sftp = self._chk()
        target = self._abs(path)
        try:
            if is_dir:
                sftp.rmdir(target)
            else:
                sftp.remove(target)
        except IOError as e:
            raise SessionError(f"删除失败 {target}：{e}") from e

    def rename(self, src: str, dst: str) -> None:
        sftp = self._chk()
        s, d = self._abs(src), self._abs(dst)
        try:
            # posix-rename 允许目标已存在（覆盖），OpenSSH 都支持
            sftp.posix_rename(s, d)
        except (IOError, OSError, AttributeError):
            try:
                sftp.rename(s, d)
            except IOError as e:
                raise SessionError(f"重命名失败 {s} → {d}：{e}") from e

    def chmod(self, path: str, mode: int) -> None:
        sftp = self._chk()
        target = self._abs(path)
        try:
            sftp.chmod(target, mode)
        except IOError as e:
            raise SessionError(f"修改权限失败 {target}：{e}") from e

    def supports_resume(self) -> bool:
        return True

    # ---------------------------------------------------------------- 传输
    def get(
        self,
        remote: str,
        local: str,
        offset: int = 0,
        progress: Optional[ProgressCb] = None,
        cancel_flag: Optional[CancelCb] = None,
    ) -> None:
        sftp = self._chk()
        rp = self._abs(remote)
        try:
            total = sftp.stat(rp).st_size or 0
        except IOError as e:
            raise SessionError(f"读取远端文件失败 {rp}：{e}") from e

        os.makedirs(os.path.dirname(os.path.abspath(local)) or ".", exist_ok=True)
        done = offset
        try:
            with sftp.open(rp, "rb") as rf:
                if offset:
                    rf.seek(offset)
                try:
                    rf.prefetch(total)      # 预取，显著提升大文件吞吐
                except Exception:  # noqa: BLE001
                    pass
                with open(local, "ab" if offset else "wb") as lf:
                    while True:
                        if cancel_flag and cancel_flag():
                            raise TransferCancelled()
                        chunk = rf.read(CHUNK)
                        if not chunk:
                            break
                        lf.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, max(total, done))
        except TransferCancelled:
            raise
        except IOError as e:
            raise SessionError(f"下载失败 {rp}：{e}") from e

        if progress:
            progress(done, total or done)

    def put(
        self,
        local: str,
        remote: str,
        offset: int = 0,
        progress: Optional[ProgressCb] = None,
        cancel_flag: Optional[CancelCb] = None,
    ) -> None:
        sftp = self._chk()
        rp = self._abs(remote)
        total = os.path.getsize(local)
        done = offset

        try:
            with open(local, "rb") as lf:
                lf.seek(offset)
                # 续传时远端文件应已存在；不存在就重新整传
                try:
                    rf = sftp.open(rp, "r+b" if offset else "wb")
                except IOError:
                    rf = sftp.open(rp, "wb")
                    lf.seek(0)
                    done = 0
                with rf:
                    if done:
                        rf.seek(done)
                    while True:
                        if cancel_flag and cancel_flag():
                            raise TransferCancelled()
                        chunk = lf.read(CHUNK)
                        if not chunk:
                            break
                        rf.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
        except TransferCancelled:
            raise
        except IOError as e:
            raise SessionError(f"上传失败 {rp}：{e}") from e

        if progress:
            progress(total, total)
