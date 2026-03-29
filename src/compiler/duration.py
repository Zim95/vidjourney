"""
Calculate scene durations based on paragraph word count.
A narrator reads ~150 words per minute.

The total narration time is distributed across scene phases:
- spawn: elements appear
- body: arrows move, resources displayed
- close: elements disappear
"""

WORDS_PER_MINUTE = 150.0
MIN_SCENE_DURATION = 4.0
SPAWN_TIME = 1.0
CLOSE_TIME = 1.0


def reading_time(text: str) -> float:
    """Estimate reading time in seconds for a text block."""
    words = len(text.split())
    seconds = (words / WORDS_PER_MINUTE) * 60.0
    return max(MIN_SCENE_DURATION, seconds)


def scene_durations(text: str, num_arrows: int = 0) -> dict[str, float]:
    """
    Calculate duration for each phase of a scene.

    Returns:
        spawn: time for elements to appear
        body: time for arrows/resource display (the main content)
        per_arrow: time allocated per arrow movement
        close: time for elements to disappear
        total: full scene duration
    """
    total = reading_time(text)

    spawn = SPAWN_TIME
    close = CLOSE_TIME
    body = max(1.0, total - spawn - close)

    per_arrow = body / max(num_arrows, 1)

    return {
        "spawn": round(spawn, 1),
        "body": round(body, 1),
        "per_arrow": round(per_arrow, 1),
        "close": round(close, 1),
        "total": round(total, 1),
    }
