"""FTP 协议后端（RFC 959）。

要点：
- 默认被动模式（PASV / EPSV），主动模式保留开关
- 目录列表优先 MLSD（RFC 3659，结构化，解析最可靠），
  服务端不支持时回退 LIST 文本解析（同时兼容 Unix 与 DOS 两种格式）
- 断点续传依赖 REST 命令
"""
from __future__ import annotations

import ftplib
import logging
import os
import re
import socket
from typing import Dict, List, Optional

from .base import (
    CHUNK,
    CancelCb,
    FtpSession,
    ProgressCb,
    RemoteFile,
    SessionConfig,
    SessionError,
    TransferCancelled,
    UnsupportedOperation,
)

log = logging.getLogger(__name__)

# 控制连接统一走的"管道编码"：0-255 与字节一一对应，收发都不会抛异常。
# 真实文件名编码在 FtpBackend._decode_name / _encode_name 里处理。
_WIRE_ENCODING = "latin-1"

# Unix: -rw-r--r-- 1 owner group 1234 Jan 01 12:00 name
_UNIX_RE = re.compile(
    r"^(?P<perm>[-dlbcps][rwxsStT-]{9})\s+"
    r"(?P<nlink>\d+)\s+"
    r"(?P<owner>\S+)\s+"
    r"(?P<group>\S+)\s+"
    r"(?P<size>\d+)\s+"
    r"(?P<date>[A-Za-z]{3}\s+\d{1,2}\s+(?:\d{1,2}:\d{2}|\d{4}))\s+"
    r"(?P<name>.+)$"
)

# DOS: 01-01-24  12:00PM       <DIR>          name
_DOS_RE = re.compile(
    r"^(?P<date>\d{2}-\d{2}-\d{2,4})\s+(?P<time>\d{1,2}:\d{2}\s*[AP]M)\s+"
    r"(?P<size><DIR>|\d+)\s+(?P<name>.+)$",
    re.IGNORECASE,
)

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_perm(perm: str) -> int:
    """把 'rwxr-xr-x' 转成八进制权限位。"""
    mode = 0
    for i, ch in enumerate(perm):
        if ch in "rwxst":
            mode |= 1 << (8 - i)
        elif ch in "sS" and i in (2, 5, 8):
            pass
    # 特殊位
    if perm[2] in "sS":
        mode |= 0o4000
    if perm[5] in "sS":
        mode |= 0o2000
    if perm[8] in "tT":
        mode |= 0o1000
    return mode


def _parse_unix_mtime(date_text: str) -> float:
    """'Jan 01 12:00' 或 'Jan 01 2024' -> 时间戳。缺年份按当前年推断。"""
    import datetime as dt

    parts = date_text.split()
    if len(parts) != 3:
        return 0.0
    mon = _MONTHS.get(parts[0][:3].lower())
    if not mon:
        return 0.0
    day = int(parts[1])
    now = dt.datetime.now()
    if ":" in parts[2]:
        hh, mm = parts[2].split(":")
        year = now.year
        try:
            guess = dt.datetime(year, mon, day, int(hh), int(mm))
            # 列表里出现"未来时间"说明其实是去年
            if guess > now + dt.timedelta(days=1):
                guess = guess.replace(year=year - 1)
            return guess.timestamp()
        except ValueError:
            return 0.0
    try:
        return dt.datetime(int(parts[2]), mon, day).timestamp()
    except ValueError:
        return 0.0


class FtpBackend(FtpSession):
    protocol = "ftp"

    # 自动探测时依次尝试的编码。顺序有讲究：
    # 先 ASCII 兼容性最好的，再试中文常见编码，最后兜底不丢字节的 latin-1
    CANDIDATE_ENCODINGS = ("utf-8", "gbk", "gb18030", "big5", "latin-1")

    def __init__(self, cfg: SessionConfig):
        super().__init__(cfg)
        self._ftp: Optional[ftplib.FTP] = None
        self._cwd = "/"
        self._supports_mlsd: Optional[bool] = None
        self._supports_rest: Optional[bool] = None
        self._supports_site_chmod: Optional[bool] = None
        # 实际生效的编码：可能由探测自动修正，用于界面回显
        self.effective_encoding: str = cfg.encoding or "utf-8"
        self.encoding_autodetected: bool = False

    # ------------------------------------------------------------------ 连接
    def connect(self) -> None:
        cfg = self.cfg
        host, port = cfg.host, cfg.default_port()
        if not host:
            raise SessionError("主机地址不能为空")
        try:
            self._ftp = self._make_client()
            self._ftp.connect(host, port, timeout=cfg.timeout)
            self._ftp.login(cfg.username or "anonymous", cfg.password)
            self._ftp.set_pasv(cfg.passive)
            try:
                self._ftp.voidcmd("TYPE I")  # 二进制模式
            except ftplib.all_errors:
                pass
            raw_cwd = "/"
            try:
                raw_cwd = self._ftp.pwd() or "/"
            except ftplib.all_errors:
                raw_cwd = "/"
            self._probe_features()
            # 先探测编码，再把 pwd 解码成界面形式
            self._detect_encoding()
            self._cwd = self._from_wire(raw_cwd)
            log.info("FTP 已连接 %s:%s (cwd=%s, encoding=%s%s)",
                     host, port, self._cwd, self.effective_encoding,
                     " 自动探测" if self.encoding_autodetected else "")
        except ftplib.error_perm as e:
            self.close()
            raise SessionError(f"服务器拒绝：{e}") from e
        except (socket.timeout, TimeoutError) as e:
            self.close()
            raise SessionError(f"连接超时（{cfg.timeout}s）：{host}:{port}") from e
        except socket.gaierror as e:
            self.close()
            raise SessionError(f"无法解析主机名：{host}") from e
        except OSError as e:
            self.close()
            raise SessionError(f"连接失败：{e}") from e

    def _make_client(self) -> ftplib.FTP:
        # FTP 与 FTPS 共用实现，FTPS 在子类里替换。
        # 固定用 latin-1：它到 0-255 一一映射，收发都不会抛编解码异常，
        # 真正的文件名编码在 _decode_name / _encode_name 里处理。
        return ftplib.FTP(encoding=_WIRE_ENCODING)

    def _decode_name(self, raw: str, enc: Optional[str] = None) -> str:
        """把 latin-1 管道里的字符串按真实编码还原成文件名。

        失败时退回 UTF-8 宽容模式，绝不抛异常——列表里夹一个乱码名，
        总比整个目录打不开要好。
        """
        enc = enc or self.effective_encoding
        try:
            return raw.encode(_WIRE_ENCODING).decode(enc)
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        try:
            return raw.encode(_WIRE_ENCODING).decode(enc, errors="replace")
        except Exception:  # noqa: BLE001
            return raw

    def _encode_name(self, name: str) -> str:
        """把本地文件名转成服务端期望的编码（再转成 wire 形式）。"""
        try:
            return name.encode(self.effective_encoding).decode(_WIRE_ENCODING)
        except (UnicodeEncodeError, UnicodeDecodeError):
            # 服务端编码表示不了这个字符（比如 GBK 站点上的 emoji），
            # 原样送出，让服务端自己决定拒绝还是存下来
            return name

    def _probe_features(self) -> None:
        """探测服务端能力，避免每次操作都靠异常试错。

        注意：FEAT 是**多行响应**，但走的是控制连接（不走数据连接）。
        所以不能用 retrlines（它会建数据连接），也不能只用 sendcmd
        （只读第一行，剩余行留在缓冲区，会让后续每条命令错位吞响应）。
        正确做法是发命令后用 getmultiline 读完整段响应。
        """
        feats = ""
        try:
            self._ftp.putcmd("FEAT")
            resp = self._ftp.getmultiline() or ""
            feats = resp
        except ftplib.all_errors:
            feats = ""
        upper = feats.upper()
        self._supports_mlsd = "MLSD" in upper or "MLST" in upper
        self._supports_rest = "REST STREAM" in upper
        self._supports_site_chmod = "SITE CHMOD" in upper
        log.debug("FEAT 探测：MLSD=%s REST=%s CHMOD=%s",
                  self._supports_mlsd, self._supports_rest, self._supports_site_chmod)

    # -------------------------------------------------------------- 编码探测
    def _raw_listing(self) -> List[str]:
        """取一次原始目录列表（wire 编码的原始字符串），供编码探测用。

        关键：固定列**根目录 "/"**，绝不用 self._cwd。
        因为 _cwd 此时是"已解码的界面路径"（可能含中文），
        直接拼进命令会触发 latin-1 编码异常，进而让控制连接的
        响应错位（表现为后续命令读到上一条的 200 Type set to A.）。
        "/" 是纯 ASCII，任何编码下都安全。
        """
        ftp = self._chk()
        lines: List[str] = []
        try:
            ftp.retrlines("LIST /", lines.append)
        except ftplib.all_errors as e:
            log.debug("编码探测取列表失败：%s", e)
            self._resync()
        return lines

    def _resync(self) -> None:
        """一次传输失败后清理残留响应，避免后续命令错位。

        FTP 是严格一问一答的协议，任何一次传输中途失败（数据连接断了、
        命令被拒），都可能在控制连接里留下未读的响应行。不清理的话，
        下一条命令就会读到上一条的响应——就是 "200 Type set to A." 这类怪错。
        """
        if self._ftp is None or self._ftp.sock is None:
            return
        sock = self._ftp.sock
        try:
            old_timeout = sock.gettimeout()
            sock.settimeout(0.2)
            drained = 0
            while True:
                try:
                    buf = sock.recv(4096)
                except (socket.timeout, TimeoutError):
                    break           # 缓冲区空了
                except OSError:
                    break           # 连接已断，交给上层重连
                if not buf:
                    break
                drained += len(buf)
            sock.settimeout(old_timeout if old_timeout else self.cfg.timeout)
            if drained:
                log.debug("已丢弃 %d 字节残留响应", drained)
            # 重建文件对象，丢弃可能处于半读状态的旧缓冲
            self._ftp.file = sock.makefile("r", encoding=_WIRE_ENCODING)
        except Exception as e:  # noqa: BLE001
            log.debug("清理残留响应失败：%s", e)

    def _detect_encoding(self) -> None:
        """自动判断服务端文件名编码。

        做法：拿一次原始目录列表，逐个候选编码尝试严格解码，
        第一个能让**全部文件名**都解通的编码即为命中。
        用户显式指定且能解通时，优先尊重用户的选择。

        本方法绝不抛异常：探测失败最多退化成用户设置，
        不能让"编码认不出"导致连不上服务器。
        """
        user_enc = (self.cfg.encoding or "utf-8").strip() or "utf-8"
        self.effective_encoding = user_enc
        try:
            self._do_detect(user_enc)
        except Exception as e:  # noqa: BLE001
            log.debug("编码探测失败，沿用用户设置 %s：%s", user_enc, e)
            self.effective_encoding = user_enc

    def _do_detect(self, user_enc: str) -> None:
        lines = self._raw_listing()
        if not lines:
            return

        # 从原始行里抠出文件名部分（兼容 Unix / DOS 两种 LIST 格式）
        raw_names = []
        for line in lines:
            line = line.rstrip("\r\n")
            if not line.strip():
                continue
            m = _UNIX_RE.match(line) or _DOS_RE.match(line)
            if m:
                raw_names.append(m.group("name"))
            else:
                raw_names.append(line)
        if not raw_names:
            return

        def decodes_cleanly(enc: str) -> bool:
            for name in raw_names:
                try:
                    name.encode(_WIRE_ENCODING).decode(enc)
                except (UnicodeEncodeError, UnicodeDecodeError):
                    return False
            return True

        # 1) 用户指定的编码能解通就直接用
        if user_enc.lower().replace("-", "") != "latin1" and decodes_cleanly(user_enc):
            self.effective_encoding = user_enc
            self.encoding_autodetected = False
            return

        # 2) 依次试候选编码，跳过等效的 latin-1（它永远"成功"但只是原样搬运）
        for enc in self.CANDIDATE_ENCODINGS:
            if enc == "latin-1":
                continue
            if decodes_cleanly(enc):
                self.effective_encoding = enc
                self.encoding_autodetected = enc.lower() != user_enc.lower()
                if self.encoding_autodetected:
                    log.info("服务端文件名编码自动识别为 %s（用户设置：%s）", enc, user_enc)
                return

        # 3) 全都不行：保留用户设置，靠 _decode_name 的宽容模式兜底
        self.effective_encoding = user_enc

    def set_encoding(self, enc: str) -> None:
        """手动切换编码，并刷新当前目录。"""
        self.effective_encoding = enc or "utf-8"
        self.cfg.encoding = self.effective_encoding
        self.encoding_autodetected = False

    def close(self) -> None:
        if self._ftp is not None:
            try:
                self._ftp.quit()
            except Exception:
                try:
                    self._ftp.close()
                except Exception:
                    pass
            self._ftp = None

    def is_connected(self) -> bool:
        if self._ftp is None:
            return False
        try:
            self._ftp.voidcmd("NOOP")
            return True
        except Exception:
            return False

    def noop(self) -> None:
        try:
            self._ftp.voidcmd("NOOP")
        except Exception:
            pass

    def _chk(self) -> ftplib.FTP:
        if self._ftp is None:
            raise SessionError("尚未连接")
        return self._ftp

    # -------------------------------------------------------------- 目录列表
    def _abs(self, path: str) -> str:
        """把界面用的路径转成可直接发给服务端的 wire 形式。

        所有 FTP 命令都经由这里，所以编码转换只需在这一处做。
        """
        if not path:
            raw = self._cwd
        elif path.startswith("/"):
            raw = path
        else:
            base = self._cwd.rstrip("/")
            raw = f"{base}/{path}"
        return self._to_wire(raw)

    def _to_wire(self, path: str) -> str:
        """路径按服务端编码重新编码，转成 wire（latin-1）形式。

        编码失败绝不能把原文返回——原文里的非 ASCII 字符无法通过
        latin-1 发送，会让 ftplib 在发命令时抛异常，把控制连接搞乱。
        这里明确抛 SessionError，让上层给出可读提示。
        """
        enc = (self.effective_encoding or "utf-8").lower().replace("-", "")
        if enc in ("latin1", "ascii"):
            try:
                path.encode("ascii")
                return path
            except UnicodeEncodeError:
                pass
        try:
            return path.encode(self.effective_encoding).decode(_WIRE_ENCODING)
        except (UnicodeEncodeError, UnicodeDecodeError) as e:
            raise SessionError(
                f"文件名「{path}」无法用 {self.effective_encoding} 编码发送，"
                f"请切换文件名编码后重试") from e

    def listdir(self, path: str) -> List[RemoteFile]:
        ftp = self._chk()
        target = self._abs(path)
        entries = self._list_mlsd(target)
        if entries is None:
            entries = self._list_text(target)
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return entries

    def _list_mlsd(self, path: str) -> Optional[List[RemoteFile]]:
        """MLSD 返回结构化的 'fact=value;fact=value; name' 行，解析最可靠。"""
        if self._supports_mlsd is False:
            return None
        try:
            lines: List[str] = []
            ftp = self._chk()
            ftp.retrlines(f"MLSD {path}".strip(), lines.append)
        except ftplib.error_perm:
            # MLSD 被拒（老服务端常见），先清理残留响应再回退 LIST，
            # 否则 LIST 的 TYPE A 会读到 MLSD 遗留的响应，报
            # "error_reply: 200 Type set to A."
            self._supports_mlsd = False
            self._resync()
            return None
        except ftplib.all_errors as e:
            log.debug("MLSD 失败，回退 LIST: %s", e)
            self._resync()
            return None

        self._supports_mlsd = True
        out: List[RemoteFile] = []
        for line in lines:
            if not line or " " not in line:
                continue
            facts, _, name = line.partition(" ")
            name = name.strip()
            if not name or name in (".", ".."):
                continue
            kv: Dict[str, str] = {}
            for fact in facts.split(";"):
                if "=" in fact:
                    k, v = fact.split("=", 1)
                    kv[k.strip().lower()] = v.strip()
            ftype = kv.get("type", "").lower()
            is_dir = ftype.startswith("dir")
            is_link = ftype.startswith("os.unix=symlink") or ftype == "link"
            try:
                size = int(kv.get("size", "0"))
            except ValueError:
                size = 0
            mtime = _parse_mlsx_time(kv.get("modify", ""))
            mode = None
            if "unix.mode" in kv:
                try:
                    mode = int(kv["unix.mode"], 8)
                except ValueError:
                    mode = None
            if mode is None and "perm" in kv:
                # 部分服务端（如 pyftpdlib）只给 perm=elradfmwMT，不给 unix.mode。
                # 这里按 FTP 权限字母推导一个近似权限位，仅供界面展示。
                mode = _mode_from_perm(kv["perm"], is_dir)
            # 文件名按服务端编码还原；path 当前是 wire 形式，解回界面形式再拼接
            real_name = self._decode_name(name)
            out.append(
                RemoteFile(
                    name=real_name,
                    path=_join(self._from_wire(path), real_name),
                    size=size,
                    mtime=mtime,
                    is_dir=is_dir,
                    is_link=is_link,
                    mode=mode,
                    owner=self._decode_name(kv.get("unix.owner", kv.get("unix.uid", ""))),
                    group=self._decode_name(kv.get("unix.group", kv.get("unix.gid", ""))),
                )
            )
        return out

    def _from_wire(self, raw: str) -> str:
        """wire 形式的路径还原成界面形式。"""
        return self._decode_name(raw)

    def _list_text(self, path: str) -> List[RemoteFile]:
        """回退方案：解析 LIST 的文本输出，兼容 Unix / DOS 两种风格。"""
        ftp = self._chk()
        lines: List[str] = []
        try:
            ftp.retrlines(f"LIST {path}".strip(), lines.append)
        except ftplib.error_perm as e:
            # 服务端拒绝后控制连接可能残留响应，必须清理再抛
            self._resync()
            raise SessionError(
                f"无法列出目录 {self._from_wire(path)}：{e}") from e
        except ftplib.all_errors as e:
            self._resync()
            raise SessionError(
                f"读取目录失败 {self._from_wire(path)}：{e}") from e

        out: List[RemoteFile] = []
        for line in lines:
            parsed = self._parse_list_line(line, path)
            if parsed is not None:
                out.append(parsed)
        return out

    def _parse_list_line(self, line: str, parent: str) -> Optional[RemoteFile]:
        """解析一行 LIST 文本。注意：传入的 line 是 wire 形式，
        正则先按原始字节匹配（分隔符都是 ASCII，不受编码影响），
        取到文件名后再按服务端编码还原。
        """
        line = line.rstrip("\r\n")
        if not line.strip():
            return None
        parent_real = self._from_wire(parent)

        m = _UNIX_RE.match(line)
        if m:
            name = m.group("name")
            if " -> " in name:  # 符号链接把目标去掉
                name, _, target = name.partition(" -> ")
            if name in (".", ".."):
                return None
            perm = m.group("perm")
            real_name = self._decode_name(name)
            return RemoteFile(
                name=real_name,
                path=_join(parent_real, real_name),
                size=int(m.group("size")),
                mtime=_parse_unix_mtime(m.group("date")),
                is_dir=perm.startswith("d"),
                is_link=perm.startswith("l"),
                mode=_parse_perm(perm[1:]),
                owner=self._decode_name(m.group("owner")),
                group=self._decode_name(m.group("group")),
            )

        m = _DOS_RE.match(line)
        if m:
            name = m.group("name").strip()
            if name in (".", ".."):
                return None
            real_name = self._decode_name(name)
            return RemoteFile(
                name=real_name,
                path=_join(parent_real, real_name),
                size=0 if m.group("size").upper() == "<DIR>" else int(m.group("size")),
                mtime=0.0,
                is_dir=m.group("size").upper() == "<DIR>",
            )
        log.debug("无法解析的 LIST 行: %r", line)
        return None

    def stat(self, path: str) -> RemoteFile:
        target = self._abs(path)
        parent, _, name = target.rstrip("/").rpartition("/")
        parent = parent or "/"
        if not name:
            return RemoteFile(name="/", path="/", is_dir=True)
        for entry in self.listdir(parent):
            if entry.name == name:
                return entry
        raise SessionError(f"路径不存在：{target}")

    def cwd(self) -> str:
        return self._cwd

    def change_dir(self, path: str) -> str:
        ftp = self._chk()
        target = self._abs(path)
        try:
            ftp.cwd(target)
            # pwd() 返回 wire 形式，必须解码回界面形式，否则后续路径拼接会编码两次
            real = ftp.pwd() or target
            self._cwd = self._from_wire(real)
        except ftplib.error_perm as e:
            raise SessionError(f"无法进入目录 {self._from_wire(target)}：{e}") from e
        return self._cwd

    # -------------------------------------------------------------- 写操作
    def mkdir(self, path: str) -> None:
        target = self._abs(path)
        try:
            self._chk().mkd(target)
        except ftplib.error_perm as e:
            raise SessionError(f"创建目录失败 {target}：{e}") from e

    def remove(self, path: str, is_dir: bool = False) -> None:
        ftp = self._chk()
        target = self._abs(path)
        try:
            if is_dir:
                ftp.rmd(target)
            else:
                ftp.delete(target)
        except ftplib.error_perm as e:
            raise SessionError(f"删除失败 {target}：{e}") from e

    def rename(self, src: str, dst: str) -> None:
        a, b = self._abs(src), self._abs(dst)
        try:
            self._chk().rename(a, b)
        except ftplib.error_perm as e:
            raise SessionError(f"重命名失败 {a} -> {b}：{e}") from e

    def chmod(self, path: str, mode: int) -> None:
        ftp = self._chk()
        target = self._abs(path)
        try:
            ftp.sendcmd(f"SITE CHMOD {mode:o} {target}")
        except ftplib.error_perm as e:
            raise UnsupportedOperation(f"服务端不支持 SITE CHMOD：{e}") from e

    # -------------------------------------------------------------- 传输
    def supports_resume(self) -> bool:
        return self._supports_rest is not False

    def get(
        self,
        remote: str,
        local: str,
        offset: int = 0,
        progress: Optional[ProgressCb] = None,
        cancel_flag: Optional[CancelCb] = None,
    ) -> None:
        ftp = self._chk()
        remote_path = self._abs(remote)
        try:
            total = ftp.size(remote_path) or 0
        except ftplib.all_errors:
            total = 0

        os.makedirs(os.path.dirname(os.path.abspath(local)) or ".", exist_ok=True)
        done = offset
        mode = "ab" if offset else "wb"

        with open(local, mode) as fh:
            def _cb(data: bytes) -> None:
                nonlocal done
                if cancel_flag and cancel_flag():
                    raise TransferCancelled()
                fh.write(data)
                done += len(data)
                if progress:
                    progress(done, max(total, done))

            try:
                kwargs = {"rest": offset} if offset else {}
                ftp.retrbinary(f"RETR {remote_path}", _cb, **kwargs)
            except TransferCancelled:
                raise
            except ftplib.error_perm as e:
                raise SessionError(f"下载失败 {remote_path}：{e}") from e
            except ftplib.all_errors as e:
                raise SessionError(f"下载中断 {remote_path}：{e}") from e

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
        ftp = self._chk()
        remote_path = self._abs(remote)
        total = os.path.getsize(local)

        # storbinary 需要一个带 read() 的类文件对象，不能用生成器
        reader = _ProgressReader(local, offset, total, progress, cancel_flag)
        try:
            if offset:
                ftp.storbinary(f"STOR {remote_path}", reader, rest=offset)
            else:
                ftp.storbinary(f"STOR {remote_path}", reader)
        except TransferCancelled:
            raise
        except ftplib.error_perm as e:
            raise SessionError(f"上传失败 {remote_path}：{e}") from e
        except ftplib.all_errors as e:
            raise SessionError(f"上传中断 {remote_path}：{e}") from e
        finally:
            reader.close()

        if progress:
            progress(total, total)

    def read_head(self, remote: str, limit: int) -> bytes:
        """只取文件头部（预览用）。

        FTP 没有"读前 N 字节"的命令，只能发 RETR 收到够数就主动中止。
        中止后数据通道没传输完，这条控制连接已经不可靠了 —— 所以这里
        直接关掉它，调用方应当把这个会话当一次性的用（预览就是新建一个）。
        """
        ftp = self._chk()
        remote_path = self._abs(remote)
        buf = bytearray()

        class _Enough(Exception):
            """收到够多数据，中断传输。"""

        def _cb(data: bytes) -> None:
            buf.extend(data)
            if len(buf) >= limit:
                raise _Enough()

        try:
            ftp.retrbinary(f"RETR {remote_path}", _cb)
        except _Enough:
            pass
        except ftplib.error_perm as e:
            if not buf:
                raise SessionError(f"读取失败 {remote_path}：{e}") from e
        except ftplib.all_errors as e:
            if not buf:
                raise SessionError(f"读取中断 {remote_path}：{e}") from e

        out = bytes(buf[:limit])
        if len(buf) >= limit:
            self.close()
        return out


def _join(parent: str, name: str) -> str:
    p = parent.rstrip("/")
    return f"{p}/{name}" if p else f"/{name}"


def _mode_from_perm(perm: str, is_dir: bool) -> int:
    """把 MLSD 的 perm 字母串推导成近似八进制权限位。

    FTP 的 perm 只有功能语义（r 下载 / a 追加 / d 删除 / f 改名 / m 建目录 /
    w 上传 / M 改权限 / e 进目录 / l 列目录 / c 建目录），本身不含 owner/group/other
    的区分，所以这里只做展示用途的粗略映射，不能用于真实权限判断。
    """
    readable = "r" in perm or "l" in perm
    writable = "w" in perm or "a" in perm or "d" in perm
    executable = is_dir and ("e" in perm or "l" in perm)
    bits = 0
    for shift in (6, 3, 0):
        if readable:
            bits |= 0o4 << shift
        if writable:
            bits |= 0o2 << shift
        if executable:
            bits |= 0o1 << shift
    return bits


class _ProgressReader:
    """包装本地文件，供 storbinary 读取，同时上报进度并支持取消。

    ftplib 要求传入的对象有 read(size)，这里逐块读出并计数。
    """

    def __init__(
        self,
        path: str,
        offset: int = 0,
        total: int = 0,
        progress: Optional[ProgressCb] = None,
        cancel_flag: Optional[CancelCb] = None,
        chunk: int = CHUNK,
    ):
        self._fh = open(path, "rb")
        self._fh.seek(offset)
        self._done = offset
        self._total = total
        self._progress = progress
        self._cancel = cancel_flag
        self._chunk = chunk
        if self._total <= 0:
            try:
                self._total = os.fstat(self._fh.fileno()).st_size
            except OSError:
                self._total = 0

    def read(self, size: int = -1) -> bytes:
        if self._cancel and self._cancel():
            raise TransferCancelled()
        if size is None or size < 0:
            size = self._chunk
        buf = self._fh.read(min(size, self._chunk))
        if buf:
            self._done += len(buf)
            if self._progress:
                self._progress(self._done, self._total)
        return buf

    def close(self) -> None:
        try:
            self._fh.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _parse_mlsx_time(text: str) -> float:
    """MLSD 的 modify 形如 '20240101120000' 或 '20240101120000.000'。"""
    import datetime as dt

    text = text.strip()
    if not text:
        return 0.0
    digits = text.split(".")[0]
    if len(digits) < 14:
        return 0.0
    try:
        return dt.datetime.strptime(digits[:14], "%Y%m%d%H%M%S").timestamp()
    except ValueError:
        return 0.0
