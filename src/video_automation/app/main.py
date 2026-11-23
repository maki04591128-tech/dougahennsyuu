"""アプリ起動 (P1-08/09)。API(別スレッド) + PySide6 UI。"""

import argparse
import sys
import threading
import time

import uvicorn

from video_automation.api.app import create_app
from video_automation.app.settings import get_settings


def _start_api(host: str, port: int) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(create_app(), host=host, port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="video-automation")
    parser.add_argument("--headless", action="store_true", help="UIなしでAPIのみ起動")
    args = parser.parse_args(argv)
    s = get_settings()

    if args.headless:
        uvicorn.run(create_app(s), host=s.host, port=s.port)
        return 0

    server = _start_api(s.host, s.port)
    try:
        from PySide6.QtWidgets import QApplication

        from video_automation.ui.api_client import ApiClient
        from video_automation.ui.main_window import MainWindow

        app = QApplication(sys.argv)
        window = MainWindow(ApiClient(s.api_base_url))
        window.show()
        window.refresh()
        return app.exec()
    finally:
        server.should_exit = True


if __name__ == "__main__":
    raise SystemExit(main())
