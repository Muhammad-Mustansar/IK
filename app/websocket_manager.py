import uuid
from collections import defaultdict
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from app.chat_schemas import ChatWSIncoming, ChatWSOutgoing, WSMessageType


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, websocket: WebSocket, session_id: str) -> None:
        await websocket.accept()
        self._connections[session_id].add(websocket)

    def disconnect(self, websocket: WebSocket, session_id: str) -> None:
        self._connections[session_id].discard(websocket)
        if not self._connections[session_id]:
            del self._connections[session_id]

    async def send_json(self, websocket: WebSocket, payload: dict[str, Any]) -> None:
        await websocket.send_json(payload)

    async def send_model(self, websocket: WebSocket, message: ChatWSOutgoing) -> None:
        await websocket.send_json(message.model_dump(exclude_none=True))

    async def broadcast_typing(self, session_id: str, active: bool) -> None:
        message = ChatWSOutgoing(
            type=WSMessageType.TYPING,
            active=active,
            session_id=session_id,
        )
        for connection in list(self._connections.get(session_id, set())):
            await self.send_model(connection, message)

    async def handle_ping(self, websocket: WebSocket) -> None:
        await self.send_model(websocket, ChatWSOutgoing(type=WSMessageType.PONG))


manager = ConnectionManager()


def resolve_session_id(incoming: ChatWSIncoming) -> str:
    return incoming.session_id or str(uuid.uuid4())


async def safe_receive_json(websocket: WebSocket) -> ChatWSIncoming | None:
    try:
        data = await websocket.receive_json()
        return ChatWSIncoming.model_validate(data)
    except WebSocketDisconnect:
        return None
    except Exception:
        return None
