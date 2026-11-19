"""统一会话抽象层。

四种协议（SFTP / SCP / FTP / FTPS）都必须实现 FtpSession 接口，
上层 UI 与传输调度只依赖这个抽象，不感知底层协议差异。
"""
from __future__ import annotations

import os
import stat as stat_mod
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional

CHUNK = 256 * 1024

ProgressCb = Callable[[int, int], None]
CancelCb = Callable[[], bool]


class SessionError(Exception):
    """连接 / 认证阶段的可展示错误。"""


class TransferCancelled(Exception):
    """用户在传输过程中主动取消。"""


class UnsupportedOperation(NotImplementedError):
    """当前协议不支持该操作（例如 SCP 的 chmod / 续传）。"""


@dataclass
class RemoteFile:
    name: str
    path: str
    size: int = 0
    mtime: float = 0.0
    is_dir: bool = False
    is_link: bool = False
    mode: Optional[int] = None  # 权限位，FTP 服务端可能给不出
    owner: str = ""
    group: str = ""

    @property
    def mode_text(self) -> str:
        if self.mode is None:
            return "-"
        return stat_mod.filemode(self.mode | (stat_mod.S_IFDIR if self.is_dir else stat_mod.S_IFREG))


@dataclass
class SessionConfig:
    """一份连接配置，站点管理器存的就是它。"""

    protocol: str = "ftp"           # ftp | ftps | sftp | scp
    host: str = ""
    port: Optional[int] = None      # None 表示用协议默认端口
    username: str = "anonymous"
    password: str = ""
    # FTPS 专用
    ftps_implicit: bool = False     # True = 990 隐式 TLS
    verify_cert: bool = True
    # SFTP / SCP 专用
    keyfile: str = ""
    timeout: int = 15
    passive: bool = True            # FTP 被动模式
    encoding: str = "utf-8"         # 老服务端可能是 gbk

    def default_port(self) -> int:
        if self.port:
            return self.port
        if self.protocol in ("sftp", "scp"):
            return 22
        if self.protocol == "ftps" and self.ftps_implicit:
            return 990
        return 21


def walk_listing(session: "FtpSession", root: str) -> Iterable[tuple[str, RemoteFile]]:
    """递归遍历远端目录，产出 (父目录, 条目)。用于统计目录大小与递归下载。"""
    stack = [root]
    while stack:
        cur = stack.pop()
        for entry in session.listdir(cur):
            yield cur, entry
            if entry.is_dir and not entry.is_link:
                stack.append(entry.path)


class FtpSession(ABC):
    """四种协议的统一接口。"""

    protocol = "base"

    def __init__(self, cfg: SessionConfig):
        self.cfg = cfg

    # ---------- 生命周期 ----------
    def __enter__(self) -> "FtpSession":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    def is_connected(self) -> bool:
        return False

    def noop(self) -> None:
        """保活。UI 可定时调用防止连接超时。"""

    # ---------- 目录与元信息 ----------
    @abstractmethod
    def listdir(self, path: str) -> List[RemoteFile]: ...

    @abstractmethod
    def stat(self, path: str) -> RemoteFile: ...

    def exists(self, path: str) -> bool:
        try:
            self.stat(path)
            return True
        except Exception:
            return False

    def cwd(self) -> str:
        return getattr(self, "_cwd", "/")

    # ---------- 写操作 ----------
    @abstractmethod
    def mkdir(self, path: str) -> None: ...

    @abstractmethod
    def remove(self, path: str, is_dir: bool = False) -> None: ...

    @abstractmethod
    def rename(self, src: str, dst: str) -> None: ...

    def chmod(self, path: str, mode: int) -> None:
        raise UnsupportedOperation(f"{self.protocol.upper()} 不支持 chmod")

    # ---------- 文件传输 ----------
    @abstractmethod
    def get(
        self,
        remote: str,
        local: str,
        offset: int = 0,
        progress: Optional[ProgressCb] = None,
        cancel_flag: Optional[CancelCb] = None,
    ) -> None: ...

    @abstractmethod
    def put(
        self,
        local: str,
        remote: str,
        offset: int = 0,
        progress: Optional[ProgressCb] = None,
        cancel_flag: Optional[CancelCb] = None,
    ) -> None: ...

    def read_head(self, remote: str, limit: int) -> bytes:
        """只读文件头部 limit 字节，用于预览。

        默认实现是整份下载再截断——宁可慢也别乱给数据；能随机读的协议
        （FTP 的 REST / SFTP 的 seek）都会覆盖它，避免为了看一眼日志
        把几百兆全拉下来。
        """
        import tempfile

        fd, tmp = tempfile.mkstemp(prefix="linftp-head-")
        os.close(fd)
        try:
            self.get(remote, tmp)
            with open(tmp, "rb") as f:
                return f.read(limit)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def supports_resume(self) -> bool:
        return True
