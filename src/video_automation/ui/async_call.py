"""UI から時間のかかる API 呼び出し（ローカル LLM の生成など）を別スレッドで実行する。"""

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from PySide6.QtCore import QObject, QTimer


def error_text(exc: BaseException) -> str:
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            return str(resp.json().get("message") or resp.text)
        except Exception:  # noqa: BLE001
            return str(resp.text)
    return str(exc)


class AsyncCaller(QObject):
    """call(fn, done) で fn を別スレッド実行し、完了後に UI スレッドで done(結果) を呼ぶ。
    enabled=False（テスト用）なら同期実行する。"""

    def __init__(
        self,
        parent: QObject,
        on_error: Callable[[BaseException], None],
        on_busy: Callable[[bool], None],
    ) -> None:
        super().__init__(parent)
        self.enabled = True
        self.on_error = on_error
        self.on_busy = on_busy
        self._pool = ThreadPoolExecutor(max_workers=1)
        self.pending: list[tuple[Future[Any], Callable[[Any], None]]] = []
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self.poll)

    def run_now(self, fn: Callable[[], Any], done: Callable[[Any], None]) -> None:
        try:
            done(fn())
        except Exception as exc:  # noqa: BLE001 - API のエラー内容を表示する
            self.on_error(exc)

    def call(self, fn: Callable[[], Any], done: Callable[[Any], None]) -> None:
        if not self.enabled:
            self.run_now(fn, done)
            return
        self.on_busy(True)
        self.pending.append((self._pool.submit(fn), done))
        self._timer.start()

    def poll(self) -> None:
        for fut, done in list(self.pending):
            if not fut.done():
                continue
            self.pending.remove((fut, done))
            exc = fut.exception()
            if exc is not None:
                self.on_error(exc)
                continue
            try:
                done(fut.result())
            except Exception as e:  # noqa: BLE001
                self.on_error(e)
        if not self.pending:
            self._timer.stop()
            self.on_busy(False)
