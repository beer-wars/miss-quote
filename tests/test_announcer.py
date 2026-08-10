from datetime import datetime, timedelta, timezone
from itertools import count
from pathlib import Path

import discord
import pytest

import miss_quote.bot.announcer as announcer_module
from miss_quote.bot.announcer import (
    EMBED_DESCRIPTION_LIMIT,
    EMBED_TOTAL_LIMIT,
    MESSAGE_LIMIT,
    DiscordAnnouncer,
    paged,
    split,
)
from miss_quote.config import FileConfig, ServerConfig

SERVER_ID = 123456789012345678
ALIAS = "first-server"
CHANNEL = "session-summaries"

TITLE = "general-voice — Wed 26 Jul 2026, 20:14"
OTHER_TITLE = "other-voice — Wed 26 Jul 2026, 20:14"

SUMMARY = "They argued about the rules for an hour and nobody won."
FULLER = SUMMARY + " Then they argued about the argument."

BOT_ID = 42
SOMEBODY_ELSE = 43

SERVER_ERROR = 500

OPENED = datetime(2026, 7, 26, 20, 14, tzinfo=timezone.utc)

# Comfortably over one message's worth of embed, so an account built from it has
# to be more than one message however the pieces fall.
LONG_PARAGRAPHS = 60
WORDS_PER_PARAGRAPH = 40


class Author:
    def __init__(self, identifier: int) -> None:
        self.id = identifier


class Message:
    """One message in a fake channel, editable and deletable like the real one."""

    def __init__(self, channel, embeds, author_id: int, identifier: int) -> None:
        self.channel = channel
        self.embeds = list(embeds)
        self.author = Author(author_id)
        self.id = identifier
        self.jump_url = f"https://discord.test/{identifier}"
        self.edits = 0

    @property
    def title(self) -> str | None:
        return self.embeds[0].title if self.embeds else None

    @property
    def text(self) -> str:
        return "".join(embed.description or "" for embed in self.embeds)

    async def edit(self, embeds) -> None:
        self.embeds = list(embeds)
        self.edits += 1

    async def delete(self) -> None:
        self.channel.take(self)


class Channel:
    """A text channel that remembers what it holds, in the order it holds it."""

    def __init__(
        self,
        name: str,
        refuses: Exception | None = None,
        unreadable: Exception | None = None,
        accepts: int | None = None,
    ) -> None:
        self.name = name
        self.messages: list[Message] = []
        self.guild = None
        self._refuses = refuses
        self._unreadable = unreadable
        self.accepts = accepts
        self._ids = count(1)

    async def send(self, embeds) -> Message:
        if self._refuses is not None:
            raise self._refuses

        if self.accepts is not None:
            if self.accepts <= 0:
                raise discord.HTTPException(_response(SERVER_ERROR), "later")

            self.accepts -= 1

        message = Message(self, embeds, BOT_ID, next(self._ids))
        self.messages.append(message)

        return message

    def take(self, message: Message) -> None:
        self.messages = [held for held in self.messages if held.id != message.id]

    def left(self, embeds, author_id: int = BOT_ID) -> Message:
        """A message somebody else put here — a previous process, or a person."""
        message = Message(self, embeds, author_id, next(self._ids))
        self.messages.append(message)

        return message

    def history(self, after=None, oldest_first: bool = True, limit: int = 100):
        if self._unreadable is not None:
            raise self._unreadable

        ordered = list(self.messages)
        if not oldest_first:
            ordered.reverse()

        async def stream():
            for message in ordered[:limit]:
                yield message

        return stream()


class Guild:
    def __init__(self, *channels: Channel) -> None:
        self.text_channels = list(channels)
        self.me = Author(BOT_ID)

        for channel in channels:
            channel.guild = self


@pytest.fixture(autouse=True)
def known_server(monkeypatch):
    """One server in the mounted file, so an alias resolves back to an ID."""
    monkeypatch.setattr(
        announcer_module,
        "file_cfg",
        FileConfig(
            path=Path("/config/config.yaml"),
            servers={SERVER_ID: ServerConfig(alias=ALIAS, users={}, tools={})},
            problems=(),
            found=True,
        ),
    )


def _announcer(guild: Guild | None) -> DiscordAnnouncer:
    return DiscordAnnouncer(lambda server_id: guild)


def _long(paragraphs: int = LONG_PARAGRAPHS) -> str:
    return "\n\n".join(
        f"Paragraph {number}. " + "word " * WORDS_PER_PARAGRAPH
        for number in range(paragraphs)
    )


def _embed(title: str | None, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description)


# ── putting one up ────────────────────────────


async def test_a_named_channel_gets_the_account():
    channel = Channel(CHANNEL)

    assert await _announcer(Guild(channel)).revise(
        ALIAS, CHANNEL, TITLE, SUMMARY, OPENED
    )

    assert len(channel.messages) == 1
    assert channel.messages[0].title == TITLE
    assert channel.messages[0].text == SUMMARY


async def test_only_the_named_channel_gets_it():
    wanted = Channel(CHANNEL)
    other = Channel("general")

    await _announcer(Guild(other, wanted)).revise(
        ALIAS, CHANNEL, TITLE, SUMMARY, OPENED
    )

    assert len(wanted.messages) == 1
    assert other.messages == []


async def test_a_name_that_points_nowhere_is_reported(caplog):
    posted = await _announcer(Guild(Channel("general"))).revise(
        ALIAS, CHANNEL, TITLE, SUMMARY, OPENED
    )

    assert not posted
    assert CHANNEL in caplog.text


async def test_a_server_that_is_not_configured_posts_nothing():
    assert not await _announcer(Guild(Channel(CHANNEL))).revise(
        "somewhere-else", CHANNEL, TITLE, SUMMARY, OPENED
    )


async def test_resolve_answers_without_sending_anything():
    """What `prewarm` asks, so a typo is a startup line rather than a lost summary."""
    channel = Channel(CHANNEL)
    announcer = _announcer(Guild(channel))

    assert announcer.resolve(ALIAS, CHANNEL) is channel
    assert announcer.resolve(ALIAS, "nowhere") is None
    assert channel.messages == []


async def test_an_account_of_nothing_leaves_what_is_there_alone():
    channel = Channel(CHANNEL)
    announcer = _announcer(Guild(channel))

    await announcer.revise(ALIAS, CHANNEL, TITLE, SUMMARY, OPENED)

    assert not await announcer.revise(ALIAS, CHANNEL, TITLE, "   ", OPENED)
    assert channel.messages[0].text == SUMMARY


async def test_a_missing_permission_is_a_failure_that_names_the_channel(caplog):
    """Both permissions, because an embed is not message content.

    A channel that has been taking accounts for months can start refusing on
    nothing but a release, and a log line naming only Send Messages would send
    somebody to check the one that was never missing.
    """
    channel = Channel(CHANNEL, refuses=discord.Forbidden(_response(403), "nope"))

    assert not await _announcer(Guild(channel)).revise(
        ALIAS, CHANNEL, TITLE, SUMMARY, OPENED
    )
    assert "Send Messages" in caplog.text
    assert "Embed Links" in caplog.text


async def test_a_channel_that_cannot_be_read_says_which_permission(caplog):
    channel = Channel(CHANNEL, unreadable=discord.Forbidden(_response(403), "nope"))

    assert await _announcer(Guild(channel)).revise(
        ALIAS, CHANNEL, TITLE, SUMMARY, OPENED
    )
    assert "Read Message History" in caplog.text


async def test_a_server_error_is_a_failure():
    channel = Channel(
        CHANNEL, refuses=discord.HTTPException(_response(SERVER_ERROR), "later")
    )

    assert not await _announcer(Guild(channel)).revise(
        ALIAS, CHANNEL, TITLE, SUMMARY, OPENED
    )


async def test_half_an_account_is_not_a_success():
    """A partial post reads as a whole one, so it is reported as a failure."""
    channel = Channel(
        CHANNEL, refuses=discord.HTTPException(_response(SERVER_ERROR), "later")
    )

    assert not await _announcer(Guild(channel)).revise(
        ALIAS, CHANNEL, TITLE, _long(), OPENED
    )


async def test_a_run_that_broke_partway_comes_back_down():
    """The first half of an evening under a heading saying it is the whole of it."""
    channel = Channel(CHANNEL, accepts=1)

    assert not await _announcer(Guild(channel)).revise(
        ALIAS, CHANNEL, TITLE, _long(), OPENED
    )

    assert channel.messages == []


async def test_a_broken_move_leaves_the_account_where_it_was():
    channel = Channel(CHANNEL)
    announcer = _announcer(Guild(channel))

    await announcer.revise(ALIAS, CHANNEL, TITLE, SUMMARY, OPENED)
    origin = channel.messages[0]

    channel.accepts = 1

    assert not await announcer.revise(ALIAS, CHANNEL, TITLE, _long(), OPENED)

    assert channel.messages == [origin]
    assert origin.text == SUMMARY


# ── rewriting the one that is up ──────────────


async def test_a_second_revise_rewrites_the_message_it_already_has():
    """The regression: a sitting that seals twice leaves one account, not two."""
    channel = Channel(CHANNEL)
    announcer = _announcer(Guild(channel))

    await announcer.revise(ALIAS, CHANNEL, TITLE, SUMMARY, OPENED)
    await announcer.revise(ALIAS, CHANNEL, TITLE, FULLER, OPENED)

    assert len(channel.messages) == 1
    assert channel.messages[0].text == FULLER
    assert channel.messages[0].edits == 1


async def test_a_different_evening_is_a_different_message():
    channel = Channel(CHANNEL)
    announcer = _announcer(Guild(channel))

    await announcer.revise(ALIAS, CHANNEL, TITLE, SUMMARY, OPENED)
    await announcer.revise(ALIAS, CHANNEL, OTHER_TITLE, FULLER, OPENED)

    assert [message.title for message in channel.messages] == [TITLE, OTHER_TITLE]


async def test_a_deleted_message_is_posted_again_rather_than_reported():
    channel = Channel(CHANNEL)
    announcer = _announcer(Guild(channel))

    await announcer.revise(ALIAS, CHANNEL, TITLE, SUMMARY, OPENED)
    held = channel.messages[0]

    async def gone(embeds):
        raise discord.NotFound(_response(404), "tidied away")

    held.edit = gone
    channel.take(held)

    assert await announcer.revise(ALIAS, CHANNEL, TITLE, FULLER, OPENED)
    assert [message.text for message in channel.messages] == [FULLER]


# ── outgrowing the message it is in ───────────


async def test_an_account_that_outgrows_its_message_moves_and_leaves_a_pointer():
    channel = Channel(CHANNEL)
    announcer = _announcer(Guild(channel))

    await announcer.revise(ALIAS, CHANNEL, TITLE, SUMMARY, OPENED)
    origin = channel.messages[0]

    assert await announcer.revise(ALIAS, CHANNEL, TITLE, _long(), OPENED)

    assert channel.messages[0] is origin
    assert origin.title == TITLE

    run = channel.messages[1:]
    assert len(run) > 1
    assert run[0].jump_url in origin.text
    assert "Paragraph 0." in run[0].text


async def test_the_moved_run_holds_the_whole_account_in_order():
    channel = Channel(CHANNEL)
    announcer = _announcer(Guild(channel))
    account = _long()

    await announcer.revise(ALIAS, CHANNEL, TITLE, SUMMARY, OPENED)
    await announcer.revise(ALIAS, CHANNEL, TITLE, account, OPENED)

    # Per embed rather than by gluing them together: a cut consumes the break it
    # lands on, so joining the pieces back up with nothing fuses the words on
    # either side of one.
    words = [
        word
        for message in channel.messages[1:]
        for embed in message.embeds
        for word in (embed.description or "").split()
    ]

    assert words == account.split()


async def test_a_pointer_is_repointed_rather_than_chained():
    """An evening that keeps growing leaves one breadcrumb, not a trail of them."""
    channel = Channel(CHANNEL)
    announcer = _announcer(Guild(channel))

    await announcer.revise(ALIAS, CHANNEL, TITLE, SUMMARY, OPENED)
    origin = channel.messages[0]

    await announcer.revise(ALIAS, CHANNEL, TITLE, _long(), OPENED)
    superseded = channel.messages[1]

    await announcer.revise(ALIAS, CHANNEL, TITLE, _long(LONG_PARAGRAPHS + 10), OPENED)

    titled = [message for message in channel.messages if message.title == TITLE]

    # The one it began in and the one it lives in now, and nothing between them
    # still claiming to be this evening.
    assert titled == [origin, channel.messages[1]]
    assert channel.messages[1].jump_url in origin.text
    assert superseded not in channel.messages


async def test_an_account_that_shrinks_back_drops_what_it_no_longer_needs():
    channel = Channel(CHANNEL)
    announcer = _announcer(Guild(channel))

    await announcer.revise(ALIAS, CHANNEL, TITLE, SUMMARY, OPENED)
    await announcer.revise(ALIAS, CHANNEL, TITLE, _long(), OPENED)

    grew = len(channel.messages)

    await announcer.revise(ALIAS, CHANNEL, TITLE, FULLER, OPENED)

    assert len(channel.messages) < grew
    assert channel.messages[-1].text == FULLER


# ── finding one a previous process left ───────


async def test_an_account_left_by_a_previous_process_is_rewritten():
    channel = Channel(CHANNEL)
    channel.left([_embed(TITLE, SUMMARY)])

    assert await _announcer(Guild(channel)).revise(
        ALIAS, CHANNEL, TITLE, FULLER, OPENED
    )

    assert len(channel.messages) == 1
    assert channel.messages[0].text == FULLER


async def test_the_newest_message_carrying_the_title_is_the_live_one():
    """An account that already moved left an older message under the same title."""
    channel = Channel(CHANNEL)
    pointer = channel.left([_embed(TITLE, "moved")])
    live = channel.left([_embed(TITLE, SUMMARY)])

    await _announcer(Guild(channel)).revise(ALIAS, CHANNEL, TITLE, FULLER, OPENED)

    assert pointer.text == "moved"
    assert live.text == FULLER


async def test_somebody_elses_message_is_not_adopted():
    channel = Channel(CHANNEL)
    theirs = channel.left([_embed(TITLE, SUMMARY)], author_id=SOMEBODY_ELSE)

    await _announcer(Guild(channel)).revise(ALIAS, CHANNEL, TITLE, FULLER, OPENED)

    assert theirs.text == SUMMARY
    assert len(channel.messages) == 2


async def test_a_channel_holding_nothing_of_ours_gets_a_fresh_account():
    channel = Channel(CHANNEL)
    channel.left([_embed(OTHER_TITLE, "a different evening")])

    await _announcer(Guild(channel)).revise(ALIAS, CHANNEL, TITLE, SUMMARY, OPENED)

    assert [message.title for message in channel.messages] == [OTHER_TITLE, TITLE]


async def test_a_channel_that_will_not_be_read_is_posted_to_anyway(caplog):
    """A duplicate in a tidy channel beats an account that goes nowhere."""
    channel = Channel(
        CHANNEL, unreadable=discord.HTTPException(_response(SERVER_ERROR), "later")
    )

    assert await _announcer(Guild(channel)).revise(
        ALIAS, CHANNEL, TITLE, SUMMARY, OPENED
    )
    assert len(channel.messages) == 1


async def test_the_search_is_bounded_by_when_the_evening_began():
    channel = Channel(CHANNEL)
    seen = {}

    history = channel.history

    def watched(after=None, oldest_first=True, limit=100):
        seen["after"] = after
        seen["oldest_first"] = oldest_first

        return history(after=after, oldest_first=oldest_first, limit=limit)

    channel.history = watched
    channel.left([_embed(TITLE, SUMMARY)])

    await _announcer(Guild(channel)).revise(ALIAS, CHANNEL, TITLE, FULLER, OPENED)

    assert seen["after"] == OPENED
    assert seen["oldest_first"] is False


async def test_an_adopted_account_is_not_looked_for_twice():
    """A restart costs one read of the channel, not one per seal after it."""
    channel = Channel(CHANNEL)
    channel.left([_embed(TITLE, SUMMARY)])
    announcer = _announcer(Guild(channel))
    reads = []

    history = channel.history

    def counted(**kwargs):
        reads.append(kwargs)

        return history(**kwargs)

    channel.history = counted

    await announcer.revise(ALIAS, CHANNEL, TITLE, FULLER, OPENED)
    await announcer.revise(ALIAS, CHANNEL, TITLE, SUMMARY, OPENED)

    assert len(reads) == 1


# ── cutting it up ─────────────────────────────


def test_an_account_that_fits_is_one_embed_in_one_message():
    pages = paged(SUMMARY, TITLE)

    assert len(pages) == 1
    assert len(pages[0]) == 1
    assert pages[0][0].title == TITLE
    assert pages[0][0].description == SUMMARY


def test_only_the_first_embed_of_the_first_message_is_titled():
    pages = paged(_long(), TITLE)

    assert len(pages) > 1
    assert pages[0][0].title == TITLE
    assert all(embed.title is None for embed in pages[0][1:])
    assert all(embed.title is None for page in pages[1:] for embed in page)


def test_every_embed_and_every_message_is_within_its_ceiling():
    pages = paged(_long(), TITLE)

    for page in pages:
        assert all(
            len(embed.description) <= EMBED_DESCRIPTION_LIMIT for embed in page
        )
        assert sum(len(embed.description) for embed in page) <= (
            EMBED_TOTAL_LIMIT - len(TITLE)
        )


def test_a_short_body_is_one_piece():
    assert split(SUMMARY) == [SUMMARY]


def test_an_empty_body_is_no_pieces():
    assert split("   ") == []


def test_splitting_prefers_a_paragraph_break():
    first = "a" * (MESSAGE_LIMIT - 100)
    second = "b" * 200

    assert split(f"{first}\n\n{second}") == [first, second]


def test_splitting_falls_back_to_a_line_then_a_word():
    lines = "\n".join("c" * 100 for _ in range(30))
    words = " ".join("d" * 10 for _ in range(300))

    for body in (lines, words):
        pieces = split(body)
        assert all(len(piece) <= MESSAGE_LIMIT for piece in pieces)
        assert "".join(pieces.copy()).replace(" ", "").replace("\n", "") == body.replace(
            " ", ""
        ).replace("\n", "")


def test_an_unbroken_run_is_cut_at_the_limit():
    """Not something prose does, but it must not be left to be refused."""
    body = "e" * (MESSAGE_LIMIT * 2 + 10)

    pieces = split(body)

    assert all(len(piece) <= MESSAGE_LIMIT for piece in pieces)
    assert "".join(pieces) == body


def _response(status: int):
    """The minimum discord.py wants to build one of its HTTP errors around."""

    class Response:
        def __init__(self) -> None:
            self.status = status
            self.reason = "because"

    return Response()
