# tests/test_required_checks.py
#
# TEMPLATE. Copy into a target repo as tests/test_required_checks.py, commit the
# ruleset it reads alongside it, and check the three constants below. It is not
# collected in the baseline repo — see pytest.ini there — because the paths it
# resolves only exist once it has been copied out.

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
from pathlib import Path


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
            contexts[current] = _strip_comment(name.group(1)).strip("'\"")
    return contexts


def _trigger_branches(workflow: Path) -> dict[str, list[str]]:
    """
    ``{event: [branch, ...]}`` from the workflow's ``on:`` block.

    Only the inline form (``branches: [main]``) is read. A block list parses as
    no branches at all, which fails the assertion rather than passing it — the
    wrong direction to be lenient in, given what the check is for.
    """
    branches: dict[str, list[str]] = {}
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
            branches.setdefault(current, [])
            continue
        listed = re.match(r"^    branches:\s*\[(.*)\]\s*$", line)
        if listed and current:
            branches[current] = [
                item.strip().strip("'\"")
                for item in listed.group(1).split(",")
                if item.strip()
            ]
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
            assert _PROTECTED_BRANCH in triggers.get(event, []), (
                f"The workflow's `on.{event}.branches` is {triggers.get(event)!r} "
                f"and does not include {_PROTECTED_BRANCH!r}.\n"
                "The required checks would stop reporting on pull requests to "
                f"{_PROTECTED_BRANCH}, and a required check that never arrives "
                "leaves the pull request pending rather than red — "
                "indistinguishable from one still running. Either restore the "
                "branch here or narrow the ruleset to a branch CI covers."
            )


class TestRulesetKeepsItsBaseline:
    """
    The ruleset file is re-read from GitHub after any UI edit, so a decision can
    be dropped by a click and arrive here as a diff nobody reads closely. These
    are the baseline decisions worth failing on. See rulesets/README.md in the
    baseline repo for the argument behind each.
    """

    def test_it_targets_the_protected_branch(self):
        include = _load_ruleset().get("conditions", {}).get("ref_name", {}).get("include", [])
        expected = [f"refs/heads/{_PROTECTED_BRANCH}"]
        assert include == expected, (
            f"The ruleset targets {include}, not {expected}.\n"
            "Widening this is fine; narrowing or moving it leaves the branch "
            "everything ships from unprotected."
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

    def test_trigger_branches_are_collected(self, tmp_path):
        assert _trigger_branches(_WORKFLOW).get("pull_request") == [_PROTECTED_BRANCH]
        # A block list is not read, and must therefore fail closed rather than
        # reporting an empty filter as "no restriction".
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "on:\n"
            "  pull_request:\n"
            "    branches:\n"
            "      - main\n"
            "jobs:\n",
            encoding="utf-8",
        )
        assert _trigger_branches(workflow) == {"pull_request": []}

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
