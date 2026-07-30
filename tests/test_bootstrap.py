# tests/test_bootstrap.py

"""
``scripts/bootstrap-repo.py``, offline.

Nothing here touches the GitHub API. Everything that talks to ``gh`` is a thin
wrapper over ``subprocess``; what carries the actual decisions is the pure part —
how a workflow is parsed, how the ruleset body is assembled, and when two
rulesets count as the same. That is what these test.

The consequence worth stating: a green run here does not prove the script can
authenticate, or that the endpoints it calls still exist. It proves the script
would send the right body. Use ``--dry-run`` against a real repo for the other
half; it performs only GETs.
"""

import json

import pytest

from conftest import REPO_ROOT, TEMPLATES


# ── The workflow parser ───────────────────────────────────────────────────────

WORKFLOW_SHAPES = (
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
    "  contents: read\n"
)

EXPECTED_SHAPES = [
    "Backend tests (pytest)",
    "Frontend typecheck + build",
    "UI tests (Playwright)",
    # No `name:`, so GitHub reports the job id — and so must the parser.
    "unnamed",
]


class TestJobContexts:

    def test_it_reads_every_shape_yaml_allows(self, boot, tmp_path):
        workflow = tmp_path / "w.yml"
        workflow.write_text(WORKFLOW_SHAPES, encoding="utf-8")
        assert boot._job_contexts(workflow) == EXPECTED_SHAPES

    def test_step_names_are_not_collected(self, boot, tmp_path):
        # Excluded structurally by indentation, not by pattern. Asserted directly
        # because the equality this feeds would otherwise catch it only by luck
        # of a step name differing from every job name.
        workflow = tmp_path / "w.yml"
        workflow.write_text(WORKFLOW_SHAPES, encoding="utf-8")
        collected = boot._job_contexts(workflow)
        assert "Install dependencies" not in collected
        assert "CI" not in collected  # the workflow's own top-level name

    def test_keys_after_the_jobs_block_are_not_jobs(self, boot, tmp_path):
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "jobs:\n  only:\n    name: The one job\npermissions:\n  contents: read\n",
            encoding="utf-8",
        )
        assert boot._job_contexts(workflow) == ["The one job"]

    def test_a_workflow_with_no_jobs_block_exits(self, boot, tmp_path):
        # Deriving zero contexts must not quietly produce a ruleset that requires
        # nothing — that is the empty-rule failure the whole design avoids.
        workflow = tmp_path / "w.yml"
        workflow.write_text("name: CI\non:\n  push:\n    branches: [main]\n", encoding="utf-8")
        with pytest.raises(SystemExit):
            boot._job_contexts(workflow)

    def test_a_jobs_block_with_no_jobs_exits(self, boot, tmp_path):
        workflow = tmp_path / "w.yml"
        workflow.write_text("jobs:\n\npermissions:\n  contents: read\n", encoding="utf-8")
        with pytest.raises(SystemExit):
            boot._job_contexts(workflow)


class TestBothParserCopiesAgree:
    """
    The duplication CLAUDE.md documents, held to its promise.

    ``templates/tests/test_required_checks.py`` carries its own copy of this
    parser and must, because it gets copied out into other repos and cannot
    import from here. Two copies silently diverging is the failure that makes the
    guard and the tool disagree about what CI reports — which is exactly the
    class of bug both exist to catch.

    Only the parsing has to match. The return types differ on purpose: the guard
    returns ``{job id: context}`` so a failure can name the job whose ``name:``
    moved, and the tool returns just the contexts it needs to require.
    """

    def test_same_contexts_for_the_same_input(self, boot, template_guard, tmp_path):
        workflow = tmp_path / "w.yml"
        workflow.write_text(WORKFLOW_SHAPES, encoding="utf-8")
        assert list(template_guard._job_contexts(workflow).values()) == boot._job_contexts(workflow)

    def test_same_contexts_for_this_repos_own_workflow(self, boot, template_guard):
        # The synthetic fixture above covers the shapes; this covers the file that
        # actually decides whether this repo's branch protection is attached.
        workflow = REPO_ROOT / ".github" / "workflows" / "ci.yml"
        assert list(template_guard._job_contexts(workflow).values()) == boot._job_contexts(workflow)

    def test_same_comment_stripping(self, boot, template_guard):
        # `#` opens a comment at line start or after whitespace only, per YAML, so
        # a value containing one is truncated on that pattern and not on every `#`.
        for value in ("A name  # trailing", "Issue #42 in the name", "plain", "a#b", "#lead"):
            assert template_guard._strip_comment(value) == boot._strip_comment(value), value


# ── The ruleset body ──────────────────────────────────────────────────────────

class TestRulesetBody:

    def test_the_template_carries_no_required_checks(self):
        # The one field that cannot be baseline: the contexts are one repo's CI
        # job names. It is injected, so it must not be sitting in the file.
        template = json.loads((REPO_ROOT / "rulesets" / "main.json").read_text(encoding="utf-8"))
        assert "required_status_checks" not in [r["type"] for r in template["rules"]]

    def test_no_contexts_omits_the_rule_entirely(self, boot):
        # Rather than writing it empty. An empty rule looks like protection in
        # the settings UI and in a diff, and gates nothing — strictly worse than
        # an absent one, which at least reads as unfinished.
        body = boot._ruleset_body("main", [])
        assert "required_status_checks" not in [r["type"] for r in body["rules"]]

    def test_no_contexts_reproduces_the_template_verbatim(self, boot):
        template = json.loads((REPO_ROOT / "rulesets" / "main.json").read_text(encoding="utf-8"))
        assert boot._ruleset_body("main", []) == template

    def test_contexts_are_appended_with_strict_mode_on(self, boot):
        body = boot._ruleset_body("main", ["One", "Two"])
        rule = body["rules"][-1]
        assert rule["type"] == "required_status_checks"
        params = rule["parameters"]
        assert params["required_status_checks"] == [{"context": "One"}, {"context": "Two"}]
        # Without strict mode a PR merges on checks that ran against an older
        # base, so a job added while a branch was open never runs on it.
        assert params["strict_required_status_checks_policy"] is True

    def test_branch_moves_both_the_name_and_the_condition(self, boot):
        body = boot._ruleset_body("release", [])
        assert body["conditions"]["ref_name"]["include"] == ["refs/heads/release"]
        # The name too, or a ruleset protecting `release` is called "main" in the
        # settings UI, and the lookup-by-name that makes this idempotent would
        # collide with an actual `main` ruleset.
        assert body["name"] == "release"

    def test_the_baseline_rules_survive(self, boot):
        types = [r["type"] for r in boot._ruleset_body("main", ["X"])["rules"]]
        assert types == ["deletion", "non_fast_forward", "pull_request", "required_status_checks"]

    def test_a_solo_repo_requires_no_approvals(self, boot):
        # Zero is deliberate and is the one parameter whose right value depends on
        # the team rather than the repo: GitHub does not let an author approve
        # their own PR, so any positive count on a one-maintainer repo means no
        # human PR can ever merge. Raise it when there is a second person.
        rule = next(r for r in boot._ruleset_body("main", [])["rules"] if r["type"] == "pull_request")
        assert rule["parameters"]["required_approving_review_count"] == 0

    def test_the_bypass_is_an_emergency_merge_not_a_direct_push(self, boot):
        actors = boot._ruleset_body("main", [])["bypass_actors"]
        assert len(actors) == 1
        actor = actors[0]
        # 5 is the built-in Repository admin role; the ids are undocumented, and a
        # wrong one is a silent widening (2 is Write).
        assert (actor["actor_type"], actor["actor_id"]) == ("RepositoryRole", 5)
        # `always` would also permit pushing straight to the protected branch with
        # no PR, and would skip the deletion and force-push rules with it.
        assert actor["bypass_mode"] == "pull_request"


# ── Idempotency ───────────────────────────────────────────────────────────────

class TestComparisonIsOrderInsensitive:
    """
    Regression test for the bug a read-only ``--dry-run`` against a live repo
    caught: the API returns required checks in the order the workflow declares
    the jobs, so a tool that sends them in any other order compares unequal to
    what it just wrote and rewrites the ruleset on every single run.

    Every list in a ruleset is a set as far as GitHub is concerned.
    """

    def test_reordered_contexts_compare_equal(self, boot):
        a = boot._ruleset_body("main", ["Alpha", "Beta", "Gamma"])
        b = boot._ruleset_body("main", ["Gamma", "Alpha", "Beta"])
        assert a != b, "the bodies really are different objects"
        assert boot._settable(a) == boot._settable(b)

    def test_reordered_rules_compare_equal(self, boot):
        a = boot._ruleset_body("main", ["X"])
        b = dict(a, rules=list(reversed(a["rules"])))
        assert boot._settable(a) == boot._settable(b)

    def test_genuinely_different_rulesets_still_differ(self, boot):
        # The canonicalizer must not flatten real differences into equality — a
        # false "already matches" would silently skip a needed update.
        base = boot._ruleset_body("main", ["X"])
        assert boot._settable(base) != boot._settable(boot._ruleset_body("main", ["Y"]))
        assert boot._settable(base) != boot._settable(boot._ruleset_body("main", ["X", "Y"]))
        assert boot._settable(base) != boot._settable(boot._ruleset_body("release", ["X"]))

        unstrict = json.loads(json.dumps(base))
        for rule in unstrict["rules"]:
            if rule["type"] == "required_status_checks":
                rule["parameters"]["strict_required_status_checks_policy"] = False
        assert boot._settable(base) != boot._settable(unstrict)

        no_force_push = dict(
            base, rules=[r for r in base["rules"] if r["type"] != "non_fast_forward"]
        )
        assert boot._settable(base) != boot._settable(no_force_push)

    def test_server_assigned_fields_are_ignored(self, boot):
        # A live ruleset carries an id, timestamps, and `current_user_can_bypass`.
        # None is configuration, and comparing them would report a change on
        # every run — `current_user_can_bypass` especially, since it is a property
        # of the calling token rather than of the ruleset.
        body = boot._ruleset_body("main", ["X"])
        live = dict(
            body,
            id=19969354,
            node_id="RRS_abc",
            created_at="2026-07-28T00:00:00Z",
            updated_at="2026-07-29T00:00:00Z",
            current_user_can_bypass="always",
            _links={"self": {"href": "..."}},
        )
        assert boot._settable(body) == boot._settable(live)


class TestPlanAndVisibilityGating:
    """
    Rulesets are gated by plan *and* visibility, which is easy to misread as a
    permissions problem and then chase with scopes that will never help.

    A private repo on a free personal account can have neither a ruleset nor a
    classic protected branch. The 403 says "Upgrade to GitHub Pro or make this
    repository public", and the tool has to relay that rather than the raw error.
    """

    PRO_403 = (
        "gh: Upgrade to GitHub Pro or make this repository public to enable "
        "this feature. (HTTP 403)"
    )

    def test_the_gating_403_is_recognised(self, boot, monkeypatch):
        monkeypatch.setattr(boot, "_gh", lambda *a, **k: (1, "", self.PRO_403))
        with pytest.raises(boot.RulesetsUnavailable):
            boot._gh_json("repos/o/r/rulesets")

    def test_other_ruleset_failures_are_not_swallowed(self, boot, monkeypatch):
        # A real error must still stop the run. Treating every ruleset 4xx as
        # "unavailable by plan" would report an unprotected branch as expected.
        monkeypatch.setattr(boot, "_gh", lambda *a, **k: (1, "", "gh: Not Found (HTTP 404)"))
        with pytest.raises(SystemExit):
            boot._gh_json("repos/o/r/rulesets")

    def test_the_same_403_elsewhere_is_not_reinterpreted(self, boot, monkeypatch):
        # Scoped to the rulesets endpoint. The message is specific to it, and a
        # 403 on repo settings means something else entirely.
        monkeypatch.setattr(boot, "_gh", lambda *a, **k: (1, "", self.PRO_403))
        with pytest.raises(SystemExit):
            boot._gh_json("repos/o/r")


class TestOutputIsConsoleSafe:

    def test_printed_strings_are_ascii(self):
        """
        Every string this script prints must be ASCII.

        Windows consoles default to a legacy code page, and a non-ASCII character
        in printed output is mangled at best and a ``UnicodeEncodeError`` at
        worst — from a tool whose whole job is to report clearly what it did and
        did not change. An em dash in the plan-gating message did exactly that.

        Docstrings and comments are exempt: they are never written to a stream.
        """
        import re

        source = (REPO_ROOT / "scripts" / "bootstrap-repo.py").read_text(encoding="utf-8")
        offenders = set()
        for match in re.finditer(r"(print|sys\.exit|parser\.error)\((.*?)\n?\)", source, re.DOTALL):
            offenders |= {c for c in match.group(2) if ord(c) > 127}
        assert not offenders, (
            "non-ASCII characters in printed output: "
            + ", ".join(f"{c!r} ({hex(ord(c))})" for c in sorted(offenders))
        )


class TestContextsAreDedupedInOrder:

    def test_duplicates_collapse_and_order_is_preserved(self, boot, tmp_path):
        # Order carries no meaning to GitHub, but preserving the workflow's order
        # makes a newly created ruleset read top-to-bottom like CI runs.
        workflow = tmp_path / "w.yml"
        workflow.write_text(WORKFLOW_SHAPES, encoding="utf-8")
        contexts = boot._job_contexts(workflow)
        assert list(dict.fromkeys(contexts + contexts)) == EXPECTED_SHAPES
