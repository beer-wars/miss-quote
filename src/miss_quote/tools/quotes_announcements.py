"""
Announcements for a won round, written by the model rather than shipped.

The `quotes` tool says one sentence when somebody names a title, and what it
ships with is six endings slotted into one template. Six is enough to be a joke
and not enough to be a joke twice, so a server may also ask the model for whole
announcements of its own: complete sentences, each naming who won and what they
won, drawn on alongside the shipped wordings rather than in place of them.

Generated once per process and held, which is the whole shape of this module.
A catalogue is asked for at startup and never asked for again; the tool draws a
handful from it on a clock and renders those. The model is the expensive thing
in the pipeline and the one nobody can queue behind, so it is spent in one burst
while nothing is happening and left alone thereafter.

Asked for in batches. `settings.llm.max_output_tokens` is a thousand-odd by
default and fifty announcements do not fit in it — the completion would be cut
off mid-sentence, and what came back would be a list with a fragment on the end.
Several small requests cost a little more in round trips and are the difference
between a full catalogue and a truncated one.

**Nothing here raises on what the model said.** A sentence that will not
interpolate is dropped and logged, where the same sentence in a config file
stops the deployment; see `tools.quotes._checked` for the other half of that
distinction. An operator who wrote a stray brace has made a mistake worth
refusing to start over. A model that wrote one has had an ordinary afternoon,
and the tool has five other things to say.

What the model is told is prose, so it lives with the rest of the prose in
`resources/prompts.yaml` rather than in this file. What is here is the batching,
the deduplication, and the rules for which of what came back can be said.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from miss_quote.llm.client import CompletionError, complete
from miss_quote.summary.prompts import instruction
from miss_quote.utils.logging import get_logger
from miss_quote.utils.phrases import normalized

logger = get_logger(__name__)

# What a generated announcement may interpolate. These are the fields
# `tools.quotes` fills when it says one, and they are named here rather than
# imported from there because that module imports this one.
# `test_quotes_announcements` holds the two to agreement, and the shipped brief
# to spelling them out.
USER_FIELD = "user"
CREDITS_FIELD = "credits"
REMARK_FIELD = "remark"

USER_PLACEHOLDER = f"{{{USER_FIELD}}}"
CREDITS_PLACEHOLDER = f"{{{CREDITS_FIELD}}}"
REMARK_PLACEHOLDER = f"{{{REMARK_FIELD}}}"

# What a candidate is checked against, standing in for the two things only the
# moment knows. The wording is thrown away; that it interpolated at all is the
# whole of what is being asked.
PROBE = {USER_FIELD: "someone", CREDITS_FIELD: "1 credit"}

# How many to ask for at once. Small enough that a batch fits inside any output
# budget worth pointing this at, large enough that a catalogue of fifty is a
# handful of requests rather than fifty.
BATCH_SIZE = 10

# How many batches a catalogue may take, as a multiple of the batches it would
# take if every candidate were usable. A model that repeats itself or writes
# unusable lines would otherwise be asked again for as long as the process runs.
ATTEMPT_ALLOWANCE = 2

# How a model writes a list when it was asked not to. Stripped rather than
# rejected: the sentence after the bullet is usually fine, and the synthesizer
# is the only thing that would have minded.
LIST_MARKER = re.compile(r"^\s*(?:[-*•–]|\d+[.)])\s*")

# A line the model wrapped in quotation marks, which the synthesizer reads as
# nothing and a reader sees as a mistake.
SURROUNDING_QUOTES = '"“”‘’\''

LINE_SEPARATOR = "\n"
EXAMPLE_SEPARATOR = "\n"

# The brief the model works from, under this name in the shipped prompts. Read at
# import rather than per request: it does not change while the process runs, and
# a file that does not carry it is a broken image worth failing on the way up.
INSTRUCTION_NAME = "quotes_announcements"

INSTRUCTION = instruction(INSTRUCTION_NAME)


async def catalogue(size: int, examples: Sequence[str]) -> tuple[str, ...]:
    """
    Ask the model for announcements, and return the ones that will work.

    Returns fewer than asked for rather than failing, including none at all:
    every caller has something else to say, and a catalogue that came back half
    full is half a catalogue rather than a broken server.

    Batched, deduplicated, and given up on when a batch adds nothing. A model
    asked the same question five times answers it the same way more than once,
    and one that has run out of ideas will go on not having them for as long as
    it is asked.
    """
    wanted = max(size, 0)
    if not wanted:
        return ()

    # Keyed on the sentence as it would be matched rather than as it was
    # written, so two that differ by a comma are the one announcement they
    # sound like.
    kept: dict[str, str] = {}
    allowance = -(-wanted // BATCH_SIZE) * ATTEMPT_ALLOWANCE

    for _ in range(allowance):
        if len(kept) >= wanted:
            break

        try:
            said = await complete(INSTRUCTION, _request(BATCH_SIZE, examples))
        except CompletionError as exc:
            logger.error("Could not generate announcements: %s", exc)
            break

        fresh = {
            normalized(text): text
            for text in _candidates(said)
            if normalized(text) not in kept
        }

        # A batch that added nothing is a model with nothing left to add, and
        # asking it again is a round trip for the same answer.
        if not fresh:
            break

        kept.update(fresh)

    written = tuple(kept.values())[:wanted]

    logger.info(
        "Generated %d announcement(s) of the %d asked for.", len(written), wanted
    )

    return written


def _request(count: int, examples: Sequence[str]) -> str:
    """
    What the model is given to work from: how many, and the house style.

    The shipped endings go in as examples rather than being described, because
    the register is the hard part of the brief and one line of it is worth a
    paragraph about tone. They are endings rather than whole announcements and
    are labelled as such, so the model takes the voice and not the shape.
    """
    asked = f"Write {count} announcements."

    if not examples:
        return asked

    return LINE_SEPARATOR.join(
        (
            asked,
            "",
            "For the voice, here is how the bot's existing endings read. Match "
            "their register, not their grammar — yours are whole sentences.",
            "",
            EXAMPLE_SEPARATOR.join(examples),
        )
    )


def _candidates(said: str) -> tuple[str, ...]:
    """Every line of a completion that could be said out loud as it stands."""
    written = (_tidied(line) for line in said.split(LINE_SEPARATOR))

    return tuple(text for text in written if text and _usable(text))


def _tidied(line: str) -> str:
    """One line with the list it was written in taken off it."""
    return LIST_MARKER.sub("", line).strip().strip(SURROUNDING_QUOTES).strip()


def _usable(text: str) -> bool:
    """
    Whether an announcement can be said, saying why in the log where it cannot.

    Three ways it cannot. It may carry no name or no amount, which is a sentence
    that awards nothing to nobody. It may ask for `{remark}`, which is the
    shipped template's field and would multiply what has to be rendered by every
    ending the server has. Or it may not interpolate at all, which is a stray
    brace and the reason this is checked before anybody wins anything.
    """
    if REMARK_PLACEHOLDER in text:
        logger.debug("Dropping a generated announcement asking for a remark: %r", text)
        return False

    if USER_PLACEHOLDER not in text or CREDITS_PLACEHOLDER not in text:
        logger.debug("Dropping a generated announcement naming nobody: %r", text)
        return False

    try:
        text.format(**PROBE)
    except (IndexError, KeyError, ValueError) as exc:
        logger.debug("Dropping a generated announcement that will not fill: %r (%s)", text, exc)
        return False

    return True
