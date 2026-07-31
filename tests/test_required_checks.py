# tests/test_required_checks.py
#
# THE LIVE COPY, guarding this repo's own ruleset — the baseline applied to
# itself. Identical to templates/tests/test_required_checks.py except for this
# header: TestTheLiveGuardMatchesTheTemplate in tests/test_guard.py holds
# everything from the docstring down byte-for-byte, so the two cannot drift.
#
# So edit the TEMPLATE and re-copy. A fix applied only here ships broken to every
# repo that copies the template, and this repo stays green while it does.

"""
The required status checks on the protected branch must name jobs that exist.

Required checks are matched by **string**. GitHub does not verify that a required
context corresponds to anything: a context naming a job that no longer exists
simply never reports, and a rule waiting on a check that never arrives is
indistinguishable from one that has not run yet. The pull request sits *pending*
rather than failing, which reads as "still working" instead of "misconfigured".

So the branch appears protected, the settings UI agrees, and the rule applies to
nothing. That is the failure this file exists to make loud, and the reason it is
a test rather than a comment in the workflow — a comment cannot fail.

The coupling is checked from both ends, deliberately as an equality:

``TestEveryRequiredCheckExists``
    A context in the ruleset with no matching job. This is the rename, and the
    typo.

``TestEveryJobIsRequired``
    A job in the workflow that no context names. A new seam that is not required
    is a seam that does not gate — it runs, it can go red, and it does not block
    a merge. This is the likelier of the two in practice: the job gets added,
    the ruleset does not, and the gap survives until someone notices the tick
    was green for the wrong reason.

Adding a job without requiring it is a legitimate decision. It should cost a
line in the ruleset rather than happening by omission, which is what the
equality buys.


WHAT THIS DOES NOT CHECK
------------------------

It reads the committed JSON, not GitHub. Nothing here can prove the ruleset is
actually applied, or applied with this content — the API is the authority and it
needs admin credentials a test suite should not have. Someone editing the ruleset
in the web UI and not re-exporting it leaves this file green and wrong.

That is a smaller hole than the one it closes, and it points the right way: the
committed file is the reviewable record, so a change made only in the UI is a
change made outside review.


PARSING THE WORKFLOW WITHOUT PyYAML
-----------------------------------

By indentation, on purpose. A test that imports ``yaml`` needs PyYAML in
whatever manifest CI installs — and if CI installs the deploy manifest unchanged
(it should), that means a dependency in the serving image for the benefit of one
test. The job header is a fixed, shallow shape.

Three details the shape depends on:

**A job's check context is its ``name:``, or its job id when it has none.** The
parser falls back the same way GitHub does, so a job that loses its ``name:``
fails here as a rename rather than vanishing from the comparison.

**``#`` opens a comment only at line start or after whitespace**, per YAML, so a
value is truncated on that pattern and not on every ``#``.

**Keys are placed by comparing indents, not by matching a column.** The first
key under a job fixes the indent that job's own keys sit at, and everything is
decided against that. Reading each key with its own regex pinned to its own
column looked equivalent and was not: it failed *open*, because a key written
one level deeper simply did not match, and not matching means "absent" — so a
job indented four-per-level hid both its ``strategy.matrix`` and its ``name:``,
and the guard went on to require a bare job id nothing reports.

**A context GitHub composes is refused, not guessed.** A matrix job reports one
suffixed check per combination and a reusable-workflow job reports
``caller / called``; neither is derivable from this file, and both would produce
a required context nothing reports. ``_job_contexts`` stops on them, which turns
the pending-forever failure into a red suite — the same trade ``_scalar`` makes
for a block scalar. The cost is that a repo whose gate is a matrix cannot use
this guard unmodified, which is the right way round: it fails at the point the
assumption breaks rather than the day someone opens a pull request.

The same parser exists in ``scripts/bootstrap-repo.py`` in the baseline repo,
which uses it for ``--checks-from``. The duplication is deliberate: this file
gets copied into other repos and has to stand alone. Change one, change both.
"""

import json
import re
from fnmatch import fnmatch
from pathlib import Path

import pytest


# ── Per-repo constants. Check these three after copying. ──────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent

# The branch the ruleset protects. Written as a literal rather than "whatever
# this repo calls its default branch": if deployment is triggered by a branch
# pattern configured outside this repo, following the default would silently
# decouple protection from deployment the day the default moved.
_PROTECTED_BRANCH = "main"

_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_RULESET = _REPO_ROOT / ".github" / "rulesets" / "main.json"


# ── Reading the workflow ──────────────────────────────────────────────────────

def _strip_comment(value: str) -> str:
    """YAML comment rule: `#` opens a comment at line start or after whitespace."""
    return re.split(r"(?:^|\s)#", value, maxsplit=1)[0].strip()


# The unquoted spellings of null in YAML's core schema. `NULL` and `Null` are
# null; `nUll` is not, and is an ordinary string.
#
# Named rather than repeated because every place a key's value is inspected has
# to answer the same question — is there a value here at all — and answering it
# differently in different places is what this fixes. `jobs: null` and
# `strategy: null` were each read as "carries a value", so a key that says
# nothing was refused with a message describing a flow mapping that was not
# there. Quoted, these are strings and keep their value, so the comparison
# happens after any quotes have been dealt with, never before.
_NULL_TOKENS = ("~", "null", "Null", "NULL")


def _key(line: str) -> tuple[int, str, str] | None:
    """
    ``(indent, key, raw value)`` for a line that is a mapping key, else ``None``.

    One reader for every key below a job, so the caller can place a key by
    comparing its indent to the job's rather than to a constant. Each key
    having its own regex pinned to its own column is what let a
    ``strategy:`` at six spaces, or a quoted ``"matrix":``, pass unseen — and
    unseen reads as absent, which is the fail-open direction.

    Tolerant about the shapes YAML allows for the key itself: quoted either way,
    and a space before the colon. That tolerance is safe here in a way it would
    not be for a job id, and the asymmetry is deliberate. These key names are
    only a structural signal — over-recognising one costs a refusal, which is
    loud. A job id *is* the context string, so a quoting form this cannot fully
    decode is refused instead.

    The value is returned **raw**. ``_scalar`` reads quoting before comments, and
    stripping the comment out of ``name: 'Build #1'`` here would defeat it;
    callers wanting a plain value strip it themselves.

    A list item is not a key: ``- uses: actions/checkout@v4`` is a step, and
    reading it as a job's ``uses:`` would refuse nearly every real workflow.
    """
    match = re.match(
        r"^( *)(?!-(?:\s|$))(?:\"([^\"]*)\"|'([^']*)'|([^\s:#][^:]*?))\s*:(?:\s(.*)|)$",
        line,
    )
    if not match:
        return None
    indent, double, single, plain, value = match.groups()
    return len(indent), double or single or plain or "", value or ""


def _scalar(value: str) -> str | None:
    """
    What a job's ``name:`` denotes, or ``None`` when it denotes nothing and the
    job id stands as the context.

    Stripping comments unconditionally is wrong for four shapes, and wrong here
    is silent: a context that does not match what the job reports never arrives,
    and a rule waiting on a check that never arrives leaves the pull request
    pending rather than red.

    ``name: 'Build #1'``
        A quoted scalar. ``#`` opens a comment only outside quotes, so the value
        is read to its closing quote instead of truncated at the hash — which
        would derive ``Build`` and require a check nothing reports.

    ``name:`` followed only by a comment
        Denotes null, and GitHub falls back to the job id. ``None`` says so, and
        leaves the id the caller already recorded in place. A bare ``name:`` with
        nothing after it already behaved this way; this makes the two agree
        rather than deriving an empty context from one of them.

    ``name: >`` / ``name: |``
        A block scalar, whose value is on the lines below. Refused rather than
        folded: correct folding means chomping indicators, indent indicators and
        blank-line rules, in two copies of this parser that no test can prove
        right — only prove equal. A job name is a one-line label, so the shape is
        vanishingly rare and stopping costs nothing.

        The two indicators may appear in **either** order — ``|2-`` and ``|-2``
        are both valid headers — so both are matched. Matching only one order
        left ``|2-`` deriving the literal context ``|2-``, which is the silent
        failure this function exists to prevent rather than the loud one.

    ``name: null`` / ``~``
        YAML's null tokens, so the job id stands exactly as it does for a
        comment-only name. Quoted, they are ordinary strings and keep their
        value — which is why this is checked after the quoted branch, not before.

    Raised as ``ValueError`` rather than exiting, because this copy is a test and
    has no process to exit. The copy in the baseline's ``bootstrap-repo.py``
    catches it and exits; here it fails the suite, which is the same refusal.
    """
    value = value.strip()

    if re.match(r"[|>][-+]?\d*[-+]?(?:\s|$)", value):
        raise ValueError(
            f"`name:` is a block scalar ({value.split()[0]!r}), whose value is on "
            "the lines below it.\nThis parser reads one-line names only. Write "
            "the name inline."
        )

    if value.startswith("'"):
        # `''` is how YAML escapes a quote inside a single-quoted scalar, so the
        # closing quote is the first one that is not doubled.
        match = re.match(r"'((?:[^']|'')*)'", value)
        if not match:
            raise ValueError(
                f"`name:` opens with a quote it never closes: {value!r}.\n"
                "There is no value to read."
            )
        return match.group(1).replace("''", "'") or None

    if value.startswith('"'):
        match = re.match(r'"([^"\\]*)"', value)
        if not match:
            raise ValueError(
                f"`name:` is a double-quoted scalar this parser cannot read: "
                f"{value!r}.\nBackslash escapes and unterminated quotes are "
                "refused rather than guessed at."
            )
        return match.group(1) or None

    # Unquoted, so the null tokens resolve to null and the job id stands. `NULL`
    # and `Null` are null in YAML's core schema; `nUll` is not, and is a string.
    stripped = _strip_comment(value)
    return None if stripped in ("", *_NULL_TOKENS) else stripped


def _job_contexts(workflow: Path) -> dict[str, str]:
    """
    ``{job id: status-check context}`` for every job in a workflow.

    Keyed by id so a failure can name the job whose ``name:`` moved rather than
    only the string that disappeared.

    **Every level is placed by comparing indents, and none is assumed.** The
    first key-shaped line inside ``jobs:`` fixes the indent the job ids sit at,
    and the first key under a job fixes the indent that job's own keys sit at —
    so ``name:``, ``uses:`` and ``strategy:`` count where they sit at the
    latter, and a job id is a line that sits at the former. Step names are list
    items and are therefore excluded structurally, not by pattern — see
    ``test_step_names_are_not_collected``.

    Matching each of those keys at a fixed column instead was a fail-open with
    no symptom. Four-space-per-level indentation is ordinary YAML and GitHub
    runs it happily; here it matched nothing, and nothing matched reads as
    absent — so the job's ``strategy.matrix`` did not reach the refusal below
    and its ``name:`` did not reach the context, leaving the guard requiring a
    bare job id that GitHub never reports. A job whose own keys sit at two
    different indents is refused rather than half-read, which is the one shape
    comparing indents can get wrong.

    The job ids kept that fixed column one round longer, and failed the other
    way when it was wrong: a four-space ``jobs:`` block matched no job at all,
    which the guard reports as nothing collected rather than as a context it
    quietly got wrong. Loud, and still worth removing — the assumption was the
    same one, in the same loop, two lines up from where it had already been
    given up.

    **A job key is recognised by its shape before its id is read**, so a key this
    cannot vouch for stops the parse instead of being skipped. Skipping was two
    silent wrongs from one comment: ``pytest:  # the gate`` failed the
    end-of-line anchor, so the job vanished from the equality *and* the ``name:``
    beneath it was recorded against the job above — reporting a rename on a job
    nobody touched.

    **An id GitHub itself would reject stops the parse.** ``2fa:`` and ``-x:``
    are both refused, because GitHub requires the first character of a job id to
    be a letter or ``_``. This is the one shape here that can refuse a workflow
    which parsed before the rule was tightened, so it is stated rather than left
    to the message: what it costs is a workflow GitHub would not run either way.

    Whitespace before the colon is refused **on the whitespace**, not on the
    charset. ``build :`` is a legal YAML key and its id is ``build``; blaming the
    characters sends the reader to inspect an id whose characters are all fine.

    **A job whose context GitHub composes is refused**, for the same reason
    ``_scalar`` refuses a block scalar: it cannot be derived from this file, and
    deriving the wrong one is precisely the silent failure this guard exists to
    prevent. Two shapes compose:

    ``strategy.matrix``
        One check per combination, suffixed — ``pytest (3.11)``, not ``pytest``.
        The values may come from ``fromJSON`` and need not appear in the file at
        all, so there is no shape to read even in principle.

    ``uses:`` (a reusable workflow)
        The context is ``caller / called``, and the called job's name lives in
        the other file.

    ``strategy:`` written inline
        ``strategy: {matrix: [1, 2]}`` is the same matrix in a shape this parser
        does not read. Refused on the value alone rather than searched for a
        ``matrix:`` inside it — a flow mapping needs a real parser, and the only
        answer worth giving here is "stop".

        The null tokens are the exception, and the only one: ``strategy: null``
        has no block below it and so no ``matrix:`` that could exist, which
        makes it the one inline value provably safe to read past. Refusing it
        described a matrix that was not there.

    All three are refused rather than guessed at because they fail the same way
    when guessed wrong: a required context nothing reports, and a pull request
    that sits pending rather than red.
    """
    contexts: dict[str, str] = {}
    lines = workflow.read_text(encoding="utf-8").splitlines()

    # Found by shape, not by an exact line: `jobs:  # all of them` is still the
    # jobs block, and reading it as absent means parsing no jobs at all.
    start = None
    for index, line in enumerate(lines):
        key = _key(line)
        if key and key[0] == 0 and key[1] == "jobs":
            inline = _strip_comment(key[2])
            # A null is not a value to refuse: `jobs: null` is a workflow with no
            # jobs, which `test_jobs_are_collected` already reports as the empty
            # result it is. Refusing here blamed a flow mapping nobody wrote.
            if inline and inline not in _NULL_TOKENS:
                raise ValueError(
                    f"{workflow}: `jobs:` carries the value {inline!r}.\n"
                    "The jobs block is read by indentation, so a flow mapping is "
                    "refused rather than half-read."
                )
            start = index
            break
    if start is None:  # pragma: no cover - asserted directly below
        return contexts

    def _refuse(job: str, what: str, fix: str) -> None:
        raise ValueError(
            f"{workflow}, job {job!r}: {what}, so the check it reports is not "
            f"the job id or its `name:`.\nThis parser will not guess at a "
            f"composed context. {fix}"
        )

    current: str | None = None
    jobs_indent: int | None = None  # the indent the job ids themselves sit at
    body: int | None = None      # the indent `current`'s own keys sit at
    strategy: int | None = None  # the indent the keys inside its `strategy:` sit at
    in_strategy = False
    for line in lines[start + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        # A non-indented key ends the jobs block.
        if not line.startswith(" "):
            break
        # Matched on the key *shape* — an indent, then anything up to a colon —
        # rather than on the id charset, so an id this cannot read is refused
        # below instead of falling through to the `name:` branch with `current`
        # still naming the previous job.
        #
        # The indent is read off the first such line rather than fixed at two,
        # for the reason the keys below a job are: two is only the common
        # spelling. A `jobs:` block indented four per level is ordinary YAML that
        # GitHub runs, and against a fixed column it matched nothing — which
        # collects no jobs at all. That direction is loud rather than silent, so
        # it was a limitation and not the fail-open this parser was fixed for,
        # but it is the same assumption in the same file and it costs nothing to
        # stop making it. The first key-shaped line inside the block is the first
        # job: comments and blanks are already skipped above, and a mapping
        # cannot open with anything else — which is asserted below rather than
        # assumed, because reading the indent is what made it assailable.
        key = re.match(r"^( +)(?!-(?:\s|$))(\S[^:]*):(.*)$", line)
        if key and (jobs_indent is None or len(key.group(1)) == jobs_indent):
            jobs_indent = len(key.group(1))
            job_id, trailing = key.group(2), _strip_comment(key.group(3))
            if trailing:
                # A job id maps to a block, so anything left after the comment is
                # stripped is not a job at all.
                raise ValueError(
                    f"{workflow}: `{job_id}:` sits where a job id belongs but "
                    f"carries the value {trailing!r}.\nA job maps to a block. "
                    "This parser stops rather than attributing the keys below it "
                    "to the job above."
                )
            if job_id != job_id.rstrip():
                # `build :` is a legal YAML key whose id is `build`, and GitHub
                # runs it. The charset check below would refuse it too, but on
                # characters that are all permitted — sending the reader to
                # rename a job over a space. The refusal stands, because a job id
                # is the context string and this parser reads it literally rather
                # than deciding which spaces are insignificant, but it has to
                # name the space it stopped on.
                raise ValueError(
                    f"{workflow}: `{job_id}:` puts whitespace between the job id "
                    f"and its colon.\nYAML allows it and GitHub reads the id as "
                    f"{job_id.rstrip()!r}. This parser reads a job id literally, "
                    "so it stops here rather than guessing which spaces are "
                    "insignificant. Close the gap."
                )
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", job_id):
                raise ValueError(
                    f"{workflow}: {job_id!r} is not a job id this parser can "
                    "read.\nGitHub allows letters, digits, `-` and `_`, and "
                    "requires the first character to be a letter or `_`. "
                    "Anything else stops the parse rather than leaving the keys "
                    "below it attributed to the job above."
                )
            current, body, strategy, in_strategy = job_id, None, None, False
            contexts[current] = current  # the id is the context until a name says otherwise
            continue
        if current is None:
            # No job has been read yet, so this line sits where the first job id
            # belongs and is not one. A mapping cannot open with a sequence item
            # or a bare scalar, so this is not a `jobs:` block.
            #
            # Skipping it instead is a fail-open, and a subtle one: the line is
            # passed over, nothing is seeded, and the next key-shaped line seeds
            # the width instead — but that line is a key *inside* the item, one
            # level too deep. `jobs:` / `- build:` / `steps:` collects a job
            # called `steps` and requires a context no job reports, which leaves
            # every pull request pending rather than red.
            #
            # It only became reachable when the sequence-item lookahead was added
            # to the pattern above: before that a `- ` line matched as a key and
            # was refused on its id. Reading the indent rather than fixing it is
            # what makes the seed worth guarding — a fixed column refused this by
            # collecting nothing, loudly.
            #
            # Asked of `current` rather than of `jobs_indent`: the branch above
            # seeds both or raises, so it is the same question, and asking it
            # about the name the rest of the loop actually uses leaves no second
            # `current is None` test below it. That one was unreachable from the
            # moment this refusal was added, and unreachable code that reads as
            # defensive is worse than none.
            raise ValueError(
                f"{workflow}: `jobs:` opens with {line.strip()!r}, which is not "
                "a job id.\nA job id maps to a block; this is a sequence item or "
                "a scalar. This parser stops rather than reading the keys inside "
                "it as jobs."
            )
        key = _key(line)
        if key is None:
            # A step's `- uses:`, a block scalar's contents, a bare list item.
            # None of them is a key of this job.
            continue
        indent, name, value = key
        if body is None:
            # The first key under the job fixes the level its own keys sit at.
            # Read rather than assumed, because assuming four was the fail-open.
            body = indent
        if indent > body:
            # Nested: inside a step, a `with:`, or a `strategy:`.
            if in_strategy:
                if strategy is None:
                    strategy = indent
                if indent == strategy and name == "matrix":
                    _refuse(
                        current,
                        "is a matrix job, so GitHub reports one suffixed check "
                        f"per combination (`{current} (3.11)`) and none named "
                        f"`{current}`",
                        "Require each combination explicitly, or drop the matrix.",
                    )
            continue
        if indent < body:
            raise ValueError(
                f"{workflow}, job {current!r}: `{line.strip()}` sits at {indent} "
                f"spaces where this job's other keys sit at {body}.\n"
                "Keys are placed by comparing indents, so a job written at two "
                "levels at once stops the parse rather than having half of it "
                "read — the half that goes missing would be read as absent."
            )
        # A key of the job itself, so any `strategy:` block has ended.
        in_strategy, strategy = False, None
        if name == "uses":
            _refuse(
                current,
                "calls a reusable workflow, so GitHub reports it as "
                "`caller / called`",
                "Require that context explicitly, or inline the job.",
            )
        if name == "strategy":
            inline = _strip_comment(value)
            if inline and inline not in _NULL_TOKENS:
                raise ValueError(
                    f"{workflow}, job {current!r}: `strategy:` carries the inline "
                    f"value {inline!r}.\nA `matrix:` written inside it composes "
                    "the context just as a block one does, and this parser reads "
                    "neither out of a flow mapping. Write the strategy as a "
                    "block, or require each combination explicitly."
                )
            # A null strategy has no block below it and therefore no `matrix:` to
            # find, so there is nothing to enter. It is the one value here that
            # is provably harmless: refusing it named a matrix that cannot exist.
            in_strategy = not inline
            continue
        if name == "name":
            # A refusal propagates and fails the suite, naming the job. That is
            # the right outcome: a `name:` this cannot read is a context it
            # cannot check, and passing would mean checking nothing.
            try:
                scalar = _scalar(value)
            except ValueError as exc:
                raise ValueError(f"{workflow}, job {current!r}: {exc}") from None
            # None means the name denotes nothing, so the job id already recorded
            # above stands — which is what GitHub reports for such a job.
            if scalar is not None:
                contexts[current] = scalar
    return contexts


# The event-level keys that decide whether a workflow reports on a given push or
# pull request.
#
# `tags`/`tags-ignore` are absent deliberately: a tag filter does not change
# whether a *branch* is covered, which is the only question here.
#
# Longest-first is a habit here, not a requirement, and it was worth checking
# rather than asserting: the alternation is first-match-wins, so `branches` tried
# against `branches-ignore:` does match — and then the `:` that follows the group
# fails against the `-`, the engine backtracks into the alternation, and
# `branches-ignore` wins on the retry. Both orders classify all four keys
# identically. The order would begin to matter if the group were ever matched
# without that anchor after it, which is the only reason to leave it this way.
_FILTER_KEYS = ("branches-ignore", "branches", "paths-ignore", "paths")

# The top-level `on:` key. The key itself may be quoted: YAML 1.1 reads a bare
# `on` as the boolean true, and a repo bitten by that writes `"on":` instead.
_ON_KEY = re.compile(r"^[\"']?on[\"']?\s*:\s*(.*)$")

# The three patterns below are built from the indent the `on:` block actually
# uses, rather than from the two spaces it usually uses. All three carried that
# constant separately — the event patterns matched exactly two spaces and the
# filter pattern matched three or more, which is the same number written as
# "deeper than an event" — so a block written four-per-level defeated all three
# at once. That is ordinary YAML and GitHub runs it. Here its events matched
# nothing, and with nothing recognised as an event either the block came back
# empty and the guard reported a workflow that "declares no `on.pull_request`
# trigger at all", or its filter arrived with no event to attach to and was
# refused as orphaned under a commented-out event key that is not there. Both
# messages are false, and both send the reader to fix a file that is correct.
#
# Reading the indent instead is also what stops the two ends colliding: an event
# sits at the block's indent and a filter key below it, whatever that indent
# turns out to be.

# A block sequence item: a dash followed by whitespace or nothing. The space is
# the whole of what makes it a sequence — `-pull_request` is the plain scalar
# "-pull_request", not an item holding `pull_request`, and reading it as one
# turned a workflow GitHub never runs into a recognised trigger that passes.
# The indent is captured so the caller can require one consistent depth: a
# deeper `- ` does not open a nested sequence here, it continues the plain
# scalar above it.
_SEQUENCE_ITEM = re.compile(r"^( *)-(?:\s+(.*)|\s*)$")


def _block(lines: list[str]) -> list[str]:
    """
    The lines of an indented block, from just below its key to the next
    top-level one. Blank lines are dropped; comments are kept, because one of
    them may be a commented-out event key.

    **A comment does not end the block wherever it sits**, including at column
    zero. Ending it there would drop every line below the comment, and a filter
    dropped that way reads as absent — which is the direction that passes, on a
    workflow whose only fault is a note written flush left.

    **Nor does a sequence item at column zero**, which is not the edge case it
    looks like: YAML lets a block sequence sit at its key's own indent, and that
    is the spelling every serialiser emits. Treated as the next top-level key it
    ended the block immediately, leaving nothing to read and the guard reporting
    a workflow that declares no trigger — on the most ordinary way there is to
    write one.

    One reader for both passes below, so the pass that measures the block's
    indent and the pass that classifies its keys cannot disagree about where the
    block stops.
    """
    block: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        if (
            not line.startswith(" ")
            and not line.lstrip().startswith("#")
            and not _SEQUENCE_ITEM.match(line)
        ):
            break
        block.append(line)
    return block


def _event_indent(lines: list[str]) -> int | None:
    """
    The indent the events of a block ``on:`` sit at, given the block's lines, or
    ``None`` when the block holds no keys at all.

    The **smallest** indent any key under the block sits at, not the first one.
    They differ on exactly one shape and it is a shape this file already has an
    answer for: a filter orphaned under a commented-out event key, above the
    first live event. Taking the first key would fix the block's indent at that
    orphan's depth, leaving every real event below it looking mis-indented and
    unrecognised — a fail-open reached from a file whose only fault is a
    commented-out block at the top. The smallest keeps the orphan an orphan, and
    ``_trigger_branches`` already decides those deliberately.

    Read with ``_key``, so a block list item cannot fix the indent: ``- main``
    under a ``branches:`` is a value, not a key, and reading it as one would put
    the block's indent two levels too deep.
    """
    indents = [
        key[0]
        for line in lines
        if not line.lstrip().startswith("#") and (key := _key(line))
    ]
    return min(indents, default=None)


def _event_key(indent: int) -> re.Pattern[str]:
    """
    An event at the block's own indent. What follows the colon is captured
    rather than required to be empty — an event written inline is still an
    event, and saying so is the difference between "unreadable" and "absent".
    """
    return re.compile(rf"^{' ' * indent}[\"']?([A-Za-z_]+)[\"']?\s*:\s*(.*)$")


def _commented_event(indent: int) -> re.Pattern[str]:
    """
    A commented-out event key: at the event indent, and shaped like a key. What
    follows the colon is not constrained, because a disabled event is commonly
    disabled with a note saying why — ``# workflow_run:  # off for now``.
    Requiring nothing after the colon missed exactly that shape, and a missed one
    hands its orphaned keys to the live event above as if they were its filter.

    Over-matching is the safe direction here and under-matching is not, which is
    why this is loose. A comment this wrongly reads as an event key can only
    orphan the keys below it, and ``_trigger_branches`` refuses rather than
    guesses when an orphan is ambiguous. Still deliberately not every comment: a
    note with no colon leaves the event alone, and so does anything below the
    event indent.
    """
    return re.compile(rf"^{' ' * indent}#+\s*[\"']?[A-Za-z_]+[\"']?\s*:")


def _filter_key(indent: int) -> re.Pattern[str]:
    """
    A filter key anywhere below the event indent, matched by name.

    Deliberately tolerant about depth and quoting for the reason
    ``_trigger_branches`` sets out: a filter this fails to *see* reads as absent,
    and absent passes. The floor is the event indent plus one rather than a
    constant, which is the same rule the old ``\\s{3,}`` expressed when an event
    could only sit at two.
    """
    return re.compile(
        rf"^\s{{{indent + 1},}}[\"']?(" + "|".join(_FILTER_KEYS) + r")[\"']?\s*:\s*(.*)$"
    )


def _on_shorthand(value: str) -> dict[str, list[str] | None]:
    """
    ``{event: None}`` for the two shorthand forms of ``on:``.

    ``on: [push, pull_request]`` and ``on: push`` name their events and carry no
    filter — and here that is *provable* rather than lenient, which is why they
    resolve to ``None`` and pass rather than to ``[]`` and fail. Neither
    shorthand has anywhere to put a branch filter; the syntax that would express
    one is the block form.

    Anything else is refused. The shape that matters is the flow mapping,
    ``on: {pull_request: {branches: [main]}}``, which *can* carry a filter: it
    would have to be read out of nested braces by a parser that stops at
    indentation, and a filter this failed to see would read as absent and pass.
    """
    if re.fullmatch(r"\[(.*)\]", value):
        names = [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
    else:
        names = [value.strip("'\"")]

    if not names or any(not re.fullmatch(r"[A-Za-z_]+", name) for name in names):
        raise ValueError(
            f"`on:` is written as {value!r}, which this parser does not read.\n"
            "The shorthands `on: push` and `on: [push, pull_request]` are read, "
            "because neither can carry a branch filter. A flow mapping can, so "
            "it is refused rather than guessed at — write `on:` as a block."
        )
    return {name: None for name in names}


def _on_block(block: list[str]) -> dict[str, list[str] | None]:
    """
    The events of a block ``on:`` that holds no mapping keys at all.

    A block ``on:`` is usually a mapping of events, and every event pattern here
    is built for one. It does not have to be. Both shorthands ``_on_shorthand``
    reads beside the key can be written under it instead — a sequence of event
    names, or a single bare name:

    .. code-block:: yaml

        on:                  on:
          - push               pull_request
          - pull_request

    Both are ordinary YAML and GitHub runs both. Here they held no keys, so the
    block measured as empty and the result came back ``{}`` — which the assertion
    below reports as a workflow that "declares no ``on.pull_request`` trigger at
    all", on a file that declares it plainly. Fail-closed and still false, and it
    sends the reader to add a trigger already there: the same wrong message the
    shorthands beside the key used to produce, reached one step further in.

    ``None`` rather than ``[]``, and that is *provable* here for the reason it is
    in ``_on_shorthand``: neither shape has anywhere to put a branch filter. A
    sequence item that carried one would be a mapping, and a plain scalar cannot
    carry anything. So this leniency is not the fail-open direction — there is no
    filter to fail to see.

    ``{}`` is kept for the one shape it is true of: nothing under the key at all.
    ``on:`` with an empty block declares no events, and reporting the wanted one
    as missing is then accurate.

    A single line is handed to ``_on_shorthand`` rather than read here, because
    it is the same question one indent over: ``on:`` / ``[push, pull_request]``
    is the flow sequence that function already reads, and ``on:`` / ``{…}`` is
    the flow mapping it already refuses for being able to carry a filter. One
    reader for both spellings, so they cannot come to differ.

    Anything else is refused rather than read as empty. Two plain scalars on
    consecutive lines are one folded scalar and not two events; a block mixing
    sequence items with something else is not YAML this should guess at.
    Refusing is the loud direction, and the shape is rare enough to cost nothing.

    **The item shape is read strictly**, which is the one place leniency here
    would be the fail-open direction rather than a kindness. A dash binds as a
    sequence indicator only when whitespace follows it, so ``-pull_request`` is
    the plain scalar ``"-pull_request"`` — an event GitHub does not have, on a
    workflow it will not run. And items must share one indent: a deeper ``- ``
    continues the plain scalar above it instead of nesting, so ``- push`` with
    ``- pull_request`` below it at four spaces is the single scalar
    ``"push - pull_request"``. Read loosely, both resolved to real event names
    with no filter, which passes — a workflow whose checks never report,
    vouched for by the guard that exists to catch exactly that.
    """
    content = [line for line in block if not line.lstrip().startswith("#")]
    if not content:
        return {}

    items = [_SEQUENCE_ITEM.match(line) for line in content]
    if all(items):
        names = [_strip_comment(item.group(2) or "").strip("'\"") for item in items]
        if len({len(item.group(1)) for item in items}) > 1:
            names = []  # one folded scalar, not a sequence
    elif len(content) == 1:
        return _on_shorthand(_strip_comment(content[0]))
    else:
        names = []

    if not names or any(not re.fullmatch(r"[A-Za-z_]+", name) for name in names):
        raise ValueError(
            "`on:` opens a block this parser does not read:\n"
            + "\n".join(content)
            + "\nA block `on:` is a mapping of events, a sequence of event "
            "names, or one bare event name. Anything else is refused rather "
            "than read as declaring no events, because that reads as a missing "
            "trigger and the file may well declare the one it is asked for."
        )
    return {name: None for name in names}


def _trigger_branches(workflow: Path) -> dict[str, list[str] | None]:
    """
    ``{event: branch filter}`` from the workflow's ``on:`` block, where ``None``
    means the event declares no filter at all and therefore always reports.

    **``None`` and ``[]`` are different answers**, and collapsing them was a bug.
    An event with no filter covers the protected branch by covering everything;
    an event whose filter this cannot read covers nothing it can *prove*. Both
    used to parse as ``[]``, so a workflow running on every pull request — the
    shape a repo reaches for when its work targets more than one base branch —
    read here as one running on none, and the assertion below failed on a
    correctly configured repo. That direction is not the safe one it looks like:
    a guard that cries wolf on a good config is a guard someone deletes.

    Only the inline form (``branches: [main]``) is read. A block list parses as
    ``[]`` and fails the assertion rather than passing it — the wrong direction
    to be lenient in, given what the check is for. So does ``branches-ignore``,
    and so does any ``paths``/``paths-ignore``: a path-filtered workflow does not
    run on a pull request that touches nothing it lists, so its checks never
    report, which is the same pending-forever failure reached from one key over.

    **The recogniser below is the safety-critical part**, and deliberately
    tolerant about where the key sits and how it is written. It is what decides
    between "no filter" and "a filter I cannot read" — so a filter it fails to
    *see* reads as absent, and absent passes. Matching one exact indent was not
    enough: ``branches: [develop]`` at three or six spaces, under a quoted key,
    or with a space before the colon, all parsed as unfiltered and passed this
    assertion on a workflow that never reports on the protected branch at all.
    Those shapes are ordinary YAML, and before ``None`` existed they all failed
    closed, so narrow recognition turned five safe cases into silent ones.

    The residual risk is stated rather than papered over: a filter written in a
    shape this still cannot see — YAML's explicit-key form, say — reads as
    absent and passes. That is the cost of ``None`` existing at all. It is
    bounded by the filter names being a closed, stable set, and it is why they
    are matched by *name*, at any indent below the block's own, rather than by
    position.

    **That block indent is read rather than assumed.** Two spaces is what every
    hand-written workflow uses and it was written into three patterns at once —
    the event key, the commented-out event key, and the filter floor spelled
    "three or more", which is the same constant one level down. A block written
    four-per-level is ordinary YAML that GitHub runs, and it defeated all three
    together: nothing matched as an event, so the guard either reported a
    trigger the file plainly declares as absent, or refused that trigger's
    filter as orphaned under a commented-out event key the file does not
    contain. Two false messages, each sending the reader to fix a correct file.
    ``_event_indent`` measures the block instead, and the filter floor follows
    from it — which is also what stops the two recognisers overlapping now that
    neither sits at a fixed depth.

    **The shapes ``on:`` itself takes are read, not skipped.** ``on: push`` and
    ``on: [push, pull_request]`` resolve to their events with no filter;
    ``on: {…}`` is refused; ``"on":`` is the same key as ``on:``. All of them
    used to fall through a search for a bare ``on:`` line, leaving it empty and
    the guard reporting that the workflow "declares no ``on.pull_request``
    trigger at all" — a true-sounding message that sends the reader to add a
    trigger the file already has. An event written inline
    (``pull_request: {branches: [main]}``) is registered and marked unproven for
    the same reason: absent and unreadable are different answers, and the message
    a person reads has to say which one this is.

    **A block ``on:`` does not have to be a mapping**, and the two shapes that
    are not one reached that same false message from one step further in. Both
    shorthands can be written *under* the key rather than beside it — a sequence
    of event names, or one bare name — and a block holding no mapping keys was
    read as a block holding no events. ``_on_block`` reads them, and ``{}`` is
    kept for the one shape it is true of: nothing under the key at all.

    **A commented-out event key ends the event it commented out.** Anything left
    indented under it is orphaned, and reading those keys as the *previous*
    event's filter is how a workflow filtered to ``develop`` reported ``main``
    and passed — the misattribution is silent when the commented event is not one
    of the two asserted below.

    Which comments count and what happens to the keys they orphan are two
    separate decisions, and collapsing them into one is what made this look like
    a choice between two silent failures. Recognition is deliberately *loose*
    (see ``_commented_event``): a commented event key carrying a trailing note is
    still a commented event key, and missing one is the direction that fails
    open. Disposition is what keeps that safe.

    **An orphaned filter key is dropped only when it provably is not the
    previous event's**, which is narrower than it first looks. An event carries
    one of *each* filter key, so an orphan repeating a key that event has
    already been read for cannot also be its own, and the event keeps the
    specific answer that makes the assertion below say something true. An event
    already unproven is the other case: ``[]`` is the most conservative answer
    there is and no orphan can move it.

    "The event has *an* answer" is the tempting version of that test and it is
    wrong, because the keys are not interchangeable. An event is filtered by
    ``branches:`` and ``paths:`` at once, so a ``paths:`` orphaned under a note
    is the live event's real filter, and dropping it reads a path-filtered
    workflow as unfiltered — the fail-open this whole function exists to avoid.

    Otherwise the orphan is genuinely ambiguous: it is either the live event's
    filter with a note wrongly read as a key above it, or the commented event's.
    Nothing in the text distinguishes them. So this refuses, the way ``_scalar``
    refuses a block scalar — the two readings fail in opposite directions, one
    reporting a filter the event may not have and one reading as no filter at
    all, and *that* one passes.

    The cost is stated rather than papered over, because it is the cry-wolf
    direction this docstring argues against everywhere else: a correct workflow
    whose only fault is a note at the event indent, shaped like a key, sitting
    between an event and its first filter will stop the suite. It is narrow — a
    note one space further in is untouched, which is where notes about a filter
    normally sit — and the message names the fix. It is a refusal rather than a
    failed assertion for that reason: it says what cannot be read, instead of
    claiming the filter is missing.
    """
    filters: dict[str, list[str] | None] = {}
    unproven: set[str] = set()
    # Which filter keys each event has actually been read for. An orphan can be
    # dropped only against this, not against whether the event has an answer.
    seen: dict[str, set[str]] = {}
    lines = workflow.read_text(encoding="utf-8").splitlines()

    header = start = None
    for index, line in enumerate(lines):
        if not line.startswith((" ", "\t")):
            header = _ON_KEY.match(line)
            if header:
                start = index
                break
    if header is None:  # pragma: no cover - asserted directly below
        return filters

    shorthand = _strip_comment(header.group(1))
    if shorthand:
        # A refusal propagates and fails the suite, as it does for a `name:`
        # this cannot read: an `on:` block this cannot read is a filter it cannot
        # check, and passing would mean vouching for one it never saw.
        try:
            return _on_shorthand(shorthand)
        except ValueError as exc:
            raise ValueError(f"{workflow}: {exc}") from None

    block = _block(lines[start + 1:])
    indent = _event_indent(block)
    if indent is None:
        # No mapping keys under the block. That is not the same as no events:
        # `on:` written as a sequence, or as one bare name, has none either and
        # declares them plainly. Returning `{}` for all three reported a trigger
        # the file carries as absent — see `_on_block`, which keeps `{}` for the
        # empty block it is true of and refuses what it cannot classify.
        try:
            return _on_block(block)
        except ValueError as exc:
            raise ValueError(f"{workflow}: {exc}") from None
    event_key = _event_key(indent)
    commented_event = _commented_event(indent)
    key_pattern = _filter_key(indent)

    current: str | None = None
    # The event `current` held when a comment ended it. Kept rather than
    # discarded because whether its filter had already been read is what decides
    # if the keys below the comment are ambiguous or merely orphaned.
    suspended: str | None = None
    for line in block:
        if line.lstrip().startswith("#"):
            # Forgetting the current event is the whole of the fix: the keys
            # below a commented-out one belong to nothing, and attributing them
            # to the event above is worse than dropping them, because the event
            # above is real and its filter is then reported as something it is
            # not.
            if commented_event.match(line):
                # Only the first of a run of these carries the live event.
                # Overwriting on the second — a commented event whose own
                # sub-keys are commented out under it, which is how a block is
                # normally disabled — would lose it and refuse a shape the rule
                # below can settle.
                if current is not None:
                    suspended = current
                current = None
            continue
        event = event_key.match(line)
        if event:
            current = event.group(1)
            suspended = None
            filters.setdefault(current, None)  # no filter seen yet
            # `pull_request:` and `pull_request: null` are the same event with
            # no filters — the null tokens as `_scalar` reads them. Anything
            # else after the colon is a value this does not parse, most likely a
            # flow mapping, and an event whose filter cannot be read is unproven
            # rather than unfiltered.
            inline_value = _strip_comment(event.group(2))
            if inline_value and inline_value not in _NULL_TOKENS:
                unproven.add(current)
            continue
        key = key_pattern.match(line)
        if not key:
            continue
        name, value = key.group(1), _strip_comment(key.group(2))
        if current is None:
            # Orphaned: a filter key under a comment this read as an event key.
            # Dropping it is safe in exactly two cases, and both are provable
            # rather than likely.
            #
            # It repeats a key the event above has already been read for. An
            # event carries one of each filter key, so a second one cannot also
            # be that event's, and the event keeps the specific answer it had.
            #
            # Or that event is already unproven, which is `[]`, the most
            # conservative answer there is. No orphan can make it worse.
            #
            # Testing whether the event merely has *an* answer is not enough and
            # was the bug: an event carries `branches:` and `paths:` at once, so
            # a `paths:` orphaned under a note is that event's real filter, and
            # dropping it leaves a path-filtered workflow reading as unfiltered
            # — which passes, on a workflow whose checks may never report.
            if suspended is not None and (
                name in seen.get(suspended, ()) or suspended in unproven
            ):
                continue
            raise ValueError(
                f"{workflow}: `{line.strip()}` is indented under a commented-out "
                "event key, and this parser cannot tell whether it filters that "
                "event or the live one above it.\n"
                "Refused rather than guessed at: read as the live event's, it "
                "reports a filter that event may not have; dropped, it reads as "
                "no filter at all — and no filter passes this check. Move the "
                "note off the event indent, or delete the commented-out block."
            )
        seen.setdefault(current, set()).add(name)
        inline = re.fullmatch(r"\[(.*)\]", value)
        if name == "branches" and inline:
            filters[current] = [
                item.strip().strip("'\"")
                for item in inline.group(1).split(",")
                if item.strip()
            ]
            continue
        # `branches-ignore`, a path filter, or a `branches:` whose value is on
        # the lines below. None of them can be shown to leave the protected
        # branch covered, so the event is unproven rather than unfiltered.
        unproven.add(current)

    # `[]` wins over any list read for the same event, whatever the line order.
    # An event carrying both `branches: [main]` and `paths-ignore:` is filtered
    # by both, and the half this can read must not vouch for the half it cannot.
    return {event: ([] if event in unproven else v) for event, v in filters.items()}


# ── Reading the ruleset ───────────────────────────────────────────────────────

def _load_ruleset() -> dict:
    return json.loads(_RULESET.read_text(encoding="utf-8"))


def _rule(ruleset: dict, rule_type: str) -> dict | None:
    return next((r for r in ruleset.get("rules", []) if r.get("type") == rule_type), None)


def _required_contexts(ruleset: dict) -> set[str]:
    rule = _rule(ruleset, "required_status_checks") or {}
    checks = rule.get("parameters", {}).get("required_status_checks", [])
    return {check["context"] for check in checks}


# ── The guard ─────────────────────────────────────────────────────────────────

class TestEveryRequiredCheckExists:
    """A required context naming a job that is not there: the rename, the typo."""

    def test_every_required_context_is_a_job(self):
        contexts = set(_job_contexts(_WORKFLOW).values())
        missing = sorted(_required_contexts(_load_ruleset()) - contexts)
        assert not missing, (
            "Required status checks that no job in the workflow reports:\n"
            + "\n".join(f"  {name}" for name in missing)
            + f"\n\nJobs currently report: {sorted(contexts)}\n\n"
            "A required check that never arrives leaves the pull request "
            "pending, not red — the rule reads as protection while applying to "
            "nothing.\n\n"
            f"This reads {_WORKFLOW.name} and nothing else, so there are two "
            "ways to get here. If the context names a job that used to be in "
            "that file, it was renamed: rename the context in the ruleset to "
            "match and re-apply it — the committed file is not what GitHub "
            "enforces until it is written through the API. If it names a job in "
            "another workflow, nothing is renamed and the ruleset may be right; "
            f"point _WORKFLOW at that file, or require only checks {_WORKFLOW.name} "
            "reports. Requiring a check from a workflow this does not read means "
            "no test can tell you when that one is renamed."
        )


class TestEveryJobIsRequired:
    """A seam that exists as a job but gates nothing."""

    def test_the_rule_exists_at_all(self):
        # Checked separately so its absence reports as itself rather than as
        # "every job is unrequired". The baseline tool omits this rule entirely
        # rather than writing it empty — an empty rule looks like protection in
        # the settings UI and in a diff, and gates nothing — so an absent rule
        # means the ruleset was applied before CI existed and never revisited.
        assert _rule(_load_ruleset(), "required_status_checks"), (
            "The ruleset has no required_status_checks rule, so no check gates a "
            "merge.\nRe-run the baseline bootstrap with --checks-from pointing "
            "at the workflow."
        )

    def test_every_job_is_a_required_context(self):
        contexts = _job_contexts(_WORKFLOW)
        required = _required_contexts(_load_ruleset())
        unrequired = sorted(
            f"{name}  (job: {job})"
            for job, name in contexts.items()
            if name not in required
        )
        assert not unrequired, (
            "Jobs in the workflow that no ruleset context requires:\n"
            + "\n".join(f"  {entry}" for entry in unrequired)
            + "\n\nThe job runs and can go red without blocking a merge. Add the "
            "context to the ruleset and re-apply, or, if the job is deliberately "
            "advisory, say so here and in the ruleset rather than leaving it to "
            "omission."
        )


class TestChecksCanActuallyReport:
    """
    A required check that never *runs* is the same failure as one that was
    renamed, reached from the other side.

    Both triggers are branch filters, so both can be edited. Point either
    somewhere else and the required contexts stop arriving on pull requests to
    the protected branch: the rule waits on a check that will never report, and
    the pull request sits pending forever rather than failing.

    The push trigger matters less but is not decorative — it is what gives each
    commit on the baseline its own verdict, and what puts a passing run on the
    protected branch for a branch to be compared against.
    """

    def test_ci_runs_on_the_protected_branch(self):
        triggers = _trigger_branches(_WORKFLOW)
        for event in ("pull_request", "push"):
            # Membership first, and separately. `None` means "declared, with no
            # branch filter" — every branch, which covers the protected one. A
            # missing event is the opposite and would read as exactly the same
            # `None` out of `.get`, passing the assertion that follows.
            assert event in triggers, (
                f"The workflow declares no `on.{event}` trigger at all.\n"
                f"Nothing would report on {event} to {_PROTECTED_BRANCH!r}, so a "
                "required check waiting on it never arrives — and a pull request "
                "waiting on a check that never arrives sits pending rather than "
                "red."
            )
            covered = triggers[event]
            assert covered is None or _PROTECTED_BRANCH in covered, (
                f"The workflow's `on.{event}.branches` is {covered!r} and does "
                f"not include {_PROTECTED_BRANCH!r}.\n"
                "The required checks would stop reporting on pull requests to "
                f"{_PROTECTED_BRANCH}, and a required check that never arrives "
                "leaves the pull request pending rather than red — "
                "indistinguishable from one still running. Either restore the "
                "branch here or narrow the ruleset to a branch CI covers.\n\n"
                "An empty list also means this parser could not read the filter "
                "— a block list or a `branches-ignore`. If that is the shape "
                "here, the filter is unproven rather than wrong; write it inline "
                "or assert the coverage some other way."
            )


class TestRulesetKeepsItsBaseline:
    """
    The ruleset file is re-read from GitHub after any UI edit, so a decision can
    be dropped by a click and arrive here as a diff nobody reads closely. These
    are the baseline decisions worth failing on. See rulesets/README.md in the
    baseline repo for the argument behind each.
    """

    def test_it_is_actively_enforced(self):
        # `evaluate` is GitHub's dry-run mode and `disabled` is off. Both leave
        # every rule below intact in the file and in the settings UI, and neither
        # blocks anything — so without this, flipping one word unprotects the
        # branch while the whole of this suite stays green.
        enforcement = _load_ruleset().get("enforcement")
        assert enforcement == "active", (
            f"The ruleset's enforcement is {enforcement!r}, not 'active'.\n"
            "`evaluate` reports what it would have done and blocks nothing; "
            "`disabled` does not even do that. The rules below are all still "
            "present in either case, which is what makes this worth asserting "
            "separately."
        )

    def test_it_targets_branches(self):
        # A ruleset retargeted to `tag` keeps its name, its rules, and its
        # ref_name condition — `refs/heads/main` simply matches no tag, so it
        # applies to nothing while reading as fully configured.
        target = _load_ruleset().get("target")
        assert target == "branch", (
            f"The ruleset's target is {target!r}, not 'branch'.\n"
            "Its conditions still name a branch, so it now matches nothing at "
            "all — the rules are intact and enforce against an empty set."
        )

    def test_it_targets_the_protected_branch(self):
        include = _load_ruleset().get("conditions", {}).get("ref_name", {}).get("include", [])
        expected = [f"refs/heads/{_PROTECTED_BRANCH}"]
        assert include == expected, (
            f"The ruleset targets {include}, not {expected}.\n"
            "This is exact equality, so widening it fails here too — deliberately. "
            "Widening is a real decision and should cost a line in this test "
            "rather than passing silently; narrowing or moving it leaves the "
            "branch everything ships from unprotected."
        )

    def test_the_protected_branch_is_not_excluded(self):
        # `exclude` wins over `include`, and nothing above reads it. A ruleset
        # that includes refs/heads/main and excludes it too keeps its name, its
        # target, its enforcement and every rule — and covers nothing. Same shape
        # as a ruleset retargeted to tags, one key over, and it was invisible
        # here until it was asserted.
        #
        # Patterns are matched with fnmatch, which is close to GitHub's ref
        # syntax but not identical; an exotic pattern that covers the branch
        # without matching here would still slip past.
        ref = f"refs/heads/{_PROTECTED_BRANCH}"
        exclude = _load_ruleset().get("conditions", {}).get("ref_name", {}).get("exclude", [])
        covering = [pattern for pattern in exclude if fnmatch(ref, pattern)]
        assert not covering, (
            f"The ruleset excludes {ref} via {covering}.\n"
            "An exclude overrides the include, so the rules apply to nothing "
            "while the ruleset still reads as active and correctly targeted in "
            "the settings UI and in this file."
        )

    def test_branches_must_be_up_to_date(self):
        rule = _rule(_load_ruleset(), "required_status_checks") or {}
        assert rule.get("parameters", {}).get("strict_required_status_checks_policy") is True, (
            "strict_required_status_checks_policy is not true.\n"
            "Without it a pull request merges on checks that ran against an "
            "older base — so a job added to CI while a branch was already open "
            "does not run on that branch, including when that branch is the one "
            "the job was added to protect."
        )

    def test_force_push_and_deletion_are_blocked(self):
        ruleset = _load_ruleset()
        for rule_type in ("non_fast_forward", "deletion"):
            assert _rule(ruleset, rule_type), (
                f"The {rule_type} rule is absent.\n"
                "This does not affect pull request branches, which are rebased "
                "and force-pushed routinely — the ruleset applies to the "
                "protected branch alone. Loosening it is not the fix for "
                "friction on a feature branch."
            )

    def test_a_pull_request_is_required(self):
        assert _rule(_load_ruleset(), "pull_request"), (
            "The pull_request rule is absent, so a direct push to the protected "
            "branch is accepted."
        )


# ── Per-repo pins go in a sibling file ────────────────────────────────────────
#
# Everything above is baseline and survives a copy unchanged. What does not
# belong here is anything about *this* repo's deployment or team, because it
# would be edited on every copy and would rot into an assertion nobody trusts.
# Put those in tests/test_ruleset_pins.py instead. Two worth writing:
#
#   * The approval count. Zero is right for a solo repo — GitHub does not let an
#     author approve their own pull request, so any positive count means no human
#     pull request can ever merge. It is wrong the moment there are two people,
#     and nothing above will tell you it drifted.
#
#   * The coupling between the protected branch and whatever triggers a deploy.
#     If deployment keys off a branch pattern configured outside this repo, find
#     the file that creates it and assert the two name the same branch. Written
#     so a single grep can verify it — one assignment, interpolated everywhere —
#     the check is a few lines. A deploy branch without the ruleset is an
#     unprotected production branch, and every test above would still be green.
#
#   * The bypass actor, if one is granted. Assert the count, the role, and the
#     mode. `pull_request` and `always` look near-identical in a diff and are
#     very different grants.


# ── The guard's own seams ─────────────────────────────────────────────────────
#
# Every assertion above passes vacuously if the parser returns nothing, and the
# equality passes if it returns nothing on both sides. These make that
# impossible.

class TestGuardIsNotVacuous:

    def test_both_files_exist(self):
        assert _WORKFLOW.is_file(), f"{_WORKFLOW} is missing"
        assert _RULESET.is_file(), f"{_RULESET} is missing"

    def test_jobs_are_collected(self):
        # A floor, not an inventory — the equality above is what keeps the set
        # exact. Raise it to the number of seams this repo checks.
        assert len(_job_contexts(_WORKFLOW)) >= 1, "no jobs parsed out of the workflow"

    def test_required_contexts_are_collected(self):
        assert len(_required_contexts(_load_ruleset())) >= 1

    def test_step_names_are_not_collected(self):
        # If the parser ever matched `- name:`, the equality above would fail on
        # it only by luck of the strings differing. Assert the structural
        # exclusion directly.
        collected = set(_job_contexts(_WORKFLOW).values())
        assert "Install dependencies" not in collected
        # The workflow's own top-level `name:` sits at indent 0 and is not a job.
        assert "CI" not in collected

    def test_parser_handles_the_shapes_yaml_allows(self, tmp_path):
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "name: CI\n"
            "on:\n"
            "  pull_request:\n"
            "    branches: [main]\n"
            "jobs:\n"
            "  named:\n"
            "    name: Backend tests (pytest)\n"
            "    steps:\n"
            "      - name: Install dependencies\n"
            "        run: pip install -r requirements.txt\n"
            "  quoted:\n"
            '    name: "Frontend typecheck + build"\n'
            "  commented:\n"
            "    name: UI tests (Playwright)  # the slow one\n"
            "  unnamed:\n"
            "    runs-on: ubuntu-latest\n"
            "\n"
            "permissions:\n"
            "  contents: read\n",
            encoding="utf-8",
        )
        assert _job_contexts(workflow) == {
            "named": "Backend tests (pytest)",
            "quoted": "Frontend typecheck + build",
            "commented": "UI tests (Playwright)",
            # No `name:`, so GitHub reports the job id — and so does this.
            "unnamed": "unnamed",
        }

    def test_a_job_key_with_a_trailing_comment_is_still_a_job(self, tmp_path):
        # The end-of-line anchor this used to carry dropped the job from the
        # equality and left `current` naming the job above, so the `name:` below
        # was recorded against *that* job — a rename reported on a job nobody
        # touched, from one comment on one line.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "jobs:\n"
            "  first:\n"
            "    name: The first job\n"
            "  gate:  # the one that blocks a merge\n"
            "    name: The gate\n",
            encoding="utf-8",
        )
        assert _job_contexts(workflow) == {
            "first": "The first job",
            "gate": "The gate",
        }

    def test_a_job_key_this_parser_cannot_read_stops_it(self, tmp_path):
        # Fails closed rather than skipping. A skipped key leaves `current`
        # pointed at the previous job, so the next `name:` silently overwrites a
        # context that was correct — worse than not parsing at all.
        for key in ('  "quoted id":', "  has spaces:", "  value: here"):
            workflow = tmp_path / "w.yml"
            workflow.write_text(
                f"jobs:\n  first:\n    name: The first job\n{key}\n"
                "    name: Not the first job\n",
                encoding="utf-8",
            )
            with pytest.raises(ValueError):
                _job_contexts(workflow)

    def test_a_matrix_job_is_refused(self, tmp_path):
        # GitHub reports `pytest (3.11)`, never `pytest`. Deriving `pytest` gives
        # a required context nothing reports, which leaves the pull request
        # pending rather than red — silent, and the reason this stops instead.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "jobs:\n"
            "  pytest:\n"
            "    strategy:\n"
            "      matrix:\n"
            "        python-version: ['3.11', '3.12']\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="matrix job"):
            _job_contexts(workflow)

    def test_a_strategy_without_a_matrix_is_not_refused(self, tmp_path):
        # `fail-fast` on its own does not compose the context, so refusing here
        # would fail a correct workflow — the direction that gets a guard deleted.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "jobs:\n"
            "  pytest:\n"
            "    strategy:\n"
            "      fail-fast: false\n"
            "    name: The gate\n",
            encoding="utf-8",
        )
        assert _job_contexts(workflow) == {"pytest": "The gate"}

    def test_a_matrix_is_refused_wherever_the_job_puts_it(self, tmp_path):
        # The refusal above used to key on `strategy:` at exactly four spaces and
        # `matrix:` at exactly six, unquoted. Both shapes below are ordinary YAML
        # that GitHub runs, and both slipped past into the silent failure: a
        # required context of `pytest`, which a matrix job never reports.
        shapes = {
            "four-per-level": (
                "jobs:\n"
                "  pytest:\n"
                "      strategy:\n"
                "          matrix:\n"
                "            python-version: ['3.11', '3.12']\n"
            ),
            "quoted keys": (
                "jobs:\n"
                "  pytest:\n"
                '    "strategy":\n'
                "      'matrix':\n"
                "        python-version: ['3.11']\n"
            ),
            "space before the colon": (
                "jobs:\n  pytest:\n    strategy :\n      matrix :\n        v: [1]\n"
            ),
        }
        for label, text in shapes.items():
            workflow = tmp_path / f"{label.replace(' ', '-')}.yml"
            workflow.write_text(text, encoding="utf-8")
            with pytest.raises(ValueError, match="matrix job"):
                _job_contexts(workflow)

    def test_a_strategy_written_inline_is_refused(self, tmp_path):
        # The one shape where the matrix is real but there is no `matrix:` line
        # to find. Reading the flow mapping needs a parser this is deliberately
        # not, so the value alone is enough to stop on.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "jobs:\n"
            "  pytest:\n"
            "    strategy: {matrix: {python-version: ['3.11', '3.12']}}\n"
            "    name: The gate\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="inline value"):
            _job_contexts(workflow)

    @pytest.mark.parametrize("null", ["null", "~", "Null", "NULL", "null  # none"])
    def test_a_null_strategy_is_not_an_inline_one(self, tmp_path, null):
        # The refusal above reads any value after `strategy:` as a flow mapping
        # with a matrix hidden in it. A null has no block below it, so there is
        # no matrix it could be hiding — and stopping the parse to announce one
        # is the guard failing a workflow GitHub runs without complaint.
        #
        # The same tokens `_scalar` treats as null, because a workflow that
        # writes one of them here means by it exactly what it means there.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            f"jobs:\n"
            f"  pytest:\n"
            f"    strategy: {null}\n"
            f"    name: The gate\n",
            encoding="utf-8",
        )
        assert _job_contexts(workflow) == {"pytest": "The gate"}

    def test_a_quoted_null_strategy_is_still_refused(self, tmp_path):
        # The bound on the case above. Quoted, `null` is an ordinary string and
        # not a null at all, so the value is a value and this stops — the same
        # asymmetry `_scalar` keeps between `name: null` and `name: 'null'`.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "jobs:\n"
            "  pytest:\n"
            "    strategy: 'null'\n"
            "    name: The gate\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="inline value"):
            _job_contexts(workflow)

    @pytest.mark.parametrize("width", [1, 2, 3, 4, 8])
    def test_the_jobs_block_may_be_indented_any_width(self, tmp_path, width):
        # Two spaces is the common spelling of a `jobs:` block, not the required
        # one. Pinned to two, everything but the middle case here collected no
        # jobs — the guard then has nothing to compare the ruleset against, and
        # says so, which is why this was a limitation rather than the fail-open
        # the keys below a job had. The refusals still have to be reachable at
        # whatever width the file uses, which is what the next two cases check.
        pad = " " * width
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            f"jobs:\n"
            f"{pad}pytest:\n"
            f"{pad * 2}name: The gate\n"
            f"{pad * 2}steps:\n"
            f"{pad * 3}- uses: actions/checkout@v4\n"
            f"{pad}lint:\n"
            f"{pad * 2}runs-on: ubuntu-latest\n",
            encoding="utf-8",
        )
        assert _job_contexts(workflow) == {"pytest": "The gate", "lint": "lint"}

    def test_a_matrix_is_refused_at_a_width_the_parser_was_never_pinned_to(self, tmp_path):
        # The reason the case above is not enough on its own. Reading the ids at
        # the wrong indent does not misread a job, it drops it — and a dropped
        # job reaches none of the refusals, so a matrix at four-per-level would
        # have gone unmentioned rather than wrong.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "jobs:\n"
            "    pytest:\n"
            "        strategy:\n"
            "            matrix:\n"
            "                python-version: ['3.11', '3.12']\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="matrix job"):
            _job_contexts(workflow)

    def test_a_job_id_at_the_wrong_indent_does_not_start_a_job(self, tmp_path):
        # The bound. The first job fixes the width, so a second id written at a
        # different one does not quietly become a job: it falls through to the
        # keys of the job above, sits at an indent that job's own keys do not
        # use, and is refused there. Reading it as a job instead would mean
        # taking any indent as a job id, which is the fixed column's opposite
        # error rather than its absence.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "jobs:\n"
            "  first:\n"
            "    name: A\n"
            "   second:\n"
            "     name: B\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="sits at 3 spaces"):
            _job_contexts(workflow)

    def test_a_null_jobs_block_has_no_jobs_rather_than_an_unreadable_one(self, tmp_path):
        # `jobs: null` is a workflow with no jobs. That is worth reporting, and
        # `test_jobs_are_collected` reports it — as nothing collected, which is
        # what it is. What this must not do is refuse it as a flow mapping,
        # sending the reader to look for braces that were never written.
        workflow = tmp_path / "w.yml"
        workflow.write_text("jobs: ~\n", encoding="utf-8")
        assert _job_contexts(workflow) == {}

    def test_a_step_key_called_matrix_is_not_a_matrix_job(self, tmp_path):
        # The cost of recognising `matrix:` more loosely, and the bound on it.
        # A key by that name outside a `strategy:` block composes nothing, and
        # refusing here would fail a correct workflow — the direction that gets
        # a guard deleted rather than fixed.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "jobs:\n"
            "  build:\n"
            "    name: The gate\n"
            "    steps:\n"
            "      - uses: ./.github/actions/setup\n"
            "        with:\n"
            "          matrix: not-a-strategy\n",
            encoding="utf-8",
        )
        assert _job_contexts(workflow) == {"build": "The gate"}

    def test_a_name_is_read_wherever_the_job_puts_it(self, tmp_path):
        # The same defect as the matrix one, one key over and quieter: a `name:`
        # this failed to see left the *job id* standing as the context. That is a
        # plausible-looking answer, so nothing downstream could tell it was
        # derived from a line the parser never read.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "jobs:\n  gate:\n      name: The gate\n      runs-on: ubuntu-latest\n",
            encoding="utf-8",
        )
        assert _job_contexts(workflow) == {"gate": "The gate"}

    def test_a_job_written_at_two_indents_is_refused(self, tmp_path):
        # What comparing indents can get wrong. The keys of one mapping share an
        # indent, so this is not a workflow GitHub would run either — but the
        # parser must stop rather than read `name:` as the job's and `runs-on:`
        # as something else, or the other way round.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "jobs:\n  gate:\n      name: The gate\n    runs-on: ubuntu-latest\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="two levels at once"):
            _job_contexts(workflow)

    def test_the_jobs_block_is_found_by_shape(self, tmp_path):
        # `jobs:  # all of them` failed an exact-line comparison, and a jobs
        # block that is not found is a workflow with no jobs — vacuous rather
        # than wrong, but it takes the whole equality down with it.
        for header in ("jobs:  # all of them", '"jobs":', "jobs :"):
            workflow = tmp_path / "w.yml"
            workflow.write_text(
                f"{header}\n  build:\n    name: The gate\n", encoding="utf-8"
            )
            assert _job_contexts(workflow) == {"build": "The gate"}, header
        # Inline, it is a flow mapping this does not read. Refused rather than
        # reported as a workflow with no jobs at all.
        workflow = tmp_path / "inline.yml"
        workflow.write_text("jobs: {build: {name: The gate}}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="flow mapping"):
            _job_contexts(workflow)

    def test_a_reusable_workflow_job_is_refused(self, tmp_path):
        # The context is `caller / called` and the called name is in the other
        # file, so there is nothing here to read even in principle.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "jobs:\n  build:\n    uses: ./.github/workflows/shared.yml\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="reusable workflow"):
            _job_contexts(workflow)

    def test_a_step_that_uses_an_action_is_not_a_reusable_workflow(self, tmp_path):
        # `- uses:` at six spaces is a step. Matching it would refuse nearly every
        # real workflow, so the distinction is structural and asserted directly.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "jobs:\n"
            "  build:\n"
            "    name: The gate\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n",
            encoding="utf-8",
        )
        assert _job_contexts(workflow) == {"build": "The gate"}

    def test_a_hash_inside_quotes_is_not_a_comment(self):
        # `#` opens a comment only outside quotes. Truncating here would derive
        # `Build` for a job reporting `Build #1`, and a required check nothing
        # reports leaves the pull request pending rather than red.
        assert _scalar("'Build #1'") == "Build #1"
        assert _scalar('"Build #1"') == "Build #1"
        assert _scalar("'Bob''s job'") == "Bob's job"

    def test_a_name_denoting_nothing_falls_back_to_the_job_id(self):
        # `name:` followed only by a comment, and YAML's null tokens, all resolve
        # to null — GitHub reports the job id, so None leaves it standing.
        for value in ("# just a comment", "~", "null", "Null", "NULL"):
            assert _scalar(value) is None, value
        # Quoted, they are ordinary strings.
        assert _scalar("'null'") == "null"

    def test_shapes_this_parser_will_not_guess_at_are_refused(self):
        # Loud beats silently wrong: a `name:` this cannot read is a context it
        # cannot check. Both indicator orders count — `|2-` derived the literal
        # context `|2-` before it did.
        for value in ("|", ">-", "|2", "|2-", ">2-", "|3+", "'unterminated"):
            with pytest.raises(ValueError):
                _scalar(value)
        # And the refusal keys on the header shape, not on the character.
        assert _scalar("Backend > frontend") == "Backend > frontend"

    def test_trigger_events_are_collected(self):
        # Every assertion about the filters passes vacuously if the parser never
        # finds the `on:` block, so pin that it finds both events here. The
        # filter *values* are asserted by test_ci_runs_on_the_protected_branch —
        # deliberately not pinned to a shape, because both shapes are correct and
        # which one a repo uses is not this file's business.
        assert {"pull_request", "push"} <= set(_trigger_branches(_WORKFLOW))

    def test_an_absent_filter_is_not_an_empty_one(self, tmp_path):
        # The distinction the assertion above rests on. `pull_request:` with no
        # `branches:` runs on every branch, which covers the protected one; that
        # is the shape used by a repo whose pull requests target more than one
        # base. Reporting it as `[]` failed the guard on a correct config.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "on:\n"
            "  pull_request:\n"
            "  push:\n"
            "    branches: [main]\n"
            "  workflow_dispatch:\n"
            "jobs:\n",
            encoding="utf-8",
        )
        assert _trigger_branches(workflow) == {
            "pull_request": None,  # no filter at all: every branch
            "push": ["main"],
            "workflow_dispatch": None,
        }

    def test_filters_this_parser_cannot_read_fail_closed(self, tmp_path):
        # The other half, and the reason the case above is not simply lenient. A
        # block list, a `branches-ignore` and a path filter all leave the
        # protected branch unproven, so they must read as `[]` and fail — never
        # as `None`, which now passes. The path filter belongs here because a
        # workflow that skips a pull request reports no check on it, and a
        # required check that never reports is the failure this file is for.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "on:\n"
            "  pull_request:\n"
            "    branches:\n"
            "      - main\n"
            "  push:\n"
            "    branches-ignore: [main]\n"
            "  workflow_run:\n"
            "    paths-ignore: ['**/*.md']\n"
            "jobs:\n",
            encoding="utf-8",
        )
        assert _trigger_branches(workflow) == {
            "pull_request": [], "push": [], "workflow_run": [],
        }

    def test_a_filter_written_oddly_is_still_seen(self, tmp_path):
        # Regression test, and the reason the recogniser matches by name at any
        # indent. Every shape here is ordinary YAML naming `develop`, and every
        # one of them parsed as `None` — unfiltered, therefore covered — when the
        # key was matched at one exact indent. The guard passed while CI never
        # reported on the protected branch, which is precisely the silent failure
        # it exists to prevent, introduced by the fix for the noisy one.
        shapes = {
            "six spaces":      "      branches: [develop]",
            "three spaces":    "   branches: [develop]",
            "quoted key":      '    "branches": [develop]',
            "space before :":  "    branches : [develop]",
            "trailing comment": "    branches: [develop]  # only the pivot",
        }
        for label, filter_line in shapes.items():
            workflow = tmp_path / f"{label.replace(' ', '-')}.yml"
            workflow.write_text(
                f"on:\n  pull_request:\n{filter_line}\njobs:\n", encoding="utf-8"
            )
            assert _trigger_branches(workflow) == {"pull_request": ["develop"]}, label

    def test_an_on_block_is_read_at_whatever_indent_it_uses(self, tmp_path):
        # Two spaces is a convention, not a rule, and assuming it put the same
        # constant in three patterns at once. Four-per-level is ordinary YAML
        # that GitHub runs; here its events matched nothing at all, and the two
        # ways that came out were both false. Unfiltered, the block read as
        # empty and the guard reported a workflow that "declares no
        # `on.pull_request` trigger at all" — sending the reader to add a
        # trigger the file already has. Filtered, the filter arrived with no
        # event to attach to and was refused as orphaned under a commented-out
        # event key that is nowhere in the file.
        shapes = {
            "four-per-level": (
                "on:\n    pull_request:\n        branches: [develop]\njobs:\n",
                {"pull_request": ["develop"]},
            ),
            "four-per-level, unfiltered": (
                "on:\n    pull_request:\n    push:\njobs:\n",
                {"pull_request": None, "push": None},
            ),
            # The filter floor follows the block rather than sitting at three,
            # so a shallower block has to keep working too.
            "one space": (
                "on:\n pull_request:\n  branches: [develop]\njobs:\n",
                {"pull_request": ["develop"]},
            ),
        }
        for label, (text, expected) in shapes.items():
            workflow = tmp_path / f"{label.replace(' ', '-').replace(',', '')}.yml"
            workflow.write_text(text, encoding="utf-8")
            assert _trigger_branches(workflow) == expected, label

    def test_a_commented_out_event_is_read_at_the_blocks_own_indent(self, tmp_path):
        # The comment rules follow the measured indent as well, in both
        # directions. At the block's indent a commented event still ends the
        # event above it, and one level in it still does not — the second half
        # is what keeps a note about a filter from dropping that filter, which
        # would read as unfiltered and pass.
        workflow = tmp_path / "ends-it.yml"
        workflow.write_text(
            "on:\n"
            "    pull_request:\n"
            "        branches: [develop]\n"
            "    # workflow_run:  # off for now\n"
            "        branches: [main]\n"
            "jobs:\n",
            encoding="utf-8",
        )
        assert _trigger_branches(workflow) == {"pull_request": ["develop"]}

        workflow = tmp_path / "leaves-it.yml"
        workflow.write_text(
            "on:\n"
            "    pull_request:\n"
            "      # only the pivot\n"
            "        branches: [main]\n"
            "jobs:\n",
            encoding="utf-8",
        )
        assert _trigger_branches(workflow) == {"pull_request": ["main"]}

    def test_an_orphan_does_not_fix_the_blocks_indent(self, tmp_path):
        # Why the indent is the smallest one under the block and not the first.
        # A commented-out event at the top of the block orphans its keys above
        # the first live event, so the first key here sits at four — and taking
        # it would put every real event, at two, below the block's own indent
        # and out of sight. `push` would vanish, and `pull_request` with it.
        #
        # Refused, as this shape is refused at any other indent: the orphan is
        # either the commented event's filter or nothing's, and nothing in the
        # file says which.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "on:\n"
            "  # pull_request:\n"
            "    branches: [main]\n"
            "  push:\n"
            "    branches: [main]\n"
            "jobs:\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="commented-out event key"):
            _trigger_branches(workflow)

    def test_a_note_flush_left_does_not_end_the_on_block(self, tmp_path):
        # A comment ends the block nowhere, including at column zero. Ending it
        # there would drop every line below the note — and a filter dropped that
        # way reads as absent, which is the direction that passes.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "on:\n"
            "  pull_request:\n"
            "# restore the other trigger later\n"
            "    branches: [develop]\n"
            "jobs:\n",
            encoding="utf-8",
        )
        assert _trigger_branches(workflow) == {"pull_request": ["develop"]}

    def test_the_shorthand_forms_of_on_are_read(self, tmp_path):
        # Neither shorthand can carry a branch filter, so `None` here is proved
        # rather than assumed. Skipping them instead reported "declares no
        # `on.pull_request` trigger at all" on a workflow that declares one —
        # the reader is then sent to add a trigger that is already there, and
        # the guard is the thing that is wrong.
        shapes = {
            "on: [push, pull_request]\njobs:\n": {"push": None, "pull_request": None},
            "on: pull_request\njobs:\n": {"pull_request": None},
            '"on": [pull_request]\njobs:\n': {"pull_request": None},
            "on: [push]  # everything\njobs:\n": {"push": None},
        }
        for text, expected in shapes.items():
            workflow = tmp_path / "w.yml"
            workflow.write_text(text, encoding="utf-8")
            assert _trigger_branches(workflow) == expected, text

    def test_the_shorthand_forms_are_read_under_the_key_too(self, tmp_path):
        # The same two shorthands written as a block. Both are ordinary YAML
        # that GitHub runs, and neither holds a mapping key — so the block
        # measured as empty and the guard reported "declares no
        # `on.pull_request` trigger at all" on a file that declares it on the
        # line above. Fail-closed and false, which sends the reader to add a
        # trigger already there.
        shapes = {
            "on:\n  - push\n  - pull_request\njobs:\n":
                {"push": None, "pull_request": None},
            # At the key's own indent, which is what every YAML serialiser
            # emits and the spelling most likely to be met in the wild. Read as
            # a top-level key it ended the block on its first line.
            "on:\n- push\n- pull_request\njobs:\n":
                {"push": None, "pull_request": None},
            "on:\n  pull_request\njobs:\n": {"pull_request": None},
            # The flow sequence one indent over from where `_on_shorthand`
            # reads it. Same shape, same answer, or the two spellings differ.
            "on:\n  [push, pull_request]\njobs:\n":
                {"push": None, "pull_request": None},
            "on:\n  - pull_request  # only this one for now\njobs:\n":
                {"pull_request": None},
            "on:\n  # - push\n  - pull_request\njobs:\n": {"pull_request": None},
        }
        for text, expected in shapes.items():
            workflow = tmp_path / "w.yml"
            workflow.write_text(text, encoding="utf-8")
            assert _trigger_branches(workflow) == expected, text

    def test_an_on_block_with_nothing_under_it_declares_no_events(self, tmp_path):
        # The one shape `{}` is true of, and the reason the branch above it is
        # not simply deleted. `on:` with an empty block declares no events, so
        # reporting the wanted one as missing is accurate rather than false —
        # which is what separates this from the two shapes beside it.
        for text in ("on:\njobs:\n", "on:\n  # everything is off\njobs:\n"):
            workflow = tmp_path / "w.yml"
            workflow.write_text(text, encoding="utf-8")
            assert _trigger_branches(workflow) == {}, text

    def test_a_block_on_that_is_neither_mapping_nor_sequence_is_refused(self, tmp_path):
        # Two plain scalars on consecutive lines are one folded scalar, not two
        # events, and a sequence item mixed with something else is not a shape
        # to guess at. Refused rather than read as empty: empty reads as a
        # missing trigger, and the file may well declare the one it is asked for.
        #
        # The last two are the fail-open direction rather than the noisy one,
        # and are why the item shape is matched strictly. A dash is a sequence
        # indicator only when whitespace follows, so `-pull_request` is the
        # scalar `"-pull_request"`; and a deeper `- ` continues the scalar above
        # it rather than nesting, so those two lines are `"push - pull_request"`.
        # Read loosely, both yielded real event names carrying no filter, which
        # passes — on workflows GitHub will not run at all.
        for text in (
            "on:\n  push\n  pull_request\njobs:\n",
            "on:\n  - push\n  pull_request\njobs:\n",
            "on:\n  - {pull_request: {branches: [develop]}}\njobs:\n",
            "on:\n  -pull_request\njobs:\n",
            "on:\n  - push\n    - pull_request\njobs:\n",
        ):
            workflow = tmp_path / "w.yml"
            workflow.write_text(text, encoding="utf-8")
            with pytest.raises(ValueError, match="does not read"):
                _trigger_branches(workflow)

    def test_an_on_block_this_parser_cannot_read_is_refused(self, tmp_path):
        # A flow mapping is the one shorthand that *can* carry a filter, so it
        # is the one that must not be guessed at. Loud, like a block-scalar
        # `name:`: silently reading no filter out of a workflow that has one is
        # exactly the fail-open direction `None` opened up.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "on: {pull_request: {branches: [develop]}}\njobs:\n", encoding="utf-8"
        )
        with pytest.raises(ValueError):
            _trigger_branches(workflow)

    def test_an_event_written_inline_is_unproven_rather_than_absent(self, tmp_path):
        # One level down from the case above and the same distinction: the event
        # is declared, so reporting it missing is false. It is its filter that
        # cannot be read, which is `[]` — the answer that fails.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "on:\n"
            "  pull_request: {branches: [main]}\n"
            "  push:  # nothing after the colon is still no filter\n"
            "  schedule: null\n"
            "jobs:\n",
            encoding="utf-8",
        )
        assert _trigger_branches(workflow) == {
            "pull_request": [], "push": None, "schedule": None,
        }

    def test_a_commented_out_event_does_not_lend_its_filter_to_the_one_above(self, tmp_path):
        # The keys under a commented-out event are orphaned. Attributing them to
        # the event above reported `pull_request` as filtered to `main` when its
        # own filter says `develop` — and passed. The commented event is absent
        # either way; what this fixes is the answer given for the live one.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "on:\n"
            "  pull_request:\n"
            "    branches: [develop]\n"
            "  # workflow_run:\n"
            "    branches: [main]\n"
            "jobs:\n",
            encoding="utf-8",
        )
        assert _trigger_branches(workflow) == {"pull_request": ["develop"]}

    def test_a_commented_out_event_is_recognised_through_a_trailing_note(self, tmp_path):
        # The case above, one trailing comment over. Requiring nothing after the
        # colon missed a disabled event annotated with why it is disabled, which
        # is how most of them are written — and a missed one hands `[main]` to
        # `pull_request` and passes on a workflow that only runs on `develop`.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "on:\n"
            "  pull_request:\n"
            "    branches: [develop]\n"
            "  # workflow_run:  # off for now\n"
            "    branches: [main]\n"
            "jobs:\n",
            encoding="utf-8",
        )
        assert _trigger_branches(workflow) == {"pull_request": ["develop"]}

    def test_a_note_below_the_event_indent_does_not_end_the_event(self, tmp_path):
        # Recognition is loose, but not unbounded. A note where notes about a
        # filter actually sit — one level in from the event — must leave the
        # filter attached, and so must one carrying no colon at all. Dropping
        # either would read as unfiltered, which passes.
        for note in ("    # only the pivot", "  # restore the other one later"):
            workflow = tmp_path / "w.yml"
            workflow.write_text(
                f"on:\n  pull_request:\n{note}\n    branches: [main]\njobs:\n",
                encoding="utf-8",
            )
            assert _trigger_branches(workflow) == {"pull_request": ["main"]}, note

    def test_an_ambiguous_orphan_is_refused_rather_than_guessed_at(self, tmp_path):
        # A note at the event indent that happens to parse as a key, sitting
        # between a bare event and its filter. The filter below is either that
        # event's or the commented one's, and nothing in the text says which.
        #
        # Refused because the two readings fail in opposite directions and one
        # of them passes: dropping the filter leaves `pull_request` looking
        # unfiltered, which covers every branch, which covers the protected one.
        # This is the cry-wolf cost of reading comments at all — the workflow
        # below is legal YAML and its filter is written perfectly well.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "on:\n"
            "  pull_request:\n"
            "  # TODO:\n"
            "    branches: [develop]\n"
            "  push:\n"
            "    branches: [main]\n"
            "jobs:\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="commented-out event key"):
            _trigger_branches(workflow)

    def test_an_orphan_is_dropped_once_the_event_has_a_filter_of_its_own(self, tmp_path):
        # The other side of that refusal, and the reason it is conditional.
        # This is the *unproven* arm of the two: `paths:` has already reduced
        # `pull_request` to `[]`, the most conservative answer there is, so no
        # reading of the orphan can make it worse and dropping it is not a
        # guess. The arm keyed on a repeated filter name is the other one, and
        # it is exercised by test_a_run_of_commented_lines_keeps_the_event_it_ended.
        #
        # Worth its own case because refusing here too would cost a specific
        # answer and replace it with a message that only says the file could
        # not be read.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "on:\n"
            "  pull_request:\n"
            "    paths: ['src/**']\n"
            "  # workflow_run:\n"
            "    branches: [main]\n"
            "jobs:\n",
            encoding="utf-8",
        )
        # `paths:` already made it unproven; the orphan must not overwrite that.
        assert _trigger_branches(workflow) == {"pull_request": []}

    def test_an_orphan_of_a_different_key_is_not_dropped(self, tmp_path):
        # The reverse direction, and the one that matters: the filter keys are
        # not interchangeable. `pull_request` is filtered by `branches:` AND by
        # `paths:`, so the orphaned `paths:` here is its own — dropping it on
        # the grounds that it "already has an answer" reports the event as
        # covering `main` when the path filter may stop it running at all.
        #
        # Asserted as a refusal rather than as `[]` because the parser cannot
        # actually tell whose it is; what it must not do is drop it.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "on:\n"
            "  pull_request:\n"
            "    branches: [main]\n"
            "  # NOTE: keep an eye on this\n"
            "    paths: ['src/**']\n"
            "jobs:\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="commented-out event key"):
            _trigger_branches(workflow)

    def test_a_run_of_commented_lines_keeps_the_event_it_ended(self, tmp_path):
        # Commenting out an event takes its sub-keys with it, so a run of these
        # is the normal shape rather than an odd one. Only the first line ends a
        # live event; if a later one overwrote what the first remembered, the
        # orphan below would be refused instead of settled, and a correct
        # workflow would stop the suite.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "on:\n"
            "  pull_request:\n"
            "    branches: [develop]\n"
            "  # workflow_run:\n"
            "  #   types: [completed]\n"
            "    branches: [main]\n"
            "jobs:\n",
            encoding="utf-8",
        )
        assert _trigger_branches(workflow) == {"pull_request": ["develop"]}

    def test_an_unreadable_filter_beats_a_readable_one_on_the_same_event(self, tmp_path):
        # Order-independence, asserted in both directions. An event filtered by
        # branch *and* by path is gated by both, so the half this can read must
        # not vouch for the half it cannot — whichever line comes first.
        for first, second in (
            ("    branches: [main]", "    paths-ignore: ['docs/**']"),
            ("    paths-ignore: ['docs/**']", "    branches: [main]"),
        ):
            workflow = tmp_path / "w.yml"
            workflow.write_text(
                f"on:\n  pull_request:\n{first}\n{second}\njobs:\n", encoding="utf-8"
            )
            assert _trigger_branches(workflow) == {"pull_request": []}, first

    def test_keys_after_the_jobs_block_are_not_jobs(self, tmp_path):
        # Asserted separately because a parser that walked to EOF would still
        # produce the correct entries in the fixture above.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "jobs:\n"
            "  only:\n"
            "    name: The one job\n"
            "permissions:\n"
            "  contents: read\n",
            encoding="utf-8",
        )
        assert _job_contexts(workflow) == {"only": "The one job"}
