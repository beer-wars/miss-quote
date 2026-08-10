"""
Keeping one message in a text channel and rewriting it in place.

The third way words leave this process, and the counterpart to `bot.topic` and
`bot.announcer`. A topic is one line under a voice channel's name and holds no
history. The other two both keep one message and rewrite it, so editing is not
what tells them apart — **how long the text stays worth reading is**. An account
of an evening is read afterwards, so the announcer leaves it up. A running
transcript is worth reading while the room is talking and is clutter by morning,
so this pins it while it lives and deletes it when the room empties.

That difference is why the two draw opposite conclusions from the same limits.
An account is long and rewritten a handful of times a night, so it goes in embeds
and buys the room to stay one message at almost any length an evening runs to.
This is short by construction — at most `transcript_lines` of them, and trimmed
to fit a message by dropping the oldest — and rewritten every couple of seconds,
so it stays message content: at that cadence what an embed would buy in ceiling
it would spend in repainting a container nobody asked to have redrawn.

**The message is pinned while it is live**, which is what makes it reachable
while a room is talking rather than something to scroll for. Deleting it unpins
it, so taking the feed down needs no second call and nothing is left holding one
of a channel's fifty pins.

**The pin list is the memory.** Which message is being written to is held in
memory and nowhere else, so a process that goes away mid-session leaves one
behind — pinned, and no longer written to. Rather than persist an ID to a file
that would have to be kept in step with a channel somebody may have cleared, the
next post reads the channel's pins and takes down whatever this bot left there.
Fifty pins is a ceiling a slow leak would eventually reach; a leak that is swept
on the way past never gets there. See `_swept`.

This bot pins one other thing, and the two spend from the same fifty:
`bot.announcer` pins the head of every account it files, in this same channel,
and ages the older ones off the list under its own `pinned_sessions`. `_swept`
tells them apart by their embeds — an account has them and a feed does not —
rather than by anything either has to remember about the other across a
restart.

**Only the pin needs a permission.** Posting needs Send Messages, and everything
after it is the bot's own message — editing one and deleting one are ungoverned,
which is why the sweep only ever takes down blocks it wrote itself. Pinning is
`Pin Messages`, which is **not** carried by Manage Messages: Discord split the two
apart, so a bot trusted to delete anybody's message in a channel can still be
refused a pin on its own. That is a confusing thing to read in a log, so the
refusal says which permission it means.

**Rate limits are why this exists at all.** Editing a message is a per-channel
bucket of roughly five requests every five seconds, where setting a voice
channel's status is two every ten minutes; that gap is the whole reason a running
transcript is a message rather than a topic. discord.py sleeps out a 429 rather
than raising, so going over does not fail — it silently lags, which is why the
tool that calls this waits out its own interval after each write rather than
writing on a fixed tick. See `Summary._ticking`.

Whatever is shown is cut to Discord's message limit rather than split across
several, unlike an account: what is being shown is the current state of
something, and a state cut in half across two messages is two states. The cut
here is a backstop — a caller that has lines is expected to have dropped whole
ones against `limit` before it gets here, since cutting a fenced block at a
character costs it the fence it opens with.
"""

from __future__ import annotations

import asyncio
from typing import Any

import discord

from miss_quote.tools.base import MESSAGE_LIMIT, Finder
from miss_quote.utils.logging import get_logger

logger = get_logger(__name__)

# A request Discord will not accept however many times it is sent — the same
# distinction `bot.topic` and `bot.announcer` draw, and for the same reason.
REFUSED = 400

# What a message cut to the limit ends with, so a reader can tell text that ran
# out from text that was cut off.
ELLIPSIS = "…"

# What Discord will not take a fifty-first of. Its own case because it is the one
# refusal that says something about the channel rather than about the request:
# every other 400 here is a message Discord would never accept, and this one is a
# channel that has no room for another pin.
PINS_FULL = 30003

# What a refused pin has to ask for, as the permission is named in the client.
#
# Pinning is its own permission and Manage Messages does not carry it, which is
# worth spelling out where it is refused: a bot that can delete anybody's message
# in a channel and still cannot pin its own reads like a bug in the bot rather
# than a permission nobody has granted. Nothing else here needs a permission at
# all beyond posting — a message of the bot's own is the bot's to edit and to
# delete — so this is the only line that names one.
PIN_PERMISSION = "Pin Messages"


class DiscordTicker:
    """Holds one message per channel and edits it as a tool changes its mind."""

    # What `Ticker` promises a caller, so that a tool trims to the same number
    # this enforces rather than to one of its own that could drift from it.
    limit = MESSAGE_LIMIT

    def __init__(self, finder: Finder) -> None:
        # The announcer, which already resolves a channel name against the
        # guilds and is the thing `Finder` was written for. Resolving it twice
        # would be two answers to one question the moment either changed.
        self._finder = finder

        # The message being rewritten, per server and channel. One per pair
        # rather than one per server: two rooms showing two transcripts are two
        # messages, and which channel they are in is what tells them apart.
        self._shown: dict[tuple[str, str], Any] = {}

    async def show(self, server: str, channel: str, text: str) -> bool:
        """
        Rewrite this channel's message, posting one if there is not one yet.

        A message that has gone — deleted by somebody tidying the channel, or
        lost with a channel that was cleared — is posted again rather than
        reported. The point of this is that a room can watch it, and a reader
        who deleted the block has not asked for the feed to stop; they have
        asked for it to stop being where it was.

        Everything else is reported the way an announcement is, since a caller
        that cannot show anything wants to know once rather than to keep being
        told: a missing permission and a body Discord will not parse are both a
        deployment to go and fix.
        """
        target = self._finder.resolve(server, channel)
        if target is None:
            logger.warning(
                "No text channel called '%s' in %s; %d characters were not shown.",
                channel,
                server,
                len(text),
            )
            return False

        held = self._shown.get((server, channel))
        body = trimmed(text)

        if held is None:
            return await self._post(server, channel, target, body)

        try:
            await held.edit(content=body, allowed_mentions=_unmentioned())
        except discord.NotFound:
            logger.info(
                "The message showing %s's transcript in '%s' is gone; posting another.",
                server,
                channel,
            )
            self._shown.pop((server, channel), None)

            return await self._post(server, channel, target, body)
        except discord.Forbidden:
            logger.warning(
                "Not allowed to edit in '%s'; %s will not keep a transcript there. "
                "Editing its own message takes no permission of its own, so what "
                "has gone is the bot's access to the channel.",
                channel,
                server,
            )
            return False
        except discord.HTTPException as exc:
            if exc.status == REFUSED:
                logger.error(
                    "Discord will not take %d characters for '%s': %s",
                    len(body),
                    channel,
                    exc,
                )
                return False

            logger.warning("Could not edit the message in '%s': %s", channel, exc)
            return False
        except (OSError, asyncio.TimeoutError) as exc:
            logger.warning(
                "Could not reach Discord to edit the message in '%s': %s", channel, exc
            )
            return False

        return True

    async def clear(self, server: str, channel: str) -> None:
        """
        Delete the message being rewritten, if there is one.

        What the feed is for is a room watching itself, and a room that has
        emptied is not watching anything: what would be left is the last thing
        said before everybody went to bed, sitting in the channel looking
        current. The summary is what the evening leaves behind.

        Deleting is all of it. A message that is gone is off the pin list by
        definition, so there is no unpinning to do and nothing left holding one
        of the channel's fifty.

        Nothing is reported. A message somebody deleted first is the state being
        asked for, and everything else is a channel the bot is on its way out of
        — there is no next attempt to make it worth telling anybody about, so a
        failure is a line in the log and a handle let go of either way.
        """
        held = self._shown.pop((server, channel), None)
        if held is None:
            return

        try:
            await held.delete()
        except discord.NotFound:
            logger.debug(
                "The message showing %s's transcript in '%s' was already gone.",
                server,
                channel,
            )
        except discord.Forbidden:
            logger.warning(
                "Not allowed to delete in '%s'; %s's transcript will stay up. "
                "Deleting its own message takes no permission of its own, so what "
                "has gone is the bot's access to the channel.",
                channel,
                server,
            )
        except (discord.HTTPException, OSError, asyncio.TimeoutError) as exc:
            logger.warning(
                "Could not take %s's transcript out of '%s': %s", server, channel, exc
            )
        else:
            logger.info("Took %s's transcript out of '#%s'.", server, channel)

    async def _post(self, server: str, channel: str, target: Any, body: str) -> bool:
        """
        Put the first message up, pin it, and hold on to it for every one after.

        Held only on success, so a channel the bot cannot post in is tried again
        next time rather than remembered as somewhere it already posted.

        The sweep comes first. Anything this bot left pinned here is a feed from
        a process that went away mid-session, and posting beside it would leave
        two blocks up with only one of them moving — as well as spending a pin
        that nothing will ever come back for.
        """
        await self._swept(server, channel, target)

        try:
            posted = await target.send(body, allowed_mentions=_unmentioned())
            self._shown[(server, channel)] = posted
        except discord.Forbidden:
            logger.warning(
                "Not allowed to post in '%s'; %s will not get a transcript there. "
                "The bot needs Send Messages on the channel.",
                channel,
                server,
            )
            return False
        except discord.HTTPException as exc:
            if exc.status == REFUSED:
                logger.error(
                    "Discord will not take %d characters for '%s': %s",
                    len(body),
                    channel,
                    exc,
                )
                return False

            logger.warning("Could not post to '%s': %s", channel, exc)
            return False
        except (OSError, asyncio.TimeoutError) as exc:
            logger.warning("Could not reach Discord to post to '%s': %s", channel, exc)
            return False

        logger.info("Showing %s's transcript in '#%s'.", server, channel)

        await self._pinned(server, channel, posted)

        return True

    async def _pinned(self, server: str, channel: str, message: Any) -> None:
        """
        Pin the block, so a room talking can reach it rather than scroll for it.

        Never fatal. The message is up, which is the thing that was asked for; a
        feed nobody can reach from the pin list is worse than one that scrolls
        and better than none. A channel with no room left for a pin is worth its
        own line, because what has to be done about it is emptying a pin list
        rather than granting a permission.
        """
        try:
            await message.pin()
        except discord.Forbidden:
            logger.warning(
                "Not allowed to pin in '%s'; %s's transcript will not be pinned. "
                "The bot needs %s on the channel — pinning has its own permission "
                "and Manage Messages does not carry it.",
                channel,
                server,
                PIN_PERMISSION,
            )
        except discord.HTTPException as exc:
            if exc.code == PINS_FULL:
                logger.warning(
                    "'%s' has no room for another pin, so %s's transcript stays "
                    "unpinned. Something has to come off the pin list.",
                    channel,
                    server,
                )
                return

            logger.warning("Could not pin the transcript in '%s': %s", channel, exc)
        except (OSError, asyncio.TimeoutError) as exc:
            logger.warning(
                "Could not reach Discord to pin the transcript in '%s': %s",
                channel,
                exc,
            )

    async def _swept(self, server: str, channel: str, target: Any) -> None:
        """
        Take down anything this bot left pinned in the channel.

        What that is, is a feed from a process that went away mid-session: the
        message it was writing to is held in memory, so nothing came back for it
        and it is both stale and holding one of the channel's fifty pins. The
        pin list is where those are findable, which is the whole reason the live
        message is pinned at all.

        Only this bot's own messages, and only the ones that are feeds. Both
        halves matter, and the second is newer than the first: a pin somebody put
        on somebody else's message is not ours to take off, and the bot's own
        pinned messages are no longer all feeds. `bot.announcer` pins the head of
        every account it files, in this same channel, and an account is a thing
        the evening left behind rather than something this is entitled to tidy.

        What separates them is structural rather than guessed at. A feed is
        message content and carries no embed; an account is embeds and carries no
        content. So anything with an embed is somebody else's business, and
        nothing has to be tracked across processes to know it.

        Never fatal, and swallowed whole: this runs on the way to posting, and a
        channel whose pins cannot be read is a feed that goes up beside its
        predecessor rather than no feed at all.
        """
        me = getattr(getattr(target, "guild", None), "me", None)
        if me is None:
            return

        try:
            for pinned in await target.pins():
                if pinned.author.id != me.id or pinned.embeds:
                    continue

                await pinned.delete()
                logger.info(
                    "Took a transcript %s left pinned in '#%s' after a restart.",
                    server,
                    channel,
                )
        except (discord.HTTPException, OSError, asyncio.TimeoutError) as exc:
            logger.warning("Could not read what is pinned in '%s': %s", channel, exc)


def _unmentioned() -> discord.AllowedMentions:
    """
    Nothing in a shown message pings anybody.

    Belt and braces beside the code fence the caller wraps a transcript in: a
    fence already stops a mention being parsed, and a caller that forgets one
    should still not be able to ping a room by transcribing somebody saying
    "at everyone" out loud.
    """
    return discord.AllowedMentions.none()


def trimmed(text: str, limit: int = MESSAGE_LIMIT) -> str:
    """
    One body as much of it as Discord will take, cut at the end.

    Cut rather than split, unlike an account. What is being shown is the current
    state of something and the newest line is the one being watched, so a body
    over the limit loses its front rather than becoming a second message nobody
    is looking at.

    **This is the backstop, not the trimming.** Cutting at a character is the
    wrong tool for anything with structure at the front of it — a fenced block
    loses the fence that opens it and stops being a block at all — so a caller
    with lines trims to `Ticker.limit` itself and drops whole ones. See
    `Summary._fitting`. What is left for this is a caller that did not, which is
    a rendering nobody wants rather than a message Discord refuses.
    """
    if len(text) <= limit:
        return text

    return ELLIPSIS + text[-(limit - len(ELLIPSIS)) :]
