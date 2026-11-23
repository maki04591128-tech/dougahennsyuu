from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/events")
async def events(ws: WebSocket) -> None:
    """Job進捗などのイベントを配信する。"""
    await ws.accept()
    try:
        async with ws.app.state.bus.subscribe() as queue:
            while True:
                await ws.send_json(await queue.get())
    except WebSocketDisconnect:
        pass
