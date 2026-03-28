from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
import asyncio
import random
from .models import (
    CreateRoomRequest, JoinRoomRequest, UpdateSettingsRequest,
    CreateRoomResponse, RoomStateResponse, GamePhase,
)
from . import rooms as room_manager
from . import game as game_engine
from .websocket_manager import manager

router = APIRouter()

# Active timer tasks per room
_choosing_timers: dict[str, asyncio.Task] = {}
_drawing_timers: dict[str, asyncio.Task] = {}
_round_over_timers: dict[str, asyncio.Task] = {}

CHOOSING_TIME_LIMIT = 10  # seconds
ROUND_OVER_DELAY = 3  # seconds before auto-advancing


def _cancel_choosing_timer(room_id: str):
    task = _choosing_timers.pop(room_id, None)
    if task and not task.done():
        task.cancel()


def _cancel_drawing_timer(room_id: str):
    task = _drawing_timers.pop(room_id, None)
    if task and not task.done():
        task.cancel()


def _cancel_round_over_timer(room_id: str):
    task = _round_over_timers.pop(room_id, None)
    if task and not task.done():
        task.cancel()


async def _end_if_last_player(room_id: str):
    """End an active game if only one player remains in the room."""
    room = room_manager.get_room(room_id)
    if not room:
        return

    if room.phase in (GamePhase.LOBBY, GamePhase.GAME_OVER):
        return

    if len(room.players) != 1:
        return

    # Stop any active timers for this room.
    _cancel_choosing_timer(room_id)
    _cancel_drawing_timer(room_id)
    _cancel_round_over_timer(room_id)

    room.phase = GamePhase.GAME_OVER
    winner = room.players[0]

    await manager.broadcast_all(room_id, {
        "type": "game_over",
        "payload": {
            "leaderboard": [
                {
                    "id": winner.id,
                    "nickname": winner.nickname,
                    "score": winner.score,
                }
            ]
        },
    })


async def _choosing_timer_task(room_id: str):
    """Auto-pick a random word after CHOOSING_TIME_LIMIT seconds."""
    try:
        for remaining in range(CHOOSING_TIME_LIMIT, 0, -1):
            await manager.broadcast_all(room_id, {
                "type": "timer",
                "payload": {"secondsLeft": remaining, "phase": "choosing"},
            })
            await asyncio.sleep(1)

        # Time's up — auto-choose a random word
        room = room_manager.get_room(room_id)
        if not room or room.phase != GamePhase.CHOOSING:
            return

        word = random.choice(room.word_choices) if room.word_choices else "unknown"
        room = game_engine.choose_word(room_id, word)
        if not room:
            return

        drawer = room.players[room.current_drawer_index]
        hint = game_engine.get_word_hint(room)

        await manager.broadcast_all(room_id, {
            "type": "new_turn",
            "payload": {
                "drawerId": drawer.id,
                "roundNum": room.current_round,
            },
        })
        await manager.broadcast(room_id, {
            "type": "word_hint",
            "payload": {"hint": hint},
        }, exclude=drawer.id)
        await manager.send_to(room_id, drawer.id, {
            "type": "your_word",
            "payload": {"word": room.current_word},
        })

        # Start drawing timer
        _start_drawing_timer(room_id)

    except asyncio.CancelledError:
        pass


async def _drawing_timer_task(room_id: str):
    """Count down draw time, broadcast ticks, check hints, auto-end round."""
    try:
        room = room_manager.get_room(room_id)
        if not room:
            return
        total = room.settings.draw_time

        for remaining in range(total, 0, -1):
            room = room_manager.get_room(room_id)
            if not room or room.phase != GamePhase.DRAWING:
                return

            room.time_left = remaining
            await manager.broadcast_all(room_id, {
                "type": "timer",
                "payload": {"secondsLeft": remaining, "phase": "drawing"},
            })

            # Check hints
            if game_engine.should_give_hint(room):
                result = game_engine.give_hint(room_id)
                if result:
                    _, hint = result
                    drawer = room.players[room.current_drawer_index]
                    await manager.broadcast(room_id, {
                        "type": "word_hint",
                        "payload": {"hint": hint, "hintNumber": room.hints_given},
                    }, exclude=drawer.id)

            await asyncio.sleep(1)

        # Time's up — end round
        room = room_manager.get_room(room_id)
        if room and room.phase == GamePhase.DRAWING:
            room.time_left = 0
            room = game_engine.end_round(room_id)
            if room:
                await manager.broadcast_all(room_id, {
                    "type": "timer",
                    "payload": {"secondsLeft": 0, "phase": "drawing"},
                })
                await manager.broadcast_all(room_id, {
                    "type": "round_over",
                    "payload": {
                        "word": room.current_word,
                        "scores": {p.id: p.score for p in room.players},
                    },
                })
                _start_round_over_timer(room_id)

    except asyncio.CancelledError:
        pass


def _start_choosing_timer(room_id: str):
    _cancel_choosing_timer(room_id)
    _choosing_timers[room_id] = asyncio.create_task(_choosing_timer_task(room_id))


def _start_drawing_timer(room_id: str):
    _cancel_drawing_timer(room_id)
    _drawing_timers[room_id] = asyncio.create_task(_drawing_timer_task(room_id))


async def _round_over_timer_task(room_id: str):
    """Wait ROUND_OVER_DELAY seconds then auto-advance to next turn."""
    try:
        await asyncio.sleep(ROUND_OVER_DELAY)
        room = room_manager.get_room(room_id)
        if not room or room.phase != GamePhase.ROUND_OVER:
            return

        room = game_engine.next_turn(room_id)
        if not room:
            return

        if room.phase == GamePhase.GAME_OVER:
            leaderboard = sorted(
                [{"id": p.id, "nickname": p.nickname, "score": p.score}
                 for p in room.players],
                key=lambda x: x["score"], reverse=True,
            )
            await manager.broadcast_all(room_id, {
                "type": "game_over",
                "payload": {"leaderboard": leaderboard},
            })
        else:
            drawer = room.players[room.current_drawer_index]
            await manager.broadcast_all(room_id, {
                "type": "new_round",
                "payload": {"drawerId": drawer.id, "round": room.current_round},
            })
            await manager.send_to(room_id, drawer.id, {
                "type": "word_choices",
                "payload": {"words": room.word_choices},
            })
            _start_choosing_timer(room_id)

    except asyncio.CancelledError:
        pass


def _start_round_over_timer(room_id: str):
    _cancel_round_over_timer(room_id)
    _round_over_timers[room_id] = asyncio.create_task(_round_over_timer_task(room_id))


# ── HTTP Endpoints ──────────────────────────────────────────────

@router.get("/avatars")
def get_avatars():
    return {"message": "Avatars are generated client-side via avatar_maker package"}


@router.post("/rooms", response_model=CreateRoomResponse)
def create_room(req: CreateRoomRequest):
    room, player = room_manager.create_room(
        req.nickname, avatar_svg=req.avatar_svg
    )
    return CreateRoomResponse(room_id=room.id, player=player)


@router.post("/rooms/{room_id}/join", response_model=CreateRoomResponse)
def join_room(room_id: str, req: JoinRoomRequest):
    try:
        room, player = room_manager.join_room(
            room_id, req.nickname, avatar_svg=req.avatar_svg
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return CreateRoomResponse(room_id=room.id, player=player)


@router.get("/rooms/{room_id}")
def get_room(room_id: str):
    room = room_manager.get_room(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    # Hide current word from response
    data = room.model_dump()
    data.pop("current_word", None)
    return data


@router.put("/rooms/{room_id}/settings")
def update_settings(room_id: str, req: UpdateSettingsRequest):
    room = room_manager.update_settings(
        room_id,
        draw_time=req.draw_time,
        word_count=req.word_count,
        hints_count=req.hints_count,
        rounds=req.rounds,
    )
    if not room:
        raise HTTPException(status_code=400, detail="Cannot update settings")
    return {"settings": room.settings.model_dump()}


@router.post("/rooms/{room_id}/start")
def start_game(room_id: str):
    room = game_engine.start_game(room_id)
    if not room:
        raise HTTPException(status_code=400, detail="Cannot start game")
    return {"status": "started"}


# ── WebSocket Endpoint ──────────────────────────────────────────

@router.websocket("/ws/{room_id}/{player_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, player_id: str):
    await websocket.accept()
    manager.connect(room_id, player_id, websocket)

    room = room_manager.get_room(room_id)
    if room:
        # Send current state to connecting player
        await manager.send_to(room_id, player_id, {
            "type": "room_state",
            "payload": room.model_dump(),
        })
        # Notify others
        player = next((p for p in room.players if p.id == player_id), None)
        if player:
            await manager.broadcast(room_id, {
                "type": "player_joined",
                "payload": player.model_dump(),
            }, exclude=player_id)

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            payload = data.get("payload", {})

            if msg_type == "draw":
                game_engine.add_stroke(room_id, payload)
                await manager.broadcast(room_id, {
                    "type": "draw",
                    "payload": payload,
                }, exclude=player_id)

            elif msg_type == "clear_canvas":
                game_engine.clear_strokes(room_id)
                await manager.broadcast(room_id, {
                    "type": "clear_canvas",
                    "payload": {},
                }, exclude=player_id)

            elif msg_type == "undo":
                game_engine.undo_stroke(room_id)
                await manager.broadcast(room_id, {
                    "type": "undo",
                    "payload": {},
                }, exclude=player_id)

            elif msg_type == "guess":
                is_correct, points, is_close = game_engine.check_guess(
                    room_id, player_id, payload.get("text", "")
                )
                if is_correct:
                    await manager.broadcast_all(room_id, {
                        "type": "correct_guess",
                        "payload": {"playerId": player_id, "points": points},
                    })
                    # Check if all players guessed
                    room = room_manager.get_room(room_id)
                    if room:
                        non_drawer = [
                            p for i, p in enumerate(room.players)
                            if i != room.current_drawer_index
                        ]
                        if all(p.has_guessed for p in non_drawer):
                            _cancel_drawing_timer(room_id)
                            room = game_engine.end_round(room_id)
                            if room:
                                await manager.broadcast_all(room_id, {
                                    "type": "round_over",
                                    "payload": {
                                        "word": room.current_word,
                                        "scores": {p.id: p.score for p in room.players},
                                    },
                                })
                                _start_round_over_timer(room_id)
                elif is_close:
                    # Send "close guess" only to the guesser
                    await manager.send_to(room_id, player_id, {
                        "type": "close_guess",
                        "payload": {"text": payload.get("text", "")},
                    })
                    # Still broadcast as chat to everyone
                    await manager.broadcast_all(room_id, {
                        "type": "chat",
                        "payload": {"playerId": player_id, "text": payload.get("text", "")},
                    })
                else:
                    # Broadcast as chat message
                    await manager.broadcast_all(room_id, {
                        "type": "chat",
                        "payload": {"playerId": player_id, "text": payload.get("text", "")},
                    })

            elif msg_type == "choose_word":
                _cancel_choosing_timer(room_id)
                room = game_engine.choose_word(room_id, payload.get("word", ""))
                if room:
                    # Send hint to non-drawers
                    hint = game_engine.get_word_hint(room)
                    await manager.broadcast_all(room_id, {
                        "type": "new_turn",
                        "payload": {
                            "drawerId": room.players[room.current_drawer_index].id,
                            "roundNum": room.current_round,
                        },
                    })
                    await manager.broadcast(room_id, {
                        "type": "word_hint",
                        "payload": {"hint": hint},
                    }, exclude=player_id)
                    # Send actual word to drawer
                    await manager.send_to(room_id, player_id, {
                        "type": "your_word",
                        "payload": {"word": room.current_word},
                    })
                    # Start drawing countdown
                    _start_drawing_timer(room_id)

            elif msg_type == "start_game":
                room = game_engine.start_game(room_id)
                if room:
                    drawer = room.players[room.current_drawer_index]
                    await manager.broadcast_all(room_id, {
                        "type": "game_started",
                        "payload": {
                            "drawerId": drawer.id,
                            "round": room.current_round,
                            "settings": room.settings.model_dump(),
                        },
                    })
                    await manager.send_to(room_id, drawer.id, {
                        "type": "word_choices",
                        "payload": {"words": room.word_choices},
                    })
                    # Start choosing countdown
                    _start_choosing_timer(room_id)

            elif msg_type == "update_settings":
                room = room_manager.update_settings(room_id, **payload)
                if room:
                    await manager.broadcast_all(room_id, {
                        "type": "settings_updated",
                        "payload": room.settings.model_dump(),
                    })

            elif msg_type == "request_hint":
                # Hints now handled by drawing timer — no-op for backwards compat
                pass

            elif msg_type == "next_turn":
                _cancel_drawing_timer(room_id)
                _cancel_choosing_timer(room_id)
                _cancel_round_over_timer(room_id)
                room = game_engine.next_turn(room_id)
                if room:
                    if room.phase == GamePhase.GAME_OVER:
                        leaderboard = sorted(
                            [{"id": p.id, "nickname": p.nickname, "score": p.score}
                             for p in room.players],
                            key=lambda x: x["score"], reverse=True,
                        )
                        await manager.broadcast_all(room_id, {
                            "type": "game_over",
                            "payload": {"leaderboard": leaderboard},
                        })
                    else:
                        drawer = room.players[room.current_drawer_index]
                        await manager.broadcast_all(room_id, {
                            "type": "new_round",
                            "payload": {"drawerId": drawer.id, "round": room.current_round},
                        })
                        await manager.send_to(room_id, drawer.id, {
                            "type": "word_choices",
                            "payload": {"words": room.word_choices},
                        })
                        # Start choosing countdown
                        _start_choosing_timer(room_id)

    except WebSocketDisconnect:
        manager.disconnect(room_id, player_id)
        room = room_manager.remove_player(player_id)
        if room:
            await manager.broadcast_all(room_id, {
                "type": "player_left",
                "payload": {"playerId": player_id},
            })
            # If game is active and one player remains, end game and declare winner.
            await _end_if_last_player(room_id)
        else:
            # Room removed (last player left) — ensure timers are cleaned up.
            _cancel_choosing_timer(room_id)
            _cancel_drawing_timer(room_id)
            _cancel_round_over_timer(room_id)
