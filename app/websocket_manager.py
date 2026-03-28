import json
import asyncio
from fastapi import WebSocket, WebSocketDisconnect
from typing import Optional
from .models import WSMessage


class ConnectionManager:
    """Manages WebSocket connections per room."""

    def __init__(self):
        # room_id -> {player_id: WebSocket}
        self._rooms: dict[str, dict[str, WebSocket]] = {}

    def connect(self, room_id: str, player_id: str, ws: WebSocket):
        if room_id not in self._rooms:
            self._rooms[room_id] = {}
        self._rooms[room_id][player_id] = ws

    def disconnect(self, room_id: str, player_id: str):
        if room_id in self._rooms:
            self._rooms[room_id].pop(player_id, None)
            if not self._rooms[room_id]:
                del self._rooms[room_id]

    async def send_to(self, room_id: str, player_id: str, message: dict):
        ws = self._rooms.get(room_id, {}).get(player_id)
        if ws:
            await ws.send_json(message)

    async def broadcast(self, room_id: str, message: dict, exclude: str | None = None):
        connections = self._rooms.get(room_id, {})
        for pid, ws in connections.items():
            if pid != exclude:
                try:
                    await ws.send_json(message)
                except Exception:
                    pass

    async def broadcast_all(self, room_id: str, message: dict):
        await self.broadcast(room_id, message, exclude=None)


manager = ConnectionManager()
