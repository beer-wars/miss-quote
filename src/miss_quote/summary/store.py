"""
Where an account of a session is kept, and how the right one is found.

The tree is the transcripts' tree with a different root: the same guild and
channel directories, from the same `Source.relative_directory`, and a file named
for the transcript it describes rather than for the moment it was written. A
summary and its conversation are therefore found from each other by changing one
path segment, and a session that took an ordinal to avoid a collision keeps it
here — two sessions that could not share a transcript name cannot share a
summary name either.

**One sitting is not always one session either**, and that is a different
question with a different answer. A room on a capture schedule is written down
for a stretch of clock somebody named — `Wed 17:00-00:00` — and the sessions
filed inside one occurrence of it are one thing to write about, however many
times the room emptied and refilled. That is a `Sitting`, and it is what
`summary` writes its account from: every seal inside the window rewrites the same
file, named for the session that opened the sitting, so the evening leaves one
account rather than four overlapping ones. A session opened outside every window
— a room put on the record by hand — is nobody's sitting and is summarized on its
own.

**One evening is not always one session.** A transcript is one connection to a
voice channel, and `resume_window_seconds` is a handful of seconds — long enough
to ride out a client dropping, not long enough for a pod restart or for a room
that empties while everybody refills a glass. Past it, the rest of the night is
filed separately and summarized separately. So what is looked up here is a
`Chain`: the run of consecutive sessions with only a short gap between one
ending and the next beginning, which is what somebody means by "last time".

That gap is measured **close to open**, and the close is the awkward half. A
filename is the moment a session opened and nothing on disk is the moment it
closed; the nearest thing is the timestamp of the last line in the JSONL, which
is why finding a chain reads transcripts and not only names. A session whose
transcript is gone — pruned ahead of its summary, which is a thing deployments
are told they may want — is treated as having closed when it opened, so the
chain stops there rather than being stitched on a guess.

Sessions are enumerated from **both** trees. One too short to have been worth
summarizing has no summary and is still the bridge between two that do; looking
only at summaries would break a chain at exactly the point something has to hold
it together. It can equally be the newest session in the channel, or the last
one on the day somebody named, which is what `find` takes several anchors for.

Everything is read by **filename** rather than by mtime. The name is the moment
the session opened; the mtime is the moment a file happened to be written, which
is a different thing the instant anything is ever regenerated or restored from a
backup. Retention ages files the same way and for the same reason.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from miss_quote.config import summary_cfg, transcript_cfg
from miss_quote.summary.when import LATEST, When
from miss_quote.transcript.schedule import Occurrence
from miss_quote.transcript.writer import (
    Source,
    Transcript,
    Utterance,
    date_from_filename,
    last_spoken,
    opened_from_filename,
    utterances_in,
)
from miss_quote.utils import duration
from miss_quote.utils.logging import get_logger

logger = get_logger(__name__)

FILE_ENCODING = "utf-8"

# Written to and renamed over, so a process killed mid-write leaves something
# nothing will ever read as a summary rather than half of one.
PARTIAL_SUFFIX = ".partial"

# How the parts of one evening are set end to end. Blank lines and nothing else:
# a rule or a heading between them is a seam the reteller would read out.
PART_SEPARATOR = "\n\n"

# Which evening it was, so a retelling can place the story. Spelled out rather
# than left to the model to infer from prose that never mentions a date.
WHEN_LINE = "This was on {when}."
WHEN_FORMAT = "%A, %d %B %Y"


@dataclass(frozen=True)
class Session:
    """One filed session: when it opened, and whatever it left behind."""

    opened: datetime
    transcript: Path | None
    summary: Path | None

    @property
    def name(self) -> str:
        """The session's name, which is when it opened."""
        found = self.summary or self.transcript

        return found.stem if found else ""

    @property
    def closed(self) -> datetime:
        """
        When the last thing in this session was said.

        Falls back to the moment it opened when there is no transcript to ask,
        or nothing in it. That reads as a session of no length, so the gap to
        whatever came next is as wide as it can be and the chain stops — which
        is the right way to be wrong. Stitching on an unknown ending would hand
        somebody an unrelated conversation as part of their own.
        """
        if self.transcript is None:
            return self.opened

        return last_spoken(self.transcript) or self.opened

    def read(self) -> list[Utterance]:
        """
        Everything said in this session, or nothing where there is no transcript.

        A session whose transcript has been pruned out from under its summary is
        a session nothing can be re-read from. What it left behind is the
        account already written, which is a different thing from the raw
        material and is not substituted here.
        """
        if self.transcript is None:
            return []

        return utterances_in(self.transcript)


@dataclass(frozen=True)
class Sitting:
    """
    One occurrence of a capture window, and every session filed inside it.

    A room on a schedule empties and fills several times in an evening — people
    leave, the bot is dragged elsewhere, a pod restarts — and each of those is
    its own connection and its own transcript. None of them is the evening. What
    the window says is which of them belong together, so the account is written
    from all of them and filed under the name of the one that opened the sitting.

    Sessions are ordered oldest first, so what a model is handed reads in the
    order the room said it.
    """

    sessions: tuple[Session, ...]

    @property
    def opened(self) -> datetime:
        """When the sitting started, which is when its first session did."""
        return self.sessions[0].opened

    @property
    def name(self) -> str:
        """
        What the sitting is filed as, which is what its first session is called.

        Stable as the sitting grows: every seal inside the window writes the same
        file, so a room that came and went four times leaves one account rather
        than four overlapping ones.
        """
        return self.sessions[0].name

    def read(self) -> list[Utterance]:
        """The whole sitting, in the order it was spoken."""
        return [
            utterance for session in self.sessions for utterance in session.read()
        ]


@dataclass(frozen=True)
class Chain:
    """One evening: the sessions it was filed as, oldest first."""

    sessions: tuple[Session, ...]

    @property
    def opened(self) -> datetime:
        return self.sessions[0].opened

    @property
    def name(self) -> str:
        """
        What this evening is called, which is what its first session is called.

        Stable as the evening grows: a chain found again after another session
        joined the end of it answers to the same name, which is what lets a
        caller tell "the story I just told" from "a different one".
        """
        return self.sessions[0].name

    @property
    def parts(self) -> int:
        """How many of this evening's sessions were written about."""
        return sum(1 for session in self.sessions if session.summary is not None)

    def read(self) -> str:
        """
        The whole evening, as the text a reteller is given.

        The parts are set end to end with blank lines between them and nothing
        else. Each was written as a standalone account, so three of them in a
        row read as three beginnings — telling the reteller that they are one
        evening is the retelling prompt's job, and it is written to do it.

        A part that cannot be read costs that part. The rest of the evening is
        still an evening, and a retelling missing its middle hour is better than
        no retelling at all.
        """
        written = [WHEN_LINE.format(when=self.opened.strftime(WHEN_FORMAT))]

        for session in self.sessions:
            if session.summary is None:
                continue

            try:
                written.append(session.summary.read_text(encoding=FILE_ENCODING))
            except OSError as exc:
                logger.error("Could not read the summary at %s: %s", session.summary, exc)

        return PART_SEPARATOR.join(part.strip() for part in written)


class SummaryStore:
    """Files summaries beside the transcripts they came from, and finds them."""

    def __init__(
        self,
        directory: Path | None = None,
        retention: float | None = None,
        transcripts: Path | None = None,
    ) -> None:
        self._directory = Path(directory or summary_cfg.directory)
        self._retention = (
            summary_cfg.retention if retention is None else retention
        )
        self._transcripts = Path(transcripts or transcript_cfg.directory)

    @property
    def retention_enabled(self) -> bool:
        return self._retention > duration.NEVER

    def path_for(self, transcript: Transcript, name: str | None = None) -> Path:
        """
        Where one transcript's summary belongs.

        Named from the transcript's own stem rather than from the clock, so the
        pairing survives a summary written days late — by a backfill, or by a
        deployment that was pointed at a working endpoint after the fact.

        `name` files it under some other session instead, which is how every seal
        inside one capture window writes the same account rather than a fresh one
        beside the last. See `Sitting.name`.
        """
        return (
            self._directory
            / transcript.source.relative_directory
            / f"{name or transcript.path.stem}{summary_cfg.filename_suffix}"
        )

    def write(
        self, transcript: Transcript, text: str, name: str | None = None
    ) -> Path | None:
        """
        Store one summary, reporting where it went.

        Through a temporary file and a rename, so a process killed partway
        through leaves no half-written summary to be read back as a whole one.
        A directory that cannot be written to costs the summary and is reported;
        the transcript it came from is untouched and can be summarized again.

        `name` is `path_for`'s, and is what makes a rewrite a rewrite: an account
        of a sitting replaces the one written when the sitting was shorter.
        """
        path = self._path_prepared(transcript, name)
        if path is None:
            return None

        partial = path.parent / (path.name + PARTIAL_SUFFIX)

        try:
            partial.write_text(text, encoding=FILE_ENCODING)
            partial.replace(path)
        except OSError as exc:
            logger.error("Could not write the summary at %s: %s", path, exc)
            partial.unlink(missing_ok=True)
            return None

        return path

    def find(self, source: Source, when: When, gap: timedelta) -> Chain | None:
        """
        The evening somebody asked for in one channel, if it had one.

        An **anchor** is picked and the chain grown out from it, rather than
        every chain in the channel's history being built and one of them chosen.
        Listing names is a directory scan and reading transcripts is not, and a
        channel that has been kept forever has a great many of them; this way an
        evening of three sessions costs three reads whether the channel is a
        week old or two years old.

        Growing runs **both ways**. Backwards is the point of it — the anchor is
        the last session of an evening and the rest of the evening is behind it.
        Forwards matters for a date: an evening that began before midnight on
        the twelfth is asked for as the twelfth and does not end there.

        A chain with no summaries anywhere in it is nothing to tell — and not
        the end of the search. The best anchor is frequently a session nobody
        wrote about: one still in progress, whose summary is written when the
        transcript seals, or one at the end of a night that was too short to be
        worth summarizing. Either can be the newest session in the channel or
        the last one on the day somebody named, and stopping there answers "no
        notes" with the notes sitting an hour behind it. So anchors are taken in
        order until one of them yields an evening with something in it.

        A chain already rejected is never grown again from the inside. Every
        session in one produces that same chain, so remembering it is what keeps
        a channel of unsummarized sessions linear instead of quadratic.
        """
        sessions = self._sessions(source)
        rejected: set[Session] = set()

        for anchor in _anchors(sessions, when):
            if anchor in rejected:
                continue

            chain = _grown(sessions, anchor, gap)
            if any(session.summary for session in chain):
                return Chain(sessions=tuple(chain))

            rejected.update(chain)

        return None

    def latest(self, source: Source, gap: timedelta) -> Chain | None:
        """The most recent evening in one channel, if it has had any."""
        return self.find(source, LATEST, gap)

    def sitting(self, source: Source, occurrence: Occurrence) -> Sitting | None:
        """
        Every session one channel filed inside one occurrence of a window.

        Membership is decided by when a session **opened**, which is the same
        moment the schedule was asked about when it decided whether to write that
        session down at all. A window says when a sitting may start rather than
        how long it may run, so the session that opened at twenty to midnight and
        sealed at one belongs to the evening it began in — and that is usually
        the longest part of it.

        Both trees, on `_sessions`' terms: a session too short to have been worth
        summarizing still said something, and a sitting that skipped it would be
        an account with a hole in the middle. Nothing where the window produced
        nothing, which is a window nobody sat through.
        """
        inside = tuple(
            session
            for session in self._sessions(source)
            if occurrence.covers(session.opened)
        )

        return Sitting(sessions=inside) if inside else None

    def prune(self) -> list[Path]:
        """
        Delete summaries older than the retention window.

        Aged by the date at the front of the filename, on the same reasoning as
        the transcripts: the name says when the session was, and the mtime says
        when a file was last touched, which is not the same question.
        """
        if not self.retention_enabled or not self._directory.is_dir():
            return []

        # The same clock the session was named by, so a summary is not kept or
        # dropped a day early for having been taken in a different timezone from
        # the one the process is reading it in.
        # Taken back as a moment and then down to a day, because what a name
        # carries is a date: a window shorter than a day leaves only today's.
        now = datetime.now(ZoneInfo(transcript_cfg.timezone))
        cutoff = (now - timedelta(seconds=self._retention)).date()
        removed: list[Path] = []

        for path in self._directory.rglob(f"*{summary_cfg.filename_suffix}"):
            taken = date_from_filename(path)
            if taken is None or taken >= cutoff:
                continue

            try:
                path.unlink()
            except OSError as exc:
                logger.error("Could not prune %s: %s", path, exc)
                continue

            removed.append(path)
            logger.info(
                "Pruned summary %s (older than %s).",
                path.relative_to(self._directory),
                duration.spoken(self._retention),
            )

        return removed

    def _sessions(self, source: Source) -> list[Session]:
        """
        Every session one channel has filed, oldest first.

        From both trees, keyed by the name they share. A stem in only the
        transcripts is a session nobody wrote about; one in only the summaries
        is a session whose transcript has been pruned out from under it, which
        is what a deployment keeping summaries longer than transcripts is asking
        for. Both are sessions and both take part in a chain.

        A name that will not parse as a moment is skipped. It is not something
        this wrote, and guessing where it belongs in the order is how a hand-
        dropped file ends up spliced into somebody's evening.
        """
        transcripts = _filed(
            self._transcripts / source.relative_directory, transcript_cfg.filename_suffix
        )
        summaries = _filed(
            self._directory / source.relative_directory, summary_cfg.filename_suffix
        )

        sessions = []

        for stem in transcripts.keys() | summaries.keys():
            transcript = transcripts.get(stem)
            summary = summaries.get(stem)

            named = transcript or summary
            opened = opened_from_filename(named) if named else None
            if opened is None:
                continue

            sessions.append(
                Session(opened=opened, transcript=transcript, summary=summary)
            )

        return sorted(sessions, key=lambda session: (session.opened, session.name))

    def _path_prepared(
        self, transcript: Transcript, name: str | None = None
    ) -> Path | None:
        """Where a summary goes, with somewhere to put it."""
        path = self.path_for(transcript, name)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("Could not make %s to summarize into: %s", path.parent, exc)
            return None

        return path


def _filed(directory: Path, suffix: str) -> dict[str, Path]:
    """Everything one directory holds of one kind, by the name it shares."""
    if not directory.is_dir():
        return {}

    return {path.stem: path for path in directory.glob(f"*{suffix}")}


def _anchors(sessions: Sequence[Session], when: When) -> Iterator[Session]:
    """
    The sessions an evening might be grown out from, best first.

    For the most recent evening that is every session, newest first. For a date
    it is the sessions near enough to the day named, the **last** one on the
    nearest qualifying day first: a day with an afternoon conversation and an
    evening one is asked about with one date, and the later of the two is what
    "what happened on the twelfth" means, on the same reading that makes "last
    time" the most recent rather than the first.

    Ties in distance go to the later day, so a target falling between two
    evenings resolves forwards. Somebody counting back weeks is counting to a
    session, and the more recent of two equally close ones is the one they are
    more likely to have been at.

    Several rather than one, because the best anchor need not be an evening
    anybody wrote about, and the caller is the only one that can tell. See
    `SummaryStore.find`.
    """
    if when.latest or when.target is None:
        yield from reversed(sessions)
        return

    named = when.target
    near = [
        session
        for session in sessions
        if abs((session.opened.date() - named).days) <= when.tolerance_days
    ]

    yield from sorted(
        near,
        key=lambda session: (
            -abs((session.opened.date() - named).days),
            session.opened,
        ),
        reverse=True,
    )


def _grown(sessions: Sequence[Session], anchor: Session, gap: timedelta) -> list[Session]:
    """Every session joined to the anchor, in either direction, oldest first."""
    at = sessions.index(anchor)

    first = at
    while first > 0 and _joined(sessions[first - 1], sessions[first], gap):
        first -= 1

    last = at
    while last + 1 < len(sessions) and _joined(sessions[last], sessions[last + 1], gap):
        last += 1

    return list(sessions[first : last + 1])


def _joined(earlier: Session, later: Session, gap: timedelta) -> bool:
    """
    Whether two sessions are one evening with an interruption in the middle.

    Close to open, not open to open. A four-hour session followed ten minutes
    later by another is one evening; two one-hour sessions four hours apart are
    two, and the only thing telling them apart is when the first one ended.
    """
    return later.opened - earlier.closed <= gap
