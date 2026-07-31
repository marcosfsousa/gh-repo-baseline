# tests/test_guard.py

"""
``templates/tests/test_required_checks.py``, exercised as the thing it is.

That file is a guard, and a guard that cannot go red is not a guard. Asserting it
passes on a correct repo proves almost nothing — a file containing no assertions
at all would do the same. So this suite assembles a throwaway repo out of
``templates/``, checks the guard passes, and then breaks the repo seventeen
different ways and checks the guard fails **on exactly the intended tests and no
others**.

The "no others" half matters as much as the rest. A mutation that trips six tests
instead of the one that describes it means the failure message a person reads
will not name the thing that actually broke.

How it runs: pytest in a subprocess, against a directory under ``tmp_path``. It
has to be a subprocess, because what is under test is a test session's verdict
rather than a return value. The temp directory is outside this repo, so the
nested run picks up neither this ``pytest.ini`` nor these fixtures.
"""

import json
import re
import shutil
import subprocess
import sys

import pytest

from conftest import REPO_ROOT, TEMPLATES


# ── The two copies of the guard ───────────────────────────────────────────────

class TestTheLiveGuardMatchesTheTemplate:
    """
    ``tests/test_required_checks.py`` is a copy of the template, guarding this
    repo's own ruleset. Two copies of the same file in one repo is a drift
    problem, and the drift is asymmetric in the worst way: a fix applied to the
    live copy alone leaves this repo green while every repo that copies the
    template gets the broken version.

    Only the header comment may differ — the live copy says it is the live copy.
    Everything from the module docstring down must be byte-identical.
    """

    @staticmethod
    def _from_docstring(path) -> str:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        start = next(i for i, line in enumerate(lines) if line.rstrip("\r\n") == '"""')
        return "".join(lines[start:])

    def test_they_are_identical_below_the_header(self):
        live = self._from_docstring(REPO_ROOT / "tests" / "test_required_checks.py")
        template = self._from_docstring(TEMPLATES / "tests" / "test_required_checks.py")
        assert live == template, (
            "tests/test_required_checks.py and "
            "templates/tests/test_required_checks.py have diverged below the "
            "header.\n\nEdit the template, then re-copy it over the live one and "
            "restore only the header. Fixing the live copy alone leaves this repo "
            "green while shipping the bug to everything that copies the template."
        )

    def test_only_the_header_differs(self):
        # Guards the guard: if the marker line moved, `_from_docstring` could
        # return the whole file for both and the comparison above would pass
        # vacuously while comparing headers too.
        live = (REPO_ROOT / "tests" / "test_required_checks.py").read_text(encoding="utf-8")
        template = (TEMPLATES / "tests" / "test_required_checks.py").read_text(encoding="utf-8")
        assert live != template, "the live copy still carries the template's header"
        assert "THE LIVE COPY" in live.split('"""')[0]
        assert "TEMPLATE." in template.split('"""')[0]


# ── Assembling a repo out of the templates ────────────────────────────────────

def _assemble(root, boot):
    """
    A minimal, correct target repo: the workflow template, the guard, and a
    ruleset generated for whatever job names that workflow reports.

    Built from ``templates/`` rather than from a hand-written fixture on purpose.
    A hand-written one would keep passing after the templates drifted away from
    it, which is the one failure this file cannot afford.
    """
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "rulesets").mkdir(parents=True)
    (root / "tests").mkdir()

    shutil.copy(TEMPLATES / "ci.yml", root / ".github" / "workflows" / "ci.yml")
    shutil.copy(
        TEMPLATES / "tests" / "test_required_checks.py",
        root / "tests" / "test_required_checks.py",
    )

    contexts = boot._job_contexts(root / ".github" / "workflows" / "ci.yml")
    _write_ruleset(root, boot._ruleset_body("main", contexts))
    return root


def _run_guard(root) -> tuple[int, set[str]]:
    """
    ``(exit code, {names of failing tests})``.

    ``--color=no`` is load-bearing rather than tidiness. The names are read out
    of the ``-rf`` summary by matching the literal ``FAILED`` at line start, and
    pytest colours that word whenever colour is forced — ``FORCE_COLOR`` in the
    environment is honoured even though this output is captured, which is
    exactly the case that matters. The prefix then matches nothing, every name
    goes missing, and all sixteen mutations below report that the guard fired on
    the wrong tests.

    That reads as sixteen regressions in the guard rather than one bug in this
    reader, and it reads that way most convincingly right after a change to the
    parser those mutations exercise. So the setting is pinned rather than
    inherited: this run's colour is nobody's preference, and the only thing read
    back out of it is a machine-parsed summary line.
    """
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", str(root / "tests"),
            "-q", "--no-header", "-rf", "--color=no", "-p", "no:cacheprovider",
        ],
        capture_output=True, text=True,
    )
    failed = {
        line.split("::")[-1].split()[0]
        for line in result.stdout.splitlines()
        if line.startswith("FAILED")
    }
    return result.returncode, failed


def _workflow(root):
    return root / ".github" / "workflows" / "ci.yml"


def _ruleset_path(root):
    return root / ".github" / "rulesets" / "main.json"


def _read_ruleset(root) -> dict:
    return json.loads(_ruleset_path(root).read_text(encoding="utf-8"))


def _write_ruleset(root, data: dict) -> None:
    _ruleset_path(root).write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def _edit_workflow(root, old: str, new: str) -> None:
    text = _workflow(root).read_text(encoding="utf-8")
    assert old in text, f"the mutation found nothing to replace: {old!r}"
    _workflow(root).write_text(text.replace(old, new), encoding="utf-8", newline="\n")


def _drop_rule(root, rule_type: str) -> None:
    data = _read_ruleset(root)
    before = len(data["rules"])
    data["rules"] = [r for r in data["rules"] if r["type"] != rule_type]
    assert len(data["rules"]) == before - 1, f"no {rule_type} rule to drop"
    _write_ruleset(root, data)


def _set_top_level(root, key: str, value) -> None:
    data = _read_ruleset(root)
    assert key in data, f"no {key} to change"
    data[key] = value
    _write_ruleset(root, data)


def _edit_checks_params(root, key: str, value) -> None:
    data = _read_ruleset(root)
    rule = next(r for r in data["rules"] if r["type"] == "required_status_checks")
    rule["parameters"][key] = value
    _write_ruleset(root, data)


def _require_context(root, context: str) -> None:
    data = _read_ruleset(root)
    rule = next(r for r in data["rules"] if r["type"] == "required_status_checks")
    rule["parameters"]["required_status_checks"].append({"context": context})
    _write_ruleset(root, data)


# ── The guard on a correct repo ───────────────────────────────────────────────

# How many tests the template guard collects once it has been copied into a
# repo. Pinned as a number and compared as one, because the assertion below is
# the only thing standing between a hollowed-out template and a suite that reads
# as green: everything after it breaks a repo and checks the guard notices, and
# a guard that collects two tests notices nothing while satisfying every one of
# those cases.
#
# It was a substring test — `"41 tests collected" in stdout` — which a session of
# 141 satisfies too. The direction that matters is the count falling, and a
# substring can see a fall only when the smaller number is not a prefix of the
# larger. Read the number back and compare it as an integer instead.
#
# Bump it when the template gains or loses a test. That is a line in a diff,
# which is the point: the count is a decision, not an incidental.
_TEMPLATE_GUARD_TESTS = 58


class TestTheGuardPasses:

    def test_a_repo_assembled_from_templates_is_green(self, boot, tmp_path):
        root = _assemble(tmp_path / "repo", boot)
        code, failed = _run_guard(root)
        assert code == 0, f"the guard failed on a correct repo: {sorted(failed)}"

    def test_it_is_not_a_trivial_suite(self, boot, tmp_path):
        # A file that collected two tests would satisfy every case below while
        # checking almost nothing. Assert the session is the size it should be.
        root = _assemble(tmp_path / "repo", boot)
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(root / "tests"),
             "-q", "--no-header", "--collect-only", "--color=no",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True,
        )
        # Checked separately so a session that never collected reports as itself
        # rather than as a wrong count — a collection error and a shrunken suite
        # are different problems and the message has to say which one this is.
        collected = re.search(r"(\d+) tests? collected", result.stdout)
        assert collected, (
            "pytest reported no collection count for the template guard, so it "
            "did not get as far as collecting.\n"
            "That is an import error or a syntax error in the copied file, not a "
            "count that drifted.\n\n" + result.stdout[-600:]
        )
        assert int(collected.group(1)) == _TEMPLATE_GUARD_TESTS, (
            f"the template guard collects {collected.group(1)} tests, not "
            f"{_TEMPLATE_GUARD_TESTS}.\n"
            "If the template gained or lost a test on purpose, update "
            "_TEMPLATE_GUARD_TESTS. If it did not, tests are no longer being "
            "collected — a class renamed out of pytest's convention collects "
            "nothing and raises nothing, and every mutation below would still "
            "fail on the tests that remain.\n\n" + result.stdout[-600:]
        )

    def test_the_readme_states_the_right_number_of_mutations(self):
        # README.md quotes this count in prose. The stale `# 48 tests` it used to
        # carry was dropped rather than corrected, and replacing it with another
        # hand-maintained number would have re-created exactly that problem one
        # line down. So the number is read back and asserted instead.
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        stated = re.search(r"breaks it \*\*(\w+) ways\*\*", readme)
        assert stated, "README.md no longer states how many ways the guard is broken"
        words = {
            8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve",
            13: "thirteen", 14: "fourteen", 15: "fifteen", 16: "sixteen",
            17: "seventeen", 18: "eighteen", 19: "nineteen", 20: "twenty",
        }
        # Read out rather than indexed, because a count past the end of the
        # table is the ordinary way this test is reached — adding a mutation is
        # what moves the number — and a bare KeyError names neither the file to
        # edit nor the reason.
        expected = words.get(len(MUTATIONS))
        assert expected, (
            f"MUTATIONS has {len(MUTATIONS)} entries and this test cannot spell "
            "that number.\nAdd it to `words` above; the README states the count "
            "in prose, so it has to be compared as a word."
        )
        assert stated.group(1) == expected, (
            f"README.md says the guard is broken {stated.group(1)!r} ways; "
            f"MUTATIONS has {len(MUTATIONS)} entries "
            f"({expected!r}).\nUpdate the README, and the list of "
            "mutations it enumerates alongside the number."
        )


# ── The guard on seventeen broken repos ──────────────────────────────────────
#
# Each entry is (label, mutation, the exact set of tests that must fail).

class TestTheFailureNamesAreReadBack:
    """
    ``_run_guard`` reads the names of failing tests out of pytest's own output,
    which makes that output part of this suite's contract rather than a display
    detail.

    Worth asserting on its own because the failure lands one layer away from its
    cause. Every mutation below compares the *set* of names, so a reader that
    returns an empty one turns a single bug here into sixteen failures over
    there, each claiming the guard fired on the wrong tests — a report that
    points at the guard, then at the parser, and never at this function.
    """

    def test_forced_colour_does_not_hide_them(self, boot, tmp_path, monkeypatch):
        # Not a contrived environment: pytest honours FORCE_COLOR even when its
        # output is captured, and the nested run inherits it from this process.
        # Any CI job or wrapper that sets it puts the suite in this state.
        monkeypatch.setenv("FORCE_COLOR", "3")
        root = _assemble(tmp_path / "repo", boot)
        _set_top_level(root, "enforcement", "disabled")

        code, failed = _run_guard(root)

        assert code != 0
        assert failed == {"test_it_is_actively_enforced"}, (
            "the failing test's name did not survive being read back out of the "
            f"nested run: {sorted(failed)}.\nAn empty set means colour reached "
            "the summary line and the `FAILED` prefix matched nothing."
        )


MUTATIONS = [
    pytest.param(
        lambda root: _edit_workflow(
            root, "name: Placeholder (replace me)", "name: Placeholder (renamed)"
        ),
        {"test_every_required_context_is_a_job", "test_every_job_is_a_required_context"},
        id="a-job-is-renamed",
        # The rename detaches the rule. Both ends of the equality fire: the old
        # context now names no job, and the new job is required by nothing. This
        # is the failure that is otherwise invisible — the PR sits pending with a
        # rule that applies to nothing.
    ),
    pytest.param(
        lambda root: _workflow(root).write_text(
            _workflow(root).read_text(encoding="utf-8")
            + "\n  extra:\n    name: A new seam\n    runs-on: ubuntu-latest\n",
            encoding="utf-8", newline="\n",
        ),
        {"test_every_job_is_a_required_context"},
        id="a-seam-is-added-but-not-required",
        # The likelier direction in practice: the job gets added, the ruleset does
        # not, and it runs without gating anything.
    ),
    pytest.param(
        lambda root: _drop_rule(root, "required_status_checks"),
        {"test_the_rule_exists_at_all", "test_every_job_is_a_required_context",
         "test_branches_must_be_up_to_date", "test_required_contexts_are_collected"},
        id="the-rule-is-dropped",
    ),
    pytest.param(
        lambda root: _edit_checks_params(root, "required_status_checks", []),
        {"test_every_job_is_a_required_context", "test_required_contexts_are_collected"},
        id="the-rule-is-present-but-empty",
        # Note test_the_rule_exists_at_all does NOT fire here: the rule is there.
        # That is the point of the empty case — it looks like protection.
    ),
    pytest.param(
        lambda root: _edit_checks_params(root, "strict_required_status_checks_policy", False),
        {"test_branches_must_be_up_to_date"},
        id="strict-mode-is-switched-off",
    ),
    pytest.param(
        lambda root: _edit_workflow(root, "branches: [main]", "branches: [develop]"),
        {"test_ci_runs_on_the_protected_branch"},
        id="ci-stops-running-on-the-protected-branch",
        # The other side of the same failure: the checks are required and can
        # never report.
    ),
    pytest.param(
        lambda root: _edit_workflow(
            root,
            "  pull_request:\n    branches: [main]",
            "  pull_request:\n    branches-ignore: [main]",
        ),
        {"test_ci_runs_on_the_protected_branch"},
        id="ci-is-told-to-skip-the-protected-branch",
        # The inverse filter, and the regression test for the fail-closed half of
        # `_trigger_branches`. An event with no `branches:` key now passes,
        # because running on every branch covers the protected one — so a shape
        # the parser does not evaluate must be recorded as unproven rather than
        # falling through to that pass. `branches-ignore: [main]` is the case
        # where the difference is the whole answer.
    ),
    pytest.param(
        lambda root: _edit_workflow(
            root,
            "  pull_request:\n    branches: [main]",
            "  pull_request:\n      branches: [develop]",
        ),
        {"test_ci_runs_on_the_protected_branch"},
        id="the-branch-filter-moves-somewhere-the-parser-must-still-see",
        # Same defect as the mutation above, reached by indentation rather than
        # by key name. Six spaces is ordinary YAML and the filter is real, so the
        # only way this passes is if the parser failed to *see* it and defaulted
        # to unfiltered — which is the fail-open direction, and the one no
        # assertion catches downstream. Kept end-to-end rather than left to the
        # unit test because that default is reached through the guard's own
        # assertion, not through the parser's return value alone.
    ),
    pytest.param(
        lambda root: _edit_workflow(
            root,
            "  pull_request:\n    branches: [main]",
            "  pull_request:\n    branches: [develop]\n"
            "  # workflow_run:  # off for now\n    branches: [main]",
        ),
        {"test_ci_runs_on_the_protected_branch"},
        id="a-commented-out-event-lends-its-filter-to-the-live-one",
        # The third of the fail-open shapes that has to be reached end-to-end.
        # The keys under a commented-out event are orphaned, and reading them as
        # the *previous* event's filter is silent: `pull_request` is filtered to
        # `develop` and reports as covering `main`, which passes. Nothing in the
        # parser's return value says it happened — the wrong answer is a
        # perfectly ordinary list — so, like the mutation above, the only place
        # it shows is through the guard's own assertion.
        #
        # The trailing note is not decoration. A disabled event is normally
        # annotated with why, and a recogniser requiring nothing after the colon
        # misses exactly that shape, which is what hands `[main]` over.
    ),
    pytest.param(
        lambda root: _require_context(root, "security / codeql"),
        {"test_every_required_context_is_a_job"},
        id="a-check-from-another-workflow-is-required",
        # Not a rename, and the ruleset may well be right — the guard reads one
        # workflow and that context comes from another. It fires here either way,
        # which is correct: a required check from a file nothing parses is a
        # check no test can tell you was renamed. What it must not do is say the
        # job was renamed, which is what its message used to claim.
        #
        # `test_every_job_is_a_required_context` deliberately does not fire: every
        # job is still required. Only the unmatched direction breaks.
    ),
    pytest.param(
        lambda root: _drop_rule(root, "non_fast_forward"),
        {"test_force_push_and_deletion_are_blocked"},
        id="force-push-protection-is-removed",
    ),
    pytest.param(
        lambda root: _write_ruleset(root, {
            **_read_ruleset(root),
            "conditions": {"ref_name": {"include": ["refs/heads/develop"], "exclude": []}},
        }),
        {"test_it_targets_the_protected_branch"},
        id="the-ruleset-is-retargeted",
    ),
    pytest.param(
        lambda root: _drop_rule(root, "pull_request"),
        {"test_a_pull_request_is_required"},
        id="a-direct-push-becomes-possible",
        # The last repo-level assertion that had never been proven able to go
        # red. Without the rule a push straight to the protected branch is
        # accepted, and every other test here stays green.
    ),
    pytest.param(
        lambda root: _set_top_level(root, "enforcement", "disabled"),
        {"test_it_is_actively_enforced"},
        id="enforcement-is-switched-off",
    ),
    pytest.param(
        lambda root: _set_top_level(root, "enforcement", "evaluate"),
        {"test_it_is_actively_enforced"},
        id="enforcement-is-set-to-dry-run",
        # The sneakier of the two: `evaluate` reports what it would have blocked
        # and blocks nothing, so the settings UI shows a live ruleset with every
        # rule intact. Asserted separately from `disabled` because it is the one
        # someone reaches for while testing and then forgets to put back.
    ),
    pytest.param(
        lambda root: _set_top_level(root, "target", "tag"),
        {"test_it_targets_branches"},
        id="the-ruleset-is-retargeted-to-tags",
        # Name, rules and ref_name condition all survive; `refs/heads/main`
        # simply matches no tag, so the whole thing enforces against nothing.
    ),
    pytest.param(
        lambda root: _write_ruleset(root, {
            **_read_ruleset(root),
            "conditions": {"ref_name": {
                "include": ["refs/heads/main"], "exclude": ["refs/heads/main"],
            }},
        }),
        {"test_the_protected_branch_is_not_excluded"},
        id="the-protected-branch-is-excluded",
        # The include still names the branch, so the retarget mutation above
        # does not catch this. `exclude` wins, and the ruleset covers nothing.
    ),
]


@pytest.mark.parametrize("mutate,must_fail", MUTATIONS)
class TestTheGuardFails:

    def test_it_fails_on_exactly_the_intended_tests(self, boot, tmp_path, mutate, must_fail):
        root = _assemble(tmp_path / "repo", boot)
        mutate(root)
        code, failed = _run_guard(root)

        assert code != 0, "the guard passed on a repo that should have failed"
        assert failed == must_fail, (
            "the guard fired on the wrong tests.\n"
            f"  missing:    {sorted(must_fail - failed) or 'none'}\n"
            f"  unexpected: {sorted(failed - must_fail) or 'none'}\n\n"
            "Missing means the mutation is not actually caught. Unexpected means "
            "it is caught by something whose failure message does not describe "
            "it, so the person reading the output is pointed at the wrong thing."
        )
