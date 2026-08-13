"""
A span of time, however it was written down.

A setting that names a window is written the way people say one: `30s`, `5m`,
`90d`, `1h30m`. Units compound and are summed, so a span nobody has a round
unit for is still one line, and the unit lives in the value rather than in the
key — which means the same setting can be written in whichever unit suits the
deployment, and reads as what it is without anybody doing the arithmetic.

A bare number is seconds. It is what somebody writing a window without thinking
about the format means, and it keeps `0` and `-1` saying what they say
everywhere else.

Everything comes back as float seconds, because that is what the things reading
these want: `asyncio.sleep`, `asyncio.timeout`, and a subtraction of two
`time.monotonic()` readings all take one. Nothing here returns a `timedelta`.

Its own module rather than a helper inside `config`, for the same reason
`utils.slugs` is: the tools parse their own per-server windows and cannot import
the module that imports them.
"""

from __future__ import annotations

import re
from typing import Any

from miss_quote.utils.stems import plural

# No window at all. Every duration that can be turned off is turned off by a
# value at or below this, so the readings differ — a retention of nothing keeps
# forever, a backoff of nothing answers every time — while the test does not.
NEVER = 0.0

MILLISECONDS_PER_SECOND = 1000
SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60
HOURS_PER_DAY = 24
DAYS_PER_WEEK = 7

SECOND = 1.0
MILLISECOND = SECOND / MILLISECONDS_PER_SECOND
MINUTE = SECOND * SECONDS_PER_MINUTE
HOUR = MINUTE * MINUTES_PER_HOUR
DAY = HOUR * HOURS_PER_DAY
WEEK = DAY * DAYS_PER_WEEK

# What each suffix is worth. `ms` and `m` share a first letter, which the
# alternation below resolves by trying the longer one first; written the other
# way round, `500ms` reads as 500 minutes with a stray `s` after it.
UNITS: dict[str, float] = {
    "ms": MILLISECOND,
    "s": SECOND,
    "m": MINUTE,
    "h": HOUR,
    "d": DAY,
    "w": WEEK,
}

# Turning one off, in words. `off` and `no` are deliberately absent: YAML reads
# both as booleans, so a file saying `retention: off` hands this a `False` that
# never reaches a keyword lookup. These two are safe in every YAML spelling.
FOREVER = ("forever", "never")

SIGN = "-"

_UNIT_ALTERNATION = "|".join(sorted(UNITS, key=len, reverse=True))
_NUMBER = r"\d+(?:\.\d+)?"

# One `<number><unit>` group, and the whole string as a run of them. Matched
# separately so that a value is validated end to end before any of it counts:
# scanning for groups alone would read `5m30` as five minutes and quietly drop
# the rest. Groups may be spaced apart, since `1h 30m` is how somebody who did
# not know the format would write it and it can only mean the one thing.
GROUP = re.compile(rf"({_NUMBER})({_UNIT_ALTERNATION})")
WRITTEN = re.compile(rf"(?:{_NUMBER}(?:{_UNIT_ALTERNATION})\s*)+")

# How `spoken` reads a span back. Weeks are missing on purpose: they divide
# badly into the spans anybody actually configures, and a retention of ninety
# days reported as "12.9 weeks" is worse than the number that was written.
SCALES: tuple[tuple[float, str], ...] = (
    (DAY, "day"),
    (HOUR, "hour"),
    (MINUTE, "minute"),
    (SECOND, "second"),
    (MILLISECOND, "millisecond"),
)

# A count of units reads as a whole number wherever it is one, so an hour is
# "1 hour" rather than "1.0 hours", and a span between two units keeps enough of
# itself to be recognized.
COUNT_FORMAT = "%g"
SINGULAR = 1


def parse(value: Any) -> float:
    """
    A span of time in seconds, from whatever the file said.

    Raises `ValueError` on anything unreadable. What that costs depends on who
    asked: a deployment-wide setting reports the complaint and falls back to its
    default, while a window one server wrote into a tool's config stops that
    tool from starting. Both are in the callers rather than here.
    """
    if isinstance(value, bool):
        raise ValueError(
            f"{value!r} is not a duration; to turn one off write "
            f"{_or(FOREVER)}, 0, or a negative span like '-1d'"
        )

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().lower()
    if not text:
        raise ValueError("a duration cannot be blank")

    if text in FOREVER:
        return NEVER

    negative = text.startswith(SIGN)
    if negative:
        text = text[len(SIGN):].lstrip()

    total = _written(text) if WRITTEN.fullmatch(text) else _bare(text)
    return -total if negative else total


def spoken(seconds: float) -> str:
    """
    A span read back the way a log line wants it.

    The largest unit there is at least one of, so a retention reports in days
    and a fade in milliseconds without either being told which it is. Used for
    the lines that report what was pruned and why, where the alternative is a
    number whose unit the reader has to know already.
    """
    if seconds <= NEVER:
        return FOREVER[0]

    for scale, name in SCALES:
        if seconds >= scale:
            return _counted(seconds / scale, name)

    return _counted(seconds / MILLISECOND, SCALES[-1][1])


def _written(text: str) -> float:
    """A run of `<number><unit>` groups, summed."""
    return sum(float(count) * UNITS[unit] for count, unit in GROUP.findall(text))


def _bare(text: str) -> float:
    """
    A number with no unit on it, which is seconds.

    Every one of these settings was a number of seconds before it was a
    duration, and somebody writing one without a suffix means the thing they
    have always meant.
    """
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(
            f"{text!r} is not a duration; write one like '30s', '5m', or "
            f"'1h30m', a bare number of seconds, or {_or(FOREVER)}"
        ) from exc


def _counted(count: float, name: str) -> str:
    return f"{COUNT_FORMAT % count} {name if count == SINGULAR else plural(name)}"


def _or(words: tuple[str, ...]) -> str:
    """The keywords, listed the way a complaint offers them."""
    return " or ".join(repr(word) for word in words)
