import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from miss_quote.config import ServerConfig, ToolSettings
from miss_quote.tools.base import Tool
from miss_quote.tools.runner import ToolRunner
from miss_quote.transcript.writer import Source, Transcript, Utterance

FIRST_SERVER = 123456789012345678
SECOND_SERVER = 876543210987654321

FIRST_ALIAS = "first-server"
SECOND_ALIAS = "second-server"

TOOL_NAME = "recorder"
OTHER_TOOL_NAME = "second-recorder"
WARMING_TOOL_NAME = "warming-recorder"

ROSTER = {234567890123456789: "Speaker One"}

PATIENCE_SECONDS = 2.0

FIRST_SOURCE = Source(
    guild_id=FIRST_SERVER, guild_alias=FIRST_ALIAS, channel_id=1, channel="general-voice"
)
SECOND_SOURCE = Source(
    guild_id=SECOND_SERVER, guild_alias=SECOND_ALIAS, channel_id=2, channel="general-voice"
)


class Recorder(Tool):
    """A tool that handles both moments and remembers being called."""

    name = TOOL_NAME

    def __init__(self, context):
        super().__init__(context)
        self.utterances = []
        self.transcripts = []

    async def handle_utterance(self, utterance, session) -> None:
        self.utterances.append(utterance)

    async def handle_finished(self, transcript) -> None:
        self.transcripts.append(transcript)


class UtteranceOnly(Tool):
    name = "utterance-only"

    def __init__(self, context):
        super().__init__(context)
        self.calls = 0

    async def handle_utterance(self, utterance, session) -> None:
        self.calls += 1


class FinishedOnly(Tool):
    name = "finished-only"

    def __init__(self, context):
        super().__init__(context)
        self.calls = 0

    async def handle_finished(self, transcript) -> None:
        self.calls += 1


class JoinOnly(Tool):
    name = "join-only"

    def __init__(self, context):
        super().__init__(context)
        self.joined = []

    async def handle_joined(self, source) -> None:
        self.joined.append(source)


class Warming(Recorder):
    """A tool with something to prepare before it is asked for anything."""

    name = WARMING_TOOL_NAME

    def __init__(self, context):
        super().__init__(context)
        self.warmed = 0

    async def prewarm(self) -> None:
        self.warmed += 1


class WarmingInert(Tool):
    """A tool that warms and handles nothing. Prepared for a moment it never sees."""

    name = "warming-inert"

    def __init__(self, context):
        super().__init__(context)
        self.warmed = 0

    async def prewarm(self) -> None:
        self.warmed += 1


class Inert(Tool):
    """A tool that handles nothing. Configured, but it can never run."""

    name = "inert"


class Serving(Tool):
    """A tool with something of its own to do and nothing to be told."""

    name = "serving"

    def __init__(self, context):
        super().__init__(context)
        self.running = asyncio.Event()
        self.closed = 0

    async def run(self) -> None:
        self.running.set()
        await asyncio.Event().wait()

    async def close(self) -> None:
        self.closed += 1


class Neighbourly(Recorder):
    """A tool that reaches for another one when something is said."""

    name = "neighbourly"
    requires = (Serving, Inert)

    def __init__(self, context):
        super().__init__(context)
        self.found: list[Tool | None] = []

    async def handle_utterance(self, utterance, session) -> None:
        await super().handle_utterance(utterance, session)
        self.found.append(self.tools.find(Serving))


class Presumptuous(Recorder):
    """A tool that reaches for one it never said it wanted."""

    name = "presumptuous"

    async def handle_utterance(self, utterance, session) -> None:
        await super().handle_utterance(utterance, session)
        self.found = self.tools.find(Serving)


class Chicken(Recorder):
    name = "chicken"


class Egg(Recorder):
    name = "egg"


# Settled after both exist, because each names the other and no single pass
# through a module can do that. A real pair would be two imports pointing at
# each other, which is the thing worth catching.
Chicken.requires = (Egg,)
Egg.requires = (Chicken,)


class Exploding(Tool):
    name = "exploding"

    async def handle_utterance(self, utterance, session) -> None:
        raise RuntimeError("no")

    async def handle_finished(self, transcript) -> None:
        raise RuntimeError("still no")

    async def handle_joined(self, source) -> None:
        raise RuntimeError("nor that")

    async def prewarm(self) -> None:
        raise RuntimeError("not even that")


class Unbuildable(Tool):
    name = "unbuildable"

    def __init__(self, context):
        raise ValueError("missing something it needed")


class FakeSession:
    def __init__(self, source: Source) -> None:
        self.source = source


def _servers(
    users: dict[int, str] | None = None, **tools_by_server: dict[str, ToolSettings]
) -> dict[int, ServerConfig]:
    """Build a servers mapping from `alias=<tools>` keyword arguments."""
    ids = {FIRST_ALIAS: FIRST_SERVER, SECOND_ALIAS: SECOND_SERVER}
    aliases = {"first": FIRST_ALIAS, "second": SECOND_ALIAS}

    return {
        ids[aliases[key]]: ServerConfig(
            alias=aliases[key], users=users or {}, tools=tools
        )
        for key, tools in tools_by_server.items()
    }


def _enabled(config: dict | None = None) -> ToolSettings:
    return ToolSettings(enabled=True, config=config or {})


def _utterance(text: str = "something") -> Utterance:
    return Utterance(
        timestamp=datetime.now().astimezone(), user_id=1, user="someone", text=text
    )


def _transcript(source: Source, path: Path) -> Transcript:
    opened = datetime.now().astimezone()
    return Transcript(
        path=path,
        source=source,
        opened=opened,
        closed=opened + timedelta(minutes=5),
        utterances=0,
    )


def _only_tool(runner: ToolRunner, server_id: int, moment: str) -> Tool:
    bucket = getattr(runner, moment)
    return bucket[server_id][0]


def _only_serving(runner: ToolRunner) -> Tool:
    """The one tool with a run of its own, whatever it is keyed under."""
    (tool,) = runner._serving.values()
    return tool


# ── construction ──────────────────────────────────


def test_an_enabled_tool_is_built_with_its_config():
    config = {"some-setting": "a value"}
    runner = ToolRunner(
        _servers(first={TOOL_NAME: _enabled(config)}), {TOOL_NAME: Recorder}
    )

    tool = _only_tool(runner, FIRST_SERVER, "_on_utterance")

    assert tool.server == FIRST_ALIAS
    assert tool.config == config
    assert runner.problems == []


def test_a_disabled_tool_is_not_built():
    runner = ToolRunner(
        _servers(first={TOOL_NAME: ToolSettings(enabled=False, config={})}),
        {TOOL_NAME: Recorder},
    )

    assert runner.describe() == {}
    assert runner.problems == []


def test_a_tool_nobody_registered_is_reported_and_skipped():
    runner = ToolRunner(_servers(first={"nonexistent": _enabled()}), {})

    assert runner.describe() == {}
    assert any("nonexistent" in problem for problem in runner.problems)


def test_a_tool_that_will_not_start_is_reported_and_skipped():
    runner = ToolRunner(
        _servers(first={TOOL_NAME: _enabled()}), {TOOL_NAME: Unbuildable}
    )

    assert runner.describe() == {}
    assert any("missing something it needed" in problem for problem in runner.problems)


def test_a_tool_handling_neither_moment_is_reported():
    """Configured but inert is a mistake worth naming, not a silent no-op."""
    runner = ToolRunner(_servers(first={TOOL_NAME: _enabled()}), {TOOL_NAME: Inert})

    assert runner.describe() == {}
    assert any("never run" in problem for problem in runner.problems)


def test_tools_are_sorted_by_the_moments_they_handle():
    registry = {
        "utterance-only": UtteranceOnly,
        "finished-only": FinishedOnly,
        "join-only": JoinOnly,
    }
    runner = ToolRunner(
        _servers(
            first={
                "utterance-only": _enabled(),
                "finished-only": _enabled(),
                "join-only": _enabled(),
            }
        ),
        registry,
    )

    assert [type(tool) for tool in runner._on_utterance[FIRST_SERVER]] == [UtteranceOnly]
    assert [type(tool) for tool in runner._on_finished[FIRST_SERVER]] == [FinishedOnly]
    assert [type(tool) for tool in runner._on_joined[FIRST_SERVER]] == [JoinOnly]
    assert runner.describe() == {
        FIRST_ALIAS: ("finished-only", "join-only", "utterance-only")
    }


def test_a_tool_that_only_hears_a_join_is_not_inert():
    """Nothing is said and no transcript is read, and it is still a tool."""
    runner = ToolRunner(_servers(first={"join-only": _enabled()}), {"join-only": JoinOnly})

    assert runner.describe() == {FIRST_ALIAS: ("join-only",)}
    assert runner.problems == []


def test_a_tool_is_built_with_its_servers_roster():
    """Who might speak is the one thing knowable before anybody does."""
    runner = ToolRunner(
        _servers(users=ROSTER, first={TOOL_NAME: _enabled()}), {TOOL_NAME: Recorder}
    )

    assert _only_tool(runner, FIRST_SERVER, "_on_utterance").users == ROSTER


def test_a_server_with_no_roster_hands_over_an_empty_one():
    """So a tool never has to check whether it was given anything."""
    runner = ToolRunner(_servers(first={TOOL_NAME: _enabled()}), {TOOL_NAME: Recorder})

    assert _only_tool(runner, FIRST_SERVER, "_on_utterance").users == {}


def test_each_server_gets_its_own_instance():
    """A tool holds per-server state, so two servers must not share one."""
    runner = ToolRunner(
        _servers(first={TOOL_NAME: _enabled()}, second={TOOL_NAME: _enabled()}),
        {TOOL_NAME: Recorder},
    )

    first = _only_tool(runner, FIRST_SERVER, "_on_utterance")
    second = _only_tool(runner, SECOND_SERVER, "_on_utterance")

    assert first is not second
    assert first.server == FIRST_ALIAS
    assert second.server == SECOND_ALIAS


# ── dispatch ──────────────────────────────────────


async def test_an_utterance_reaches_its_servers_tool():
    runner = ToolRunner(_servers(first={TOOL_NAME: _enabled()}), {TOOL_NAME: Recorder})
    tool = _only_tool(runner, FIRST_SERVER, "_on_utterance")

    await runner.dispatch_utterance(FakeSession(FIRST_SOURCE), _utterance("hello"))

    assert [utterance.text for utterance in tool.utterances] == ["hello"]


async def test_a_finished_transcript_reaches_its_servers_tool(tmp_path):
    runner = ToolRunner(_servers(first={TOOL_NAME: _enabled()}), {TOOL_NAME: Recorder})
    tool = _only_tool(runner, FIRST_SERVER, "_on_finished")
    transcript = _transcript(FIRST_SOURCE, tmp_path / "session.jsonl")

    await runner.dispatch_finished(transcript)

    assert tool.transcripts == [transcript]


async def test_a_join_reaches_its_servers_tool():
    runner = ToolRunner(_servers(first={"join-only": _enabled()}), {"join-only": JoinOnly})
    tool = _only_tool(runner, FIRST_SERVER, "_on_joined")

    await runner.dispatch_joined(FIRST_SOURCE)

    assert tool.joined == [FIRST_SOURCE]


async def test_one_servers_tool_never_hears_anothers_join():
    runner = ToolRunner(
        _servers(first={"join-only": _enabled()}, second={"join-only": _enabled()}),
        {"join-only": JoinOnly},
    )
    first = _only_tool(runner, FIRST_SERVER, "_on_joined")
    second = _only_tool(runner, SECOND_SERVER, "_on_joined")

    await runner.dispatch_joined(SECOND_SOURCE)

    assert first.joined == []
    assert second.joined == [SECOND_SOURCE]


async def test_one_servers_tool_never_sees_anothers_transcript(tmp_path):
    runner = ToolRunner(
        _servers(first={TOOL_NAME: _enabled()}, second={TOOL_NAME: _enabled()}),
        {TOOL_NAME: Recorder},
    )
    first = _only_tool(runner, FIRST_SERVER, "_on_utterance")
    second = _only_tool(runner, SECOND_SERVER, "_on_utterance")

    await runner.dispatch_utterance(FakeSession(SECOND_SOURCE), _utterance())
    await runner.dispatch_finished(_transcript(SECOND_SOURCE, tmp_path / "s.jsonl"))

    assert first.utterances == []
    assert first.transcripts == []
    assert len(second.utterances) == 1


async def test_a_server_with_no_tools_dispatches_nothing(tmp_path):
    runner = ToolRunner({}, {TOOL_NAME: Recorder})

    await runner.dispatch_utterance(FakeSession(FIRST_SOURCE), _utterance())
    await runner.dispatch_finished(_transcript(FIRST_SOURCE, tmp_path / "s.jsonl"))
    await runner.dispatch_joined(FIRST_SOURCE)


# ── pre-warming ───────────────────────────────────


async def test_a_tool_that_warms_is_warmed_at_startup():
    runner = ToolRunner(
        _servers(first={WARMING_TOOL_NAME: _enabled()}), {WARMING_TOOL_NAME: Warming}
    )
    tool = _only_tool(runner, FIRST_SERVER, "_on_utterance")

    await runner.prewarm()

    assert tool.warmed == 1


async def test_a_tool_that_does_not_warm_is_not_asked_to():
    runner = ToolRunner(_servers(first={TOOL_NAME: _enabled()}), {TOOL_NAME: Recorder})

    await runner.prewarm()

    assert runner._warming == []


async def test_each_servers_tool_is_warmed():
    """A phrase is per server, because the roster it comes from is."""
    runner = ToolRunner(
        _servers(
            first={WARMING_TOOL_NAME: _enabled()}, second={WARMING_TOOL_NAME: _enabled()}
        ),
        {WARMING_TOOL_NAME: Warming},
    )

    await runner.prewarm()

    assert [tool.warmed for tool in runner._warming] == [1, 1]


async def test_a_tool_that_can_never_run_is_not_warmed():
    """Nothing should be prepared for a tool that will never be asked for it."""
    runner = ToolRunner(
        _servers(first={WarmingInert.name: _enabled()}),
        {WarmingInert.name: WarmingInert},
    )

    await runner.prewarm()

    assert runner._warming == []
    assert any("never run" in problem for problem in runner.problems)


async def test_warming_nothing_is_not_an_error():
    await ToolRunner({}, {TOOL_NAME: Recorder}).prewarm()


async def test_a_raising_prewarm_does_not_stop_the_others(caplog):
    registry = {WARMING_TOOL_NAME: Warming, OTHER_TOOL_NAME: Exploding}
    runner = ToolRunner(
        _servers(first={OTHER_TOOL_NAME: _enabled(), WARMING_TOOL_NAME: _enabled()}),
        registry,
    )
    warming = next(tool for tool in runner._warming if isinstance(tool, Warming))

    with caplog.at_level("ERROR"):
        await runner.prewarm()

    assert warming.warmed == 1
    assert any("exploding" in record.message.lower() for record in caplog.records)


async def test_prewarm_cancellation_is_not_swallowed():
    """Shutting down mid-warm must not be mistaken for a tool failing."""

    class Cancelling(Recorder):
        name = "cancelling"

        async def prewarm(self) -> None:
            raise asyncio.CancelledError()

    runner = ToolRunner(
        _servers(first={TOOL_NAME: _enabled()}), {TOOL_NAME: Cancelling}
    )

    with pytest.raises(asyncio.CancelledError):
        await runner.prewarm()


# ── failure isolation ─────────────────────────────


async def test_a_raising_tool_does_not_stop_the_others(tmp_path, caplog):
    registry = {TOOL_NAME: Recorder, OTHER_TOOL_NAME: Exploding}
    runner = ToolRunner(
        _servers(first={OTHER_TOOL_NAME: _enabled(), TOOL_NAME: _enabled()}), registry
    )
    recorder = next(
        tool
        for tool in runner._on_utterance[FIRST_SERVER]
        if isinstance(tool, Recorder)
    )

    await runner.dispatch_utterance(FakeSession(FIRST_SOURCE), _utterance("survives"))
    await runner.dispatch_finished(_transcript(FIRST_SOURCE, tmp_path / "s.jsonl"))

    assert [utterance.text for utterance in recorder.utterances] == ["survives"]
    assert len(recorder.transcripts) == 1


async def test_a_raising_join_handler_does_not_stop_the_others(caplog):
    """A join reaches a channel; one tool failing to write on it is its own affair."""
    registry = {"join-only": JoinOnly, OTHER_TOOL_NAME: Exploding}
    runner = ToolRunner(
        _servers(first={OTHER_TOOL_NAME: _enabled(), "join-only": _enabled()}), registry
    )
    joiner = next(
        tool for tool in runner._on_joined[FIRST_SERVER] if isinstance(tool, JoinOnly)
    )

    with caplog.at_level("ERROR"):
        await runner.dispatch_joined(FIRST_SOURCE)

    assert joiner.joined == [FIRST_SOURCE]
    assert any("exploding" in record.message.lower() for record in caplog.records)


async def test_a_raising_tool_is_logged(caplog):
    runner = ToolRunner(
        _servers(first={TOOL_NAME: _enabled()}), {TOOL_NAME: Exploding}
    )

    with caplog.at_level("ERROR"):
        await runner.dispatch_utterance(FakeSession(FIRST_SOURCE), _utterance())

    assert any("exploding" in record.message.lower() for record in caplog.records)


async def test_a_raising_tool_does_not_reach_the_caller():
    """Nothing a tool does may cost an utterance."""
    runner = ToolRunner(
        _servers(first={TOOL_NAME: _enabled()}), {TOOL_NAME: Exploding}
    )

    await runner.dispatch_utterance(FakeSession(FIRST_SOURCE), _utterance())


async def test_cancellation_is_not_swallowed():
    """Shutdown must not be mistaken for a tool failing."""

    class Cancelling(Tool):
        name = "cancelling"

        async def handle_utterance(self, utterance, session) -> None:
            raise asyncio.CancelledError()

    runner = ToolRunner(
        _servers(first={TOOL_NAME: _enabled()}), {TOOL_NAME: Cancelling}
    )

    with pytest.raises(asyncio.CancelledError):
        await runner.dispatch_utterance(FakeSession(FIRST_SOURCE), _utterance())


# ── running ───────────────────────────────────────


async def test_a_tool_that_only_runs_is_not_inert():
    """It hears nothing and says nothing, which is not the same as doing nothing."""
    runner = ToolRunner(_servers(first={Serving.name: _enabled()}), {Serving.name: Serving})

    assert runner.describe() == {FIRST_ALIAS: (Serving.name,)}
    assert runner.problems == []


async def test_a_tool_with_something_to_do_is_set_going():
    runner = ToolRunner(_servers(first={Serving.name: _enabled()}), {Serving.name: Serving})
    tool = _only_serving(runner)

    tasks = runner.start()

    async with asyncio.timeout(PATIENCE_SECONDS):
        await tool.running.wait()

    for task in tasks:
        task.cancel()


async def test_each_servers_tool_is_set_going():
    runner = ToolRunner(
        _servers(first={Serving.name: _enabled()}, second={Serving.name: _enabled()}),
        {Serving.name: Serving},
    )

    tasks = runner.start()

    assert len(tasks) == 2
    for task in tasks:
        task.cancel()


async def test_nothing_is_set_going_twice():
    """However many readies the gateway sends."""
    runner = ToolRunner(_servers(first={Serving.name: _enabled()}), {Serving.name: Serving})
    started = runner.start()

    assert runner.start() == started
    for task in started:
        task.cancel()


async def test_a_tool_whose_run_raises_is_logged(caplog):
    """Nothing awaits the task, so the exception would otherwise never be collected."""

    class Failing(Tool):
        name = "failing"

        async def run(self) -> None:
            raise RuntimeError("it fell over")

    runner = ToolRunner(_servers(first={TOOL_NAME: _enabled()}), {TOOL_NAME: Failing})

    with caplog.at_level("ERROR"):
        await asyncio.gather(*runner.start())

    assert any("it fell over" in record.message for record in caplog.records)


# ── closing ───────────────────────────────────────


async def test_a_tool_with_something_to_finish_is_closed():
    runner = ToolRunner(_servers(first={Serving.name: _enabled()}), {Serving.name: Serving})
    tool = _only_serving(runner)

    await runner.close()

    assert tool.closed == 1


async def test_a_tool_that_does_not_close_is_not_asked_to():
    runner = ToolRunner(_servers(first={TOOL_NAME: _enabled()}), {TOOL_NAME: Recorder})

    await runner.close()

    assert runner._closing == []


async def test_a_tool_that_can_never_run_is_not_closed():
    """Nothing should be finished on behalf of a tool that never started."""
    runner = ToolRunner(_servers(first={Inert.name: _enabled()}), {Inert.name: Inert})

    await runner.close()

    assert runner._closing == []


async def test_a_raising_close_does_not_stop_the_others(caplog):
    class Stubborn(Serving):
        name = "stubborn"

        async def close(self) -> None:
            raise RuntimeError("it will not lie down")

    registry = {Serving.name: Serving, Stubborn.name: Stubborn}
    runner = ToolRunner(
        _servers(first={Stubborn.name: _enabled(), Serving.name: _enabled()}), registry
    )
    closing = next(tool for tool in runner._closing if type(tool) is Serving)

    with caplog.at_level("ERROR"):
        await runner.close()

    assert closing.closed == 1
    assert any("will not lie down" in record.message for record in caplog.records)


# ── one tool reaching another ─────────────────────


async def test_a_tool_can_reach_another_on_its_server():
    registry = {Neighbourly.name: Neighbourly, Serving.name: Serving}
    runner = ToolRunner(
        _servers(first={Neighbourly.name: _enabled(), Serving.name: _enabled()}), registry
    )
    tool = _only_tool(runner, FIRST_SERVER, "_on_utterance")

    await runner.dispatch_utterance(FakeSession(FIRST_SOURCE), _utterance())

    assert isinstance(tool.found[0], Serving)


async def test_a_tool_reaches_one_built_after_it():
    """
    Which is why a tool looks when it needs something rather than when it is made.

    The box is handed over before either exists and fills as they are built, so
    the order the config file lists them in cannot decide what is reachable.
    """
    registry = {Neighbourly.name: Neighbourly, Serving.name: Serving}
    runner = ToolRunner(
        _servers(first={Neighbourly.name: _enabled(), Serving.name: _enabled()}), registry
    )

    serving = _only_serving(runner)

    assert list(serving.tools._tools) == [
        _only_tool(runner, FIRST_SERVER, "_on_utterance"),
        serving,
    ]


async def test_a_tool_that_is_not_enabled_is_not_reachable():
    runner = ToolRunner(
        _servers(first={Neighbourly.name: _enabled()}), {Neighbourly.name: Neighbourly}
    )
    tool = _only_tool(runner, FIRST_SERVER, "_on_utterance")

    await runner.dispatch_utterance(FakeSession(FIRST_SOURCE), _utterance())

    assert tool.found == [None]


async def test_one_servers_tool_never_reaches_anothers():
    """A tool holds per-server state; finding the wrong one would cross the two."""
    registry = {Neighbourly.name: Neighbourly, Serving.name: Serving}
    runner = ToolRunner(
        _servers(
            first={Neighbourly.name: _enabled()},
            second={Neighbourly.name: _enabled(), Serving.name: _enabled()},
        ),
        registry,
    )
    here = _only_tool(runner, FIRST_SERVER, "_on_utterance")

    await runner.dispatch_utterance(FakeSession(FIRST_SOURCE), _utterance())

    assert here.found == [None]


async def test_a_tool_that_can_never_run_is_not_reachable():
    """It will never do anything; it should not be what a neighbour settles for."""
    registry = {Neighbourly.name: Neighbourly, Inert.name: Inert}
    runner = ToolRunner(
        _servers(first={Neighbourly.name: _enabled(), Inert.name: _enabled()}), registry
    )
    tool = _only_tool(runner, FIRST_SERVER, "_on_utterance")

    assert tool.tools.find(Inert) is None


async def test_a_tool_reaching_for_what_it_never_declared_is_refused(caplog):
    """A declaration nothing enforces is one the cycle check reads and gets wrong."""
    registry = {Presumptuous.name: Presumptuous, Serving.name: Serving}
    runner = ToolRunner(
        _servers(first={Presumptuous.name: _enabled(), Serving.name: _enabled()}),
        registry,
    )
    tool = _only_tool(runner, FIRST_SERVER, "_on_utterance")

    await runner.dispatch_utterance(FakeSession(FIRST_SOURCE), _utterance())

    assert tool.found is None
    assert "without declaring it" in caplog.text


# ── tools that require each other ─────────────────


def _circular() -> ToolRunner:
    return ToolRunner(
        _servers(first={Chicken.name: _enabled(), Egg.name: _enabled()}),
        {Chicken.name: Chicken, Egg.name: Egg},
    )


async def test_a_circle_of_tools_is_reported():
    problems = _circular().problems

    assert len(problems) == 1
    assert "circle" in problems[0]


async def test_a_circle_names_every_tool_in_it():
    (problem,) = _circular().problems

    assert Chicken.name in problem
    assert Egg.name in problem


async def test_nothing_in_a_circle_is_built():
    """A server that starts and then wedges is a worse way to find out."""
    runner = _circular()

    assert runner.describe() == {}


async def test_a_tool_outside_a_circle_is_still_built():
    """One bad pair should not take the rest of the server down with it."""
    registry = {Chicken.name: Chicken, Egg.name: Egg, Serving.name: Serving}
    runner = ToolRunner(
        _servers(
            first={
                Chicken.name: _enabled(),
                Egg.name: _enabled(),
                Serving.name: _enabled(),
            }
        ),
        registry,
    )

    assert runner.describe() == {FIRST_ALIAS: (Serving.name,)}


async def test_a_circle_nobody_enabled_is_not_a_circle():
    """An edge pointing at a tool the server left off reaches nothing to call."""
    runner = ToolRunner(
        _servers(first={Chicken.name: _enabled()}), {Chicken.name: Chicken}
    )

    assert runner.problems == []
    assert runner.describe() == {FIRST_ALIAS: (Chicken.name,)}


async def test_closing_stops_a_running_tool_first():
    """A tool writing itself out mid-loop would be racing itself for the file."""
    runner = ToolRunner(_servers(first={Serving.name: _enabled()}), {Serving.name: Serving})
    tasks = runner.start()

    await runner.close()

    assert all(task.done() for task in tasks)


# ── switching ─────────────────────────────────────


SETTING_KEY = "window_seconds"
FILE_SETTING = 300.0
ASKED_SETTING = 5.0
IMPOSSIBLE_SETTING = -1.0


class SecondRecorder(Recorder):
    """A second tool of the same shape, for what happens to the one beside it."""

    name = OTHER_TOOL_NAME


class Fussy(Recorder):
    """A tool that reads one setting and refuses a value it cannot work with."""

    name = "fussy"

    def __init__(self, context):
        super().__init__(context)
        self.window = context.config.get(SETTING_KEY, FILE_SETTING)

        if self.window < 0:
            raise ValueError("a window cannot run backwards")


def _disabled(config: dict | None = None) -> ToolSettings:
    return ToolSettings(enabled=False, config=config or {})


def _state(runner: ToolRunner, name: str, server_id: int = FIRST_SERVER):
    return runner.status(server_id, name)


def _only_built(runner: ToolRunner) -> Tool | None:
    """The one instance that exists, or nothing if none does."""
    built = list(runner._built.values())
    return built[0] if built else None


async def test_a_tool_the_file_left_off_can_be_switched_on():
    """The config block is read either way, so there is something to build from."""
    runner = ToolRunner(
        _servers(first={Fussy.name: _disabled({SETTING_KEY: ASKED_SETTING})}),
        {Fussy.name: Fussy},
    )

    assert _only_built(runner) is None

    await runner.enable(FIRST_SERVER, Fussy.name)
    tool = _only_tool(runner, FIRST_SERVER, "_on_utterance")

    assert tool.window == ASKED_SETTING


async def test_a_tool_switched_off_hears_nothing():
    runner = ToolRunner(_servers(first={TOOL_NAME: _enabled()}), {TOOL_NAME: Recorder})
    tool = _only_tool(runner, FIRST_SERVER, "_on_utterance")

    await runner.disable(FIRST_SERVER, TOOL_NAME)
    await runner.dispatch_utterance(FakeSession(FIRST_SOURCE), _utterance())

    assert tool.utterances == []
    assert runner._on_utterance[FIRST_SERVER] == []


async def test_a_tool_switched_back_on_is_the_one_that_left():
    """Which is the whole reason an instance is kept rather than closed."""
    runner = ToolRunner(_servers(first={TOOL_NAME: _enabled()}), {TOOL_NAME: Recorder})
    tool = _only_tool(runner, FIRST_SERVER, "_on_utterance")
    await runner.dispatch_utterance(FakeSession(FIRST_SOURCE), _utterance("before"))

    await runner.disable(FIRST_SERVER, TOOL_NAME)
    await runner.enable(FIRST_SERVER, TOOL_NAME)
    await runner.dispatch_utterance(FakeSession(FIRST_SOURCE), _utterance("after"))

    assert _only_tool(runner, FIRST_SERVER, "_on_utterance") is tool
    assert [utterance.text for utterance in tool.utterances] == ["before", "after"]


async def test_switching_a_tool_on_twice_dispatches_it_once():
    """A tool filed twice is one announcement asked for and two announcements made."""
    runner = ToolRunner(_servers(first={TOOL_NAME: _enabled()}), {TOOL_NAME: Recorder})

    await runner.enable(FIRST_SERVER, TOOL_NAME)
    await runner.dispatch_utterance(FakeSession(FIRST_SOURCE), _utterance())

    assert len(runner._on_utterance[FIRST_SERVER]) == 1
    assert len(_only_tool(runner, FIRST_SERVER, "_on_utterance").utterances) == 1


async def test_a_tool_switched_off_stops_running():
    runner = ToolRunner(_servers(first={Serving.name: _enabled()}), {Serving.name: Serving})
    tool = _only_serving(runner)
    runner.start()

    async with asyncio.timeout(PATIENCE_SECONDS):
        await tool.running.wait()

    await runner.disable(FIRST_SERVER, Serving.name)

    assert runner.running == ()


async def test_a_tool_switched_on_after_the_rest_starts_running():
    """Startup has already happened, so nothing else is going to start it."""
    registry = {Serving.name: Serving}
    runner = ToolRunner(_servers(first={Serving.name: _disabled()}), registry)
    runner.start()

    await runner.enable(FIRST_SERVER, Serving.name)
    tool = _only_serving(runner)

    async with asyncio.timeout(PATIENCE_SECONDS):
        await tool.running.wait()

    for task in runner.running:
        task.cancel()


async def test_switching_one_tool_off_leaves_another_alone():
    runner = ToolRunner(
        _servers(first={TOOL_NAME: _enabled(), OTHER_TOOL_NAME: _enabled()}),
        {TOOL_NAME: Recorder, OTHER_TOOL_NAME: SecondRecorder},
    )

    await runner.disable(FIRST_SERVER, TOOL_NAME)
    await runner.dispatch_utterance(FakeSession(FIRST_SOURCE), _utterance())

    (left,) = runner._on_utterance[FIRST_SERVER]

    assert left.name == OTHER_TOOL_NAME
    assert len(left.utterances) == 1


async def test_a_tool_switched_off_is_not_reachable_by_a_neighbour():
    """The box is what a neighbour looks in, so leaving it is what off means."""
    registry = {Neighbourly.name: Neighbourly, Serving.name: Serving}
    runner = ToolRunner(
        _servers(first={Neighbourly.name: _enabled(), Serving.name: _enabled()}), registry
    )

    await runner.disable(FIRST_SERVER, Serving.name)
    await runner.dispatch_utterance(FakeSession(FIRST_SOURCE), _utterance())

    assert _only_tool(runner, FIRST_SERVER, "_on_utterance").found == [None]


async def test_a_tool_switched_off_is_still_closed_at_shutdown():
    """A tally switched off halfway through an evening still has something to write."""
    runner = ToolRunner(_servers(first={Serving.name: _enabled()}), {Serving.name: Serving})
    tool = _only_serving(runner)

    await runner.disable(FIRST_SERVER, Serving.name)
    await runner.close()

    assert tool.closed == 1


async def test_a_name_nothing_answers_to_is_refused():
    runner = ToolRunner(_servers(first={}), {TOOL_NAME: Recorder})

    said = await runner.enable(FIRST_SERVER, "not-a-tool")

    assert "no tool named" in said


async def test_a_tool_that_will_not_build_says_why():
    runner = ToolRunner(
        _servers(first={Unbuildable.name: _disabled()}), {Unbuildable.name: Unbuildable}
    )

    said = await runner.enable(FIRST_SERVER, Unbuildable.name)

    assert "missing something it needed" in said
    assert _only_built(runner) is None


async def test_a_tool_that_is_already_on_says_so():
    runner = ToolRunner(_servers(first={TOOL_NAME: _enabled()}), {TOOL_NAME: Recorder})

    assert "already on" in await runner.enable(FIRST_SERVER, TOOL_NAME)


async def test_a_tool_that_is_already_off_says_so():
    runner = ToolRunner(_servers(first={TOOL_NAME: _disabled()}), {TOOL_NAME: Recorder})

    assert "already off" in await runner.disable(FIRST_SERVER, TOOL_NAME)


async def test_a_circle_is_refused_rather_than_built():
    """Found at startup it is a line in the report; found here it would be the process."""
    registry = {Chicken.name: Chicken, Egg.name: Egg}
    runner = ToolRunner(
        _servers(first={Chicken.name: _enabled(), Egg.name: _disabled()}), registry
    )

    said = await runner.enable(FIRST_SERVER, Egg.name)

    assert "circle" in said


# ── what a server is doing ────────────────────────


def test_every_tool_the_registry_knows_is_listed():
    """Somebody asking what can be switched on is asking about all of them."""
    runner = ToolRunner(
        _servers(first={TOOL_NAME: _enabled()}),
        {TOOL_NAME: Recorder, OTHER_TOOL_NAME: SecondRecorder},
    )

    assert [state.name for state in runner.state(FIRST_SERVER)] == sorted(
        [TOOL_NAME, OTHER_TOOL_NAME]
    )


def test_a_name_the_registry_does_not_know_is_still_listed():
    """It is in the file, somebody meant something by it, and a typo should say so."""
    runner = ToolRunner(_servers(first={"mistyped": _enabled()}), {TOOL_NAME: Recorder})

    assert not _state(runner, "mistyped").known


async def test_a_switch_reports_itself_as_changed():
    runner = ToolRunner(_servers(first={TOOL_NAME: _enabled()}), {TOOL_NAME: Recorder})

    assert not _state(runner, TOOL_NAME).changed

    await runner.disable(FIRST_SERVER, TOOL_NAME)
    state = _state(runner, TOOL_NAME)

    assert state.changed
    assert state.configured
    assert not state.on
    assert state.built


# ── configuring ───────────────────────────────────


async def test_a_setting_is_read_back_as_it_was_written():
    runner = ToolRunner(
        _servers(first={Fussy.name: _enabled({SETTING_KEY: FILE_SETTING})}),
        {Fussy.name: Fussy},
    )

    assert runner.configured_value(FIRST_SERVER, Fussy.name, SETTING_KEY) == FILE_SETTING


async def test_a_setting_replaces_what_the_file_said():
    runner = ToolRunner(
        _servers(first={Fussy.name: _enabled({SETTING_KEY: FILE_SETTING})}),
        {Fussy.name: Fussy},
    )

    await runner.reconfigure(FIRST_SERVER, Fussy.name, SETTING_KEY, ASKED_SETTING)

    assert _only_tool(runner, FIRST_SERVER, "_on_utterance").window == ASKED_SETTING


async def test_a_reconfigured_tool_is_a_new_one():
    """It reads its config once, so a new value means a new instance and a clean slate."""
    runner = ToolRunner(
        _servers(first={Fussy.name: _enabled()}), {Fussy.name: Fussy}
    )
    before = _only_tool(runner, FIRST_SERVER, "_on_utterance")
    await runner.dispatch_utterance(FakeSession(FIRST_SOURCE), _utterance())

    await runner.reconfigure(FIRST_SERVER, Fussy.name, SETTING_KEY, ASKED_SETTING)
    after = _only_tool(runner, FIRST_SERVER, "_on_utterance")

    assert after is not before
    assert after.utterances == []


async def test_a_setting_the_tool_refuses_leaves_it_as_it_was():
    runner = ToolRunner(
        _servers(first={Fussy.name: _enabled({SETTING_KEY: FILE_SETTING})}),
        {Fussy.name: Fussy},
    )

    said = await runner.reconfigure(
        FIRST_SERVER, Fussy.name, SETTING_KEY, IMPOSSIBLE_SETTING
    )

    assert "backwards" in said
    assert _only_tool(runner, FIRST_SERVER, "_on_utterance").window == FILE_SETTING
    assert runner.configured_value(FIRST_SERVER, Fussy.name, SETTING_KEY) == FILE_SETTING


async def test_configuring_a_tool_that_is_off_does_not_switch_it_on():
    """A change to a setting is not a decision to start running something."""
    runner = ToolRunner(
        _servers(first={Fussy.name: _disabled()}), {Fussy.name: Fussy}
    )

    said = await runner.reconfigure(
        FIRST_SERVER, Fussy.name, SETTING_KEY, ASKED_SETTING
    )

    assert not _state(runner, Fussy.name).on
    assert runner._on_utterance.get(FIRST_SERVER, []) == []
    assert "switched on" in said


async def test_a_setting_written_while_a_tool_is_off_is_what_it_starts_with():
    """Nothing was rebuilt, so the value waits rather than being read and discarded."""
    runner = ToolRunner(
        _servers(first={Fussy.name: _disabled()}), {Fussy.name: Fussy}
    )

    await runner.reconfigure(FIRST_SERVER, Fussy.name, SETTING_KEY, ASKED_SETTING)
    await runner.enable(FIRST_SERVER, Fussy.name)

    assert _only_tool(runner, FIRST_SERVER, "_on_utterance").window == ASKED_SETTING
