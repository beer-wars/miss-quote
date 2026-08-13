"""
Reading and writing what a server is doing, from a message rather than a file.

The file decides what a deployment starts as; this decides what it is doing
right now. Nothing here is written down, so a restart goes back to the file —
which is the point. A room that wants the fines off for an evening should not
have to earn a ConfigMap edit, and an evening should not be able to change what
the next one starts as.

Two kinds of thing answer to the same command, because they are the same
question to whoever is typing it: the tools a server has switched on, and
whether the open session is being written down. What they have in common is
that both are a switch, both are per server, and both are somebody's business
to change without redeploying.

The wording is deliberately flat. A reply to a typed command is read once, in a
channel, next to whatever else was being said — so it says what is true now and
stops.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import yaml

from miss_quote.config import FALSE_VALUES, TRUE_VALUES
from miss_quote.tools.runner import ToolState

# Whether the open session is on the record. Not a tool — it belongs to the
# channel rather than to the server — but it is a switch somebody flips for the
# same reasons and in the same breath, so it answers to the same command.
TRANSCRIBING = "transcribing"

ON = "on"
OFF = "off"

# What separates a tool from one of its own settings: `quotes.backoff_seconds`.
# Split once from the left, since a tool name may carry a dash but never a dot.
PATH_SEPARATOR = "."

# How a listing is laid out. A code block rather than a table, because Discord
# renders a table as a wall and a code block as columns.
FENCE = "```"
COLUMN_GAP = 2
NOTE_PREFIX = "  "


@dataclass(frozen=True)
class Row:
    """One switch, as a listing shows it."""

    name: str
    on: bool
    note: str = ""

    @property
    def state(self) -> str:
        return ON if self.on else OFF


def parse_path(text: str) -> tuple[str, str | None]:
    """
    What somebody named, as a target and possibly one of its settings.

    `quotes` is the tool; `quotes.backoff_seconds` is one thing about it. A
    trailing dot names no setting rather than an empty one, which is what makes
    `quotes.` the same request as `quotes` instead of a lookup that cannot
    match.
    """
    target, separator, key = text.strip().partition(PATH_SEPARATOR)

    if not separator or not key.strip():
        return target.strip(), None

    return target.strip(), key.strip()


def switch(text: str) -> bool | None:
    """
    A value read as on or off, or nothing if it is not one of those.

    The same spellings the environment and the config file already accept, so
    `on`, `true`, `yes` and `1` all mean what somebody typing them meant. None
    rather than False for anything else: a value that is not a switch is a
    setting's value, and reading `600` as "off" would be the worst possible way
    to be wrong about it.
    """
    normalized = text.strip().lower()

    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False

    return None


def value(text: str) -> Any:
    """
    A setting's value, read the way the config file would read it.

    Through the YAML parser rather than a bespoke one, so a number is a number,
    `true` is a boolean, and `[one, two]` is a list — the same spellings that
    work in the file, which is the file somebody is overriding. Anything it
    cannot parse comes back as the text as typed, which is what a bare string
    is anyway.
    """
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return text


def listing(rows: Sequence[Row]) -> str:
    """
    Every switch and its state, as a block somebody can read down.

    Padded to the longest name rather than to a fixed width, so a deployment
    that only ever adds shorter tool names never grows a column of spaces.
    """
    if not rows:
        return "Nothing here can be switched on or off."

    names = max(len(row.name) for row in rows) + COLUMN_GAP

    # The state column is padded too, so that the notes beside `on` and `off`
    # start in the same place. Otherwise the shorter word drags its note a
    # character left and the block stops reading as columns.
    states = max(len(row.state) for row in rows)

    lines = [
        f"{row.name.ljust(names)}{row.state.ljust(states)}"
        f"{NOTE_PREFIX + row.note if row.note else ''}"
        for row in rows
    ]

    return f"{FENCE}\n" + "\n".join(line.rstrip() for line in lines) + f"\n{FENCE}"


def row_for(state: ToolState) -> Row:
    """
    One tool, as a listing shows it.

    A name the registry does not answer to is listed rather than dropped: it is
    in the file, somebody meant something by it, and a list that quietly lacks
    it is how a typo survives a restart.
    """
    if not state.known:
        return Row(state.name, on=False, note="no such tool")

    if state.changed:
        return Row(state.name, on=state.on, note=f"file says {_state(state.configured)}")

    return Row(state.name, on=state.on)


def _state(on: bool) -> str:
    return ON if on else OFF
