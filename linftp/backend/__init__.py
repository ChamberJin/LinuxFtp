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
    """协议工厂。已实现 ftp / sftp；ftps / scp 在后续阶段接入。"""
    proto = (cfg.protocol or "ftp").lower()
    if proto == "ftp":
        from .ftp import FtpBackend

        return FtpBackend(cfg)
    if proto == "sftp":
        try:
            from .sftp import SftpBackend
        except ImportError as e:      # 没装 paramiko
            raise SessionError(
                "SFTP 需要 paramiko，请先执行 pip install paramiko") from e

        return SftpBackend(cfg)
    raise SessionError(f"暂不支持协议 {proto}（可用：ftp、sftp）")
