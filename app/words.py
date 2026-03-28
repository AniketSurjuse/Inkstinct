import random
import json
from pathlib import Path

_WORDS: list[dict] = []


def _load_words():
    global _WORDS
    words_path = Path(__file__).parent.parent / "data" / "words.json"
    if words_path.exists():
        with open(words_path, "r") as f:
            _WORDS = json.load(f)
            # print(len(_WORDS), "words loaded")
    else:
        # Fallback word list
        _WORDS = [
            {"word": w, "difficulty": "easy"}
            for w in [
                "cat", "dog", "house", "tree", "car", "sun", "moon", "fish",
                "bird", "book", "chair", "table", "phone", "ball", "hat",
                "shoe", "apple", "banana", "pizza", "cake", "flower", "star",
                "heart", "cloud", "rain", "snow", "fire", "water", "mountain",
                "beach", "guitar", "piano", "drum", "robot", "rocket",
                "airplane", "bicycle", "train", "bridge", "castle",
                "dinosaur", "elephant", "giraffe", "penguin", "butterfly",
                "spider", "snake", "turtle", "whale", "dolphin",
                "ice cream", "hamburger", "popcorn", "sandwich", "spaghetti",
                "telescope", "umbrella", "volcano", "waterfall", "rainbow",
            ]
        ]


def get_random_words(count: int = 3, difficulty: str | None = None) -> list[str]:
    if not _WORDS:
        _load_words()

    pool = _WORDS
    if difficulty and isinstance(pool[0], dict):
        pool = [w for w in _WORDS if w.get("difficulty") == difficulty] or _WORDS

    chosen = random.sample(list(pool), min(count, len(pool)))

    # Support both plain strings and {"word": ...} dicts
    return [w["word"] if isinstance(w, dict) else w for w in chosen]


# Eager load
_load_words()
