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

import ast
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

    def test_a_name_this_parser_refuses_exits_naming_the_job(self, boot, tmp_path):
        # The refusal has to identify the job, or a person reading it has to find
        # the offending line themselves in a workflow that may have many.
        workflow = tmp_path / "w.yml"
        workflow.write_text("jobs:\n  folded:\n    name: >\n      A name\n", encoding="utf-8")
        with pytest.raises(SystemExit, match="folded"):
            boot._job_contexts(workflow)


class TestScalarNames:
    """
    The shapes a plain comment-strip gets wrong.

    All three fail *silently* without this: the derived context does not match
    what the job reports, so the required check never arrives and the pull request
    sits pending rather than red. Handled where the right answer is cheap and
    unambiguous, refused where it is neither — see the docstring on ``_scalar``.
    """

    def test_a_hash_inside_quotes_is_not_a_comment(self, boot):
        # The finding that motivated this: `#` opens a comment only outside
        # quotes, and truncating here derives `Build` for a job reporting
        # `Build #1` — a required check nothing ever reports.
        assert boot._scalar("'Build #1'") == "Build #1"
        assert boot._scalar('"Build #1"') == "Build #1"

    def test_a_hash_outside_quotes_still_opens_a_comment(self, boot):
        assert boot._scalar("UI tests (Playwright)  # the slow one") == "UI tests (Playwright)"

    def test_a_doubled_quote_is_an_escaped_quote(self, boot):
        # YAML's escape inside a single-quoted scalar. Taking the first quote as
        # the terminator would derive `Bob`.
        assert boot._scalar("'Bob''s job'") == "Bob's job"

    def test_trailing_comments_after_a_quoted_scalar_are_dropped(self, boot):
        assert boot._scalar("'Build #1'  # still a comment") == "Build #1"

    def test_a_name_that_denotes_nothing_falls_back_to_the_job_id(self, boot, tmp_path):
        # `name:` followed only by a comment is null in YAML, so GitHub reports
        # the job id. Deriving an empty context instead would send
        # `{"context": ""}` — a required check no job can ever report.
        assert boot._scalar("# just a comment") is None
        assert boot._scalar("''") is None
        workflow = tmp_path / "w.yml"
        workflow.write_text("jobs:\n  build:\n    name: # replaced later\n", encoding="utf-8")
        assert boot._job_contexts(workflow) == ["build"]

    @pytest.mark.parametrize("value", ["|", ">", ">-", "|+", "|2", "> # c"])
    def test_block_scalars_are_refused(self, boot, value):
        # Refused rather than folded: correct folding is chomping and indent
        # indicators and blank-line rules, duplicated across two copies of this
        # parser that no test can prove right — only prove equal.
        with pytest.raises(ValueError, match="block scalar"):
            boot._scalar(value)

    def test_a_greater_than_inside_a_name_is_not_a_block_scalar(self, boot):
        # The refusal keys on the indicator shape, not on the character.
        assert boot._scalar("Backend > frontend") == "Backend > frontend"
        assert boot._scalar("|pipe| in a name") == "|pipe| in a name"

    def test_unreadable_quoting_is_refused_rather_than_guessed_at(self, boot):
        with pytest.raises(ValueError, match="never closes"):
            boot._scalar("'unterminated")
        with pytest.raises(ValueError, match="cannot read"):
            boot._scalar('"a \\" escape"')


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

    def test_a_repo_named_rulesets_is_not_mistaken_for_the_endpoint(self, boot, monkeypatch):
        # `"rulesets" in path` matched the plain repository GET for this repo.
        # That call is made from step_repo_settings, outside the try/except in
        # main that handles this exception, so it surfaced as a traceback rather
        # than the [skip] it was meant to produce.
        monkeypatch.setattr(boot, "_gh", lambda *a, **k: (1, "", self.PRO_403))
        with pytest.raises(SystemExit):
            boot._gh_json("repos/o/rulesets")

    def test_the_by_id_endpoint_is_still_recognised(self, boot, monkeypatch):
        # _find_ruleset re-reads the one match in full, and the update is a PUT to
        # the same path. Both are gated the same way.
        monkeypatch.setattr(boot, "_gh", lambda *a, **k: (1, "", self.PRO_403))
        with pytest.raises(boot.RulesetsUnavailable):
            boot._gh_json("repos/o/r/rulesets/123")

    def test_the_gate_is_detected_on_the_write_too(self, boot, monkeypatch):
        # Detecting it on reads alone leaves the case where the list endpoint
        # answers but the write is refused: exit 1 with a raw 403, instead of the
        # [skip] and exit 2 that say what to do about it.
        monkeypatch.setattr(boot, "_find_ruleset", lambda repo, name: None)
        monkeypatch.setattr(boot, "_gh", lambda *a, **k: (1, "", self.PRO_403))
        with pytest.raises(boot.RulesetsUnavailable):
            boot.step_ruleset("o/r", "main", ["X"], boot.Reporter(dry_run=False))

    def test_a_refused_write_does_not_report_success_first(self, boot, monkeypatch, capsys):
        # The report line used to be printed before the write was attempted, so a
        # refusal printed `[set] ruleset created` and then died — a claim that the
        # thing happened, one line above the error saying it had not.
        monkeypatch.setattr(boot, "_find_ruleset", lambda repo, name: None)
        monkeypatch.setattr(boot, "_gh", lambda *a, **k: (1, "", "gh: Not Found (HTTP 404)"))
        report = boot.Reporter(dry_run=False)
        with pytest.raises(SystemExit):
            boot.step_ruleset("o/r", "main", ["X"], report)
        assert "[set]" not in capsys.readouterr().out
        assert report.changed == 0


_OUTPUT_CALLS = {"print", "sys.exit", "parser.error"}


def _called_name(func) -> str | None:
    """``print``, ``sys.exit``, ``parser.error`` — the dotted form, or None."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    return None


def _non_ascii_in_output(source: str) -> set[str]:
    """
    Every non-ASCII character in a string that reaches a call writing to a stream.

    Walks the AST rather than matching source text. The regex this replaced was
    ``(print|sys\\.exit|parser\\.error)\\((.*?)\\n?\\)`` — non-greedy, so it
    stopped at the *first* ``)`` in the call and scanned about a tenth of the
    file. The plan-gating message it was written for contains
    ``(gh repo edit --visibility public)``, so everything after that line in the
    very print statement whose em dash caused the original bug went unread. A
    guard that does not cover its own regression is not a regression test.

    Docstrings and comments stay exempt for free: a comment is not in the tree at
    all, and a docstring is a bare expression rather than an argument to a call.
    f-strings are covered, which the regex managed only by accident.
    """
    offenders: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or _called_name(node.func) not in _OUTPUT_CALLS:
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                offenders |= {c for c in child.value if ord(c) > 127}
    return offenders


class TestOutputIsConsoleSafe:
    """
    Every string this script prints must be ASCII.

    Windows consoles default to a legacy code page, and a non-ASCII character in
    printed output is mangled at best and a ``UnicodeEncodeError`` at worst — from
    a tool whose whole job is to report clearly what it did and did not change. An
    em dash in the plan-gating message did exactly that.
    """

    def test_printed_strings_are_ascii(self):
        offenders = _non_ascii_in_output(
            (REPO_ROOT / "scripts" / "bootstrap-repo.py").read_text(encoding="utf-8")
        )
        assert not offenders, (
            "non-ASCII characters in printed output: "
            + ", ".join(f"{c!r} ({hex(ord(c))})" for c in sorted(offenders))
        )

    def test_the_detector_reads_past_the_first_paren(self):
        # The exact shape the old regex missed: a parenthesised aside early in a
        # multi-line print, and the offending character after it.
        source = (
            "def f():\n"
            "    print(\n"
            '        "  make the repository public "\n'
            '        "(gh repo edit --visibility public)\\n"\n'
            '        "  everything above this line — applied\\n"\n'
            "    )\n"
        )
        assert _non_ascii_in_output(source) == {"—"}

    def test_docstrings_and_comments_stay_exempt(self):
        source = (
            'def f():\n'
            '    """A docstring — never written to a stream."""\n'
            '    # A comment — likewise.\n'
            '    value = "not printed — either"\n'
            '    print("clean")\n'
        )
        assert _non_ascii_in_output(source) == set()

    def test_f_strings_are_covered(self):
        assert _non_ascii_in_output('print(f"{x} — y")') == {"—"}

    def test_calls_that_do_not_write_are_ignored(self):
        assert _non_ascii_in_output('log.info("—")') == set()


class TestContextsAreDedupedInOrder:

    def test_duplicates_collapse_and_order_is_preserved(self, boot, tmp_path):
        # Order carries no meaning to GitHub, but preserving the workflow's order
        # makes a newly created ruleset read top-to-bottom like CI runs.
        workflow = tmp_path / "w.yml"
        workflow.write_text(WORKFLOW_SHAPES, encoding="utf-8")
        contexts = boot._job_contexts(workflow)
        assert list(dict.fromkeys(contexts + contexts)) == EXPECTED_SHAPES
