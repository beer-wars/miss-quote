---
layout: page
title: Configuration
eyebrow: Reference
lede: Everything about how the bot behaves, and which servers it behaves that way in, is config.yaml. Everything a deployment points at stays in the environment. This is the whole of both.
description: The complete miss-quote configuration reference — config.yaml, the server block, every tool and its settings, the deployment-wide settings block, and every environment variable.
---

Reference first, reasoning second. Where a **Why** drawer sits under a setting, that is the argument for the way it currently behaves — collapsed rather than cut, because it is worth reading once and in the way every time afterwards. Nothing you would search this page for is inside one: names, defaults, and boundary values stay in the tables.
{: .note }

## The file

`config.yaml` is mounted at `/config/config.yaml` from a ConfigMap. Point `CONFIG_FILE` elsewhere to override the location. The file is read once at startup, so editing it means restarting the pod. The IDs in the repo copy are placeholders.

```yaml
settings:
  quotes:
    backoff_seconds: 300
  fines:
    volume_floor: 0.25

servers:
  123456789012345678:
    alias: first-server
    users:
      234567890123456789: Speaker One
    tools:
      scoreboard:
        enabled: true
      summary:
        enabled: true
        config:
          monitored_channels:
            general-voice:
              channel: session-summaries
      tts:
        enabled: true
      verbal-morality:
        enabled: true
        config:
          words: [fiddlesticks, poppycock]

  876543210987654321:
    alias: second-server
    users:
      234567890123456789: Someone Else
```

**`summary` is what puts a room on the record.** A server that does not enable it, or a voice channel absent from its `monitored_channels`, is still joined and still heard — and nothing said there reaches disk. That is one switch rather than four, and it is the line to check first when a bot that is plainly working leaves an empty transcript directory behind.

Hosts, ports, directories, and the token are environment variables; everything that is a number or a wording is `settings:`. **`settings:` is optional in its entirety**, and so is every line in it — a file that mentions none of them is a working file. It is not, however, free-form: a name nothing reads is reported at startup, and a value that will not parse falls back to its default and is reported too.

<details class="why" markdown="1">
<summary>Why a typo is reported rather than ignored, and why it does not stop the pod</summary>

A deployment running on a default against a file that plainly asks for something else is the failure with no symptom, so a name nothing reads is named on the way up.

Reporting it is as far as it goes, because this is also the file that decides which servers get joined. Refusing to start over a stray key would take the whole deployment down to protect one setting.

</details>

### Parsing reports rather than raises

A malformed server block — no `alias`, or not a mapping at all — is dropped and logged; so is a name filed under something that is not a user ID, and a tool whose settings will not parse. The bot joins one fewer server instead of crash-looping over a typo.

On startup it also reconciles the file against the servers it is actually in. Four things can be wrong and none of them raise: an entry would not parse, nothing is configured, a server is configured but the bot was never invited, or the bot is in a server nobody configured. Each is logged — see [reading the startup log]({{ '/installation/#reading-the-startup-log' | relative_url }}).

## Servers

Everything about a server lives under its ID, and the ID appears there and nowhere else.

| Key | Required | Purpose |
|---|---|---|
| `alias` | yes | Names the transcript directory, so renaming a server on Discord changes nothing about where its transcripts land |
| `users` | no | Replaces the display name Discord reports for a speaker |
| `tools` | no | Elects the server into the tools listed under it |

**`servers` is a hard gate on joining.** A server that is not listed is never joined, by autojoin or by an explicit `!join`, and an empty mapping or a missing file means the bot joins nothing at all.

<details class="why" markdown="1">
<summary>Why the gate fails closed</summary>

Joining no server is something you notice and fix. Recording a server the bot should not have been in is not something you can take back.

</details>

### users

```yaml
users:
  234567890123456789: Speaker One
  "345678901234567890": Speaker Two
```

`users` replaces the display name Discord reports for a speaker. IDs may be quoted or bare; both are read as integers. The roster is per server, and it is also **what can be warmed** and **who is eligible for the scoreboard** — a phrase naming somebody on it is rendered before anybody speaks, and somebody not on it waits for the synthesizer the first time they are named.

<details class="why" markdown="1">
<summary>Why not just use the Discord name</summary>

Discord nicknames are freely editable and often not a name at all, which makes them poor labels in a transcript a summarizer will later read. Per server, because the same person can be known differently in two places.

</details>

### tools {#server-tools}

`tools` elects the server into the tools listed under it. Each is opted into on its own, **including the ones others depend on** — `verbal-morality` hands its fines to `scoreboard` and its words to `tts`, and enabling it alone fines people silently and keeps no record of it. Each absence is reported at startup.

**A tool block holds `enabled` and `config`, and nothing else.** Every setting a tool takes goes under `config:`; written a level up, beside `enabled`, it is read by nothing and named at startup:

```yaml
      quotes:
        enabled: true
        penalize_self_answers: false   # ← wrong: reported at startup, read by nothing
        config:
          penalize_self_answers: false # ← right
```

Two tools that require each other are a circle, reported at startup and **left unbuilt** — see [the tool contract]({{ '/about/#one-tool-calling-another' | relative_url }}).

## Tools {#tools}

Five tools ship. A name nothing answers to is reported at startup and skipped, the registry being a closed list rather than whatever happens to be importable.

### quotes {#quotes}

Answers the channel with the film line it just walked into. It listens for a trigger phrase and, on hearing one, says the associated quote out loud where it was said — and then asks where the line came from.

```yaml
quotes:
  enabled: true
  config:
    quiet_seconds: 1
    chance: 1
    answer_seconds: 10
    tie_seconds: 1
    remarks:
      - having watched it more recently than is respectable.
```

| Setting | Required | Purpose |
|---|---|---|
| `quiet_seconds` | no, `1` | How long whoever said the trigger has to go quiet before the line is said. `0` says it where it was heard; see [letting the speaker finish](#letting-the-speaker-finish) |
| `backoff_seconds` | no, [`settings.quotes.backoff_seconds`](#settings-quotes) | How long a trigger stays spent after it fires, for **this** server. `0`, or below, answers every trigger every time |
| `chance` | no, `1` | The odds a trigger is answered at all, between `0` and `1`. Rolled once per utterance; see [answering only some of it](#answering-only-some-of-it) |
| `answer_seconds` | no, `10` | How long the channel has to name the title once the line has finished playing. `0` stops the tool asking at all |
| `tie_seconds` | no, `1` | How long after the first correct answer a second one is still paid. `0` pays only whoever was first |
| `penalize_self_answers` | no, `true` | Whether whoever set a line off is barred from naming it. `false` lets them answer like anybody else |
| `self_answer_penalty` | no, `5` | What an attempt costs them, in credits. Floored at `0` |
| `remarks` | no | Endings the announcement draws from, **added** to the ones the tool ships with. A lone one may be written unquoted rather than as a list |
| `announcement` | no | What the winner is told. `{user}`, `{credits}`, and `{remark}` |
| `tie_announcement` | no | What anyone paid on a tie is told. The same placeholders |
| `self_answer_announcement` | no | What somebody naming their own line is told. The same placeholders, where `{credits}` is what it cost |
| `additional_quotes` | no | Quotes this server hears and the others do not, in the [quote file's](#the-quote-file) own shape — or a path or URL to a file holding them. Merged over the deployment's list; see [what a server adds for itself](#what-a-server-adds-for-itself) |

#### The quote file

The lines come from a YAML file at `QUOTES_FILE` — a film, and under it the phrases that set its lines off. The image ships the list in `resources/quotes.yaml`; mount your own over that path to replace it.

```yaml
Firefly:
  cool: Shiny.
  behave: I aim to misbehave.

Project Hail Mary:
  question: "{user} question is dumb."

The Princess Bride:
  impossible: Inconceivable!
```

| Where | Purpose |
|---|---|
| The outer key | Where the line is from. Never spoken; it is what the round asks about, and what makes the log and the file readable |
| The inner key | The phrase that sets the line off. Matched whole and case-insensitively, however the file writes it |
| The value | What gets said. `{user}` is the only placeholder, and names whoever set it off |

**A trigger appears once in the whole file**, including across titles. The first is kept and the rest reported, so the line to go and delete is the later one.

**Two triggers may share an answer** — `awesome` and `cool` both earning `Shiny.`. There is no alternation syntax inside a trigger, so a key meaning to catch two phrases has to be two keys.

**A phrase worth answering several ways lists its lines, and one of them is drawn each time it fires:**

```yaml
Firefly:
  cool:
    - Shiny.
    - Gorram it.
```

The draw happens when the trigger fires rather than at startup, so a restart does not decide which line a channel hears for the next week. The backoff is still keyed on the trigger, so a trigger with four answers fires once per window, not four times.

Two things are worth knowing about the format itself, because both look entirely correct in the file:

- **A line starting with `{user}` has to be quoted.** Unquoted, a `{` opens a mapping and the file will not parse.
- **A trigger like `no` or a title like `1917` has to be quoted too.** Unquoted, YAML reads them as a boolean and an integer, and neither is text the matcher can ever compare against.

The file is read at startup and **reports rather than raises**: a bad entry is logged with its line number and dropped. What *does* stop the tool starting is a file that is missing, unreadable, not valid YAML, not a mapping of titles, or holding no usable entry at all.

Because a dropped entry is a line in a log nobody reads, the file is also checked before it can be merged. `scripts/validate_quotes.py` applies the loader's rules where a broken entry fails a pull request instead, plus the ones the loader has no opinion about:

| Checked | Why |
|---|---|
| Every key and value is text | An unquoted `no` is a boolean and an unquoted `1917` is an integer; both look right and neither can ever match |
| A trigger answers under one title only | It is a key, so a repeat is either impossible or a disagreement about which line a phrase earns |
| Every part populated, and unpadded | The loader strips surrounding whitespace, so the file and what it produces disagree quietly |
| `trigger` ≤ 30 characters, `quote` ≤ 150 | A trigger has to be said in passing and a line has to land before the channel moves on |
| A trigger that could actually fire | No placeholders, no repeated whitespace, at least one letter or digit |
| `{user}` is the only placeholder | Anything else drops the entry at startup, so the symptom is a line that is never said |
| No trigger listing the same line twice | A second way of answering is the point; the same way twice is a line pasted and half-edited |
| Titles non-decreasing, LF endings, trailing newline | So the file stays reviewable and two branches adding a line do not collide |

It needs PyYAML and nothing else, so it runs in seconds on every pull request rather than costing an image build.

#### What a server adds for itself {#what-a-server-adds-for-itself}

A server with an in-joke of its own writes it under `additional_quotes`, in its `config` block and in the file's own shape:

```yaml
quotes:
  enabled: true
  config:
    answer_seconds: 10
    additional_quotes:
      Firefly:
        cool: Shiny.
        behave: I aim to misbehave.
      "1917":
        over the top: "Over the top, {user}!"
```

Everything the file can say, this can say, and the [same rules](#the-quote-file) apply — including the two quoting traps: a title like `1917` and a line starting with `{user}` both need their quotes. A server keeping its list in a file of its own writes that file's name here instead; see [keeping that list somewhere else](#keeping-that-list-somewhere-else).

- **A failed entry never stops the tool.** The block is optional, and a server that gets it wrong still has the whole shipped list.
- **A trigger the shipped list already answers is overridden by this server's line**, for this server alone. The next server on the same deployment hears the shipped line.
- **Titles merge.** `Firefly` written in both places is one film with everything either said under it, and a round asks about the same title either way.
- **CI checks these too.** `scripts/validate_quotes.py --config config.yaml` walks every server's block. A trigger shared with the shipped list is the override, not a collision.

<details class="why" markdown="1">
<summary>Why the deployment's list is the default rather than the rule</summary>

A film everybody in one channel has seen is usually one everybody in the next has too, which is why the bulk of the list is one file. An in-joke is not.

So the deployment's file is what everybody agrees on rather than what they are held to: a server that would rather `cool` earned something else should not have to pick a different phrase to say so.

</details>

#### Keeping that list somewhere else {#keeping-that-list-somewhere-else}

A list long enough to be worth its own file does not have to live in `config.yaml`. Write `additional_quotes` as one string instead of the quotes themselves — a path on disk, mounted wherever the rest of the deployment's configuration is:

```yaml
quotes:
  enabled: true
  config:
    additional_quotes: /config/quotes/beer-wars.yaml
```

or somewhere to download it from, which is read once on the way up:

```yaml
quotes:
  enabled: true
  config:
    additional_quotes: https://quotes.example.com/beer-wars.yaml
```

A string beginning `http://` or `https://` is downloaded; anything else is a path, where a leading `~` is the home directory. The scheme is the whole rule — there is no second key saying which.

| | What to expect |
|---|---|
| What the file holds | Exactly what the block would have: a mapping of titles, each holding its triggers. The [file's rules](#the-quote-file) in full |
| When it is read | Once, at startup. A list that changes afterwards reaches the channel at the next restart, which is the promise `QUOTES_FILE` makes too |
| How it is merged | As an inline block is — over the deployment's list, for that server alone |
| What a download waits | 10 seconds, then the server starts without it |

**A file it cannot get is a log line and nothing worse** — missing, unreadable, bad YAML, or a server that will not answer. The server keeps the whole shipped list. Only the deployment's own file can stop the tool starting.

**A dropped entry names the line it was written on**, which an inline block cannot do: `config.yaml` has been parsed by the time the tool sees it, and a file it points at is still a file.

**CI does not follow the name.** `validate_quotes.py --config` leaves a path or URL alone, making no network calls. Check the file behind it by passing it in like any other quote file:

```bash
python scripts/validate_quotes.py quotes/beer-wars.yaml --config config.yaml
```

#### Matching and backoff

Matching is **whole words, case-insensitive**, so `real` does not fire inside `really`. A phrase trigger matches on a single space between its words, which is what an ASR transcript holds.

**One line per utterance**, however many triggers were in the sentence. The one that answers is the **earliest in the sentence** rather than the first in the file; where two start at the same word, the longer wins.

**A trigger that has just fired goes quiet for `backoff_seconds`** — the server's own, or [`settings.quotes.backoff_seconds`](#settings-quotes), five minutes by default. The window is keyed on the **trigger**, not the speaker and not the line, is per server, and is held in memory only, so a restart forgives every backoff.

**The whole list is rendered at startup**, the triggers and answers both being a closed set. A line naming whoever set it off is rendered once per name on the roster instead.

<details class="why" markdown="1">
<summary>Why one line, why a backoff, and why render ahead</summary>

Two quotes over the top of each other is a denial of service on the channel, and the earliest trigger is the one whoever spoke actually arrived at.

The joke is the recognition, and a channel that says "cool" four times in a minute does not want "Shiny." four times back. Keying the window on the trigger means two channels arriving at the same line have each made the joke once.

A callback that arrives four seconds after the line it answers is not a callback.

</details>

#### Letting the speaker finish {#letting-the-speaker-finish}

**A line waits for whoever set it off to stop talking**, `quiet_seconds` of it — one second by default. **The window starts again every time that speaker says something else**, so what is waited out is them finishing rather than a fixed pause. Only their own utterances count.

A speaker already holding a line **sets nothing else off** while they are still going; they can still answer a round somebody else opened. The round opens when the line has finished playing, so the wait moves the question along with it. `quiet_seconds: 0` says the line where it was heard, interruption and all.

<details class="why" markdown="1">
<summary>Why wait at all</summary>

The ASR returns utterances rather than sentences and breaks wherever the speaker paused, so a trigger arrives mid-thought about as often as at the end of one. A line played the moment the trigger lands is the bot talking over the rest of what somebody was saying.

The rest of the channel talking is a conversation rather than an unfinished sentence, which is why it holds nothing up. And a round somebody else opened is a question already in front of you, rather than your own sentence to finish.

</details>

#### Answering only some of it {#answering-only-some-of-it}

`chance` is the odds a trigger is answered at all, between `0` and `1`, and **everything by default**. At `0.5` a phrase comes back about every other time it is said.

The roll is **once per utterance**, not once per trigger, so a sentence carrying three is answered as often as one carrying one. A roll that goes the other way **spends nothing** — the trigger is not put on backoff. Values outside the two ends are held at them, and `0` answers nothing at all.

<details class="why" markdown="1">
<summary>Why you might want less than everything</summary>

A bot the channel is never quite sure is going to say anything is a different joke from one that always does. And `0` is a deployment that wants the rounds without the lines.

</details>

#### Naming it

**A line that has been said is also a question.** For `answer_seconds` afterwards the channel can say where it came from — `what is Firefly` — and whoever does is paid a credit through [`scoreboard`](#scoreboard), and told so out loud.

The window opens when the line has **finished playing**, not when the trigger was heard: transcription and synthesis take as long as they take, and a window started at the trigger could be over before the channel had heard the question.

**The first correct answer takes the round, and anyone inside `tie_seconds` of it is paid as well.** Nobody is paid twice for the same title however many times they say it.

<details class="why" markdown="1">
<summary>Why a tie window</summary>

Two people arriving at the same title half a second apart both knew it, and which of them the transcriber happened to return first is not a fact about who was faster.

</details>

Answers are matched forgivingly, because an ASR transcript is not punctuated the way a poster is:

| The file says | So the channel may say |
|---|---|
| `Firefly` | `what is Firefly`, `what's Firefly`, `What is Firefly?` |
| `The Matrix` | `what is the matrix`, `what is matrix` — a leading `the`, `a`, or `an` is optional either way |
| `Hitchhiker's Guide to the Galaxy` | `what is hitchhikers guide to the galaxy` — apostrophes are dropped from both sides |
| `Tucker and Dale vs Evil` | `...vs Evil`, `...vs. Evil`, `...versus Evil` |

The answer may sit anywhere in the sentence. A row with an empty `movie` asks nothing, there being no question in it. A title carrying a **numeral** is matched as a numeral — `Apollo 13` answers to `what is apollo 13` and not to `what is apollo thirteen`, which is what an ASR is likelier to return; write the title the way it will be transcribed if that matters.

**Two rounds can be open at once**, since an answer names its own title. An utterance that answers an open round is an answer and nothing else, whatever trigger it also contains — otherwise a channel naming a title could set off the line asking about the next one, a loop the tool would be driving rather than following.

A server with no `scoreboard` asks the question and pays nothing, which is said once at startup.

#### The announcement

The award is said out loud, with **no chime in front of it** — a fine opens with one because it interrupts something else, while an award answers a question the channel is already sitting in.

```
Correct! Erik, you are awarded 1 credit for quoting along at home.
```

**The ending is drawn fresh each time**, from the list the tool ships with plus whatever `remarks` adds to it — one fixed sentence is a joke told once and then endured. The shipped endings are:

- `knowing exactly where that came from, which explains a great deal.`
- `quoting along at home.`
- `a display of recall that has never once been useful.`
- `having excellent taste and nothing better to do.`
- `being the sort of person who knows that.`
- `spending your formative years exactly as you did.`

`remarks` **adds** to those rather than replacing them. None of the shipped endings says "film", and the `movie` key is named more narrowly than it behaves — a trigger answers for a series, a game, or a book as often as a picture, and an announcement that guesses wrong guesses wrong out loud. Write your own the same way.

Somebody paid on a **tie** gets the second wording — `Eli, you are also awarded 1 credit, for getting there at the same time.` — because the whole sentence again reads as though the bot had lost track of what it just said.

**Nothing this tool says is dropped for landing while something else is playing**, unlike `verbal-morality`: everything `quotes` says answers something it just said itself. Announcements wait their turn on the speaker's per-server lock and come out in the order they were earned.

#### Naming your own line

**Whoever set a line off cannot name it.** An attempt is refused out loud and **costs them `self_answer_penalty` credits**:

```
Nuh uh uh. Erik, you set it off, so you do not get to name it. You are fined 5 credits for being a dick.
```

The penalty is taken **once per round** however many times they say it, and an attempt **neither wins the round nor spoils it** — whoever names it next is the first answer and is paid in full. The bar is per round, not per person. `penalize_self_answers: false` drops the rule entirely.

<details class="why" markdown="1">
<summary>Why it costs more than the round was worth</summary>

Whoever set a line off has the trigger and the title in front of them and had to recall neither, so a round they could win is one anybody can farm by reading the quote file out loud. The penalty is deliberately larger than the single credit the attempt was worth.

Refused out loud rather than quietly ignored, because a rule nobody is told about is one everybody keeps testing.

</details>

### summary {#summary}

Writes down what happened in a voice channel once the bot leaves it, and reads it back out loud when somebody asks. It is the only tool that uses the finished-transcript moment.

```yaml
summary:
  enabled: true
  config:
    monitored_channels:
      general-voice:
        channel: session-summaries
        schedule:
          - Wed 17:00-00:00
```

That is a working block. Everything else has a default.

#### Which channels

**`monitored_channels` is the switch as well as the settings.** A channel not in it is **not transcribed**, not summarized, not posted, and does not answer the question either — one rule rather than four. The consequence is worth stating plainly: **turning this tool off stops the server writing anything down.** See [the capture schedule]({{ '/about/#the-capture-schedule' | relative_url }}).

Keys are matched through the same slug that names the transcript directory, so `General Voice` and `general-voice` are the same channel, and **the key you write is exactly the directory the summaries land in**.

<details class="why" markdown="1">
<summary>Why per channel rather than per server</summary>

A server's rooms are not interchangeable. One is where a game night happens and one is where two people are debugging something, and a bot that summarizes every room it was ever dragged into is writing files nobody asked for and posting them where everybody can read them.

</details>

#### Per-channel settings

| Setting | Default | Purpose |
|---|---|---|
| `channel` | — | Text channel to post in, by name. Unset writes to disk and posts nothing |
| `transcript_line_limit` | `-1` | Characters of one utterance shown in the feed. `-1` or `0` is no cap — a long line pushes the ones above it off instead |
| `pinned_sessions` | `5` | How many evenings stay pinned in `channel`. Older accounts are unpinned, never deleted. `0` pins nothing |
| `prompt` | `recap` | Which prompt summarizes a sealed session |
| `retelling_prompt` | `bard` | Which prompt turns a stored summary into something to say out loud |
| `retelling_words` | `200` | Roughly how long the spoken retelling should be — a target the prompt is told to aim at, not a cap it is cut to. About a minute out loud |
| `minimum_utterances` | `5` | Below this a session is not a conversation and is not summarized |
| `backoff_seconds` | `120` | How soon the channel can be told the same evening again. `0`, or below, tells it every time |
| `session_gap_minutes` | `10` | How long the room can sit quiet before the rest of the night is a different evening. Not `resume_seconds`, and not to be set to match it |
| `schedule` | *(unset)* | When a session in this room may **start** being written down — see [writing a window](#writing-a-window). Also what makes several sessions [one sitting](#one-evening-several-sessions). Unset keeps every session, or whatever `settings.transcripts.schedule` says |
| `preamble` | `Sure! Let me go look at my notes.` | What plays while the model is thinking |
| `empty` | `I don't have any notes from this channel yet.` | What plays when nothing has ever been written down in this room |
| `missing` | `I don't have any notes from then.` | What plays when there are notes, just not from the evening that was named |
| `closing` | — | A fixed line played once the story is told. Unset, and the retelling prompt's own sign-off is what says it finished |
| `hold_music` | — | A WAV in `SPEECH_DIR/chimes`, named without its `.wav`, looped under the wait once the preamble runs out. Unset leaves the wait silent |
| `hold_volume` | `0.15` | How loud that music is next to `PLAYBACK_VOLUME` — `0.15` is 15% as loud, not 15% of the amplitude ([how a volume is read](#volumes)). Clamped to `0`–`1` |
| `name` | `miss quote`, `misquote`, `missquote`, `mis quote`, `ms quote`, `mizquote`, `mrs quote`, `miss quotes`, `misquotes`, `missquotes`, `misquoted`, `missquoted` | What the bot answers to, in the spellings a transcriber returns for a name it has never been told. **Replaces** the default |
| `triggers` | `what happened`, `what did we do`, `recap`, `read me your notes`, `tell me about` | How asking **starts**; which evening is a clause after it. **Replaces** the default |
| `address_window_seconds` | `15` | How long the name is held when it arrives in an utterance of its own, so the speaker's next one can be the question. `0` wants the whole question in one breath |
| `clause_window_seconds` | `1.5` | How long a question that named no evening waits to see whether one is still coming. Covered by the preamble, so it costs nothing to listen to. `0` answers the moment the question lands |
| `post_transcripts` | `false` | Whether the room watches itself being transcribed, in one message in `channel` that is rewritten as it talks; see [showing it as it is said](#showing-it-as-it-is-said) |
| `transcript_lines` | `10` | How many lines are up at once |
| `transcript_refresh_seconds` | `2` | How long the feed waits after each write before writing again. Held at `0.25`; `0` turns the feed off |

#### One evening, several sessions {#one-evening-several-sessions}

A transcript is one **connection** to a voice channel, and a room produces several in a night — everybody steps out, somebody drags the bot next door, a pod restarts.

**Where the room is on a `schedule`, what gets summarized is the window.** Every session that opened inside one occurrence of a window is summarized together, under the name of the session that **opened** the sitting. Each subsequent seal rewrites that one file, and edits that one message in `channel`, so the evening leaves a single account that gets fuller rather than four overlapping ones.

- **Which occurrence a session belongs to is decided by when it opened.** A window says when a sitting may start, not how long it may run, so a session opening at 23:40 inside `Wed 17:00-00:00` and sealing at 01:20 is part of Wednesday's evening.
- **Overlapping windows count as one stretch**, from earliest start to latest end. Back-to-back windows (`Wed 17:00-00:00` and `Thu 00:00-02:00`) are separate sittings, the interval being half-open.
- **`minimum_utterances` is measured against the whole sitting**, so two visits of three lines are a conversation where either alone would not be. A session that wrote nothing down triggers no rewrite.
- **Outside a window, nothing changes.** A session opened by `!start-transcribing`, or in a deployment with no schedule at all, is summarized on its own under its own name.

**The first message of an account is pinned**, and that is how an evening is found. An account that still fits its messages is rewritten in place; one that has outgrown them is posted again whole and the old run deleted, which takes its own pin off the list.

<details class="why" markdown="1">
<summary>Why a sitting rather than a session</summary>

None of those interruptions is the evening, and an account per connection is four half-summaries of one conversation. What somebody wants an account of is the night.

Starting one by hand is the deliberate exception: it is an account of one conversation, and the sessions on either side of it were deliberately not kept.

</details>

#### Showing it as it is said {#showing-it-as-it-is-said}

`post_transcripts` puts the last `transcript_lines` of what the room has said into **one message** in the same `channel` the summary goes to, and rewrites it as the room talks. It is **off unless a channel asks for it**: the transcript on disk is a file in a volume with a retention window, while the same words in a text channel are permanent, searchable, and readable by everyone in the server rather than everyone who was in the room.

**It needs Pin Messages, which Manage Messages does not carry.** Discord split the two apart, so a bot trusted to delete anybody's message can still be refused a pin. Without it the feed works unpinned and says so once per session in the log. Posting needs Send Messages; editing and deleting its own messages is ungoverned.

**`transcript_lines` is a maximum rather than a promise.** A ring holds lines and a message holds characters, so the feed shows as many of the newest lines as fit and drops the oldest off the top — whole lines, never a cut partway through the block.

**A long line costs the lines above it rather than its own tail.** There is no cap on one utterance unless a channel sets `transcript_line_limit`, which puts an `…` on anything longer.

**It writes on change, not on a timer**, waiting out `transcript_refresh_seconds` from the **end** of each write. A quiet room costs nothing, a burst of four people landing together is one edit, and a slow Discord slows the feed instead of building a backlog. Below `0.25` is held there.

**It comes down when the room does**, deleted as the session seals rather than after the summary is written. The next session posts a new one; what the evening leaves behind is the summary.

<details class="why" markdown="1">
<summary>Why whole lines, a rate floor, and a sweep on restart</summary>

The first thing at the front of the block is a code fence, and a block that loses its fence stops being a block: no monospace for the column of names, and every asterisk the transcriber returned handed back to Markdown. The fence is also what stops an ASR transcript of somebody saying "at everyone" from pinging the server. Lines are `Name: what they said`, with backticks removed and whitespace collapsed so neither can break out.

Truncating a long line is the wrong trade: somebody watching the feed is watching to see what the transcriber heard, and a sentence that stops at a number tells them nothing, while the lines above were about to be lost anyway. The feed also spends 100 characters less than a message holds, because being wrong costs a message Discord refuses while being careful costs one line.

Editing a message is roughly five requests every five seconds per channel — against two every ten minutes for a voice channel's status, which is why this is a message and [`scoreboard`](#scoreboard) is a topic. Two seconds spends a quarter of that. Below `0.25` is refused because discord.py sleeps out a rate limit rather than failing, so asking for more buys a feed silently running behind a room that thinks it is watching itself live.

Which message is being written to is held in memory only, so a process that goes away mid-session leaves one behind. The next post reads the channel's pins and deletes whatever this bot left there, rather than persisting an ID that would have to be kept in step with a channel somebody may have cleared. The sweep skips summary accounts: a feed is message content and carries no embed, an account is embeds and carries no content, so the two are told apart structurally rather than remembered.

</details>

#### Writing it down

When a session seals — after the resume window, or immediately on shutdown — the JSONL is reduced to the two fields a summarizer wants:

```
Erik: that should work
Eli: it did not
```

Consecutive lines from one speaker are joined. That goes to the endpoint with the channel's `prompt`, and what comes back is written to `SUMMARY_DIR` and posted to the channel named in `channel:` — by **name**, resolved when it is posted, so a renamed channel silently stops receiving posts and an unresolvable one is reported at startup. Leaving `channel:` out writes to disk and posts nothing.

A session under `minimum_utterances` is not summarized. **A failure anywhere costs the summary and nothing else** — nothing partial is written or posted, and the transcript is untouched, so a session missed because the endpoint was down can be summarized by hand later.

**A whole session is sent in one request, and it is not truncated.** A long evening is tens of thousands of tokens, and an endpoint whose context will not take it refuses the request — logged, no file, no post, transcript intact. **Point `LLM_MODEL` at something with the context to hold a session.**

**Keep [`settings.llm.timeout_seconds`](#settings-llm) well under `terminationGracePeriodSeconds`.** A session sealed as the pod goes down is summarized inside the shutdown, so a whole LLM round trip runs inside the grace period and can be killed by it.

<details class="why" markdown="1">
<summary>Why those fields go, and why a long evening is refused rather than cut</summary>

`user_id` goes because a model cannot look anybody up by it, and the timestamp goes because the lines are already in the order they were spoken and every prompt says so. Consecutive lines are joined because the segmenter cuts on a pause rather than on a sentence, and three attributions in a row reads as an exchange that never happened.

Silently cutting a transcript would produce a summary that reads as complete and covers the first hour, which is worse than not having one.

A summary killed by the grace period is accepted: the transcript survives, and the summary is the derived artifact.

</details>

#### Reading it back

**"Miss Quote, what happened last session"** and the bot tells you, out loud, having run the stored summary through a second prompt that turns a thing you read into a thing you say.

It answers **for that channel**, with the whole of the evening asked about — [a run of sessions]({{ '/about/#one-evening-is-not-always-one-session' | relative_url }}), not one file. A session still in progress has no summary yet, so this is the previous conversation even asked in the middle of one.

Asking takes **both** a name and a trigger, the name first; punctuation is ignored on both sides. Several spellings of the name ship by default, an ASR guessing phonetically at a name it has never been told.

**It does not have to be one breath.** A name said with no question after it is **held for `address_window_seconds`**, fifteen by default, so that speaker's next utterance can finish the question. It is per speaker, and `0` wants the whole question at once. A question that named no evening then **waits `clause_window_seconds`**, a second and a half, to see whether one is still coming — covered by the preamble, so it costs nothing to listen to.

What none of this recovers is the two halves arriving the other way round: transcription runs several at a time, and an utterance is stamped when it is written rather than when it was said, so there is nothing to sort by.

<details class="why" markdown="1">
<summary>Why a name is required, and why the wait is free</summary>

An unaddressed "what happened last session" is somebody talking to the room, and answering it would be a minute of narration nobody asked for.

An ASR returns utterances rather than sentences and splits wherever the speaker paused, so "Miss Quote, what happened on the twenty ninth" arrives as two lines about as often as one, and neither half asks anything by itself. The half after the trigger is the worse one: "Miss Quote, what happened" is a complete question, so answering it immediately retells the *last* session and "on the twenty ninth" is never heard — a wrong answer rather than none.

The preamble is true whichever night is meant, so it plays *over* the clause wait rather than after it. Only an evening nobody named waits at all; a question that said which night it meant is answered with no delay. The completion is started on the evening in hand before the wait ends and thrown away only if the channel names a different one, so the single ask that pays for a second lookup is the one that changed its mind mid-sentence — and it pays while the preamble is still playing.

</details>

A trigger is the **start** of a question rather than the whole of one, and what follows it says which evening:

| Said after a trigger | Means |
|---|---|
| nothing, `last time`, `last session`, `last night`, `last one` | The most recent evening |
| `last week`, `a week ago`, `two weeks ago` … `eight weeks ago` | The evening nearest that date, within three days either way |
| `on the twelfth`, `the twenty fifth`, `the 12th` | That day exactly |

Ordinals are spelled out because that is what an ASR returns; digits work too, but a **bare** number is not a date — "recap the three things" is about something else. A named day resolves to one of the last two months, earlier this month if it has already been and the month before if it has not, with today counting as "has not". A day the resolved month does not have gets the `missing` line rather than sliding to a date nobody named. Counting back weeks gets a few days of latitude, ties going to the later evening; naming a day gets none.

**A trigger has to be followed by one of those clauses or by nothing at all**, which is what keeps "Miss Quote, what happened to my beer" from being a question about last Thursday.

The bot plays the pre-rendered `preamble` and **starts the inference before it starts saying it**, so the announcement covers the wait. The lookup happens first, so it never announces that it is going to look and then finds nothing: with nothing to find it says the `empty` line, or `missing` if the trouble is the night that was named.

A second ask while a retelling is still going is dropped rather than queued. `backoff_seconds` is per **evening** rather than per channel, since somebody asking about a different night is asking a different question.

**The retelling itself is never cached** — it is composed for one moment and nobody will ever ask for those exact words again. The preamble, the empty line, and a `closing` are kept.

<details class="why" markdown="1">
<summary>Why the trigger list is short, and why weeks get latitude</summary>

`what happened` covers every row of that table, so there is no line per date anybody might name — and because the stems carry no date of their own, the clause is what disambiguates.

A channel that meets on a night of the week does not meet on a date, so counting back weeks lands on the nearest evening within three days. Naming a day is exact, because somebody who named one meant it. A day with two conversations on it resolves to the later.

</details>

#### Hold music

A completion routinely runs longer than the preamble, and what is left over is dead air. `hold_music` fills that.

It is **off unless a channel names a clip**, and nothing is shipped. Drop a 16-bit WAV in `SPEECH_DIR/chimes` and name it here without its extension. It **loops**, so author a short passage that meets itself — ten to thirty seconds, not three minutes, since a clip is read into memory whole and held for the life of the process.

- **It fades up** over [`settings.tts.hold_fade_in_ms`](#settings-tts) (500 ms) as soon as the preamble ends. Quickly, because the gap it is covering has already started.
- **It loops for as long as the wait lasts** — the model thinking, and then the synthesizer starting on the answer. Both are covered: a completion that returns instantly still leaves the `lead_ms` head start to be waited out.
- **It fades down** over `settings.tts.hold_fade_out_ms` (2 s), starting only once the first speech is in hand, so the music reaches silence exactly where the first word begins rather than being cut off at it.

The music and the retelling are **one clip**, armed once — two calls would put a gap exactly where this is trying not to have one. `hold_volume` applies to the music alone; the retelling arrives at the loudness it would have had anyway. A clip that is missing or will not parse costs the music and not the answer, and the name is checked at startup but kept regardless, so a volume mounted later starts working without a restart.

**The story ends itself.** A retelling ends wherever the model chose to, so a channel listening has no way to tell "finished" from "stopped". `bard` is told to close on a line that means the tale is over. `closing` is the other way: a fixed sentence after the story, **unset by default**, since a fixed line following one that has just said goodbye is one goodbye too many.

#### Prompts

Prompts are named and selected by name. Three ship, as prose in `src/miss_quote/resources/prompts.yaml`:

| Name | For | Output goes to |
|---|---|---|
| `recap` | The default. An account of the evening for the people who were there, in the order it happened, naming names | A Discord message, so Markdown is fine |
| `minutes` | Topics, decisions, and open questions, as headed sections | A Discord message |
| `bard` | The default retelling. A bard telling the room its own evening back, in the third person, cut down to what actually mattered and signed off so the room knows it ended | **A speech synthesizer**, so it forbids Markdown, bullets, and emoji at some length — a synthesizer reads an asterisk out as a word |

`prompts:` adds your own, and one written under a shipped name replaces it. It sits at the tool level rather than inside a channel, a prompt being a library entry rather than a per-room setting.

A prompt of your own can pull in the text the shipped ones share by naming it in braces:

| Fragment | What it is |
|---|---|
| `{transcript_instructions}` | The paragraph describing the script format. Any prompt summarizing a session wants it; no retelling prompt should carry it, a retelling being given the summary `recap` already wrote |
| `{retelling_instructions}` | Says that an evening can arrive as several accounts set end to end, and is to be told as one story |
| `{retelling_closing}` | The instruction to end on a line that means the story is over |
| `{words}` | Filled per channel from `retelling_words`. Cannot be used as a fragment name |

Any other braces are left exactly as written, so an example of the JSON you want back survives. A shipped prompt naming a fragment that does not exist stops the bot at startup; one of your own is left alone, braces in it usually being deliberate.

**A prompt named by a name nothing answers to stops the tool from starting.** A tool running on instructions nobody asked for produces summaries that look fine and are not what the file requested, which is worse than a tool that refuses.

### scoreboard {#scoreboard}

Keeps a running balance per person, writes it down, and puts the standings under the name of whatever voice channel the bot is in. It hears nothing and says nothing out loud; what it does is count for the tools that ask it to.

```yaml
scoreboard:
  enabled: true
```

There is nothing to configure per server. What the tally is counted in and how often it is written and published are [`settings.credits`](#settings-credits); where it lives is `CREDITS_FILE`.

**It is enabled separately from whatever is counting.** A server enabling `verbal-morality` and not this announces fines and keeps nothing, which the log says once at startup.

**It needs Set Voice Channel Status** — not Manage Channels. What it sets is the channel's **status**, a voice channel having no topic. Without the permission the tool logs once per change and keeps counting.

**The standings read `Eli: -9 Erik: -2 Luke: -1 Ryan: 0`**, holding the **four furthest into the red, worst first**, ties breaking on the name. They go up as soon as the bot takes up a channel and stay current while it sits there; a channel it leaves keeps the last board it was shown.

- **A fine is a debit.** Everybody starts at nothing and goes down. Nothing assumes that direction — `quotes` calls `credit`, so a balance can climb back toward nothing and past it.
- **Only `users` are eligible.** Everyone on the roster starts on the board at nothing spent. Somebody not on it is still heard, announced, and counted, just not published. A server with no roster publishes nothing rather than an empty line.
- **Counts are per server**, keyed on the user ID, so a rename does not hand somebody else's debt to whoever inherited their nickname.
- **A restart is not an amnesty.** The tally is loaded from `CREDITS_FILE` at startup, written back on `save_seconds`, and written again on shutdown. A file that will not parse is reported and ignored.

<details class="why" markdown="1">
<summary>Why four places, why a display name is not published, and how the two intervals differ</summary>

A leaderboard rearranges itself every time somebody passes somebody else, which is the objection to publishing a whole roster in name order; at four places it is short enough to read at a glance. Ties break on the name so two people on the same balance do not swap places between one edit and the next for no reason anybody can see.

A display name its owner can set to anything is not something to put in a channel topic. And setting the status to nothing would wipe whatever a person had put there, which is why an empty roster publishes nothing at all.

The write and the edit are both driven off a revision counter, so a tally that changed four times between ticks costs one of each. They run on separate intervals because writing a few hundred bytes is cheap while a status edit is rate-limited, and saving happens first on every tick, so a pod terminated mid-edit still has the tally on disk from the tick before. The shutdown pass writes the file but does not touch the topic — a channel edit waiting out a rate limit would sit on `SIGTERM` until the pod was killed outright.

A request Discord refuses is not retried, since retrying every interval spends the channel's rate limit on an answer that cannot change; a tally that then changes is published anyway, the next text not being the refused text. For the record, `PATCH /channels/{id}` with a `topic` is refused `CHANNEL_TOPIC_INVALID`, *"Field contains at least one word that is not allowed"* — which reads like a profanity filter and refuses a topic of `test` identically.

</details>

### tts {#tts-tool}

Says things out loud, and is the only thing that plays anything. It hears nothing and decides nothing; what it does is own the rendered-speech cache, the chime library, the volume, and the voice connection, so that everything a channel hears arrives by one route.

```yaml
tts:
  enabled: true
```

There is nothing to configure per server. Which synthesizer, which voice, and how a clip is handled are [`settings.tts`](#settings-tts) and `TTS_HOST` / `TTS_PORT` / `TTS_VOICE`. All this setting says is whether **this** server is allowed to speak through it.

**It is enabled separately from whatever is talking.** A server enabling `verbal-morality` and not this counts fines and says nothing; one enabling `quotes` and not this runs its rounds, pays them, and answers nobody. Both are said once at startup.

**Other tools speak through it.** `play` is the whole interface, and `play_held` is `play` for a sentence that does not exist yet, so hold music and the answer come out as one clip. `chime` names a WAV in `SPEECH_DIR/chimes` **without its extension**; a missing chime costs the chime and not the announcement.

**Rendering in advance is its `run`.** A tool that knows at startup what it will have to say hands over the list, and this renders it in the background, one phrase at a time across the whole process. A phrase that will not synthesize is a line in the log and then the next phrase, never the end of the run.

<details class="why" markdown="1">
<summary>Why one clip is free to play and another is not</summary>

A phrase with nothing in front of it and nothing to be done to it is handed over exactly as it was stored — Opus packets, no decode, no encode, no resample. A chime, or any volume below the channel's own, means samples.

So `quotes`, which never uses a chime and never turns itself down, takes the free path every time, and a backed-off fine with a flourish in front of it does not. The whole of that path is under [Speech]({{ '/about/#speech' | relative_url }}).

</details>

### verbal-morality {#verbal-morality}

The Verbal Morality Bot, after *Demolition Man*. It listens for words the server has decided against and, on hearing one, announces the fine out loud in the channel it was said in. The credits are imaginary but they are counted, by somebody else: the fine is handed to the server's [`scoreboard`](#scoreboard). **With no `scoreboard` enabled the fine is announced and not counted**, which the log says once at startup.

```yaml
verbal-morality:
  enabled: true
  config:
    words: [fiddlestick, poppycock]
    announcement: "{user}, you are fined {credits} for {violations} of the verbal morality statute."
    repeat_announcement: "{user}, you are also fined {credits} for {violations} of the verbal morality statute."
    recall_announcement: "{user}, you said {word}."
    chime: chime
```

| Setting | Required | Purpose |
|---|---|---|
| `words` | yes | Stems of what the server objects to. A lone one may be written unquoted rather than as a list |
| `announcement` | no | What gets said. `{user}`, `{credits}`, and `{violations}` are the placeholders |
| `repeat_announcement` | no | Said instead when the same speaker is fined again inside `repeat_seconds`, below. Same placeholders |
| `recall_triggers` | no, `what did i say`, `what did i just say`, `what was that` | How somebody asks what they were just fined for. **Replaces** the default. A lone one may be written unquoted rather than as a list |
| `recall_announcement` | no | What they are told. `{user}` and `{word}` are the placeholders — not `{credits}`, which is not what is being announced |
| `chime` | no | A WAV in `SPEECH_DIR/chimes`, played ahead of the announcement, named without its `.wav`. Also the whole of a [dampened fine](#what-a-fine-costs-and-how-loudly) |
| `repeat_seconds` | no, [`settings.fines`](#settings-fines) | How soon the same speaker is told they are "also fined" rather than hearing the whole sentence again |
| `recall_seconds` | no, [`settings.fines`](#settings-fines) | How long after being fined a speaker can ask what the word was |
| `backoff_seconds` | no, [`settings.fines`](#settings-fines) | The sliding window a violation counts for against how loudly the next one is announced |
| `backoff_percent` | no, [`settings.fines`](#settings-fines) | How much of the next announcement's loudness each violation inside that window takes off |
| `volume_floor` | no, [`settings.fines`](#settings-fines) | The quietest a fine is announced once the full backoff is earned |
| `dampen_after` | no, [`settings.fines`](#settings-fines) | How many fines this server's speakers hear in full before a one-credit one drops to the chime |
| `dampen_seconds` | no, [`settings.fines`](#settings-fines) | The sliding window that budget is spent inside |

All three templates default to the lines above, which the tool carries, so a server that wants the defaults can leave them out. A template with a placeholder nothing fills is rejected at startup rather than at the moment someone swears, and the error names which setting it was and which placeholders that one actually has — `recall_announcement` has `{user}` and `{word}`, and reaching for `{credits}` in it is refused.

#### Words are stems

Each word in `words` is a stem, expanded at startup into the endings it is said with, so `fiddlestick` also catches `fiddlesticks`, `fiddlesticked`, `fiddlesticking`, `fiddlestickin`, `fiddlesticker`, and half a dozen more. Matching is **whole words, case-insensitive** — a substring match fines Scunthorpe.

Two things to check before a stem goes in the list. **Its endings can reach a word that is innocent on its own.** And a **compound** may conjugate wrong: the rules use syllable count where English uses stress, so `dipshit` correctly takes `dipshitting` only because it is named in `COMPOUND_ENDINGS` in `utils/stems.py`, which is the one line to add to.

<details class="why" markdown="1">
<summary>Why stems rather than a word list</summary>

A list that has to spell out every ending is a list somebody gets around a week after writing it.

Expansion is English spelling rather than a dictionary: a final consonant doubles after a short vowel (`shit` grows a `shitter`, not a `shiter`), a silent `e` drops before a vowel, a sibilant takes `es`, and a `y` after a consonant becomes an `i` — except before an ending that already starts with one, so it is `shittiness` and not `shittyiness`. The exact rules, and why a compound needs naming by hand, are in `utils/stems.py`.

Nothing checks whether the result is a word anybody says, and it does not need to: a form nobody utters costs a few bytes in an alternation, while a missing one costs the tool the thing it exists to catch.

</details>

#### What a fine costs, and how loudly {#what-a-fine-costs-and-how-loudly}

The settings named in this section — `repeat_seconds`, `recall_seconds`, `backoff_seconds`, `backoff_percent`, `volume_floor`, `dampen_after`, and `dampen_seconds` — are written either here, in this server's `config`, or in [`settings.fines`](#settings-fines) for every server that names none of its own. This server's wins.

**The fine scales with the utterance**: one credit per forbidden word in it. `{credits}` is filled in already pluralized and as a numeral; what a credit is *called* is [`settings.credits.currency`](#settings-credits), pluralized by the same spelling rules the word list uses, so `penny` announces as `2 pennies`. `{violations}` reads `a violation` for one and `multiple violations` for more.

**What does not scale is the number of announcements.** Three violations in one utterance earn one, and **a violation earned while an announcement is playing is counted and not announced at all**. The tally is charged either way.

**Being fined twice in a row is worded differently.** A speaker fined again inside `repeat_seconds` gets `repeat_announcement` — "you are *also* fined". It is per speaker.

**A repeat offender is announced more quietly.** Every violation inside a sliding `backoff_seconds` takes `backoff_percent` off the next announcement, down to `volume_floor` — at the defaults, 5% a violation over five minutes, floored at a quarter as loud, so fifteen reach the bottom. The percentage is off what a listener hears rather than off the amplitude ([how a volume is read](#volumes)). The first swear in a window is at full volume, and none of this affects the tally.

**Past a point the sentence stops being said at all.** After `dampen_after` fines in full inside a sliding `dampen_seconds`, a **one-credit** fine becomes the `chime` alone. **This is off unless asked for**: `-1` is the default, and `0` dampens from the first one. A fine worth **more than one credit** is never dampened, though it does spend from the budget.

A dampened fine is still counted, still answers `recall_triggers`, and is still quietened by the backoff. **It needs a `chime`** — with none configured a dampened fine says nothing at all, which is reported at startup.

**The announcements are rendered at startup**, every name in `users` against one, two, and three violations in both wordings. Anyone not on the roster pays for their first fine, and nobody pays for it again.

`chime` is resolved **inside** `SPEECH_DIR/chimes` — a bare name or a path below it; anything that climbs out is refused at startup. It must be a **16-bit WAV**, any sample rate, mono or stereo.

A server electing in with **no `words`** is enabled and listening for nothing, which is reported at startup.

<details class="why" markdown="1">
<summary>Why one announcement, why it gets quieter, and why it eventually stops</summary>

Three announcements over the top of each other is a denial of service on the channel. The speaker plays one clip at a time, so the alternative to dropping is a queue, and a channel where three people swear over each other would spend the next minute being read fines for things it has moved on from. What somebody owes is not a function of whether they were told about it.

Reading the whole sentence out again sounds like a bot that has lost track of what it just said, which is what the second wording is for.

Being fined is the joke, and the joke told fifteen times in five minutes is a denial of service on the conversation. Dampening is that argument carried to its end: a quarter-volume sentence is still a whole sentence read over whatever the channel was talking about, and a room that has settled into swearing already knows the wording. Several forbidden words in one breath is different — a thing somebody has just done rather than the one the channel has heard all evening — and the sentence naming what it cost is the whole of the joke.

Rendering stops at three violations because that is what a sentence usually holds; a fourth is remarkable enough to wait for the synthesizer. And a chime is a WAV rather than an MP3 because playing audio without ffmpeg is the point of this path, and nothing in the image can decode anything else.

</details>

#### Asking what it was

The announcement names the fine and never the word. **Saying one of `recall_triggers` within `recall_seconds` of being fined is answered with the word**, through `recall_announcement` — ten seconds by default, and that window is the whole gate. Outside it, and for anybody with no fine on record, nothing is said at all.

The answer is **the last word of the fine that earned it**, and it is **the asker's own**. **A fine that went unannounced can still be asked about**, the word being recorded whether or not anybody heard the fine.

Three ways it parts company with the fine it is about: it carries **no chime**, it is **not quietened by the backoff**, and an utterance that both asks and offends is **fined and not answered**. Like a fine, it is dropped rather than queued while something is playing. It is also **not rendered in advance**, so the first answer naming a given word waits for the synthesizer.

<details class="why" markdown="1">
<summary>Why the window is the whole gate, and why this one is not warmed</summary>

"What did I say" is a thing people say to each other. What makes it a question for the bot is that whoever asked was fined seconds ago.

No chime, because a chime is for an interruption and this answers a question the channel has just been asked. No backoff, because the speaker most likely to need it is the one who has earned the most of one. And two clips over the top of each other for one sentence is the failure the single-announcement rule exists to prevent.

What a fine can be is the roster against three counts. What an answer can be is the roster against every form of every word in the list — for a list worth having, several hundred phrases a deployment would pay a synthesizer for on every start-up.

</details>

## Settings

The `settings:` block of `config.yaml`. Every one of these has a default, so none of them has to be written down; a name or a value that will not parse is reported at startup and falls back to the default.

**Settings of the same name under different sections are unrelated.** `backoff_seconds` means one thing under [`settings.quotes`](#settings-quotes) and another under [`settings.fines`](#settings-fines), and neither reads the other. The section is part of the name.

### settings.tts {#settings-tts}

Only used by tools that answer out loud. Where the synthesizer *is* is `TTS_HOST` and `TTS_PORT`, and which voice it uses is `TTS_VOICE`.

| Setting | Default | Purpose |
|---|---|---|
| `timeout_seconds` | `30.0` | Budget for a **single** wait on the synthesizer, not for a whole clip — a long phrase arriving steadily is not cut off for taking a long time |
| `stall_seconds` | `10.0` | How long the player waits mid-clip for audio that never comes before ending it |
| `lead_ms` | `500.0` | How much speech to have in hand before a clip starts playing, so a synthesizer that renders a phrase whole leaves no gap behind a chime. `0` starts on the first chunk |
| `hold_fade_in_ms` | `500.0` | How quickly music under a wait arrives. Only the `summary` tool asks for any, and only for a channel that set `hold_music`. `0` is a cut |
| `hold_fade_out_ms` | `2000.0` | How slowly it leaves, timed to reach silence where the first word starts. `0` is a cut |
| `cache_retention_days` | `90` | Days anything in `SPEECH_DIR/cache` survives without being played, counted from the last time it was. Also what clears out clips in a format nothing can read any more, and `.partial` files orphaned by a process killed mid-write. Any value below `1` keeps them forever. Chimes live elsewhere and are never reaped |

### settings.credits {#settings-credits}

Only used by `scoreboard`. Where the tally is written down is `CREDITS_FILE`.

| Setting | Default | Purpose |
|---|---|---|
| `currency` | `credit` | What a balance is denominated in, in the singular. The plural is grown from it by the spelling, so `penny` announces as `2 pennies`. Wording only — it changes nothing about what is counted |
| `save_seconds` | `5.0` | How often a changed tally is written to disk. `0`, or any value below it, stops the loop: the tally is kept in memory and written only on shutdown |
| `topic_seconds` | `10.0` | How often a changed tally is published to the voice channel topic — set as the channel **status**, a voice channel having no topic. The board also goes up the moment the bot takes up a channel, changed or not, so a fresh room is not left blank until somebody swears. `0`, or any value below it, keeps the tally off the channel entirely |

### settings.fines {#settings-fines}

Only used by `verbal-morality`. What a fine is *worth* is the scoreboard's; these are how it is said.

**These are the deployment's answer, and a server may write any of them in its own [`verbal-morality`](#verbal-morality) config instead**, where it wins.

<details class="why" markdown="1">
<summary>Why a bad value here defaults and a bad value per server raises</summary>

A settings block is read before any server exists, so a value that will not parse falls back rather than stopping the pod. A value in a tool's config is that server electing into something specific, and ignoring a typo there would leave one channel wondering why it sounds like the other one.

</details>

| Setting | Default | Purpose |
|---|---|---|
| `repeat_seconds` | `5.0` | How soon after being fined the same speaker is told they are "also fined" rather than hearing the whole sentence again. `0`, or any value below it, turns the second wording off |
| `recall_seconds` | `10.0` | How long after being fined a speaker can ask what the word was and be told. `0`, or any value below it, never answers |
| `backoff_seconds` | `300.0` | The sliding window a violation counts for against how loudly the next one is announced |
| `backoff_percent` | `5` | How much of the next announcement's loudness each violation inside that window takes off, and off the knob rather than the amplitude, so 5% is 5% quieter to listen to ([how a volume is read](#volumes)). `0` takes nothing off, turning the backoff off; anything above `100` reaches the floor on the first repeat, and anything negative is treated as `0` rather than made louder |
| `volume_floor` | `0.25` | The quietest a fine is announced once a speaker has earned the full backoff, as how loud it is next to `PLAYBACK_VOLUME` — a quarter is a quarter as loud ([how a volume is read](#volumes)). `0` silences a repeat offender entirely; `1` turns the backoff off |
| `dampen_after` | `-1` | How many fines a speaker hears **in full** inside `dampen_seconds` before a one-credit one drops to the chime alone. `-1`, or any value below `0`, announces every fine in full; `0` is a budget of nothing, so the first one-credit fine of a window is already a chime |
| `dampen_seconds` | `3600.0` | The sliding window that budget is spent inside, so a speaker is owed a full fine again this long after the last one they heard |

### settings.quotes {#settings-quotes}

Only used by `quotes`. The triggers and the lines themselves are a YAML file at `QUOTES_FILE`, plus whatever a server [added for itself](#what-a-server-adds-for-itself).

**The deployment's answer, which a server may override in its own [`quotes`](#quotes) config**, on the same terms as a fine: one room says the same six things all night and the next one does not.

| Setting | Default | Purpose |
|---|---|---|
| `backoff_seconds` | `300.0` | How long a trigger stays spent after it fires, so a channel that keeps saying the same word hears the line once. `0`, or any value below it, answers every trigger every time |

### settings.transcripts {#transcripts}

Where transcripts are written is `TRANSCRIPT_DIR`, and what clock they are stamped with is `TZ`.

| Setting | Default | Purpose |
|---|---|---|
| `retention_days` | `-1` | Days to keep. `-1`, or any value below `1`, keeps forever |
| `resume_seconds` | `5.0` | How long a transcript is held open for a reconnect to the same channel. `0` seals it on disconnect |
| `schedule` | *(unset)* | The default windows for a room listed in `monitored_channels` that names none of its own — see [writing a window](#writing-a-window). **Not** what decides which rooms are kept; that is the room list itself |

Pruning is **off by default**, and any value below `1` disables it entirely — `0` is a no-op rather than "delete everything", so a mis-set setting cannot destroy the archive. At a positive `N`, files older than `N` days are deleted, aged by the **date at the front of the filename** rather than mtime. Pruning runs at startup and whenever a session opens.

#### Writing a window {#writing-a-window}

A window is written as a day and a 24-hour range, and says when a session may **start** being written down — not how long it may run for. A session that opens inside one keeps writing until everybody disconnects, however far past the end.

```yaml
schedule:
  - Wed 17:00-00:00      # Wednesday 17:00 until midnight
  - Fri 21:00-02:00      # Friday 21:00 until 02:00 Saturday
```

| Rule | What it means |
|---|---|
| An end at or before the start | Runs into the following day, which is how one line says "Friday evening" |
| `24:00` | May be written for the end of a day |
| An end equal to the start | The whole 24 hours |
| The interval | Half-open — the start is included, the end is not, so `Wed 17:00-00:00` and `Thu 00:00-02:00` meet without overlapping and without leaving a minute between them |
| Days | `Mon` through `Sun`, or written out, in any case |
| The clock | `TZ` |

**A schedule nothing could be read out of writes nothing down**, rather than falling back to something wider. An entry that cannot be read is dropped and reported at startup, and if none survive, that room keeps nothing.

<details class="why" markdown="1">
<summary>Why a typo narrows rather than widens</summary>

A schedule is written by somebody narrowing what is recorded, and a typo in it must not widen it back out. An evening not written down can be had again; one that should not have been written down cannot be taken back.

</details>

### settings.presence {#settings-presence}

What the bot says about itself while a conversation is being kept. Per deployment and necessarily so: Discord has one presence per bot rather than one per server. See [the status]({{ '/about/#the-status' | relative_url }}).

| Setting | Default | Purpose |
|---|---|---|
| `transcribing` | `🎙️ transcribing...` | Shown under the bot's name while any session is on the record, and cleared when none is. Empty turns the signal off. The emoji goes in the words — a custom status has an emoji field of its own, and Discord does not apply it for a bot |

### settings.llm {#settings-llm}

Only used by `summary`. Where the endpoint *is*, what key it wants, and which model to ask for are `LLM_API_BASE`, `LLM_API_KEY`, and `LLM_MODEL`.

| Setting | Default | Purpose |
|---|---|---|
| `timeout_seconds` | `120.0` | Budget for one completion, end to end. Generous next to the ASR's, a summary being several hundred tokens of output rather than a sentence. Keep it well under the deployment's termination grace period |
| `max_output_tokens` | `1024` | A ceiling on what is **generated**. Not the context window and not the whole request: the input is not counted against it. Named for what it bounds rather than for the wire field it becomes (`max_tokens`), whose name has cost more than one person an afternoon |
| `temperature` | `0.7` | How much licence the model has. Higher than a mechanical transform would want, because the output is prose somebody reads for pleasure |
| `thinking` | `true` | Whether a model that reasons before answering is allowed to. `false` sends `chat_template_kwargs.enable_thinking`, and is sent **only** to turn reasoning off — an endpoint that has never heard of the field is never shown it |

#### On reasoning models

Two traps, both with a setting for a fix.

**Reasoning is generated, so it spends `max_output_tokens`.** Thinking and answer come out of the same budget, and running out mid-thought leaves `content` empty — a `200` carrying nothing, which reads like a broken endpoint and is a setting. Measured against a 27B reasoning model on a real 1,653-line session: 4,137 generated tokens, of which the answer was about 700. At `1024` it never reached the answer at all. The client says so rather than making you find out:

```
the model spent its whole 1024-token budget reasoning and never began the
answer. Raise 'settings.llm.max_output_tokens', or set
'settings.llm.thinking: false' to stop it reasoning at all
```

**Reasoning is also most of the wall clock.** That same session took 94s with reasoning and 12s without, for summaries of comparable quality. Nobody waits on a summary written after everyone has left, but somebody *is* waiting on the retelling — so **a deployment pointing at a reasoning model wants `thinking: false`**, for the sake of that one path.

**Thinking is stripped whichever way it arrives**, whether or not `thinking: false` is set, the setting being a request rather than a guarantee.

<details class="why" markdown="1">
<summary>Where a model puts its thinking, and why all of it is cut</summary>

That is a property of the serving stack rather than of the model: beside the answer in a `reasoning_content` field, or inline at the front of `content`, fenced in `<think>` tags. The first costs nothing to ignore, only `content` ever being read.

The second is cut out in every spelling seen in the wild — `<think>`, `<thinking>`, `<reasoning>`, `<thought>`, any casing, with attributes, several blocks — because left in, it opens the summary with the model talking to itself and the synthesizer reads the tags out loud. An opening tag with no closing partner is a model cut off mid-thought, so everything after it goes too.

</details>

### settings.summaries {#settings-summaries}

Where summaries are written is `SUMMARY_DIR`.

| Setting | Default | Purpose |
|---|---|---|
| `retention_days` | `-1` | Days to keep. `-1`, or any value below `1`, keeps forever. Its own clock, separate from the transcripts': keeping summaries for a year and transcripts for a month is a reasonable thing to want |

## Environment

What a deployment points at, rather than how it behaves. `.env` is loaded if present. Nothing about a particular deployment is baked into the image, so the same image runs anywhere the variables below point it at.

| Variable | Default | Purpose |
|---|---|---|
| `CONFIG_FILE` | `/config/config.yaml` | The mounted file holding `settings` and `servers` |

### Discord {#env-discord}

| Variable | Default | Purpose |
|---|---|---|
| `DISCORD_TOKEN` | — | Bot token. **Required** — the bot exits immediately without it |
| `COMMAND_PREFIX` | `!` | Prefix for the `!join` / `!leave` commands |
| `AUTOJOIN` | `true` | Join when a human enters a voice channel; leave when it empties. Accepts `true/false`, `1/0`, `yes/no`, `on/off` |

With `AUTOJOIN` enabled the bot connects as soon as a non-bot member enters a voice channel and disconnects once it empties of humans. A bot can occupy only one voice channel per guild, so a second channel becoming active does not make it hop — which would fragment both transcripts. `!join` and `!leave` remain available either way, and require **Message Content Intent** in the Discord Developer Portal.

### ASR {#env-asr}

| Variable | Default | Purpose |
|---|---|---|
| `WYOMING_HOST` | `localhost` | Hostname or service name of the Wyoming ASR server |
| `WYOMING_PORT` | `10300` | Wyoming's conventional port |
| `STT_LANGUAGE` | `en` | Sent as `Transcribe.language` |
| `MAX_CONCURRENT_TRANSCRIPTIONS` | `4` | Ceiling on in-flight utterances, so a busy channel cannot open unbounded connections against a shared ASR |

### TTS {#env-tts}

Only used by tools that answer out loud. A deployment with no such tool enabled never opens a connection.

| Variable | Default | Purpose |
|---|---|---|
| `TTS_HOST` | `localhost` | Hostname or service name of the Wyoming TTS server |
| `TTS_PORT` | `10200` | Wyoming's conventional TTS port |
| `TTS_VOICE` | — | Voice to ask for. Empty takes whatever the synthesizer considers its default, so a server with one voice loaded needs no setting |
| `PLAYBACK_VOLUME` | `1.0` | How loud everything played into a channel is, chime included. `1.0` is however loud the synthesizer rendered it, `0.8` is 20% quieter, `1.2` is 20% louder and clipped rather than wrapped. A knob rather than a multiplier — see [How a volume is read](#volumes). Any value below `0` is treated as silence. **Below `1.0` every clip is decoded and re-encoded on its way past**, so it has a CPU cost as well as a loudness one — turn a channel down at the Discord end where you can |
| `SPEECH_DIR` | `/speech` | Audio on disk, as one root with a directory per kind. `cache/` is rendered speech as Ogg Opus, written and reaped by the bot — mount a writable volume here, since without one every phrase is synthesized again every time it is said. `chimes/` is where you put a WAV by hand |

#### How a volume is read {#volumes}

Every volume in this bot is a knob rather than a multiplier: `1` is full, `0` is silent, and **half is half as loud to listen to**. That covers `PLAYBACK_VOLUME`, `settings.fines.volume_floor`, `settings.fines.backoff_percent`, and each channel's `hold_volume`.

The setting is converted on a power-law curve on its way to the samples, which is why these numbers do what they say:

| Setting | Amplitude | Attenuation |
|---|---|---|
| `1.0` | `1.0` | 0 dB |
| `0.8` | `0.690` | −3.2 dB |
| `0.5` | `0.316` | −10 dB |
| `0.25` | `0.100` | −20 dB |
| `0.15` | `0.043` | −27.4 dB |
| `0` | `0` | silence |

Volumes multiply as knobs, so a tool asking for half of a deployment set to half is announced at a quarter — a quarter as loud, which is a tenth of the amplitude. Nothing has to be set in decibels.

<details class="why" markdown="1">
<summary>Why a knob rather than a multiplier</summary>

The obvious implementation gets it wrong. Hearing is logarithmic, so a clip at half the *amplitude* is under 3 dB down and still sounds around four fifths as loud; halving what somebody actually hears takes about 10 dB.

A setting that scaled amplitude directly would lie: a hold-music volume of `0.15` would be 16 dB down and land at about a third of the loudness of the talking rather than a seventh, and a fine backoff of 5% a violation would move 0.45 dB, which nobody can hear until the fifth or sixth one.

</details>

### Quotes {#env-quotes}

| Variable | Default | Purpose |
|---|---|---|
| `QUOTES_FILE` | `/app/src/miss_quote/resources/quotes.yaml` | The triggers and the lines they answer with, as a YAML mapping of title to trigger to line. The deployment's list; the image ships the one in `resources/`, and mounting a file over that path replaces it. A path and only a path; a server that wants a list fetched over HTTP says so under [`additional_quotes`](#keeping-that-list-somewhere-else) |

### Credits {#env-credits}

| Variable | Default | Purpose |
|---|---|---|
| `CREDITS_FILE` | `/credits/credits.json` | The running tally, as JSON. One file behind every server's board. Mount a volume at its directory to keep what everybody owes across restarts |

### LLM {#env-llm}

Only used by `summary`. An OpenAI-compatible chat-completions endpoint and nothing more specific: a root, an optional bearer token, and a model name. `/chat/completions` is the whole of the API surface used, so a hosted API, a gateway in front of one, and a model on the next machine over are the same three variables.

| Variable | Default | Purpose |
|---|---|---|
| `LLM_API_BASE` | `http://localhost:8080/v1` | The API root, with `/chat/completions` appended. There is no default that will work out of the box, in the same way there is none for the ASR |
| `LLM_API_KEY` | — | Sent as a bearer token when there is one. Empty sends **no `Authorization` header at all**, rather than an empty credential for an endpoint to decide what to do with. Never logged and never in an error message |
| `LLM_MODEL` | — | What to ask for. **Required** by `summary`; there is no default, a model name being a deployment's own and a guess being a 404 that reads like a broken endpoint |

### Transcripts {#env-transcripts}

| Variable | Default | Purpose |
|---|---|---|
| `TRANSCRIPT_DIR` | `/transcripts` | Directory the session files are written to |
| `SUMMARY_DIR` | `/summaries` | Directory the summaries are written to, in a tree the same shape as the transcripts'. A separate root so the two can be mounted and shared on different terms |
| `TZ` | `America/Los_Angeles` | Timezone for session filenames and the offset stamped on each line |

### Speech segmentation {#env-segmentation}

| Variable | Default | Purpose |
|---|---|---|
| `SPEECH_FLUSH_TIMEOUT_SECONDS` | `2.0` | Transcribe a speech buffer that stopped receiving audio, e.g. a speaker who muted mid-sentence |
| `USER_TIMEOUT_SECONDS` | `60` | Discard per-user VAD state after this much silence |
| `LOG_LEVEL` | `INFO` | Standard Python log levels |

VAD thresholds, the pre-roll depth, and the Wyoming chunk size are deliberately **not** configurable at all, from either place — they are tied to Silero's fixed 512-sample frame and live in `config.py`.

<nav class="page-nav" aria-label="Previous and next page">
  <a class="page-nav-prev" rel="prev" href="{{ '/installation/' | relative_url }}"><span class="page-nav-label">← Previous</span><strong>Installation</strong><span class="page-nav-blurb">Volumes, permissions, and deploying it</span></a>
</nav>
