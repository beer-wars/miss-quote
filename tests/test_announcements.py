"""What the model is asked for, what comes back, and which of it is fit to say."""

import pytest

from miss_quote.llm import announcements
from miss_quote.llm.announcements import (
    BATCH_SIZE,
    CREDITS_FIELD,
    CREDITS_PLACEHOLDER,
    REMARK_FIELD,
    REMARK_PLACEHOLDER,
    USER_FIELD,
    USER_PLACEHOLDER,
    _candidates,
    _request,
    _tidied,
    _usable,
    catalogue,
)
from miss_quote.llm.client import CompletionError

# A sentence carrying both placeholders, which is the whole of what a usable
# announcement has to be.
GOOD = "Correct! {user}, that is {credits} for knowing that instantly."

NONE_AT_ALL = 0
EXAMPLES = ("quoting along at home.",)

LINE_SEPARATOR = "\n"


def _numbered(index: int) -> str:
    """One distinct usable announcement, so a batch can be told from the last."""
    return f"{{user}} takes {{credits}} for reason number {index}."


def _answering(monkeypatch, *replies: str) -> list[str]:
    """
    Answer for the endpoint, one reply per call, keeping what was asked.

    The last reply stands for every call after it, so a test about batching says
    what a batch holds rather than how many batches there will be.
    """
    asked: list[str] = []

    async def _complete(instruction, text):
        asked.append(text)

        return replies[min(len(asked) - 1, len(replies) - 1)]

    monkeypatch.setattr(announcements, "complete", _complete)

    return asked


def _batch(start: int, count: int = BATCH_SIZE) -> str:
    """One completion's worth of distinct announcements, as the model writes them."""
    return LINE_SEPARATOR.join(_numbered(index) for index in range(start, start + count))


# ── reading what came back ────────────────────────


def test_a_bulleted_line_is_the_sentence_after_the_bullet():
    """The synthesizer is the only thing that would have minded."""
    assert _tidied(f"- {GOOD}") == GOOD


def test_a_numbered_line_is_the_sentence_after_the_number():
    assert _tidied(f"3. {GOOD}") == GOOD


def test_a_quoted_line_loses_its_quotation_marks():
    """Read aloud they are nothing, and read on a page they are a mistake."""
    assert _tidied(f'"{GOOD}"') == GOOD


def test_blank_lines_are_not_candidates():
    said = LINE_SEPARATOR.join((GOOD, "", "   "))

    assert _candidates(said) == (GOOD,)


def test_every_usable_line_is_a_candidate():
    said = LINE_SEPARATOR.join((f"1. {GOOD}", _numbered(1)))

    assert _candidates(said) == (GOOD, _numbered(1))


# ── which of it can be said ───────────────────────


def test_an_announcement_carrying_both_placeholders_is_usable():
    assert _usable(GOOD)


def test_an_announcement_naming_nobody_is_dropped():
    """A sentence awarding nothing to nobody is not an announcement."""
    assert not _usable("That was a good one, frankly.")


def test_an_announcement_with_no_amount_is_dropped():
    assert not _usable("Correct, {user}, and well done.")


def test_an_announcement_asking_for_a_remark_is_dropped():
    """
    That is the shipped template's field.

    One that asked for it would have to be rendered against every ending the
    server has, which is the cross-product this feature exists to stay out of.
    """
    assert not _usable(f"{GOOD} {REMARK_PLACEHOLDER}")


def test_an_announcement_that_will_not_interpolate_is_dropped():
    """A stray brace is why this is checked before anybody wins anything."""
    assert not _usable("{user} takes {credits} for {being clever}")


# ── asking for a catalogue ────────────────────────


async def test_a_catalogue_is_asked_for_in_batches(monkeypatch):
    """
    A budget of a thousand-odd tokens does not hold fifty announcements.

    Asked for ten at a time, so what comes back is a full catalogue rather than
    one with a fragment on the end of it.
    """
    asked = _answering(monkeypatch, _batch(0), _batch(BATCH_SIZE))

    written = await catalogue(BATCH_SIZE * 2, EXAMPLES)

    assert len(asked) == 2
    assert len(written) == BATCH_SIZE * 2


async def test_a_catalogue_holds_no_more_than_it_was_asked_for(monkeypatch):
    _answering(monkeypatch, _batch(0))

    written = await catalogue(3, EXAMPLES)

    assert len(written) == 3


async def test_a_model_repeating_itself_is_deduplicated(monkeypatch):
    """Asked the same question twice, a model answers it the same way more than once."""
    _answering(monkeypatch, LINE_SEPARATOR.join((GOOD, GOOD, _numbered(1))))

    written = await catalogue(BATCH_SIZE, EXAMPLES)

    assert written == (GOOD, _numbered(1))


async def test_two_announcements_differing_only_in_punctuation_are_one(monkeypatch):
    """Matched as they would be heard rather than as they were typed."""
    _answering(monkeypatch, LINE_SEPARATOR.join((GOOD, GOOD.replace(",", ""))))

    assert len(await catalogue(BATCH_SIZE, EXAMPLES)) == 1


async def test_a_batch_that_adds_nothing_ends_the_asking(monkeypatch):
    """A model with nothing left to add goes on not having any for as long as it is asked."""
    asked = _answering(monkeypatch, GOOD)

    written = await catalogue(BATCH_SIZE * 3, EXAMPLES)

    assert written == (GOOD,)
    assert len(asked) == 2


async def test_a_catalogue_nobody_asked_for_is_empty(monkeypatch):
    asked = _answering(monkeypatch, _batch(0))

    assert await catalogue(NONE_AT_ALL, EXAMPLES) == ()
    assert asked == []


async def test_an_endpoint_that_refuses_yields_what_had_arrived(monkeypatch):
    """
    Half a catalogue rather than a broken server.

    Every caller has something else to say, so a generation that failed partway
    costs the announcements it had not written yet and nothing else.
    """
    replies = [_batch(0), None]

    async def _complete(instruction, text):
        reply = replies.pop(0) if replies else None

        if reply is None:
            raise CompletionError("the model is on fire")

        return reply

    monkeypatch.setattr(announcements, "complete", _complete)

    assert len(await catalogue(BATCH_SIZE * 2, EXAMPLES)) == BATCH_SIZE


async def test_an_endpoint_that_refuses_at_once_yields_nothing(monkeypatch):
    async def _complete(instruction, text):
        raise CompletionError("no endpoint is configured")

    monkeypatch.setattr(announcements, "complete", _complete)

    assert await catalogue(BATCH_SIZE, EXAMPLES) == ()


def test_the_request_carries_the_shipped_endings_as_examples():
    """The register is the hard part of the brief, and one line of it beats a paragraph."""
    written = _request(BATCH_SIZE, EXAMPLES)

    assert EXAMPLES[0] in written
    assert str(BATCH_SIZE) in written


def test_the_instruction_names_both_placeholders():
    """A model that was never told the spelling writes something else."""
    assert USER_PLACEHOLDER in announcements.INSTRUCTION
    assert CREDITS_PLACEHOLDER in announcements.INSTRUCTION


# ── the contract with the tool that fills them ────


def test_the_fields_agree_with_the_tool_that_fills_them():
    """
    `tools.quotes` imports this module, so it cannot be imported back.

    The two name the same three fields independently for that reason, and this
    is what holds them to it: a rename on either side that is not made on the
    other is an announcement that stops interpolating in front of everybody.
    """
    from miss_quote.tools import quotes

    assert (USER_FIELD, CREDITS_FIELD, REMARK_FIELD) == (
        quotes.USER_FIELD,
        quotes.CREDITS_FIELD,
        quotes.REMARK_FIELD,
    )
