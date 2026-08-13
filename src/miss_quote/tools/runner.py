"""
Builds each server's tools once, and dispatches to them.

Which moments a tool has is settled at startup by inspecting the instance, so
the per-utterance path costs a dictionary lookup rather than a `hasattr` per
line. A tool that raises is logged and otherwise invisible: nothing a tool does
may cost an utterance, hold up a disconnect, or stop another tool.

Every tool a server has elected into shares one `Toolbox`, which is what lets one
of them call another. The box is handed over at construction and filled as each
tool is built, so it is complete by the time anything is dispatched and only
partly filled while the building is going on — which is why a tool looks in it
when it needs something rather than when it is made. What each tool is given is
its own view of that box, serving what its class declared in `requires`.

Those declarations are walked before anything is built, because a tool that
requires another which requires it back is a stack that does not end. Found here
it is a line in the startup report; found later it is the process.

A tool can also be switched on and off while the process runs, which is what
`enable` and `disable` are for. Switching one off takes it out of the moments
and out of its server's box, and stops whatever it was running of its own; it
does *not* throw the instance away, so switching it back on is instant and the
tool remembers what it knew — a backoff window, a round in progress, a tally.
Nothing here is written down: the file is what a restart goes back to.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from miss_quote.config import ServerConfig, file_cfg
from miss_quote.tools.base import (
    Announcer,
    Closer,
    FinishedHandler,
    JoinHandler,
    Service,
    SilentAnnouncer,
    SilentSpeaker,
    SilentTicker,
    SilentTopic,
    Speaker,
    Ticker,
    Tool,
    Toolbox,
    ToolContext,
    Topic,
    UtteranceHandler,
    Warmer,
    cycles,
)
from miss_quote.tools.registry import TOOLS
from miss_quote.transcript.writer import Source, Transcript, TranscriptSession, Utterance
from miss_quote.utils.logging import get_logger

logger = get_logger(__name__)

UTTERANCE_MOMENT = "an utterance"
FINISHED_MOMENT = "a finished transcript"
JOINED_MOMENT = "a channel joined"
PREWARM_MOMENT = "a pre-warm"
SERVICE_MOMENT = "a run of its own"
CLOSE_MOMENT = "a shutdown"

CYCLE_ARROW = " → "

# What a tool is keyed by everywhere below: one server's instance of one name.
# A tool is already per server, so the pair is what identifies an instance, a
# task, and a set of overrides alike.
ToolKey = tuple[int, str]

# What "nothing was written here" looks like where None is a value somebody
# could plausibly have written. Only `reconfigure` needs it, to tell an override
# it has to put back from one it has to take away again.
_UNSET = object()


@dataclass(frozen=True)
class ToolState:
    """
    What one server's one tool was configured as, and what it is doing now.

    The two are the same until somebody says otherwise, which is what `changed`
    reports: a deployment reading this wants to know which of these it will get
    back by restarting the pod and which it already had.
    """

    name: str

    # Whether the registry answers to the name at all. A name it does not is
    # still worth reporting rather than omitting: somebody asking after it has
    # misremembered a tool, and a list that silently lacks it does not say so.
    known: bool

    # What the file said, and what is true right now.
    configured: bool
    on: bool

    # Whether an instance exists. A tool switched off keeps its own, which is
    # what makes switching it back on instant.
    built: bool

    @property
    def changed(self) -> bool:
        return self.on is not self.configured


class ToolRunner:
    """Holds every server's tool instances and routes events to them."""

    def __init__(
        self,
        servers: Mapping[int, ServerConfig] | None = None,
        registry: Mapping[str, type[Tool]] | None = None,
        speaker: Speaker | None = None,
        topic: Topic | None = None,
        announcer: Announcer | None = None,
        ticker: Ticker | None = None,
    ) -> None:
        self._servers = file_cfg.servers if servers is None else servers
        self._registry = TOOLS if registry is None else registry

        self._speaker = SilentSpeaker() if speaker is None else speaker
        self._topic = SilentTopic() if topic is None else topic
        self._announcer = SilentAnnouncer() if announcer is None else announcer
        self._ticker = SilentTicker() if ticker is None else ticker
        self._on_utterance: dict[int, list[Tool]] = {}
        self._on_finished: dict[int, list[Tool]] = {}
        self._on_joined: dict[int, list[Tool]] = {}
        self._warming: list[Tool] = []
        self._closing: list[Tool] = []
        self._enabled: dict[str, list[str]] = {}
        self.problems: list[str] = []

        # Why the last tool that would not run would not run, as its own
        # sentence. The report keeps the same reason with the server and the
        # tool named around it; see `_refuse`.
        self._refusal = ""

        # One box per server, kept rather than dropped once its tools are built:
        # a tool switched on later joins the same box its neighbours are already
        # looking in.
        self._toolboxes: dict[int, Toolbox] = {}

        # Every instance ever built, whether or not it is answering. A tool
        # switched off stays here, which is what its state survives in.
        self._built: dict[ToolKey, Tool] = {}
        self._on: set[ToolKey] = set()

        # Services, and the task each is running under. Keyed rather than
        # listed so one tool can be stopped without disturbing the rest.
        self._serving: dict[ToolKey, Tool] = {}
        self._tasks: dict[ToolKey, asyncio.Task] = {}
        self._started = False

        # What somebody has said instead of the file, per tool. Merged over the
        # file's config when a tool is built rather than written into it, so
        # what the file said is still there to go back to.
        self._overrides: dict[ToolKey, dict[str, Any]] = {}

        for server_id, server in self._servers.items():
            self._build_server(server_id, server, self._registry)

    # ── startup ───────────────────────────────────

    def _build_server(
        self,
        server_id: int,
        server: ServerConfig,
        registry: Mapping[str, type[Tool]],
    ) -> None:
        """
        Build one server's tools, into one box they all share.

        The box is what a tool reaches its neighbours through, so it is made
        before any of them and handed to every one of them — including the ones
        built before whatever they will eventually go looking for. Each gets its
        own view of it, which serves only what that tool's class declared.

        Tools caught in a circle are left unbuilt. The alternative is a server
        that starts and then hangs the first time one of them calls the other,
        which is a worse way to find out and a harder one to read.
        """
        toolbox = self._toolboxes.setdefault(server_id, Toolbox())
        wanted = self._enabled_classes(server, registry)
        circular = self._circular(server.alias, wanted)

        for name, tool_class in wanted.items():
            if name in circular:
                continue

            tool = self._build_tool(server_id, server, name, tool_class)
            if tool is None:
                continue

            if self._place(server_id, server.alias, name, tool):
                toolbox.add(tool)

    def _build_tool(
        self,
        server_id: int,
        server: ServerConfig,
        name: str,
        tool_class: type[Tool],
    ) -> Tool | None:
        """
        Build one tool, or report why it would not build.

        A tool that raises is one that read its own config and refused what it
        found, which is a decision worth keeping: the server runs without it and
        the reason is a line rather than a crash. Whether that line is a startup
        report or a reply in a channel is the caller's business.
        """
        toolbox = self._toolboxes.setdefault(server_id, Toolbox())
        config = self._config_for(server_id, server, name)

        try:
            tool = tool_class(
                ToolContext(
                    server=server.alias,
                    config=config,
                    speaker=self._speaker,
                    users=server.users,
                    tools=toolbox.view(tool_class),
                    topic=self._topic,
                    announcer=self._announcer,
                    ticker=self._ticker,
                )
            )
        except Exception as exc:
            self._refuse(server.alias, name, f"would not start: {exc}")
            return None

        self._built[(server_id, name)] = tool

        return tool

    def _config_for(
        self, server_id: int, server: ServerConfig, name: str
    ) -> Mapping[str, Any]:
        """
        What one tool is built against: the file's config, under anything said
        since.

        A tool listed with `enabled: false` still has its config read, so a tool
        switched on later is built against what the file already says about it
        rather than against nothing.
        """
        settings = server.tools.get(name)
        config = dict(settings.config) if settings is not None else {}
        config.update(self._overrides.get((server_id, name), {}))

        return config

    def _enabled_classes(
        self, server: ServerConfig, registry: Mapping[str, type[Tool]]
    ) -> dict[str, type[Tool]]:
        """
        The classes behind the names one server switched on, in the order it
        listed them.

        Resolved before any of them is built, because the cycle check reads
        classes and it has to read all of them at once. A name nothing answers to
        is reported here rather than where the building happens, so it is
        reported once.
        """
        wanted: dict[str, type[Tool]] = {}

        for name, settings in server.tools.items():
            if not settings.enabled:
                continue

            tool_class = registry.get(name)
            if tool_class is None:
                self.problems.append(
                    f"Server '{server.alias}': no tool named '{name}'; skipping it."
                )
                continue

            wanted[name] = tool_class

        return wanted

    def _circular(self, alias: str, wanted: Mapping[str, type[Tool]]) -> set[str]:
        """
        The names of the tools caught in a circle, reporting each circle once.

        A circle is named in the order it was walked and closed back to where it
        started, so the line reads as the call that would not have returned.
        """
        circular: set[str] = set()

        for circle in cycles(wanted.values()):
            named = [tool.name for tool in circle]
            circular.update(named)

            self.problems.append(
                f"Server '{alias}': tools "
                f"{CYCLE_ARROW.join([*named, named[0]])} require each other in a "
                "circle; none of them will be built."
            )

        return circular

    def _place(self, server_id: int, alias: str, name: str, tool: Tool) -> bool:
        """
        File a built tool under its moments, reporting whether it has any.

        A tool with none is left out of the box as well: it will never do
        anything itself, and it should not be what another tool finds when it
        goes looking for something that works.

        Filing is idempotent, because a tool switched off and on again comes
        back through here holding the same instance it left with. A tool filed
        twice would be dispatched twice, and would be one announcement asked for
        and two announcements made.
        """
        handled = False

        if isinstance(tool, UtteranceHandler):
            self._file(self._on_utterance, server_id, tool)
            handled = True

        if isinstance(tool, FinishedHandler):
            self._file(self._on_finished, server_id, tool)
            handled = True

        if isinstance(tool, JoinHandler):
            self._file(self._on_joined, server_id, tool)
            handled = True

        if isinstance(tool, Service):
            self._serving[(server_id, name)] = tool
            handled = True

        if not handled:
            self._refuse(
                alias,
                name,
                "handles no moment and has nothing of its own to run, so it will "
                "never run.",
            )
            return False

        # After the check, so nothing is prepared for, or awaited on behalf of, a
        # tool that can never use it.
        if isinstance(tool, Warmer) and tool not in self._warming:
            self._warming.append(tool)

        if isinstance(tool, Closer) and tool not in self._closing:
            self._closing.append(tool)

        listed = self._enabled.setdefault(alias, [])
        if name not in listed:
            listed.append(name)

        self._on.add((server_id, name))

        return True

    @staticmethod
    def _file(handlers: dict[int, list[Tool]], server_id: int, tool: Tool) -> None:
        """One tool under one moment, once however often it is filed."""
        filed = handlers.setdefault(server_id, [])
        if tool not in filed:
            filed.append(tool)

    def _unplace(self, server_id: int, alias: str, name: str, tool: Tool) -> None:
        """
        Take a tool back out of every moment it was filed under.

        The mirror of `_place`, with one deliberate asymmetry: the tool stays in
        `_closing`. A tally switched off halfway through an evening still has
        something to write when the process ends, and a tool that is asked to
        finish twice is a tool that was switched on again — which is the case
        `_place` already guards by filing it once.
        """
        for handlers in (self._on_utterance, self._on_finished, self._on_joined):
            filed = handlers.get(server_id)
            if filed is not None and tool in filed:
                filed.remove(tool)

        self._serving.pop((server_id, name), None)

        if tool in self._warming:
            self._warming.remove(tool)

        listed = self._enabled.get(alias)
        if listed is not None and name in listed:
            listed.remove(name)

        self._on.discard((server_id, name))

    def describe(self) -> Mapping[str, Sequence[str]]:
        """Tool names in play, by server alias, for the startup report."""
        return {alias: tuple(sorted(names)) for alias, names in self._enabled.items()}

    async def prewarm(self) -> None:
        """
        Let every tool prepare whatever it can prepare in advance.

        Serial rather than concurrent, unlike dispatch: nothing is waiting on
        this, and the tools that have anything to warm are all talking to one
        synthesizer. One at a time leaves that server free for whatever is
        actually being said while this runs.

        A tool that raises is logged and the rest still get their turn, on the
        same terms as the moments: nothing a tool does at startup may cost
        another tool its own.
        """
        for tool in list(self._warming):
            await self._warm(tool)

    @staticmethod
    async def _warm(tool: Tool) -> None:
        """One tool's preparation, with whatever it fails on kept to itself."""
        try:
            await tool.prewarm()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Tool '%s' failed on %s: %s",
                tool.name,
                PREWARM_MOMENT,
                exc,
                exc_info=exc,
            )

    def start(self) -> Sequence[asyncio.Task]:
        """
        Set going every tool that has something of its own to do.

        The tasks are handed back for the caller to cancel on the way down, and
        cancelling them is what has to happen before `close`: a tool asked to
        write itself out while its own loop is still going would be racing itself
        for the file.

        Once per process, however many readies the gateway sends. A tool
        switched on after this has run starts its own task there and then, which
        is why what is remembered here is that starting has happened rather than
        how many tasks it produced.
        """
        if self._started:
            return self.running

        self._started = True

        for key, tool in self._serving.items():
            self._tasks[key] = asyncio.create_task(self._serve(tool))

        return self.running

    @property
    def running(self) -> Sequence[asyncio.Task]:
        """Every task a tool is currently running under."""
        return tuple(self._tasks.values())

    async def _stop(self) -> None:
        """
        Bring every running tool to a halt, and wait until it has stopped.

        Cancelling is a request; a task is only over once it has been let go of.
        Gathered rather than awaited in turn so one that takes a moment to unwind
        does not hold up the rest.
        """
        tasks = self.running
        if not tasks:
            return

        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def _halt(self, key: ToolKey) -> None:
        """
        Stop one tool's loop, and wait until it has stopped.

        Waited for rather than merely cancelled, on the same reasoning as
        `_stop`: a tool switched off and straight back on again would otherwise
        have two of its loops going, one of them on its way out and both of them
        writing the same file.
        """
        task = self._tasks.pop(key, None)
        if task is None:
            return

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    @staticmethod
    async def _serve(tool: Tool) -> None:
        """
        Run one tool's loop, and say so if it stops.

        A service that returns has decided it has nothing to do, which is
        ordinary — a tally with saving switched off says so and stops. One that
        raises has not, and the failure is otherwise silent: nothing is waiting
        on this task, so nobody would ever collect the exception.
        """
        try:
            await tool.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Tool '%s' stopped on %s: %s",
                tool.name,
                SERVICE_MOMENT,
                exc,
                exc_info=exc,
            )

    async def close(self) -> None:
        """
        Let every tool finish whatever has to outlive the process.

        The services are stopped first, and waited for rather than merely
        cancelled: a tool writing itself out while its own loop is still going
        would be racing itself for a file. The caller has usually cancelled them
        already, which this makes an ordering guarantee rather than a hope.

        Serial after that: what is left to do at this point is small and mostly a
        write, and running them together would buy nothing but a log that is
        harder to read when one of them fails.
        """
        await self._stop()

        for tool in self._closing:
            try:
                await tool.close()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Tool '%s' failed on %s: %s",
                    tool.name,
                    CLOSE_MOMENT,
                    exc,
                    exc_info=exc,
                )

    # ── switching ─────────────────────────────────

    def state(self, server_id: int) -> Sequence[ToolState]:
        """
        Every tool this server could be running, and whether it is.

        Built from the registry rather than from the server's own block, so a
        tool the file never mentioned is listed as off rather than omitted:
        somebody asking what can be switched on is asking about all of them.
        """
        server = self._servers.get(server_id)
        if server is None:
            return ()

        names = sorted({*self._registry, *server.tools})

        return tuple(
            ToolState(
                name=name,
                known=name in self._registry,
                configured=self._configured(server, name),
                on=(server_id, name) in self._on,
                built=(server_id, name) in self._built,
            )
            for name in names
        )

    def configured_value(self, server_id: int, name: str, key: str) -> Any | None:
        """
        What one tool was built against for one of its settings.

        What the file said, under anything said since — which is the value the
        tool read, not whatever it made of it. A tool turns its config into
        compiled patterns and expanded stems, and none of that is something to
        hand back to whoever wrote the line.
        """
        server = self._servers.get(server_id)
        if server is None:
            return None

        return self._config_for(server_id, server, name).get(key)

    def status(self, server_id: int, name: str) -> ToolState | None:
        """One tool's state, or nothing if this server has no such name."""
        for state in self.state(server_id):
            if state.name == name:
                return state

        return None

    @staticmethod
    def _configured(server: ServerConfig, name: str) -> bool:
        settings = server.tools.get(name)
        return settings is not None and settings.enabled

    async def enable(self, server_id: int, name: str) -> str:
        """
        Switch one tool on for one server, building it if it has never run.

        A tool that was switched off is put back holding the instance it left
        with, so what it knew — a backoff window, a round part-answered, a
        speaker's recent violations — is still there. One that has never been
        built is built now, against whatever the file says about it, which is
        why a tool listed `enabled: false` with a config block underneath can be
        brought up without touching the file.

        The cycle check runs again rather than being trusted from startup: what
        it answers is a question about the set of tools that are on, and this is
        the call that changes that set.
        """
        server = self._servers.get(server_id)
        if server is None:
            return f"'{name}' cannot be switched on: this server is not a known server."

        tool_class = self._registry.get(name)
        if tool_class is None:
            return f"There is no tool named '{name}'."

        key = (server_id, name)
        if key in self._on:
            return f"'{name}' is already on."

        circle = self._would_circle(server_id, tool_class)
        if circle is not None:
            return (
                f"'{name}' cannot be switched on: it and "
                f"{CYCLE_ARROW.join(circle)} require each other in a circle."
            )

        tool = self._built.get(key)
        remembered = tool is not None

        if tool is None:
            tool = self._build_tool(server_id, server, name, tool_class)
            if tool is None:
                return f"'{name}' {self._refusal}"

        if not self._place(server_id, server.alias, name, tool):
            return f"'{name}' {self._refusal}"

        self._toolboxes.setdefault(server_id, Toolbox()).add(tool)

        if self._started and key in self._serving:
            self._tasks[key] = asyncio.create_task(self._serve(tool))

        logger.info("Tool '%s' switched on for '%s'.", name, server.alias)

        if remembered:
            return f"'{name}' is back on, and remembers what it knew."

        return f"'{name}' is on."

    async def disable(self, server_id: int, name: str) -> str:
        """
        Switch one tool off for one server, keeping what it is.

        The instance is kept rather than closed, so switching it back on costs
        nothing and loses nothing. What stops is everything anybody can observe:
        it is dispatched no moments, it is found by no neighbour, and whatever
        it was running of its own is cancelled and waited for.
        """
        server = self._servers.get(server_id)
        if server is None:
            return f"'{name}' cannot be switched off: this server is not a known server."

        key = (server_id, name)
        tool = self._built.get(key)

        if tool is None or key not in self._on:
            return f"'{name}' is already off."

        self._unplace(server_id, server.alias, name, tool)
        self._toolboxes.setdefault(server_id, Toolbox()).remove(tool)
        await self._halt(key)

        logger.info("Tool '%s' switched off for '%s'.", name, server.alias)

        return f"'{name}' is off."

    async def reconfigure(self, server_id: int, name: str, key: str, value: Any) -> str:
        """
        Say something other than what the file says about one tool, and rebuild
        it.

        A rebuild rather than a poke, because a tool reads its config once and
        turns it into whatever it actually uses — compiled patterns, expanded
        word stems, a window in seconds. The cost is the instance's memory, and
        it is a real one: a rebuilt tool has forgotten who it recently fined.
        That is why switching a tool off deliberately does not do this.

        A tool that refuses the new value keeps the one it had. The override is
        dropped with it, so a second attempt starts from what the file says
        rather than from a value nothing accepted.
        """
        server = self._servers.get(server_id)
        if server is None:
            return f"'{name}' cannot be configured: this server is not a known server."

        if name not in self._registry:
            return f"There is no tool named '{name}'."

        overrides = self._overrides.setdefault((server_id, name), {})
        previous = overrides.get(key, _UNSET)
        overrides[key] = value

        # Read before the first rebuild and handed to both, because taking the
        # tool out is what tells the runner it is off: asking again afterwards
        # would get back the answer this call had just caused.
        was_on = (server_id, name) in self._on

        if not await self._rebuild(server_id, server, name, was_on):
            refusal = self._refusal

            if previous is _UNSET:
                overrides.pop(key, None)
            else:
                overrides[key] = previous

            await self._rebuild(server_id, server, name, was_on)

            return f"'{name}.{key}' was refused: the tool {refusal}"

        if not was_on:
            return (
                f"'{name}.{key}' is now {value!r}, and will be read when '{name}' is "
                "switched on."
            )

        return f"'{name}.{key}' is now {value!r}. It has forgotten what it knew."

    async def _rebuild(
        self, server_id: int, server: ServerConfig, name: str, was_on: bool
    ) -> bool:
        """
        Replace one server's instance of one tool with a fresh one.

        Only a tool that was on comes back on. One that was off is discarded and
        left off, so it is built against the new config whenever somebody
        switches it on rather than being quietly started by a change to a
        setting.
        """
        key = (server_id, name)
        old = self._built.pop(key, None)

        if old is not None:
            self._unplace(server_id, server.alias, name, old)
            self._toolboxes.setdefault(server_id, Toolbox()).remove(old)
            await self._halt(key)

            if old in self._closing:
                self._closing.remove(old)

        if not was_on:
            return True

        tool = self._build_tool(server_id, server, name, self._registry[name])
        if tool is None:
            return False

        if not self._place(server_id, server.alias, name, tool):
            return False

        self._toolboxes.setdefault(server_id, Toolbox()).add(tool)

        if self._started and key in self._serving:
            self._tasks[key] = asyncio.create_task(self._serve(tool))

        return True

    def _would_circle(
        self, server_id: int, candidate: type[Tool]
    ) -> tuple[str, ...] | None:
        """
        The circle switching one tool on would close, if it would close one.

        Walked over the tools that are on plus the one being asked for, because
        a circle is a property of the set that can call each other rather than
        of the set the file listed. The candidate's own name is left off what
        comes back: the caller already has it.
        """
        classes = [
            type(tool)
            for key, tool in self._built.items()
            if key[0] == server_id and key in self._on
        ]

        for circle in cycles([*classes, candidate]):
            if candidate in circle:
                return tuple(tool.name for tool in circle if tool is not candidate)

        return None

    def _refuse(self, alias: str, name: str, reason: str) -> None:
        """
        Record why a tool will not run, for both audiences at once.

        The report wants the server and the tool named, because it is a list of
        everything wrong with a deployment. Somebody who has just typed
        something wants the reason on its own, having supplied the rest of the
        sentence themselves.
        """
        self._refusal = reason
        self.problems.append(f"Server '{alias}': tool '{name}' {reason}")

    # ── dispatch ──────────────────────────────────

    async def dispatch_utterance(
        self, session: TranscriptSession, utterance: Utterance
    ) -> None:
        await self._run(
            self._on_utterance.get(session.source.guild_id),
            lambda tool: tool.handle_utterance(utterance, session),
            UTTERANCE_MOMENT,
        )

    async def dispatch_finished(self, transcript: Transcript) -> None:
        await self._run(
            self._on_finished.get(transcript.source.guild_id),
            lambda tool: tool.handle_finished(transcript),
            FINISHED_MOMENT,
        )

    async def dispatch_joined(self, source: Source) -> None:
        await self._run(
            self._on_joined.get(source.guild_id),
            lambda tool: tool.handle_joined(source),
            JOINED_MOMENT,
        )

    @staticmethod
    async def _run(
        tools: Sequence[Tool] | None,
        call: Callable[[Tool], Awaitable[None]],
        moment: str,
    ) -> None:
        """
        Run every tool for one event, letting each fail on its own.

        Concurrent rather than serial: a tool's latency is its own, and a slow
        one should not delay the rest. Cancellation is re-raised so shutdown is
        not mistaken for a tool failing.
        """
        if not tools:
            return

        results = await asyncio.gather(
            *(call(tool) for tool in tools), return_exceptions=True
        )

        for tool, result in zip(tools, results):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, BaseException):
                logger.error(
                    "Tool '%s' failed on %s: %s",
                    tool.name,
                    moment,
                    result,
                    exc_info=result,
                )
