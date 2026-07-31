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
    return None if stripped in ("", "~", "null", "Null", "NULL") else stripped


def _job_contexts(workflow: Path) -> dict[str, str]:
    """
    ``{job id: status-check context}`` for every job in a workflow.

    Keyed by id so a failure can name the job whose ``name:`` moved rather than
    only the string that disappeared.

    Job ids sit at two spaces under ``jobs:`` and a job-level ``name:`` at four.
    Step names are ``- name:`` at six and are therefore excluded structurally,
    not by pattern — see ``test_step_names_are_not_collected``.

    **A job key is recognised by its shape before its id is read**, so a key this
    cannot vouch for stops the parse instead of being skipped. Skipping was two
    silent wrongs from one comment: ``pytest:  # the gate`` failed the
    end-of-line anchor, so the job vanished from the equality *and* the ``name:``
    beneath it was recorded against the job above — reporting a rename on a job
    nobody touched.

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

    Both are refused rather than guessed at because both fail the same way when
    guessed wrong: a required context nothing reports, and a pull request that
    sits pending rather than red.
    """
    contexts: dict[str, str] = {}
    lines = workflow.read_text(encoding="utf-8").splitlines()

    try:
        start = next(i for i, line in enumerate(lines) if line.rstrip() == "jobs:")
    except StopIteration:  # pragma: no cover - asserted directly below
        return contexts

    def _refuse(job: str, what: str, fix: str) -> None:
        raise ValueError(
            f"{workflow}, job {job!r}: {what}, so the check it reports is not "
            f"the job id or its `name:`.\nThis parser will not guess at a "
            f"composed context. {fix}"
        )

    current: str | None = None
    strategy_of: str | None = None  # the job whose `strategy:` we are inside
    for line in lines[start + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        # A non-indented key ends the jobs block.
        if not line.startswith(" "):
            break
        # Matched on the key *shape* — two spaces, then anything up to a colon —
        # rather than on the id charset, so an id this cannot read is refused
        # below instead of falling through to the `name:` branch with `current`
        # still naming the previous job.
        key = re.match(r"^  (\S[^:]*):(.*)$", line)
        if key:
            job_id, trailing = key.group(1), _strip_comment(key.group(2))
            if trailing:
                # A job id maps to a block, so anything left after the comment is
                # stripped is not a job at all.
                raise ValueError(
                    f"{workflow}: `{job_id}:` sits where a job id belongs but "
                    f"carries the value {trailing!r}.\nA job maps to a block. "
                    "This parser stops rather than attributing the keys below it "
                    "to the job above."
                )
            if not re.fullmatch(r"[A-Za-z0-9_-]+", job_id):
                raise ValueError(
                    f"{workflow}: {job_id!r} is not a job id this parser can "
                    "read.\nGitHub allows letters, digits, `-` and `_`. Anything "
                    "else stops the parse rather than leaving the keys below it "
                    "attributed to the job above."
                )
            current = job_id
            strategy_of = None
            contexts[current] = current  # the id is the context until a name says otherwise
            continue
        if current is None:
            continue
        # `uses:` at four spaces is a reusable-workflow call; a step's is `- uses:`
        # at six and does not match.
        if re.match(r"^    uses\s*:", line):
            _refuse(
                current,
                "calls a reusable workflow, so GitHub reports it as "
                "`caller / called`",
                "Require that context explicitly, or inline the job.",
            )
        if re.match(r"^    strategy\s*:", line):
            strategy_of = current
            continue
        if re.match(r"^    \S", line):
            strategy_of = None  # a sibling key ended the strategy block
        if strategy_of == current and re.match(r"^      matrix\s*:", line):
            _refuse(
                current,
                "is a matrix job, so GitHub reports one suffixed check per "
                f"combination (`{current} (3.11)`) and none named `{current}`",
                "Require each combination explicitly, or drop the matrix.",
            )
        name = re.match(r"^    name:\s*(.+)$", line)
        if name and current:
            # A refusal propagates and fails the suite, naming the job. That is
            # the right outcome: a `name:` this cannot read is a context it
            # cannot check, and passing would mean checking nothing.
            try:
                scalar = _scalar(name.group(1))
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

# An event under a block `on:`. What follows the colon is captured rather than
# required to be empty — an event written inline is still an event, and saying
# so is the difference between "unreadable" and "absent".
_EVENT_KEY = re.compile(r"^  [\"']?([A-Za-z_]+)[\"']?\s*:\s*(.*)$")

# A commented-out event key: at the event indent, and shaped like a key. What
# follows the colon is not constrained, because a disabled event is commonly
# disabled with a note saying why — `# workflow_run:  # off for now`. Requiring
# nothing after the colon missed exactly that shape, and a missed one hands its
# orphaned keys to the live event above as if they were its filter.
#
# Over-matching is the safe direction here and under-matching is not, which is
# why this is loose. A comment this wrongly reads as an event key can only
# orphan the keys below it, and `_trigger_branches` refuses rather than guesses
# when an orphan is ambiguous. Still deliberately not every comment: a note with
# no colon leaves the event alone, and so does anything below the event indent.
_COMMENTED_EVENT = re.compile(r"^  #+\s*[\"']?[A-Za-z_]+[\"']?\s*:")


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
    are matched by *name* at any indent rather than by position.

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

    **A commented-out event key ends the event it commented out.** Anything left
    indented under it is orphaned, and reading those keys as the *previous*
    event's filter is how a workflow filtered to ``develop`` reported ``main``
    and passed — the misattribution is silent when the commented event is not one
    of the two asserted below.

    Which comments count and what happens to the keys they orphan are two
    separate decisions, and collapsing them into one is what made this look like
    a choice between two silent failures. Recognition is deliberately *loose*
    (see ``_COMMENTED_EVENT``): a commented event key carrying a trailing note is
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

    key_pattern = re.compile(
        r"^\s{3,}[\"']?(" + "|".join(_FILTER_KEYS) + r")[\"']?\s*:\s*(.*)$"
    )

    current: str | None = None
    # The event `current` held when a comment ended it. Kept rather than
    # discarded because whether its filter had already been read is what decides
    # if the keys below the comment are ambiguous or merely orphaned.
    suspended: str | None = None
    for line in lines[start + 1:]:
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            # Forgetting the current event is the whole of the fix: the keys
            # below a commented-out one belong to nothing, and attributing them
            # to the event above is worse than dropping them, because the event
            # above is real and its filter is then reported as something it is
            # not.
            if _COMMENTED_EVENT.match(line):
                # Only the first of a run of these carries the live event.
                # Overwriting on the second — a commented event whose own
                # sub-keys are commented out under it, which is how a block is
                # normally disabled — would lose it and refuse a shape the rule
                # below can settle.
                if current is not None:
                    suspended = current
                current = None
            continue
        if not line.startswith(" "):
            break
        event = _EVENT_KEY.match(line)
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
            if inline_value and inline_value not in ("~", "null", "Null", "NULL"):
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
        # The other side of that refusal, and the reason it is conditional. An
        # event carries one `branches:`, so once it has been read the orphan
        # provably is not its filter and dropping it is not a guess.
        #
        # Worth its own case because refusing here too would cost the specific
        # message — `develop` does not cover `main` — and replace it with one
        # that only says the file could not be read.
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
