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

Two details the shape depends on:

**A job's check context is its ``name:``, or its job id when it has none.** The
parser falls back the same way GitHub does, so a job that loses its ``name:``
fails here as a rename rather than vanishing from the comparison.

**``#`` opens a comment only at line start or after whitespace**, per YAML, so a
value is truncated on that pattern and not on every ``#``.

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
    """
    contexts: dict[str, str] = {}
    lines = workflow.read_text(encoding="utf-8").splitlines()

    try:
        start = next(i for i, line in enumerate(lines) if line.rstrip() == "jobs:")
    except StopIteration:  # pragma: no cover - asserted directly below
        return contexts

    current: str | None = None
    for line in lines[start + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        # A non-indented key ends the jobs block.
        if not line.startswith(" "):
            break
        job = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if job:
            current = job.group(1)
            contexts[current] = current  # the id is the context until a name says otherwise
            continue
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


def _trigger_branches(workflow: Path) -> dict[str, list[str] | None]:
    """
    ``{event: branch filter}`` from the workflow's ``on:`` block, where ``None``
    means the event declares no ``branches:`` filter and therefore fires on every
    branch.

    **``None`` and ``[]`` are different answers**, and collapsing them was a bug.
    An event with no filter covers the protected branch by covering everything;
    an event whose filter this cannot read covers nothing it can *prove*. Both
    used to parse as ``[]``, so a workflow running on every pull request — the
    shape a repo reaches for when its work targets more than one base branch —
    read here as one running on none, and the assertion below failed on a
    correctly configured repo. That direction is not the safe one it looks like:
    a guard that cries wolf on a good config is a guard someone deletes.

    Only the inline form (``branches: [main]``) is read. A block list still
    parses as ``[]`` and fails the assertion rather than passing it — the wrong
    direction to be lenient in, given what the check is for.

    ``branches-ignore`` is recorded as ``[]`` for that same reason. It is a
    filter this does not evaluate, so it cannot show the protected branch
    survives it, and it must not reach the unfiltered pass above by being
    mistaken for "no filter at all".
    """
    branches: dict[str, list[str] | None] = {}
    lines = workflow.read_text(encoding="utf-8").splitlines()

    try:
        start = next(i for i, line in enumerate(lines) if line.rstrip() == "on:")
    except StopIteration:  # pragma: no cover - asserted directly below
        return branches

    current: str | None = None
    for line in lines[start + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            break
        event = re.match(r"^  ([A-Za-z_]+):\s*$", line)
        if event:
            current = event.group(1)
            branches.setdefault(current, None)  # no filter seen yet
            continue
        if not current:
            continue
        listed = re.match(r"^    branches:\s*\[(.*)\]\s*$", line)
        if listed:
            branches[current] = [
                item.strip().strip("'\"")
                for item in listed.group(1).split(",")
                if item.strip()
            ]
            continue
        # A `branches:` this cannot read, or a `branches-ignore:` it will not
        # evaluate. Either way a filter is present and unproven, which is not the
        # same as absent — so it must not stay `None`.
        if re.match(r"^    branches(?:-ignore)?:", line):
            branches[current] = []
    return branches


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
            "nothing. If a job was renamed, rename the context in the ruleset "
            "to match and re-apply it; the committed file is not what GitHub "
            "enforces until it is written through the API."
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
        # block list and a `branches-ignore` both leave the protected branch
        # unproven, so they must read as `[]` and fail — never as `None`, which
        # now passes.
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "on:\n"
            "  pull_request:\n"
            "    branches:\n"
            "      - main\n"
            "  push:\n"
            "    branches-ignore: [main]\n"
            "jobs:\n",
            encoding="utf-8",
        )
        assert _trigger_branches(workflow) == {"pull_request": [], "push": []}

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
