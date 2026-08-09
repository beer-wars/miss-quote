"""
Which hours are written down, and when the room is off the record.

A deployment says so as a list of windows in `settings.transcripts.schedule`:

    schedule:
      - Wed 17:00-00:00

It is read once per session, when the bot joins, and decides whether that whole
session is on the record. A window is when an evening may *start* being written
down, not how long it may go on for: a session that opens inside one keeps
writing until everybody disconnects, however far past the end of the window that
is. An evening does not stop being the evening at midnight, and a transcript cut
off mid-conversation is worse than either keeping the whole thing or none of it.

A window is also what says that several sessions were one sitting. It recurs
weekly and a session belongs to a particular one of its occurrences — the
Wednesday evening it opened in, rather than Wednesday evenings in general — which
is what `summary` folds a room's comings and goings together by. See
`Window.occurrence`.

Only the writing down is scheduled. Speech is still transcribed outside a window
and still handed to the tools that read one utterance at a time, so a fine is
still announced and still counted; what the schedule decides is whether the
session reaches disk, and so whether anything is left for a summary to be
written from or for somebody to go back to later.

Saying nothing captures everything, which is what every deployment did before
this existed. Saying something that will not parse captures nothing: a schedule
is written by somebody narrowing what is recorded, and a typo in it must not
widen it back out — an evening not written down can be had again, and one that
should not have been written down cannot be taken back.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

DAYS_IN_A_WEEK = 7
ONE_DAY = timedelta(days=1)

# Monday first, so an index into this is what `datetime.weekday()` answers.
DAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

# Written out rather than taken from `calendar`, whose names are the locale's:
# a config file is read the same way wherever the pod happens to be running.
ABBREVIATION_LENGTH = 3

DAYS = {name: index for index, name in enumerate(DAY_NAMES)} | {
    name[:ABBREVIATION_LENGTH]: index for index, name in enumerate(DAY_NAMES)
}

DAY_GROUP = "day"
START_GROUP = "start"
END_GROUP = "end"

ENTRY = re.compile(
    rf"^(?P<{DAY_GROUP}>[a-z]+)\s+"
    rf"(?P<{START_GROUP}>\d{{1,2}}:\d{{2}})\s*-\s*(?P<{END_GROUP}>\d{{1,2}}:\d{{2}})$"
)

TIME_FORMAT = "%H:%M"

# The end of a day, spelled the way somebody who has just written `17:00-` tends
# to finish the line. It means the same instant as `00:00`, which is where the
# wrap rule already puts it; accepting both costs a line and saves a schedule
# that would otherwise be thrown out, and a thrown-out schedule records nothing.
END_OF_DAY = "24:00"
MIDNIGHT = time(0, 0)

WINDOW_SEPARATOR = ", "
PROBLEM_SEPARATOR = "; "

SCHEDULE_SETTING = "settings.transcripts.schedule"


@dataclass(frozen=True)
class Occurrence:
    """
    One window as it happened, on one date: the stretch of clock itself.

    A window is a weekly recurrence and says nothing about which week. This is a
    particular one of them, which is what answers whether two sessions were the
    same sitting — and so what the `summary` tool folds together into one
    account. See `Window.occurrence`.
    """

    start: datetime
    end: datetime

    def covers(self, moment: datetime) -> bool:
        """
        Whether a moment falls inside this occurrence.

        Half-open on the same terms as the window it came from, so a session
        opening exactly as one occurrence ends belongs to the next.
        """
        return self.start <= moment < self.end


@dataclass(frozen=True)
class Window:
    """One stretch of one day of the week, as a half-open interval."""

    day: int
    start: time
    end: time

    @property
    def wraps(self) -> bool:
        """
        Whether the window runs past midnight into the following day.

        An end at or before the start is the only way to say so — a window is
        one line naming one day, and `Wed 17:00-00:00` has to mean Wednesday
        evening rather than nothing at all. An end equal to the start therefore
        reads as a whole day, which is the same rule taken to its end.
        """
        return self.end <= self.start

    def covers(self, moment: datetime) -> bool:
        """
        Whether a moment falls inside this window.

        Half-open: the start is in and the end is out, so two windows written
        back to back — `Wed 17:00-00:00` and `Thu 00:00-02:00` — meet without
        overlapping and without leaving a minute between them.
        """
        weekday = moment.weekday()
        clock = moment.time()

        if not self.wraps:
            return weekday == self.day and self.start <= clock < self.end

        if weekday == self.day:
            return clock >= self.start

        return weekday == (self.day + 1) % DAYS_IN_A_WEEK and clock < self.end

    def occurrence(self, moment: datetime) -> Occurrence | None:
        """
        The particular stretch of clock this window is around a moment, if it
        covers it at all.

        Dated from the moment rather than from the calendar, so the answer is
        the occurrence a session belongs to however long ago it was and whichever
        side of midnight it landed. A window that wraps is dated from the day its
        start falls on, which for a moment in the small hours is the day before.
        """
        if not self.covers(moment):
            return None

        day = moment.date()

        if not self.wraps:
            return Occurrence(_at(day, self.start, moment), _at(day, self.end, moment))

        if moment.weekday() == self.day:
            return Occurrence(
                _at(day, self.start, moment), _at(day + ONE_DAY, self.end, moment)
            )

        return Occurrence(
            _at(day - ONE_DAY, self.start, moment), _at(day, self.end, moment)
        )

    def describe(self) -> str:
        return (
            f"{DAY_NAMES[self.day][:ABBREVIATION_LENGTH].capitalize()} "
            f"{self.start.strftime(TIME_FORMAT)}-{self.end.strftime(TIME_FORMAT)}"
        )


@dataclass(frozen=True)
class Schedule:
    """
    The windows a deployment asked for, and what it got wrong asking.

    `configured` is what tells "no schedule" apart from "a schedule with nothing
    left in it", which are opposite answers: the first captures everything and
    the second captures nothing. They cannot be told apart from `windows` alone,
    and getting them the wrong way round records an evening nobody agreed to.
    """

    windows: tuple[Window, ...] = ()
    configured: bool = False
    problems: tuple[str, ...] = ()

    @classmethod
    def parse(cls, entries: Iterable[str], where: str = SCHEDULE_SETTING) -> "Schedule":
        """
        Read a list of windows, keeping the ones that made sense.

        `where` names the setting the entries came from, since the same list can
        be written per deployment or per channel and a complaint that names the
        wrong one sends somebody to the wrong part of the file.

        An entry that will not parse is dropped and reported rather than raised
        on, on the config file's rule that a typo should not stop the pod. It is
        dropped rather than ignored: what a bad entry costs is capture, which is
        the safe direction to be wrong in.
        """
        windows: list[Window] = []
        problems: list[str] = []
        configured = False

        for entry in entries:
            written = str(entry).strip()
            if not written:
                continue

            configured = True
            window = _window(written, where, problems)
            if window is not None:
                windows.append(window)

        return cls(
            windows=tuple(windows),
            configured=configured,
            problems=tuple(problems),
        )

    @property
    def empty(self) -> bool:
        """Whether a schedule was asked for and nothing survived reading it."""
        return self.configured and not self.windows

    def covers(self, moment: datetime) -> bool:
        """
        Whether a session opening at this moment is one to write down.

        Asked once, of the moment a session opened, and never again: a window is
        when an evening may start being recorded rather than how long it may run
        for. The moment is expected in the timezone the transcripts are stamped
        with, since that is the clock somebody writing `Wed 17:00` was reading.
        """
        if not self.configured:
            return True

        return any(window.covers(moment) for window in self.windows)

    def occurrence(self, moment: datetime) -> Occurrence | None:
        """
        The stretch of clock the window covering a moment is, if one does.

        What `covers` answers is whether a session is written down; this answers
        which sitting it is part of, which is how the `summary` tool knows that
        four sessions on a Wednesday evening are one thing to write about.

        Nothing where no schedule was asked for. A deployment that keeps
        everything has no windows to be inside, and reading that as one window
        with no ends would make every session a channel ever had one sitting.

        Windows that overlap are taken together, from the earliest start to the
        latest end, so a moment covered by two is in one stretch rather than in
        whichever of them the file happened to list first. Windows written back
        to back do not overlap — the interval is half-open — and stay separate,
        which is what `Wed 17:00-00:00` and `Thu 00:00-02:00` are asking for.
        """
        if not self.configured:
            return None

        covering = [
            occurrence
            for occurrence in (window.occurrence(moment) for window in self.windows)
            if occurrence is not None
        ]

        if not covering:
            return None

        return Occurrence(
            min(occurrence.start for occurrence in covering),
            max(occurrence.end for occurrence in covering),
        )

    def describe(self) -> str:
        return WINDOW_SEPARATOR.join(window.describe() for window in self.windows)


# No schedule was asked for, so everything is written down.
ALWAYS = Schedule()

# A schedule was asked for and nothing in it covers anything, so nothing is.
# What a room absent from `monitored_channels` gets, and what a schedule nothing
# could be read out of falls back to; see `Schedule.empty`.
NEVER = Schedule(configured=True)


def _at(day: date, clock: time, moment: datetime) -> datetime:
    """One time of day on one date, on the clock the moment was read against."""
    return datetime.combine(day, clock, tzinfo=moment.tzinfo)


def _window(entry: str, where: str, problems: list[str]) -> Window | None:
    """One window, or nothing and a complaint naming the entry it came from."""
    matched = ENTRY.match(entry.casefold())
    if matched is None:
        problems.append(
            f"'{where}' has an entry it cannot read, {entry!r}. "
            f"An entry is a day and a range, as in 'Wed 17:00-00:00'."
        )
        return None

    day = DAYS.get(matched.group(DAY_GROUP))
    if day is None:
        problems.append(
            f"'{where}' entry {entry!r} does not start with a day of "
            f"the week. Those are "
            f"{WINDOW_SEPARATOR.join(name.capitalize() for name in DAY_NAMES)}, "
            f"in full or abbreviated to {ABBREVIATION_LENGTH} letters."
        )
        return None

    start = _time(matched.group(START_GROUP))
    end = _time(matched.group(END_GROUP))

    if start is None or end is None:
        problems.append(
            f"'{where}' entry {entry!r} has a time that is not one. "
            f"Times are 24-hour, as in '17:00', and midnight is '00:00' or "
            f"'{END_OF_DAY}'."
        )
        return None

    return Window(day=day, start=start, end=end)


def _time(written: str) -> time | None:
    """
    A time of day, or nothing if it is not one.

    `24:00` is read as midnight, which the wrap rule then places at the end of
    the day rather than the start; see `Window.wraps`.
    """
    if written == END_OF_DAY:
        return MIDNIGHT

    try:
        return datetime.strptime(written, TIME_FORMAT).time()
    except ValueError:
        return None
