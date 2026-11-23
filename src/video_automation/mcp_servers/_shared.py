"""MCPサーバー共通：API と同じサービス構成（AppContainer）を遅延生成して共有する。"""

from video_automation.app.bootstrap import AppContainer, build_container
from video_automation.app.settings import get_settings

_container: AppContainer | None = None


async def get_container() -> AppContainer:
    global _container
    if _container is None:
        _container = await build_container(get_settings())
    return _container


def set_container(container: AppContainer | None) -> None:
    """テスト用。"""
    global _container
    _container = container
