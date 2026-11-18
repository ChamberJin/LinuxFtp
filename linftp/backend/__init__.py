from .base import (
    FtpSession,
    RemoteFile,
    SessionConfig,
    SessionError,
    TransferCancelled,
    UnsupportedOperation,
)

__all__ = [
    "FtpSession",
    "RemoteFile",
    "SessionConfig",
    "SessionError",
    "TransferCancelled",
    "UnsupportedOperation",
    "create_session",
]


def create_session(cfg: SessionConfig) -> FtpSession:
    """协议工厂。P0 只实现 FTP；后续接入 ftps / sftp / scp 时在这里加分支。"""
    proto = (cfg.protocol or "ftp").lower()
    if proto == "ftp":
        from .ftp import FtpBackend

        return FtpBackend(cfg)
    raise NotImplementedError(f"协议 {proto} 将在后续阶段实现")
