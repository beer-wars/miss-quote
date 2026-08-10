"""
Keeping one account of an evening in a named text channel.

The other half of `tools.summary`, which knows what an account says and nothing
about where it goes, and the counterpart to `bot.topic` and `bot.ticker`: a topic
is one line under a voice channel's name, a ticker holds one message and rewrites
it as a room talks, and this holds one message per evening and rewrites it as the
evening grows.

**Channels are named rather than identified.** A tool holds a server alias and a
channel name, so a name is what it can ask for, and a name is also what a person
writing the config file has in front of them. The cost is that a channel renamed
on Discord silently stops receiving accounts, and that two categories may hold
channels of the same name — the first match wins. Both are why a name that
resolves to nothing is a warning that says which name it was, and why the tool
that posts checks its channel once at startup instead of at the end of the first
session it summarizes.

**An evening is written about several times.** A room on a capture schedule
empties and refills — people leave, the bot is dragged next door, a pod restarts
— and every one of those seals a session and asks for the account again, each
time covering more of the night than the last. On disk that rewrites one file.
Here it has to rewrite one message, or a channel ends an evening holding four
accounts of it that are indistinguishable from one another at a glance.

**What identifies an account is its title.** The caller makes that stable across
a whole evening by dating it from when the sitting opened rather than from the
seal, and two voice channels posting into one text channel differ in it. A title
that ever stopped being stable would post a second account rather than rewrite
the wrong one, which is the right way to be wrong.

**The channel is the memory.** Which message an account lives in is held in
memory and nowhere else, so a process that went away mid-evening would come back
and post beside what it left. Rather than persist an ID to a file that would have
to be kept in step with a channel somebody may have cleared, a revise that finds
nothing held reads the channel's recent history for its own title — the same
trade `bot.ticker` makes with the pin list. See `_adopted`.

**Everything goes in an embed**, which is what makes rewriting in place work at
all. Discord will not take more than 2000 characters of message content and does
not extend a bot the ceiling it sells to people, but an embed description holds
4096 and one message holds 6000 across its embeds. An evening's account is one
message at almost any length it runs to, and one message is one edit.

Past that it is more than one message, and an account is rewritten in place for
as long as it still fits the messages it is in — three edits to three messages,
and the evening stays where a reader last saw it. What cannot be done is
extending it: the message it would need next is sent to the bottom of the
channel, so a continuation written an hour after the part it continues arrives
under whatever was said in between. An account that outgrows its run is therefore
posted again whole and the old run comes down. See `_replaced`.

**The head of a run is pinned**, which is what makes that affordable. Nothing has
to be left at the old address pointing at the new one, because a reader looks an
account up in the pin list rather than where they last saw it — and deleting a
message unpins it, so a run that is replaced takes its own pin off on the way
out. It is also what tells `bot.ticker` apart from this in a channel they share:
the ticker sweeps pins it left behind and skips anything carrying an embed, which
an account always has and a feed never does.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import discord

from miss_quote.config import file_cfg
from miss_quote.tools.base import MESSAGE_LIMIT
from miss_quote.utils.logging import get_logger

logger = get_logger(__name__)

# Discord's ceilings on embeds. Also the API's numbers. A message may carry ten
# of them, which is a limit nothing here can reach: two descriptions already
# exhaust the per-message budget.
EMBED_DESCRIPTION_LIMIT = 4096
EMBED_TOTAL_LIMIT = 6000

# The bar down the left of an account, so a channel of them reads as a set of
# things one bot files rather than as loose messages that happen to be boxed.
ACCOUNT_COLOUR = 0x5865F2

PARAGRAPH_BREAK = "\n\n"
LINE_BREAK = "\n"
WORD_BREAK = " "

# Tried in order, largest first, so a body is cut where a reader would have
# paused anyway and only falls back to a word when it has nothing else.
BOUNDARIES = (PARAGRAPH_BREAK, LINE_BREAK, WORD_BREAK)

# What a refused pin has to ask for, and the one refusal that says something
# about the channel rather than about the request: a channel with fifty pins
# already has nowhere to put another, which is a pin list to go and empty rather
# than a permission to go and grant.
PIN_PERMISSION = "Pin Messages"
PINS_FULL = 30003

# What a refused post has to ask for, as the permissions are named in the client.
#
# Two of them, and the second is the one to read twice: an embed is not message
# content, and a bot allowed to talk in a channel is not thereby allowed to put
# an embed in it. Send Messages alone was enough while an account was text, so a
# channel that has been working for months can start refusing on nothing but a
# release. Naming both is what stops a log line sending somebody to check the
# permission that was never the problem.
POST_PERMISSIONS = "Send Messages and Embed Links"

# What reading the channel for an account a restart left behind asks for. Its
# own line because what it costs to omit is not a failure anybody sees — the
# account goes up, beside the one already there.
HISTORY_PERMISSION = "Read Message History"

# A request Discord will not accept however many times it is sent — the same
# distinction `bot.topic` draws, and for the same reason.
REFUSED = 400


@dataclass
class Account:
    """One evening's account, as the run of messages holding it."""

    # Every message the account occupies, oldest first. The first of them is
    # pinned, which is what makes an account findable without scrolling and what
    # removes the need to leave anything behind when the run is replaced: a
    # reader looks in the pin list rather than at wherever they last saw it.
    run: list[Any] = field(default_factory=list)


class DiscordAnnouncer:
    """Holds one message per evening and rewrites it as the evening grows."""

    def __init__(self, guilds: Callable[[int], Any | None]) -> None:
        # Resolved through a callable rather than the bot itself, for the same
        # reason the speaker and the topic are: this is built before the bot
        # whose guilds it looks things up in.
        self._guilds = guilds

        # What is being rewritten, per server, channel, and title. Keyed on the
        # title because that is what identifies an account: one evening in one
        # room is one entry however many times its room emptied.
        self._accounts: dict[tuple[str, str, str], Account] = {}

    def resolve(self, server: str, channel: str) -> Any | None:
        """
        The text channel one name points at, if there is one.

        Public because the point of naming a channel rather than identifying it
        is that the name can be wrong, and a tool wants to say so at startup
        rather than at the end of the first conversation it has to file.
        """
        server_id = file_cfg.id_for(server)
        if server_id is None:
            return None

        guild = self._guilds(server_id)
        if guild is None:
            return None

        return discord.utils.get(getattr(guild, "text_channels", ()), name=channel)

    async def revise(
        self, server: str, channel: str, title: str, text: str, since: datetime
    ) -> bool:
        """
        Put an evening's account in one channel, replacing the account it had.

        Called once per seal rather than once per evening, each time with more
        of the night than the last, and what it has to leave behind is one
        account either way.

        `since` bounds the search for an account this process did not post
        itself. Nothing older than the evening can be this evening's account, so
        the moment the sitting opened is as far back as the channel is read.

        Every piece has to land for this to be True. Half an account in a
        channel is worse than none, in that it reads as a whole one, so a failure
        partway through is reported as a failure rather than as a partial
        success.
        """
        target = self.resolve(server, channel)
        if target is None:
            logger.warning(
                "No text channel called '%s' in %s; %d characters were not posted.",
                channel,
                server,
                len(text),
            )
            return False

        key = (server, channel, title)
        pages = paged(text, title)

        # An account of nothing is not an account, and an embed with no
        # description is a box Discord will refuse. Nothing said is nothing to
        # replace what is there with.
        if not pages:
            logger.warning(
                "Nothing to put in '%s' for %s; what is there was left alone.",
                channel,
                server,
            )
            return False

        held = self._accounts.get(key)

        if held is None:
            held = await self._adopted(server, channel, target, title, since)
            if held is not None:
                self._accounts[key] = held

        if held is None:
            return await self._posted(server, channel, target, key, pages)

        # An account that still fits the messages it is in is rewritten where it
        # stands, however many that is: three edits to three messages leave the
        # evening where a reader last saw it. One that has outgrown them cannot
        # be — the message it needs next would be sent to the bottom of the
        # channel, an unknown distance below the part it continues — so the run
        # is replaced whole. See `_replaced`.
        if len(pages) <= len(held.run):
            return await self._rewritten(server, channel, target, key, held, pages)

        return await self._replaced(server, channel, target, key, held, pages)

    async def _posted(
        self,
        server: str,
        channel: str,
        target: Any,
        key: tuple[str, str, str],
        pages: list[list[discord.Embed]],
    ) -> bool:
        """Put an account the channel does not have yet up, and hold on to it."""
        run = await self._run(server, channel, target, pages)
        if run is None:
            return False

        self._accounts[key] = Account(run=run)
        logger.info("Posted an account for %s to '#%s'.", server, channel)

        await self._pinned(server, channel, run[0])

        return True

    async def _rewritten(
        self,
        server: str,
        channel: str,
        target: Any,
        key: tuple[str, str, str],
        held: Account,
        pages: list[list[discord.Embed]],
    ) -> bool:
        """
        Rewrite the messages the account is already in, leaving them where they are.

        The ordinary case, and the one the whole design is for: an evening that
        emptied and refilled four times leaves the messages it started in,
        saying more each time. The pin is untouched, because the message it
        points at is untouched.

        A shrinking account comes through here too. The messages it still needs
        are rewritten and the surplus tail comes down, which keeps the pin where
        it is — the head is the one message a shrink can never be the loss of.

        A message that has gone — deleted by somebody tidying the channel —
        drops the account and posts the whole run again. Somebody who deleted it
        has not asked for the evening to go unrecorded.
        """
        for message, page in zip(held.run, pages):
            if not await self._edited(server, channel, target, key, message, page):
                return False

        surplus = held.run[len(pages) :]
        if surplus:
            await self._taken(server, channel, surplus)
            self._accounts[key] = Account(run=held.run[: len(pages)])

        logger.info("Rewrote %s's account in '#%s'.", server, channel)

        return True

    async def _edited(
        self,
        server: str,
        channel: str,
        target: Any,
        key: tuple[str, str, str],
        message: Any,
        page: list[discord.Embed],
    ) -> bool:
        """One message of a run, rewritten, or the whole account posted again."""
        try:
            await message.edit(embeds=page)
        except discord.NotFound:
            logger.info(
                "The message holding %s's account in '%s' is gone; posting another.",
                server,
                channel,
            )
            self._accounts.pop(key, None)

            return await self._posted(server, channel, target, key, [page])
        except discord.Forbidden:
            logger.warning(
                "Not allowed to edit in '%s'; %s will not get its accounts there. "
                "Editing its own message takes no permission of its own, so what "
                "is missing is %s on the channel.",
                channel,
                server,
                POST_PERMISSIONS,
            )
            return False
        except discord.HTTPException as exc:
            if exc.status == REFUSED:
                logger.error("Discord will not take an account for '%s': %s", channel, exc)
                return False

            logger.warning("Could not rewrite the account in '%s': %s", channel, exc)
            return False
        except (OSError, asyncio.TimeoutError) as exc:
            logger.warning(
                "Could not reach Discord to rewrite the account in '%s': %s", channel, exc
            )
            return False

        return True

    async def _replaced(
        self,
        server: str,
        channel: str,
        target: Any,
        key: tuple[str, str, str],
        held: Account,
        pages: list[list[discord.Embed]],
    ) -> bool:
        """
        Post the account again as one run, take the old one down, and pin the head.

        For an account that no longer fits the messages it is in. Extending it
        where it stands is not on offer — the extra message would be sent to the
        bottom of the channel, an unknown distance below the part it continues,
        and two halves of one account with other people's conversation between
        them read as two fragments.

        Nothing is left behind at the old address and nothing needs to be. The
        head of the run is pinned, so an account that moves is still one entry in
        the pin list rather than something a reader has to find again.

        **The new run goes up before the old one comes down.** A failure anywhere
        in it leaves the account exactly where it was, which is the right way to
        be wrong: the alternative ordering spends a window with the evening
        deleted and its replacement unwritten, and a failure in that window costs
        the channel its only copy.
        """
        run = await self._run(server, channel, target, pages)
        if run is None:
            return False

        await self._taken(server, channel, held.run)

        self._accounts[key] = Account(run=run)
        logger.info(
            "%s's account outgrew its messages in '#%s'; posted it again as %d.",
            server,
            channel,
            len(run),
        )

        await self._pinned(server, channel, run[0])

        return True

    async def _pinned(self, server: str, channel: str, message: Any) -> None:
        """
        Pin the head of a run, so an account is reachable without scrolling.

        Never fatal. The account is up, which is the thing that was asked for; an
        account that has to be scrolled to is worse than one in the pin list and
        much better than none. A channel with no room left for a pin is worth its
        own line, because what has to be done about it is emptying a pin list
        rather than granting a permission.

        Deleting a message unpins it, so a replaced run takes its own pin off the
        list on the way out and nothing has to be unpinned by hand.
        """
        try:
            await message.pin()
        except discord.Forbidden:
            logger.warning(
                "Not allowed to pin in '%s'; %s's accounts will not be pinned. "
                "The bot needs %s on the channel — pinning has its own permission "
                "and Manage Messages does not carry it.",
                channel,
                server,
                PIN_PERMISSION,
            )
        except discord.HTTPException as exc:
            if exc.code == PINS_FULL:
                logger.warning(
                    "'%s' has no room for another pin, so %s's account stays "
                    "unpinned. Something has to come off the pin list.",
                    channel,
                    server,
                )
                return

            logger.warning("Could not pin the account in '%s': %s", channel, exc)
        except (OSError, asyncio.TimeoutError) as exc:
            logger.warning(
                "Could not reach Discord to pin the account in '%s': %s", channel, exc
            )

    async def _adopted(
        self, server: str, channel: str, target: Any, title: str, since: datetime
    ) -> Account | None:
        """
        The account this evening already has in the channel, if this is not the
        process that posted it.

        What that is, is an evening a pod restart landed in the middle of: the
        message is held in memory, so nothing came back for it, and the next
        seal would post a second account of the same night beside the first.

        The **newest** message carrying the title. A run that was replaced took
        its predecessor down with it, so in a tidy channel there is only one —
        but a replacement that failed partway, or a process killed between the
        two, can leave an older one behind, and the newest is the live one.

        Messages newer than it and contiguous with it are the rest of the run —
        the bot's own, untitled, and unbroken by anybody else's message, which
        is what a run posted in one go looks like from the outside. Anything
        else ends it, because a gap means the messages are no longer one thing
        a reader would read straight through.

        Never fatal, and nothing is adopted from a channel that will not answer:
        posting a second account is worse than the alternative in a tidy channel
        and much better than a summary that goes nowhere.
        """
        me = getattr(getattr(target, "guild", None), "me", None)
        if me is None:
            return None

        newer: list[Any] = []

        try:
            async for message in target.history(after=since, oldest_first=False):
                if _titled(message, me, title):
                    logger.info(
                        "Found an account %s left in '#%s' before a restart; "
                        "rewriting it rather than posting another.",
                        server,
                        channel,
                    )

                    return Account(run=[message, *reversed(newer)])

                if _continues(message, me):
                    newer.append(message)
                else:
                    newer.clear()
        except discord.Forbidden:
            logger.warning(
                "Not allowed to read '%s'; %s cannot find an account it left there "
                "before a restart and will post another beside it. The bot needs "
                "%s on the channel.",
                channel,
                server,
                HISTORY_PERMISSION,
            )
        except (discord.HTTPException, OSError, asyncio.TimeoutError) as exc:
            logger.warning(
                "Could not read what %s already has in '%s': %s", server, channel, exc
            )

        return None

    async def _run(
        self,
        server: str,
        channel: str,
        target: Any,
        pages: list[list[discord.Embed]],
    ) -> list[Any] | None:
        """
        One account as the contiguous run of messages holding it, or nothing.

        All of it or none of it. A run that broke partway through is a channel
        holding the first half of an evening under a heading that says it is the
        whole of it, so what landed comes back down and the caller is told the
        account did not go up — it has one on disk either way, and the next seal
        will ask again with more of the night in it.
        """
        run: list[Any] = []

        for page in pages:
            posted = await self._sent(target, page, server, channel)
            if posted is not None:
                run.append(posted)
                continue

            await self._taken(server, channel, run)

            return None

        return run

    @staticmethod
    async def _taken(server: str, channel: str, run: list[Any]) -> None:
        """
        Take a run of messages down, saying nothing about how it went.

        Everything that calls this has already decided what to tell the caller,
        and a message that will not come down is untidy rather than wrong.
        """
        for message in run:
            try:
                await message.delete()
            except discord.NotFound:
                continue
            except (discord.HTTPException, OSError, asyncio.TimeoutError) as exc:
                logger.warning(
                    "Could not take part of %s's account out of '%s': %s",
                    server,
                    channel,
                    exc,
                )

    @staticmethod
    async def _sent(
        channel: Any, embeds: list[discord.Embed], server: str, name: str
    ) -> Any | None:
        """
        One message, or nothing where it did not land.

        The message itself rather than a flag, because everything after the
        first post is an edit of something and the handle is what makes that
        possible. The distinction between a refusal and a failure survives as
        the level it is logged at — a missing permission is a deployment to go
        and fix, and a 500 is Discord having a moment.
        """
        try:
            return await channel.send(embeds=embeds)
        except discord.Forbidden:
            logger.warning(
                "Not allowed to post in '%s'; %s will not get its accounts there. "
                "The bot needs %s on the channel.",
                name,
                server,
                POST_PERMISSIONS,
            )
        except discord.HTTPException as exc:
            if exc.status == REFUSED:
                logger.error("Discord will not take a message for '%s': %s", name, exc)
            else:
                logger.warning("Could not post to '%s': %s", name, exc)
        except (OSError, asyncio.TimeoutError) as exc:
            logger.warning("Could not reach Discord to post to '%s': %s", name, exc)

        return None


def paged(text: str, title: str) -> list[list[discord.Embed]]:
    """
    One account as the messages it has to be sent in, each as its embeds.

    Cut twice, at the two ceilings Discord has: a message carries 6000
    characters across its embeds and one description holds 4096, so an account
    is cut into messages first and each message into descriptions after. Both
    cuts go through `split`, so both land on the largest boundary they have and
    an account breaks between paragraphs wherever it can.

    The title is charged against every message rather than only the one carrying
    it. It costs a continuation message a few dozen characters it could have
    had, and it is one budget rather than two that have to agree.

    Only the first embed of the first message is titled. That is what identifies
    the account in a channel, and a continuation repeating it would be a second
    thing answering to the evening's name.
    """
    bodies = split(text, EMBED_TOTAL_LIMIT - len(title))

    return [
        _embeds(body, title if index == 0 else None)
        for index, body in enumerate(bodies)
    ]


def _embeds(body: str, title: str | None) -> list[discord.Embed]:
    """One message's worth of an account, as the embeds carrying it."""
    return [
        discord.Embed(
            title=title if index == 0 else None,
            description=piece,
            colour=ACCOUNT_COLOUR,
        )
        for index, piece in enumerate(split(body, EMBED_DESCRIPTION_LIMIT))
    ]


def _titled(message: Any, me: Any, title: str) -> bool:
    """Whether this is the head of an account of the evening `title` names."""
    return (
        message.author.id == me.id
        and bool(message.embeds)
        and message.embeds[0].title == title
    )


def _continues(message: Any, me: Any) -> bool:
    """Whether this could be the rest of a run rather than the start of one."""
    return (
        message.author.id == me.id
        and bool(message.embeds)
        and message.embeds[0].title is None
    )


def split(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """
    One body as the pieces it has to be cut into.

    Cut at the largest boundary that falls inside the limit, so an account
    breaks between paragraphs wherever it can and between words at worst. A run
    of text longer than the limit with no boundary in it at all — which is not
    something prose does — is cut at the limit rather than left to be refused.
    """
    remaining = text.strip()
    if len(remaining) <= limit:
        return [remaining] if remaining else []

    pieces: list[str] = []

    while len(remaining) > limit:
        cut = _boundary(remaining, limit)
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()

    if remaining:
        pieces.append(remaining)

    return pieces


def _boundary(text: str, limit: int) -> int:
    """Where to cut, preferring the boundary a reader would have paused at."""
    for boundary in BOUNDARIES:
        cut = text.rfind(boundary, 0, limit)
        if cut > 0:
            return cut

    return limit
