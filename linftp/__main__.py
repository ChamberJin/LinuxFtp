"""程序入口：python -m linftp [--host H] [--user U] [--password P] [--port N]"""
from __future__ import annotations

import argparse
import logging
import sys


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="linftp", description="Linux FTP 客户端")
    parser.add_argument("--host", help="启动后直接连接的主机")
    parser.add_argument("--protocol", default="ftp", choices=["ftp", "sftp"],
                        help="协议：ftp（默认）或 sftp")
    parser.add_argument("--port", type=int, help="端口，默认按协议取 21 / 22")
    parser.add_argument("--user", default="anonymous", help="用户名")
    parser.add_argument("--password", default="", help="密码")
    parser.add_argument("--keyfile", default="", help="SFTP 私钥文件（可替代密码）")
    parser.add_argument("--local", help="本地栏初始目录")
    parser.add_argument("--no-start", action="store_true",
                        help="跳过启动页，直接进主窗口")
    parser.add_argument("--verbose", "-v", action="store_true", help="打开调试日志")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from . import theme
    from .backend import SessionConfig
    from .mainwindow import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("LinFTP")
    app.setOrganizationName("LinFTP")
    # 让窗口的 WM_CLASS 与 linftp.desktop 的 StartupWMClass 对上，
    # 任务栏/停靠栏才会把窗口归到应用图标下，而不是显示成无图标的窗口
    app.setDesktopFileName("linftp")
    theme.apply(app)

    win = MainWindow()
    if args.local:
        win.go_local(args.local)

    # 优先按上次退出时的大小和位置还原；没有记录（首次启动、外接屏拔了）
    # 才退回"主屏居中"。多屏下窗口管理器给顶层窗口挑屏幕是随机的：不显式
    # 摆位，主窗口可能开在副屏，而随后的启动页又跟到另一块屏上，看起来
    # 就像"两个窗口各占一块屏"。
    if not win.restore_window_state():
        theme.place_on_primary(win)

    auto_connect = bool(args.host)
    if auto_connect:
        cfg = SessionConfig(
            protocol=args.protocol,
            host=args.host,
            port=args.port or None,
            username=args.user,
            password=args.password,
            keyfile=args.keyfile,
        )

    # 入口分三种情况：
    #   1. 命令行指定了主机 —— 直接连，其余一概不管；
    #   2. 书签里设了"启动时默认打开" —— 直接进那个书签，不弹启动页；
    #   3. 其余 —— 有书签/最近连接就先让用户选一个入口。
    # --no-start 表示"别替我做决定，直接给我主窗口"，上面 2、3 都跳过。
    win.show()
    if auto_connect:
        win.connect_with(cfg)
    elif args.no_start:
        pass
    else:
        # 连接/弹窗都放到事件循环启动之后：主窗口能先画出来，
        # 默认书签连不上时也不会卡在 exec() 里一片空白
        QTimer.singleShot(0, win.startup_dispatch)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
