import asyncio
import time
import random
from typing import Optional
from .models import Room, GamePhase
from .words import get_random_words
from . import rooms as room_manager


PERFECT_ROUND_DRAWER_BONUS = 25


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute edit distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def start_game(room_id: str) -> Optional[Room]:
    room = room_manager.get_room(room_id)
    if not room or len(room.players) < 2:
        return None
    room.phase = GamePhase.CHOOSING
    room.current_round = 1
    room.current_drawer_index = 0
    room.strokes = []
    room.hints_given = 0
    room.word_choices = get_random_words(room.settings.word_count)
    for p in room.players:
        p.score = 0
        p.has_guessed = False
    return room


def choose_word(room_id: str, word: str) -> Optional[Room]:
    room = room_manager.get_room(room_id)
    if not room or room.phase != GamePhase.CHOOSING:
        return None
    room.current_word = word
    room.phase = GamePhase.DRAWING
    room.time_left = room.settings.draw_time
    room.strokes = []
    room.hints_given = 0
    room.drawing_started_at = time.time()
    for p in room.players:
        p.has_guessed = False
    return room


def check_guess(room_id: str, player_id: str, text: str) -> tuple[bool, int, bool]:
    """Returns (is_correct, points, is_close)."""
    room = room_manager.get_room(room_id)
    if not room or room.phase != GamePhase.DRAWING or not room.current_word:
        return False, 0, False

    player = next((p for p in room.players if p.id == player_id), None)
    if not player or player.has_guessed:
        return False, 0, False

    # Check if drawer
    drawer = room.players[room.current_drawer_index]
    if player.id == drawer.id:
        return False, 0, False

    guess = text.strip().lower()
    answer = room.current_word.lower()

    if guess == answer:
        # Guesser score: round((500 * (t / s)) * rank_multiplier)
        total_time = max(1, room.settings.draw_time)
        time_ratio = max(0.0, min(1.0, room.time_left / total_time))

        guessed_before = sum(
            1
            for i, p in enumerate(room.players)
            if i != room.current_drawer_index and p.has_guessed
        )
        rank = guessed_before + 1
        rank_multiplier = max(0.5, 1.0 - (0.1 * (rank - 1)))
        points = round((500 * time_ratio) * rank_multiplier)

        player.has_guessed = True
        player.score += points

        # Drawer score increment: (guesser_points / (n-1)) * 0.5
        possible_guessers = max(1, len(room.players) - 1)
        drawer_increment = round((points / possible_guessers) * 0.5)
        drawer.score += drawer_increment

        return True, points, False

    # Close guess: 1 edit distance away (typo detection)
    if len(guess) >= 3 and len(answer) >= 3:
        dist = _levenshtein_distance(guess, answer)
        if dist == 1:
            return False, 0, True

    return False, 0, False


def _effective_hint_count(room: Room) -> int:
    """Return effective hint count after applying short-word guardrails."""
    if not room.current_word:
        return 0

    revealable_len = sum(1 for ch in room.current_word if ch != " ")
    max_hints_by_length = max(0, (revealable_len // 2) - 1)
    return min(room.settings.hints_count, max_hints_by_length)


def _hint_trigger_times(room: Room) -> list[float]:
    """Build elapsed-time thresholds matching requested skribbl-like timing.

    Targets:
    - 1 hint: 50%
    - 2 hints: 40%, 70%
    - 3+ hints: evenly spaced with last at 70%
      (e.g. 3 => 50,60,70 | 4 => 47.5,55,62.5,70)
    """
    effective_hints = _effective_hint_count(room)
    if effective_hints <= 0:
        return []

    total = float(max(1, room.settings.draw_time))

    if effective_hints == 1:
        return [total * 0.5]

    if effective_hints == 2:
        return [total * 0.4, total * 0.7]

    # For 3+ hints, use spacing of 30% / h and anchor the last hint at 70%.
    step = total * (0.3 / effective_hints)
    return [total * 0.7 - step * i for i in range(effective_hints - 1, -1, -1)]


def get_word_hint(room: Room, force_hint_number: int | None = None) -> str:
    """Generate a hint string. If force_hint_number is given, reveal that many hints worth of letters."""
    if not room.current_word:
        return ""

    hint = list("_" * len(room.current_word))
    for i, ch in enumerate(room.current_word):
        if ch == " ":
            hint[i] = " "

    # Determine how many hints can be used and clamp requested hint number.
    hints_to_use = force_hint_number if force_hint_number is not None else room.hints_given
    effective_hints = _effective_hint_count(room)
    hints_to_use = min(hints_to_use, effective_hints)
    if hints_to_use <= 0 or effective_hints <= 0:
        return " ".join(hint)

    # Reveal exactly one new letter per hint, deterministically per room+word.
    indices = [i for i, ch in enumerate(room.current_word) if ch != " "]
    total_revealable = len(indices)
    reveal_count = min(total_revealable, hints_to_use)

    random.seed(room.id + room.current_word)  # Deterministic
    random.shuffle(indices)
    for i in indices[:reveal_count]:
        hint[i] = room.current_word[i]

    return " ".join(hint)


def should_give_hint(room: Room) -> bool:
    """Check if it's time to give a new hint based on elapsed time."""
    if not room.drawing_started_at or not room.current_word:
        return False

    effective_hints = _effective_hint_count(room)
    if room.hints_given >= effective_hints:
        return False

    elapsed = time.time() - room.drawing_started_at
    hint_times = _hint_trigger_times(room)

    if room.hints_given < len(hint_times) and elapsed >= hint_times[room.hints_given]:
        return True
    return False


def give_hint(room_id: str) -> Optional[tuple[Room, str]]:
    """Increment hints_given and return new hint string."""
    room = room_manager.get_room(room_id)
    if not room or room.phase != GamePhase.DRAWING:
        return None

    effective_hints = _effective_hint_count(room)
    if room.hints_given >= effective_hints:
        return None

    room.hints_given += 1
    hint = get_word_hint(room)
    return room, hint


def apply_perfect_round_bonus(room_id: str) -> int:
    """Award a small bonus to the drawer when everyone guesses before timeout."""
    room = room_manager.get_room(room_id)
    if not room or room.phase != GamePhase.DRAWING or not room.players:
        return 0

    drawer = room.players[room.current_drawer_index]
    drawer.score += PERFECT_ROUND_DRAWER_BONUS
    return PERFECT_ROUND_DRAWER_BONUS


def end_round(room_id: str) -> Optional[Room]:
    room = room_manager.get_room(room_id)
    if not room:
        return None
    room.phase = GamePhase.ROUND_OVER
    return room


def next_turn(room_id: str) -> Optional[Room]:
    room = room_manager.get_room(room_id)
    if not room:
        return None

    room.current_drawer_index += 1

    if room.current_drawer_index >= len(room.players):
        room.current_drawer_index = 0
        room.current_round += 1

        if room.current_round > room.settings.rounds:
            room.phase = GamePhase.GAME_OVER
            return room

    room.phase = GamePhase.CHOOSING
    room.word_choices = get_random_words(room.settings.word_count)
    room.current_word = None
    room.strokes = []
    room.hints_given = 0
    room.drawing_started_at = None
    for p in room.players:
        p.has_guessed = False
    return room


def add_stroke(room_id: str, stroke: dict):
    room = room_manager.get_room(room_id)
    if room:
        room.strokes.append(stroke)


def clear_strokes(room_id: str):
    room = room_manager.get_room(room_id)
    if room:
        room.strokes = []


def undo_stroke(room_id: str):
    room = room_manager.get_room(room_id)
    if room and room.strokes:
        room.strokes.pop()
