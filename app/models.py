from pydantic import BaseModel
from enum import Enum
from typing import Optional


class GamePhase(str, Enum):
    LOBBY = "lobby"
    CHOOSING = "choosing"
    DRAWING = "drawing"
    ROUND_OVER = "round_over"
    GAME_OVER = "game_over"


class Player(BaseModel):
    id: str
    nickname: str
    avatar_svg: str = ""
    score: int = 0
    is_host: bool = False
    has_guessed: bool = False


class RoomSettings(BaseModel):
    max_players: int = 8
    rounds: int = 3
    draw_time: int = 90
    word_count: int = 3
    hints_count: int = 2


class Room(BaseModel):
    id: str
    players: list[Player] = []
    settings: RoomSettings = RoomSettings()
    phase: GamePhase = GamePhase.LOBBY
    current_round: int = 0
    current_drawer_index: int = 0
    current_word: Optional[str] = None
    word_choices: list[str] = []
    time_left: int = 0
    strokes: list[dict] = []
    hints_given: int = 0
    drawing_started_at: Optional[float] = None  # timestamp when drawing began


class CreateRoomRequest(BaseModel):
    nickname: str = ""
    avatar_svg: str = ""


class JoinRoomRequest(BaseModel):
    nickname: str = ""
    avatar_svg: str = ""


class UpdateSettingsRequest(BaseModel):
    draw_time: Optional[int] = None
    word_count: Optional[int] = None
    hints_count: Optional[int] = None
    rounds: Optional[int] = None


class CreateRoomResponse(BaseModel):
    room_id: str
    player: Player


class RoomStateResponse(BaseModel):
    room: Room
    your_player_id: str


class WSMessage(BaseModel):
    type: str
    payload: dict = {}
