from miss_quote.bot import settings
from miss_quote.tools.runner import ToolState

TOOL = "quotes"
SETTING = "backoff_seconds"


def _state(
    name: str = TOOL,
    known: bool = True,
    configured: bool = True,
    on: bool = True,
    built: bool = True,
) -> ToolState:
    return ToolState(
        name=name, known=known, configured=configured, on=on, built=built
    )


# ── paths ─────────────────────────────────────────


def test_a_bare_name_is_the_tool_and_nothing_else():
    assert settings.parse_path(TOOL) == (TOOL, None)


def test_a_dotted_name_is_the_tool_and_one_of_its_settings():
    assert settings.parse_path(f"{TOOL}.{SETTING}") == (TOOL, SETTING)


def test_a_name_with_a_dash_in_it_survives():
    """Tool names carry dashes; only the dot means anything here."""
    assert settings.parse_path("verbal-morality.repeat_seconds") == (
        "verbal-morality",
        "repeat_seconds",
    )


def test_a_trailing_dot_names_no_setting():
    """`quotes.` is somebody who stopped typing, not a lookup that cannot match."""
    assert settings.parse_path(f"{TOOL}.") == (TOOL, None)


def test_a_path_is_taken_without_the_spaces_around_it():
    assert settings.parse_path(f"  {TOOL} . {SETTING}  ") == (TOOL, SETTING)


def test_only_the_first_dot_splits():
    """Everything after the tool is the setting, however it is spelled."""
    assert settings.parse_path("summary.monitored_channels.general") == (
        "summary",
        "monitored_channels.general",
    )


# ── switches ──────────────────────────────────────


def test_the_spellings_of_on_are_all_on():
    assert all(settings.switch(said) for said in ("on", "true", "yes", "1", "ON"))


def test_the_spellings_of_off_are_all_off():
    assert not any(
        settings.switch(said) is not False for said in ("off", "false", "no", "0")
    )


def test_something_that_is_not_a_switch_is_not_read_as_one():
    """Reading `600` as 'off' would be the worst available way to be wrong."""
    assert settings.switch("600") is None
    assert settings.switch("fiddlestick") is None


# ── values ────────────────────────────────────────


def test_a_number_is_a_number():
    assert settings.value("600") == 600


def test_a_decimal_is_a_number():
    assert settings.value("0.25") == 0.25


def test_a_boolean_is_a_boolean():
    assert settings.value("true") is True


def test_a_list_is_a_list():
    """The same spelling that works in the file, which is what is being overridden."""
    assert settings.value("[fiddlestick, poppycock]") == ["fiddlestick", "poppycock"]


def test_text_is_text():
    assert settings.value("a recap") == "a recap"


def test_something_yaml_cannot_read_comes_back_as_it_was_typed():
    assert settings.value("{unclosed") == "{unclosed"


# ── listings ──────────────────────────────────────


def test_a_listing_shows_the_state_beside_the_name():
    said = settings.listing([settings.Row(TOOL, on=True)])

    assert TOOL in said
    assert settings.ON in said


def test_a_listing_lines_the_states_up():
    """Padded to the longest name, so it reads as two columns."""
    lines = settings.listing(
        [settings.Row("tts", on=True), settings.Row("verbal-morality", on=False)]
    ).splitlines()

    assert lines[1].index(settings.ON) == lines[2].index(settings.OFF)


def test_a_listing_lines_the_notes_up_across_different_states():
    """`on` is a character shorter than `off` and must not drag its note left."""
    lines = settings.listing(
        [
            settings.Row("tts", on=True, note="file says off"),
            settings.Row("quotes", on=False, note="file says on"),
        ]
    ).splitlines()

    assert lines[1].index("file says") == lines[2].index("file says")


def test_a_row_without_a_note_carries_no_trailing_spaces():
    (line,) = settings.listing([settings.Row(TOOL, on=True)]).splitlines()[1:2]

    assert line == line.rstrip()


def test_a_listing_is_fenced_so_discord_leaves_the_spacing_alone():
    said = settings.listing([settings.Row(TOOL, on=True)])

    assert said.startswith(settings.FENCE)
    assert said.endswith(settings.FENCE)


def test_a_listing_of_nothing_says_so_rather_than_showing_an_empty_block():
    assert settings.FENCE not in settings.listing([])


# ── rows ──────────────────────────────────────────


def test_a_tool_doing_what_the_file_said_is_shown_without_a_note():
    assert settings.row_for(_state()).note == ""


def test_a_tool_switched_off_says_what_the_file_says():
    """So somebody can tell what a restart would give them back."""
    row = settings.row_for(_state(configured=True, on=False))

    assert not row.on
    assert settings.ON in row.note


def test_a_tool_switched_on_says_the_file_had_it_off():
    row = settings.row_for(_state(configured=False, on=True))

    assert row.on
    assert settings.OFF in row.note


def test_a_name_nothing_answers_to_is_shown_as_such():
    """It is in the file and somebody meant something by it."""
    row = settings.row_for(_state(name="mistyped", known=False, on=False))

    assert "no such tool" in row.note

