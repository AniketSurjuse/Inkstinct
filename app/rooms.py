import uuid
import json
import random
from pathlib import Path
from typing import Optional
from .models import Room, Player, RoomSettings, GamePhase

# In-memory room store
_rooms: dict[str, Room] = {}
# Map player_id -> room_id
_player_rooms: dict[str, str] = {}

# Random names pool
_names: list[str] = []
_names_path = Path(__file__).parent.parent / "data" / "names.json"
if _names_path.exists():
    with open(_names_path, "r") as _f:
        _names = json.load(_f)


def _random_nickname() -> str:
    if _names:
        return random.choice(list(_names))
    return f"Player{random.randint(1000, 9999)}"


def _generate_room_code() -> str:
    """Generate a short uppercase room code."""
    return uuid.uuid4().hex[:6].upper()


def create_room(nickname: str, avatar_svg: str = "") -> tuple[Room, Player]:
    room_id = _generate_room_code()
    room = Room(id=room_id)

    player = Player(
        id=uuid.uuid4().hex[:8],
        nickname=nickname.strip() or _random_nickname(),
        avatar_svg=avatar_svg,
        is_host=True,
    )
    room.players.append(player)
    _rooms[room_id] = room
    _player_rooms[player.id] = room_id
    return room, player


def join_room(room_id: str, nickname: str, avatar_svg: str = "") -> tuple[Room, Player]:
    room = _rooms.get(room_id.upper())
    if not room:
        raise ValueError("Room not found")
    if len(room.players) >= room.settings.max_players:
        raise ValueError("Room is full")
    if room.phase != GamePhase.LOBBY:
        raise ValueError("Game already in progress")

    player = Player(
        id=uuid.uuid4().hex[:8],
        nickname=nickname.strip() or _random_nickname(),
        avatar_svg=avatar_svg,
    )
    room.players.append(player)
    _player_rooms[player.id] = room_id.upper()
    return room, player


def update_settings(room_id: str, **kwargs) -> Optional[Room]:
    room = _rooms.get(room_id.upper())
    if not room or room.phase != GamePhase.LOBBY:
        return None
    for key, val in kwargs.items():
        if val is not None and hasattr(room.settings, key):
            setattr(room.settings, key, val)
    return room


def get_room(room_id: str) -> Optional[Room]:
    return _rooms.get(room_id.upper())


def remove_player(player_id: str) -> Optional[Room]:
    room_id = _player_rooms.pop(player_id, None)
    if not room_id:
        return None
    room = _rooms.get(room_id)
    if not room:
        return None
    room.players = [p for p in room.players if p.id != player_id]
    if not room.players:
        del _rooms[room_id]
        return None
    # Transfer host if needed
    if not any(p.is_host for p in room.players):
        room.players[0].is_host = True
    return room


def list_rooms() -> list[Room]:
    return list(_rooms.values())
