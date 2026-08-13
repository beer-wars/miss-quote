import pytest

from miss_quote.utils import duration

# What a suffix is worth, checked against arithmetic rather than against the
# module's own constants, so a unit quietly redefined is a failure here.
A_MINUTE = 60.0
AN_HOUR = 60 * A_MINUTE
A_DAY = 24 * AN_HOUR
A_WEEK = 7 * A_DAY
A_MILLISECOND = 0.001


@pytest.mark.parametrize(
    ("written", "seconds"),
    [
        ("500ms", 500 * A_MILLISECOND),
        ("30s", 30.0),
        ("5m", 5 * A_MINUTE),
        ("2h", 2 * AN_HOUR),
        ("90d", 90 * A_DAY),
        ("2w", 2 * A_WEEK),
    ],
)
def test_every_unit_is_read_as_itself(written: str, seconds: float) -> None:
    assert duration.parse(written) == seconds


def test_units_compound_into_one_span() -> None:
    """A window nobody has a round unit for is still one line."""
    assert duration.parse("1h30m") == AN_HOUR + 30 * A_MINUTE


def test_compounded_units_may_be_spaced_apart() -> None:
    """Somebody who did not know the format writes it this way, and means it."""
    assert duration.parse("1h 30m") == duration.parse("1h30m")


def test_a_span_may_be_fractional() -> None:
    assert duration.parse("1.5h") == AN_HOUR + 30 * A_MINUTE


def test_case_and_surrounding_space_do_not_matter() -> None:
    assert duration.parse("  45S  ") == 45.0


def test_milliseconds_are_not_read_as_minutes() -> None:
    """`ms` and `m` share a letter, and the longer one has to win."""
    assert duration.parse("500ms") < duration.parse("500m")


def test_a_bare_number_is_seconds() -> None:
    """What every one of these settings is, written without thinking about it."""
    assert duration.parse(30) == 30.0
    assert duration.parse("30") == 30.0
    assert duration.parse(1.5) == 1.5


@pytest.mark.parametrize("written", ["forever", "never", "FOREVER"])
def test_the_keywords_are_a_span_of_nothing(written: str) -> None:
    assert duration.parse(written) == duration.NEVER


def test_zero_is_a_span_of_nothing() -> None:
    assert duration.parse(0) == duration.NEVER
    assert duration.parse("0") == duration.NEVER


def test_a_negative_span_is_read_as_one() -> None:
    """The other way of saying a reaper is off, and the sign covers the whole."""
    assert duration.parse("-1d") == -A_DAY
    assert duration.parse("-1h30m") == -(AN_HOUR + 30 * A_MINUTE)
    assert duration.parse(-1) == -1.0


@pytest.mark.parametrize(
    "written", ["s", "5y", "5m30", "", "   ", "a moment", "soon", "1..5h"]
)
def test_anything_unreadable_is_refused(written: str) -> None:
    with pytest.raises(ValueError):
        duration.parse(written)


@pytest.mark.parametrize("written", [True, False])
def test_a_boolean_is_refused_and_says_what_to_write(written: bool) -> None:
    """
    YAML reads `off` and `no` as booleans before this ever sees them, so a file
    turning a window off that way arrives here as one. Refused rather than
    coerced: `float(True)` is a second, which is nobody's meaning.
    """
    with pytest.raises(ValueError, match="forever"):
        duration.parse(written)


def test_a_trailing_number_takes_the_whole_value_with_it() -> None:
    """Scanning for groups alone would read this as five minutes and move on."""
    with pytest.raises(ValueError):
        duration.parse("5m30")


@pytest.mark.parametrize(
    ("seconds", "said"),
    [
        (90 * A_DAY, "90 days"),
        (A_DAY, "1 day"),
        (AN_HOUR, "1 hour"),
        (AN_HOUR + 30 * A_MINUTE, "1.5 hours"),
        (5 * A_MINUTE, "5 minutes"),
        (30.0, "30 seconds"),
        (500 * A_MILLISECOND, "500 milliseconds"),
    ],
)
def test_a_span_reads_back_in_the_largest_unit_it_fills(
    seconds: float, said: str
) -> None:
    assert duration.spoken(seconds) == said


@pytest.mark.parametrize("seconds", [duration.NEVER, -A_DAY])
def test_a_span_of_nothing_reads_back_as_forever(seconds: float) -> None:
    assert duration.spoken(seconds) == "forever"
