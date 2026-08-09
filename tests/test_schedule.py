from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from miss_quote.transcript.schedule import ALWAYS, Schedule

TIMEZONE = "America/Los_Angeles"
ZONE = ZoneInfo(TIMEZONE)

# 2026-07-29 is a Wednesday, which is what every window below is written against.
WEDNESDAY = "2026-07-29"
THURSDAY = "2026-07-30"
FRIDAY = "2026-07-31"

EVENING = "Wed 17:00-00:00"


def _at(day: str, clock: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=ZONE)


# ── reading a schedule ────────────────────────────


def test_a_window_is_a_day_and_a_range():
    schedule = Schedule.parse([EVENING])

    assert schedule.problems == ()
    assert schedule.configured is True
    assert schedule.describe() == "Wed 17:00-00:00"


@pytest.mark.parametrize(
    "written",
    ["Wed 17:00-19:00", "wed 17:00-19:00", "WEDNESDAY 17:00-19:00", "Wed 17:00 - 19:00"],
)
def test_a_day_is_read_however_it_was_written(written):
    """An ASR is not writing these; a person is, in whatever case they type in."""
    schedule = Schedule.parse([written])

    assert schedule.problems == ()
    assert schedule.covers(_at(WEDNESDAY, "18:00"))


def test_several_windows_are_kept_in_the_order_they_were_written():
    schedule = Schedule.parse(["Wed 17:00-00:00", "Sat 12:00-14:00"])

    assert schedule.describe() == "Wed 17:00-00:00, Sat 12:00-14:00"


# ── what a window covers ──────────────────────────


def test_the_start_is_in_and_the_end_is_out():
    """Half-open, so two windows written back to back neither overlap nor gap."""
    schedule = Schedule.parse(["Wed 17:00-19:00"])

    assert not schedule.covers(_at(WEDNESDAY, "16:59"))
    assert schedule.covers(_at(WEDNESDAY, "17:00"))
    assert schedule.covers(_at(WEDNESDAY, "18:59"))
    assert not schedule.covers(_at(WEDNESDAY, "19:00"))


def test_a_window_ending_at_midnight_runs_to_the_end_of_its_day():
    schedule = Schedule.parse([EVENING])

    assert not schedule.covers(_at(WEDNESDAY, "16:59"))
    assert schedule.covers(_at(WEDNESDAY, "17:00"))
    assert schedule.covers(_at(WEDNESDAY, "23:59"))
    assert not schedule.covers(_at(THURSDAY, "00:00"))


def test_a_window_ending_after_midnight_runs_into_the_next_day():
    """An end before the start is the only way one line can say 'past midnight'."""
    schedule = Schedule.parse(["Wed 21:00-02:00"])

    assert schedule.covers(_at(WEDNESDAY, "23:59"))
    assert schedule.covers(_at(THURSDAY, "01:59"))
    assert not schedule.covers(_at(THURSDAY, "02:00"))
    assert not schedule.covers(_at(THURSDAY, "21:00"))


def test_an_end_equal_to_the_start_is_a_whole_day():
    schedule = Schedule.parse(["Wed 00:00-00:00"])

    assert schedule.covers(_at(WEDNESDAY, "00:00"))
    assert schedule.covers(_at(WEDNESDAY, "23:59"))
    assert not schedule.covers(_at(THURSDAY, "00:00"))


def test_the_end_of_a_day_may_be_written_as_twenty_four():
    """The same instant as `00:00`, and the way somebody finishing the line writes it."""
    schedule = Schedule.parse(["Wed 17:00-24:00"])

    assert schedule.problems == ()
    assert schedule.covers(_at(WEDNESDAY, "23:59"))
    assert not schedule.covers(_at(THURSDAY, "00:00"))


def test_a_day_the_schedule_says_nothing_about_is_not_covered():
    schedule = Schedule.parse([EVENING])

    assert not schedule.covers(_at(FRIDAY, "18:00"))


# ── which occurrence of a window ──────────────────


def test_an_occurrence_is_the_stretch_of_clock_around_the_moment():
    """The Wednesday evening a session opened in, not Wednesday evenings at large."""
    occurrence = Schedule.parse(["Wed 17:00-19:00"]).occurrence(_at(WEDNESDAY, "18:00"))

    assert occurrence.start == _at(WEDNESDAY, "17:00")
    assert occurrence.end == _at(WEDNESDAY, "19:00")


def test_an_occurrence_of_a_wrapping_window_runs_into_the_next_day():
    occurrence = Schedule.parse([EVENING]).occurrence(_at(WEDNESDAY, "23:40"))

    assert occurrence.start == _at(WEDNESDAY, "17:00")
    assert occurrence.end == _at(THURSDAY, "00:00")


def test_a_moment_in_the_small_hours_belongs_to_the_evening_before_it():
    """Which is what makes a session that opened at 23:40 and one at 00:20 one sitting."""
    schedule = Schedule.parse(["Wed 17:00-02:00"])

    assert schedule.occurrence(_at(THURSDAY, "00:20")) == schedule.occurrence(
        _at(WEDNESDAY, "23:40")
    )


def test_a_whole_day_window_is_one_occurrence():
    occurrence = Schedule.parse(["Wed 17:00-17:00"]).occurrence(_at(THURSDAY, "09:00"))

    assert occurrence.start == _at(WEDNESDAY, "17:00")
    assert occurrence.end == _at(THURSDAY, "17:00")


def test_a_moment_outside_every_window_is_in_no_occurrence():
    assert Schedule.parse([EVENING]).occurrence(_at(FRIDAY, "18:00")) is None


def test_no_schedule_has_no_occurrences():
    """
    A deployment that keeps everything has no windows to be inside.

    Reading that as one window with no ends would make every session a channel
    ever had part of the same sitting.
    """
    assert ALWAYS.occurrence(_at(FRIDAY, "04:00")) is None


def test_overlapping_windows_are_one_occurrence():
    """Otherwise which one a session is in depends on the order of the file."""
    occurrence = Schedule.parse(["Wed 17:00-20:00", "Wed 19:00-23:00"]).occurrence(
        _at(WEDNESDAY, "19:30")
    )

    assert occurrence.start == _at(WEDNESDAY, "17:00")
    assert occurrence.end == _at(WEDNESDAY, "23:00")


def test_windows_written_back_to_back_stay_separate():
    """Half-open, so only one of them covers any instant and neither is widened."""
    schedule = Schedule.parse(["Wed 17:00-00:00", "Thu 00:00-02:00"])

    assert schedule.occurrence(_at(WEDNESDAY, "23:00")).end == _at(THURSDAY, "00:00")
    assert schedule.occurrence(_at(THURSDAY, "00:30")).start == _at(THURSDAY, "00:00")


def test_an_occurrence_takes_its_start_and_not_its_end():
    """Half-open on the same terms as the window it came from."""
    occurrence = Schedule.parse(["Wed 17:00-19:00"]).occurrence(_at(WEDNESDAY, "18:00"))

    assert occurrence.covers(_at(WEDNESDAY, "17:00"))
    assert not occurrence.covers(_at(WEDNESDAY, "19:00"))
    assert not occurrence.covers(_at(WEDNESDAY, "16:59"))


# ── saying nothing, and saying nonsense ───────────


def test_no_schedule_captures_everything():
    """What every deployment did before there was a schedule to write."""
    assert ALWAYS.configured is False
    assert ALWAYS.covers(_at(FRIDAY, "04:00"))
    assert Schedule.parse([]).covers(_at(FRIDAY, "04:00"))
    assert Schedule.parse(["", "  "]).covers(_at(FRIDAY, "04:00"))


def test_a_schedule_nothing_could_be_read_from_captures_nothing():
    """
    The safe direction to be wrong in.

    A schedule is written by somebody narrowing what is recorded, so a typo in
    it must not widen it back out: an evening not written down can be had again,
    and one that should not have been written down cannot be taken back.
    """
    schedule = Schedule.parse(["every other tuesday"])

    assert schedule.configured is True
    assert schedule.empty is True
    assert not schedule.covers(_at(WEDNESDAY, "18:00"))
    assert len(schedule.problems) == 1


def test_an_unreadable_entry_is_dropped_and_the_rest_still_apply():
    schedule = Schedule.parse(["Wed 17:00-00:00", "Thurs evening"])

    assert schedule.describe() == "Wed 17:00-00:00"
    assert schedule.covers(_at(WEDNESDAY, "18:00"))
    assert len(schedule.problems) == 1
    assert "Thurs evening" in schedule.problems[0]


@pytest.mark.parametrize(
    "written",
    ["Wen 17:00-19:00", "Wed 25:00-19:00", "Wed 17:60-19:00", "Wed 17:00", "17:00-19:00"],
)
def test_an_entry_that_will_not_parse_is_named_in_the_complaint(written):
    """A complaint that does not quote the line leaves somebody hunting for it."""
    schedule = Schedule.parse([written])

    assert schedule.windows == ()
    assert len(schedule.problems) == 1
    assert written in schedule.problems[0]
