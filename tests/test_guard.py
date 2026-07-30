# tests/test_guard.py

"""
``templates/tests/test_required_checks.py``, exercised as the thing it is.

That file is a guard, and a guard that cannot go red is not a guard. Asserting it
passes on a correct repo proves almost nothing — a file containing no assertions
at all would do the same. So this suite assembles a throwaway repo out of
``templates/``, checks the guard passes, and then breaks the repo eight different
ways and checks the guard fails **on exactly the intended tests and no others**.

The "no others" half matters as much as the rest. A mutation that trips six tests
instead of the one that describes it means the failure message a person reads
will not name the thing that actually broke.

How it runs: pytest in a subprocess, against a directory under ``tmp_path``. It
has to be a subprocess, because what is under test is a test session's verdict
rather than a return value. The temp directory is outside this repo, so the
nested run picks up neither this ``pytest.ini`` nor these fixtures.
"""

import json
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
    """``(exit code, {names of failing tests})``."""
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", str(root / "tests"),
            "-q", "--no-header", "-rf", "-p", "no:cacheprovider",
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


def _edit_checks_params(root, key: str, value) -> None:
    data = _read_ruleset(root)
    rule = next(r for r in data["rules"] if r["type"] == "required_status_checks")
    rule["parameters"][key] = value
    _write_ruleset(root, data)


# ── The guard on a correct repo ───────────────────────────────────────────────

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
             "-q", "--no-header", "--collect-only", "-p", "no:cacheprovider"],
            capture_output=True, text=True,
        )
        assert "15 tests collected" in result.stdout, result.stdout[-600:]


# ── The guard on eight broken repos ───────────────────────────────────────────
#
# Each entry is (label, mutation, the exact set of tests that must fail).

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
        {"test_ci_runs_on_the_protected_branch", "test_trigger_branches_are_collected"},
        id="ci-stops-running-on-the-protected-branch",
        # The other side of the same failure: the checks are required and can
        # never report.
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
