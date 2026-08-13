---
layout: page
title: About
eyebrow: What it is
lede: A Discord bot that writes down what was said in a voice channel, hands the result to tools, and answers the room out loud.
description: How miss-quote is put together — the split between local audio handling and remote transcription, the transcript format on disk, the speech path, and the tool contract.
---

miss-quote is a Discord bot that sits in on your D&D session and listens to the adventures, so the evening ends up with a record instead of in everyone's half-memory of it. When the bot leaves it summarizes what happened, and next time you can ask: "what happened last session" and a bard recounts the night. It gets up to other shenanigans too.

Underneath that, it transcribes Discord voice channels to a per-session, per-speaker JSONL transcript and hands that result to tools. Transcription is delegated to a [Wyoming](https://github.com/rhasspy/wyoming) ASR server rather than run in-process, so **this container** is a CPU-only workload with no model weights and no cache volume for them.

**That moves the GPU rather than removing it.** The bot does nothing without a reachable ASR server, and one worth pointing it at wants a GPU — as does the synthesizer behind anything said out loud, and the endpoint behind a summary. The narrower claim is the useful one: **the process that sits in a voice channel all evening is cheap and schedules anywhere, and the expensive hardware sits behind a socket where several things can share it.**

It began as a hard fork of [Leehyunbin0131/Discord-Realtime-STT-Bot](https://github.com/Leehyunbin0131/Discord-Realtime-STT-Bot).

## How it works

```mermaid
graph TD
    A["Discord gateway<br/><i>somebody speaks</i>"] -->|"48 kHz stereo PCM, 20 ms frames"| B

    subgraph LOCAL["SERIAL — in process, ~4.9 ms CPU per speaker per second of audio"]
        direction TB
        B["STTAudioSink.write<br/><i>voice-recv router thread</i>"]
        B -->|"soxr resample — 0.046 ms"| C["16 kHz mono int16"]
        C -->|"loop.call_soon_threadsafe"| D["Silero VAD, per 32 ms frame<br/><i>event loop</i> — 0.082 ms"]
        D --> E["per-speaker speech_buffer<br/>+ ring-buffer pre-roll"]
    end

    E -->|"speech to silence edge<br/>asyncio.create_task"| REMOTE

    subgraph REMOTE["PARALLEL — one connection per utterance, N ≤ MAX_CONCURRENT_TRANSCRIPTIONS"]
        direction LR
        F["Wyoming client<br/>utterance 1"]
        G["Wyoming client<br/>utterance 2"]
        H["Wyoming client<br/>utterance N"]
        F ~~~ G ~~~ H
    end

    REMOTE -->|"Transcribe / AudioStart / AudioChunk* / AudioStop"| I

    I["Wyoming ASR server<br/><i>WYOMING_HOST:WYOMING_PORT</i>"] -->|"Transcript, ~70 ms"| J["TranscriptSession"]
    J --> K["TRANSCRIPT_DIR/guild/channel/session.jsonl"]
    J -->|"handle_utterance"| L["Tools for this server"]
    K -.->|"handle_finished, on disconnect"| L
    A -.->|"handle_joined, on connect"| L

    L -.->|"tts.play"| T["<b>tts</b> tool<br/><i>one per server</i>"]
    T -.->|"a phrase"| M["Speech cache<br/><i>Ogg Opus in SPEECH_DIR/cache</i>"]
    T -.->|"a chime, by name"| CH["Chime library<br/><i>WAVs in SPEECH_DIR/chimes</i>"]
    M -.->|"on a miss"| N["Wyoming TTS<br/><i>TTS_HOST:TTS_PORT</i>"]
    M -.->|"Opus packets, sent unencoded"| Z
    CH -.->|"samples, chained ahead of the words"| Z

    Z["Discord gateway<br/><i>the bot answers</i>"]
```

The gateway is drawn at both ends because it is one connection: the channel the audio came from is the channel anything gets played back into.

The dotted half is optional and only exists for servers that enabled the `tts` tool; a deployment where none did never opens a TTS connection. Everything played into a channel goes through that one tool — it owns the cache, the chime library, the volume, and the voice connection, and the tools that decide *what* to say reach it through the toolbox.

Everything runs on one event loop in one process. The split that matters is between the two halves of the pipeline: **audio handling is local and serial, transcription is remote and parallel.**

### Local: serial, continuous, cheap

Resampling and VAD are ordinary blocking calls, run one frame at a time. They are a steady cost for as long as audio arrives, not a per-utterance burst — VAD has to see every frame, because VAD is what decides which frames are speech.

| Work | Cost | Rate, per speaker |
|---|---:|---|
| soxr resample, per 20 ms Discord frame | 0.046 ms | 50/s |
| Silero VAD, per 32 ms frame | 0.082 ms | 31.25/s |

That is **~4.9 ms of CPU per speaker per second of audio**, or about 0.5% of one core — 5% at ten concurrent speakers. Being serial costs nothing at this magnitude, which is why there is no worker process: a process boundary would cost more in serialization than the work it isolated.

Resampling runs on voice-recv's router thread, which holds a lock across all speakers, so nothing slow may be added there. Frames reach the event loop via `loop.call_soon_threadsafe`.

<details class="why" markdown="1">
<summary>The VAD model carries context, and must keep doing so</summary>

Silero v5's ONNX graph scores the current frame *together with* the trailing 64 samples of the previous one. Fed a bare 512-sample frame it does not error — it silently returns near-zero probability on unmistakable speech, and the bot transcribes nothing. `stt/vad.py` carries that context between calls, and `tests/test_vad.py` guards it with real speech; silence-based tests pass either way and will not catch a regression.

</details>

### Remote: parallel, bounded

At each speech-to-silence edge the buffered utterance is handed to `asyncio.create_task` and the coroutine immediately parks on socket I/O — the loop is free in the same tick. Nothing in this process ever blocks on transcription.

The ASR server accepts overlapping utterances, so speakers do not queue behind one another; measured against a GPU-backed Wyoming server, eight simultaneous 0.88 s utterances completed in 223 ms against 555 ms if run serially. A single utterance round-trips in about 70 ms.

`MAX_CONCURRENT_TRANSCRIPTIONS` caps how many are in flight. A further utterance ending while the cap is reached parks on the semaphore — it does not stall the loop and does not drop audio, it simply waits to open its connection. The bound exists so a busy channel cannot fan out unbounded connections against an ASR that other services may share; throughput gains past four are marginal anyway.

## Transcript format

Transcripts are filed one directory per guild, one per voice channel inside it, and **one file per session** — one visit by the bot to one voice channel:

```
TRANSCRIPT_DIR/
└── first-server/
    ├── general-voice/
    │   ├── 2026-07-26T20-14-03.jsonl
    │   └── 2026-07-27T09-31-55.jsonl
    └── side-room/
        └── 2026-07-27T21-02-40.jsonl
```

A session opens when the bot joins and closes when it leaves — because the channel emptied, because someone disconnected it, or because the pod terminated. The file is named for the moment it opened and keeps that name until it closes, so **a conversation spanning midnight stays in one file** and **rejoining starts a new one**. A session that opens in the same second as another in the same channel gets a `-2` on the end rather than appending to it.

Rejoining is qualified by the **resume window** ([`settings.transcripts.resume`]({{ '/configuration/#transcripts' | relative_url }}), 5 s). A channel that empties and refills inside it is treated as one conversation with a gap in it — someone's client dropped, or the last person stepped away — so the transcript is held open and appended to rather than sealed and replaced.

The server directory is its **alias from `servers`**, fixed in configuration rather than read from Discord; channels use their Discord name. Names are lowercased and reduced to `a-z0-9_-`, dropping dots and separators rather than escaping them, so no name can express a path traversal.

Neither carries an ID, with two consequences worth knowing:

- **Renaming a channel starts a new directory**, with nothing tying it to the old one.
- **Two names that reduce to the same slug share a directory** — two servers given one alias, or channels named `General` and `general`. Their sessions stay in separate files, but nothing in the tree says which came from where.

Nothing about the path can catch either, so the bot logs an error instead: duplicate aliases at startup, colliding channels when it joins one.

JSON Lines, one object per utterance, appended and flushed as produced:

```json
{"ts":"2026-07-26T21:14:03.412-07:00","user_id":1234567890,"user":"someone","text":"that should work"}
```

Guild and channel are not repeated in the line, the path already carrying them. `user_id` is recorded alongside the display name because display names change. Timestamps carry an explicit UTC offset, resolved through `TZ`.

### The capture schedule

**`monitored_channels` is the list of rooms on the record.** A voice channel absent from it is **never transcribed** — the bot still joins it, still hears it, still fines people in it, and nothing said there reaches disk. That list lives under the [`summary`]({{ '/configuration/#summary' | relative_url }}) tool, so **a server with `summary` disabled writes nothing down at all**. Reported at startup rather than left to be noticed.

**When** a listed room is written down is its `schedule` — see [writing a window]({{ '/configuration/#writing-a-window' | relative_url }}) for the syntax. A listed room naming no windows keeps every session, or whatever `settings.transcripts.schedule` says.

**A window is when an evening may *start* being recorded, not how long it may run.** The schedule is read once per session, when the bot joins, and the answer holds until the session seals — so a session that opens inside a window keeps writing until everybody disconnects, however far past the end of it.

The rule runs the other way too, and that is the part worth knowing before setting one. **A session opened a minute early is off the record for its whole length**, and so is one opened by a rejoin after a pod restart at two in the morning. Leaving the channel and coming back opens a new session, which is what fixes both; so does `!start-transcribing`.

**A window is also what says several sessions were one evening** — see [one evening, several sessions]({{ '/configuration/#one-evening-several-sessions' | relative_url }}).

**Only the writing down is scheduled.** Off the record the bot still transcribes and still hands each line to the tools that read one utterance at a time, so a fine is announced and counted whether or not the evening is being kept. A session that wrote nothing down seals as an empty one and takes its own file away, leaving no trace in the tree and producing no summary.

<details class="why" markdown="1">
<summary>Why a window cannot cut a conversation short</summary>

An evening does not stop being the evening at midnight, and a transcript cut off mid-conversation is worse than either the whole of it or none of it.

The room list belongs to `summary` because transcribing a room, summarizing it, and telling it back are one thing to whoever is sitting in it. Coupling them is what costs a server its transcripts when the tool goes off, and that is the price of configuring both in one place.

Every room on the record is listed at startup, and an off-the-record session is logged when it opens, so what is being kept is a fact about the deployment rather than something to work out from an empty directory.

</details>

### Starting and stopping by hand

Two commands override the capture schedule for the session the bot is currently in, for an evening it did not cover, a room it does not list, or one it does that nobody wanted kept:

| Command | Effect |
|---|---|
| `!start-transcribing` | Puts the open session on the record **from here on**. Nothing said before it was buffered anywhere, so there is nothing to backfill — this starts a transcript rather than completing one. Works in a room that is not in `monitored_channels` at all, which is the only way to record one |
| `!stop-transcribing` | Takes the open session off the record. **What is already written stays written**; stopping is a decision about what happens next, not a retraction. A session that never wrote anything still takes its own file away when it seals |

**Both require Administrator on the server**, since what they decide is whether everybody in the room is on the record. A refusal is said out loud rather than silently ignored — a rule nobody is told about is one everybody keeps testing.

**The override dies with the session.** Rejoining opens a new one, which consults the schedule afresh. It does survive a resume-window reconnect, since that is the same session.

### The status

While any session is on the record the bot sets its own status to `settings.presence.transcribing` — `🎙️ transcribing...` by default — and clears it when none is.

It **follows sessions, not speech** — a session being written down shows the status whether or not anybody is talking, and one held open for a reconnect still counts. Updates are deduplicated and sent only on a transition.

Two things to know before relying on it:

- **The presence is one per bot, not one per server.** Discord has no per-guild presence for bots, so a bot recording in one of two servers says so in both. It errs toward saying a conversation may be kept when it is not, which is the safe direction.
- **The emoji is part of the text.** A custom status carries an emoji field of its own and Discord does not apply it for bots, so the only spelling that reaches anybody is one written inside the words.

<details class="why" markdown="1">
<summary>Why there is a wording for recording and none for listening</summary>

This is a transparency signal rather than a status readout. Everybody can already see the bot sitting in a channel, and hearing on its own retains nothing material: a fine is counted and the words behind it are gone. What is worth announcing is the part that leaves something afterwards — a transcript on disk, and the summaries and retellings written off it.

Driving it off utterances rather than sessions would flicker and spend the gateway's presence budget saying nothing new.

</details>

## Summaries

A transcript is raw material and nobody wants to read one. The [`summary`]({{ '/configuration/#summary' | relative_url }}) tool turns a sealed session into an account of it, and files that account in a tree with the same shape under its own root:

```
SUMMARY_DIR/
└── first-server/
    └── general-voice/
        ├── 2026-07-26T20-14-03.txt
        └── 2026-07-27T09-31-55.txt
```

The same guild and channel directories, and **a file named for the transcript it summarizes** rather than for the moment it was written — so the two are found from each other by changing one path segment and one extension. Plain text rather than JSON: what is in the file is what was posted to the channel, readable with `cat` and greppable without a parser.

<details class="why" markdown="1">
<summary>Why a separate root, and why that filename</summary>

A transcript is everything anybody said; a summary is something you would show people. They can be mounted, backed up, and shared on different terms, and `settings.summaries.retention` is its own clock — keeping summaries for a year and transcripts for a month is a reasonable thing to want.

Naming a summary for its transcript rather than for its own moment means a session that took a `-2` to avoid a collision keeps it here, and a summary written late — by a backfill, or by a deployment pointed at a working endpoint after the fact — still lands on the right name.

</details>

### One evening is not always one session

A room that empties while everyone refills a glass, or a pod that restarts mid-deploy, files the rest of the night separately — and answering with the newest of those retells the last forty minutes of a four-hour evening.

So what a question looks up is the **run** of consecutive sessions with no more than `session_gap` between one ending and the next beginning, read in order and handed to the reteller as one piece of text. Which sessions get *written* as one account is a different question, decided by the schedule — see [one evening, several sessions]({{ '/configuration/#one-evening-several-sessions' | relative_url }}).

**`session_gap` is not `settings.transcripts.resume`** and should not be set to match it. The resume window holds a session open and delays every summary behind it; this is read long afterwards, off files already on disk. Widening the resume window cannot replace it either, because shutdown seals every session regardless.

<details class="why" markdown="1">
<summary>Four details that make a run hold together</summary>

- **The gap is measured close-to-open, not open-to-open.** A filename says only when a session *started*, so four hours of conversation followed five minutes later by more of it looks like a four-hour gap to anything comparing names. When a session ended survives on disk only as the timestamp of the last line in its JSONL, so this reads transcripts as well as names.
- **Sessions with no summary still count.** One under `minimum_utterances` is exactly what bridges the two halves around a reconnect, and enumerating summaries alone would break the chain at the point something has to hold it together.
- **A session with no summary is not an answer.** It can as easily be a conversation still in progress, or two minutes at the end of a night. Anchoring on it and stopping would report "no notes" with the notes sitting an hour behind, so anchors are taken in order until one turns up an evening with something in it.
- **An unknown ending stops the chain.** A session whose transcript has been pruned out from under its summary is read as having closed when it opened — the safe way to be wrong, degrading to one-session behaviour rather than stitching an unrelated conversation onto somebody's evening.

The reteller is told the pieces may arrive that way via `{% raw %}{retelling_instructions}{% endraw %}`, because each was written as a standalone account and three in a row otherwise open three times.

</details>

## Speech

Tools answer out loud through the [`tts`]({{ '/configuration/#tts-tool' | relative_url }}) tool, which is where the cache, the chime library, the volume and the voice connection all live. Below it is a `Speaker`, which the bot implements against the voice channel an utterance came from. Nothing in `tools/` imports discord: a speaker is somewhere to play audio, and it happens to be a voice channel.

Synthesis is a second Wyoming server (`TTS_HOST`, `TTS_PORT`) — recognition and synthesis are both Wyoming, but they are two servers and only one of them wants a GPU. The voice is process-wide: a bot that answers in two voices is a bot nobody can tell is one bot.

**Audio streams.** Playback starts on the first chunk rather than the last, so a cache miss plays while it is still being rendered. `bot/speaker.py` buffers between the event loop and Discord's player thread, padding the tail to a whole frame so the last few milliseconds of a word survive. A synthesizer that stalls mid-clip costs the rest of that clip after `settings.tts.stall`, not a thread and a voice connection.

**A clip waits for a head start** (`settings.tts.lead`, 500 ms) before the first byte reaches the player.

**Loudness is a deployment setting** (`PLAYBACK_VOLUME`, `1.0` by default). It scales every sample on its way to the player, so a chime is turned down with the words behind it, and it is applied at playback rather than folded into a rendered clip — changing it does not invalidate a cache full of phrases. Above `1.0` the result is clipped at full scale rather than allowed to wrap.

<details class="why" markdown="1">
<summary>Why a head start, and why clipping rather than wrapping</summary>

Streaming is the contract, not a promise: a synthesizer is free to render a phrase whole before sending any of it, which makes the first chunk the slow one and every chunk after it instant. That is invisible for a clip that is only speech, and audible for one that opens with a chime — the flourish plays, and then the channel sits silent until the sentence it introduced arrives. Waiting for `lead` of speech first moves the wait to before the chime, where nobody is listening yet.

How loud a synthesizer renders a sentence has nothing to do with how loud a channel wants to be interrupted, which is why the volume is the deployment's. And int16 wraps to the opposite extreme, so a wrapped sample is a crack in the middle of a word rather than more of the same.

Discord's player asks for exactly one 20 ms frame at a time and treats anything short of one as the end of the clip, which is what the buffer and the padded tail are for.

</details>

**Every volume is a knob, not a multiplier** — `0.5` means half as loud to whoever is listening, not half the amplitude. `audio/gain.py` converts on that curve once, at the point a volume becomes samples, which is why a deployment's loudness and a tool's scale can be multiplied as knobs and converted together. The full table is under [how a volume is read]({{ '/configuration/#volumes' | relative_url }}).

**No ffmpeg.** It is the usual way to play audio through discord.py, but only because it is the usual way to decode a file first. Synthesized speech is already raw PCM, so `soxr` converts it to the 48 kHz stereo Discord wants and the libopus already present for receiving handles the rest.

### The cache

**Clips are cached as what Discord is sent**, so a phrase is only ever synthesized once — and, at full volume, never processed again either. One layer: Opus packets, one per 20 ms, in an Ogg container under `SPEECH_DIR/cache`. About a tenth the size of the samples they came from, and playable, so you can hear what the bot actually said.

Storing what Discord wants rather than what the synthesizer produced is what makes a cached phrase free to play: `AudioSource.is_opus` tells discord.py the frames are ready to send, so it builds no encoder at all — **no resample, no encode, no decode, nothing per play.** The cost is that **the stored bitrate is the delivered bitrate**, 32 kbps in Opus's VoIP mode, which is where the tenfold saving comes from. It is not a setting.

**A clip below full volume is decoded on the way out**, a gain being a multiplication and there being nothing to multiply in an encoded packet. That is every `verbal-morality` fine past the first, and every clip in a deployment that lowered `PLAYBACK_VOLUME`; `quotes` plays at full volume and takes the free path. It costs about 8 ms of decode per three seconds of audio and does **not** hold the event loop.

<details class="why" markdown="1">
<summary>Why the bitrate is fixed, and how the decode stays off the loop</summary>

VoIP mode is built for exactly this content, one synthesized voice. Making it a setting would silently mean two bitrates in one directory.

Packets are batched a tenth of a second at a time onto a thread rather than decoded where they arrive. Undecoded, one clip's worth stalls the loop for 11.7 ms — a third of the 32 ms in which every speaker's next VAD frame is due. Batched, the first frame lands 0.89 ms behind where it would be at full volume, against a 20 ms Discord frame.

</details>

This is why **[`SPEECH_DIR` has to be a writable volume]({{ '/installation/#volumes' | relative_url }})**. Writes go through a temporary file and a rename, so a process killed mid-write cannot cache a truncated clip forever, and a clip is only stored once the synthesizer says it is whole.

**The cache is reaped at startup** (`settings.tts.cache_retention`, ninety days by default). Age is the **mtime**, not the filename, and every hit touches the file, so what is still in use stays however old it is. Everything in the directory is reaped, all of it being the cache's.

**A phrase composed for one moment is not cached at all** — the `summary` tool's retelling. The cache is for phrases that come round again, and a sentence nobody will ever say twice is a large file on a retention clock only its own age will clear.

### Chimes

`SPEECH_DIR/chimes` holds **clips nobody synthesized** — a flourish a tool plays ahead of what it has to say, and the `summary` tool's hold music, which loops under a wait instead of playing once. Drop a 16-bit WAV in and name it from the tool's config, without the extension; it is read once, converted to playback PCM, and held for the life of the process.

It is a separate directory from the cache and that is the whole point: **nothing writes here and nothing reaps here**, so a clip put there deliberately is never on a retention clock meant for a phrase said once. Names are resolved against the directory — a bare name or a path below it, and anything that climbs out is refused — so a setting cannot be pointed at an arbitrary file on the host. The directory does not have to exist; an absent one is a missing chime rather than a failure to start.

## Writing a tool

A tool reads a server's transcripts and does something with them. Configuration decides only **which servers a tool applies to** and **what settings it is handed**; the tool decides when it runs, by defining any of four methods.

```python
from miss_quote.tools.base import Tool


class Applause(Tool):
    name = "applause"
    requires = (Tts,)

    async def handle_utterance(self, utterance, session) -> None:
        """Called as each line is written."""
        if "nailed it" not in utterance.text.lower():
            return

        tts = self.tools.find(Tts)
        if tts is not None:
            await tts.play(f"Well done, {utterance.user}.")

    async def handle_finished(self, transcript) -> None:
        """Called once the session is sealed."""

    async def handle_joined(self, source) -> None:
        """Called once the bot has taken up a voice channel."""

    async def run(self) -> None:
        """Started once the bot has connected, and left going."""
```

**Register it.** A tool is reachable from configuration only once it is in `tools/registry.py`: define the class, import it there, and add it to `TOOLS` under the name a config file will use. That keeps the set of switchable names a closed list rather than whatever happens to be importable.

**What a tool is handed.** Its `config` block, its server's `users` roster, a `tools` box holding that server's other tools, and three places to put text:

| | What it is | How long the text stays worth reading |
|---|---|---|
| `topic` | One line under a voice channel's name, holding no history | A tally worth glancing at. `scoreboard` uses it |
| `announcer` | A message in a text channel, rewritten as the thing it describes grows, left up afterwards | One summary per evening. `summary` uses it |
| `ticker` | One message, rewritten in place, pinned while it lives and deleted when the room empties | Worth reading only while it is current. `summary` uses it for the live feed |

Answering out loud is not on that list: playing audio belongs to the `tts` tool, and everything else reaches it through the box.

**Testing one.** `ToolContext` is built for this — every field except the server has a default that does nothing, so a test constructs a tool from the part it is about and leaves the rest alone. `tests/test_scoreboard.py` is the shortest example to copy.

None of the four moments exists on the base class, so their absence is meaningful: the runner inspects each instance once at startup and files it under the moments it handles. **A tool that defines none of them is reported as configured-but-inert** rather than silently doing nothing.

- **`handle_utterance`** is dispatched after the line is on disk, so a tool that reads the file sees the same thing it was handed. Not called for an empty transcription.
- **`handle_finished`** is dispatched once the resume window has passed without a reconnect, so a tool sees one whole conversation rather than a fragment per disconnect. On shutdown, open sessions are sealed immediately. Not called for a session nobody spoke in.
- **`handle_joined`** is dispatched once the bot has taken up a voice channel, whether it walked in or moved there. It is for a tool whose output lives **on** the channel; `scoreboard` uses it to put the board up without waiting for the tally to change. Leaving dispatches nothing.
- **`run`** is the tool's own, started once after the bot connects and left going for the life of the process.

All four are coroutines on the bot's event loop; anything blocking is the tool's own business to push onto a thread. A tool is constructed **once per server**, so it may hold state — but its handlers can be entered concurrently, utterances being transcribed in parallel and dispatched as they land rather than in the order they were spoken.

<details class="why" markdown="1">
<summary>Why three text surfaces rather than one</summary>

What separates them is how long the text stays worth reading. A topic is a single line under a voice channel's name with no history. An account is a message somebody scrolls back through, rewritten as the thing it describes grows and left up afterwards — one summary per evening, however many times that evening's room emptied. A ticker also keeps one message and rewrites it, but its text is worth reading only while it is current, so it is pinned while it lives and deleted when the room empties.

</details>

### Warming and closing

A tool may also define **`async def prewarm(self)`**, which the runner calls once per process in the background just after the bot connects, and **`async def close(self)`**, which it calls on the way down once every `run` has been cancelled.

`prewarm` is for work a tool can do before anybody asks anything of it — rendering what it already knows it will have to say — and, being the first moment at which every tool on a server exists, is also where to complain about one that is missing. `close` is for whatever has to outlive the process. Neither is a moment: a tool defining only these handles nothing and is still reported as inert. Warming is **serial** across tools, unlike dispatch, nothing waiting on it.

### One tool calling another

Every tool a server has enabled shares one box. A tool says which of its neighbours it uses, and reaches them by class:

```python
class VerbalMorality(Tool):
    name = "verbal-morality"
    requires = (Scoreboard, Tts)

    def _scoreboard(self) -> Scoreboard | None:
        return self.tools.find(Scoreboard)
```

Look **at the moment you need it, not in `__init__`** — the box is handed over before any of the server's tools exist and fills as each is built, so resolving at construction depends on the order the config file happens to list them in.

**A missing neighbour comes back `None` rather than raising.** `verbal-morality` without a `scoreboard` announces fines and does not count them; without a `tts` it counts them and says nothing. Both are whole working configurations.

Each tool is given a **view** of the box bound to its own class, serving only what `requires` names; asking for anything else comes back `None` with a line in the log. Two tools that require each other are reported at startup and **left unbuilt**:

```
Server 'first-server': tools chicken → egg → chicken require each other in a circle; none of them will be built.
```

**Failures are contained.** A tool that raises is logged and otherwise invisible: it cannot cost an utterance, delay a disconnect, or stop another tool from running, warming, or closing. One that will not construct is reported at startup and skipped.

<details class="why" markdown="1">
<summary>Why <code>requires</code> is declared as well as used</summary>

`requires` is the graph the startup cycle check walks, and a declaration nothing enforces is one that drifts away from the call sites it describes — so the box serves only what a tool declared.

Lookup is by class rather than by name so that what a tool depends on is an import a reader can follow.

</details>

## Project structure

```
miss-quote/
├── Makefile                   # How the tests are run, and the image is built
├── Dockerfile                 # The published image, and the stage tests run in
├── pyproject.toml             # What builds the package, and nothing else
├── setup.cfg                  # The package itself: metadata and where it lives
├── requirements.txt           # What the image installs
├── requirements-test.txt      # What the test stage adds on top of it
├── requirements-dev.txt       # Both of the above, for a working copy
├── config.yaml                # A sample of the mounted file
├── docs/                      # This site
├── scripts/
│   └── validate_quotes.py     # Checks a quote file in CI; stdlib only, imports nothing
├── src/
│   └── miss_quote/
│       ├── __main__.py        # Entry point: python -m miss_quote
│       ├── config.py          # Grouped configuration (dataclasses)
│       ├── bot/
│       │   ├── client.py      # Bot setup, voice lifecycle, auto-join policy
│       │   ├── audio_sink.py  # AudioSink + resampling bridge
│       │   ├── speaker.py     # Playback into a voice channel, fed while it plays
│       │   ├── presence.py    # Saying, under the bot's own name, that a session is on the record
│       │   ├── topic.py       # A line under the name of the channel the bot is in
│       │   ├── announcer.py   # A body of text in a text channel named by a tool
│       │   ├── ticker.py      # One message in a text channel, pinned and rewritten in place
│       │   └── voice_patches.py  # Runtime patches to voice-recv, for the encryption Discord requires
│       ├── audio/
│       │   ├── resampler.py   # soxr, both directions
│       │   ├── opus.py        # Encode to what Discord sends, and the Ogg it is kept in
│       │   ├── gain.py        # Playback loudness
│       │   ├── chimes.py      # Clips kept by hand, read out of SPEECH_DIR/chimes
│       │   ├── hold.py        # One of those looped under a wait, with an envelope
│       │   └── ring_buffer.py # Pre-speech context buffer
│       ├── stt/
│       │   ├── vad.py         # Silero VAD via onnxruntime
│       │   ├── user_state.py  # Per-user VAD state machine
│       │   ├── processor.py   # Segmentation and bounded dispatch
│       │   ├── wyoming_client.py  # Per-utterance Wyoming round-trip
│       │   └── models/
│       │       └── silero_vad.onnx  # Vendored (~2 MB)
│       ├── llm/
│       │   ├── client.py      # An OpenAI-compatible chat completion
│       │   └── announcements.py  # Announcements the model writes for `quotes`
│       ├── ledger/
│       │   └── credits.py     # What everybody has left, per server
│       ├── resources/
│       │   ├── quotes.yaml    # Triggers and the film lines they answer with
│       │   └── prompts.yaml   # What the model is told to do, as prose
│       ├── tools/
│       │   ├── base.py        # What a tool is: its moments, and what it is handed
│       │   ├── registry.py    # Tool names a config file can switch on
│       │   ├── runner.py      # Per-server instances, dispatch, failure isolation
│       │   ├── quotes.py      # Answers a trigger phrase with the line it belongs to
│       │   ├── scoreboard.py  # The tally, to disk and to the channel topic
│       │   ├── summary.py     # An account of a session, written down and read back
│       │   ├── tts.py         # Says things out loud; the only thing that plays anything
│       │   └── verbal_morality.py  # Fines a speaker, out loud, for the wrong thing
│       ├── summary/
│       │   ├── prompts.py     # Loads the prompt file, fills its placeholders
│       │   ├── dialogue.py    # A transcript as the text a model reads
│       │   ├── store.py       # Summaries on disk, and finding the last one
│       │   └── when.py        # Which evening somebody meant, from the words after the question
│       ├── transcript/
│       │   ├── schedule.py    # The windows a room may start being written down in
│       │   └── writer.py      # Per-session JSONL appender + retention
│       ├── tts/
│       │   ├── client.py      # Streaming Wyoming synthesis
│       │   └── cache.py       # Render a phrase once, keep it encoded in SPEECH_DIR/cache
│       └── utils/
│           ├── logging.py
│           ├── phrases.py     # Matching a set phrase against what an ASR wrote
│           ├── slugs.py       # A name somebody chose, reduced to something safe in a path
│           └── stems.py       # A stem and the endings it is said with
└── tests/
```

<details class="why" markdown="1">
<summary>Why the layout is shaped this way</summary>

The package directory is `miss_quote` where everything else is `miss-quote`, a hyphen not being importable. It sits under `src/` so a test run imports the package on the path rather than whatever is in the working directory — the failure a flat layout hides is a module that only resolves because pytest added the repository root.

Dependencies stay in `requirements.txt` rather than `setup.cfg`, one of them being pinned to a VCS revision the image installs verbatim. Nothing installs the package: `PYTHONPATH` points at `src/`, in the container and in `pytest.ini` both. The Silero model is vendored rather than installed, because the `silero-vad` package declares `torch` even in ONNX mode.

</details>

<nav class="page-nav" aria-label="Previous and next page">
  <a class="page-nav-next" rel="next" href="{{ '/installation/' | relative_url }}"><span class="page-nav-label">Next →</span><strong>Installation</strong><span class="page-nav-blurb">What to run it against, and how to deploy it</span></a>
</nav>
