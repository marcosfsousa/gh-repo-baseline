#!/usr/bin/env python3
# bootstrap-repo.py
# -----------------
"""
Apply this baseline's repository configuration to one GitHub repo.

    python scripts/bootstrap-repo.py OWNER/REPO --checks-from path/to/ci.yml
    python scripts/bootstrap-repo.py OWNER/REPO --check "Backend tests (pytest)"
    python scripts/bootstrap-repo.py OWNER/REPO --dry-run

Every step is idempotent and reports whether it changed anything, so this runs
against a repo created five minutes ago or five years ago, and re-runs to
correct drift. That is the whole design goal: there is no "bootstrap window"
this has to land inside.

Why a script rather than a template repository
----------------------------------------------

A template repository copies *files*. None of what this script sets is a file:
branch protection lives in repository settings, and Dependabot alerts and
security updates are toggles under Settings -> Code security. A repo created
from a template arrives with the workflows and none of the enforcement, which
is the failure this exists to prevent -- a green tick from a rule that applies
to nothing.

An organization can do better: org-level rulesets target many repos at once by
name pattern or custom property, and are strictly preferable to this script
where they are available. They are an organization feature. On a personal
account there is no equivalent, and this script is the substitute.

What this cannot do
-------------------

**Protect a private repository on a free personal account.** Rulesets are gated by
plan *and* visibility, and classic protected branches are gated the same way, so
on that combination there is no branch protection available at all -- the API
answers 403 with "Upgrade to GitHub Pro or make this repository public". Make the
repo public, upgrade the plan, or accept an unprotected branch; see
``RulesetsUnavailable``. Every other step still applies.

Per-repo secrets and variables, and the Actions policy for the repository, are
deliberately out of scope -- they are not baseline, they are per-project, and a
tool that set them would need to be told what to set.

Nothing here verifies that CI *passes*. It wires the gate; the gate's contents
are the repo's own problem.

The required-status-check contexts
---------------------------------

This is the one part of the ruleset that cannot be baseline. Required checks
are matched by **string** against the names jobs report, and GitHub does not
verify that a required context corresponds to anything: a context naming a job
that does not exist simply never reports, and a rule waiting on a check that
never arrives is indistinguishable from one that has not run yet. The pull
request sits pending rather than failing, which reads as "still working"
instead of "misconfigured".

So contexts are supplied per repo, and when none are supplied the
required_status_checks rule is **omitted entirely** rather than written empty.
An empty rule is the worst of the three outcomes: it looks like protection in
the settings UI and in a diff, and gates nothing.

``--checks-from`` derives them from a workflow file the same way
``templates/tests/test_required_checks.py`` does -- by indentation, taking a
job's ``name:`` where it has one and its job id otherwise, which is what GitHub
reports. That parser is deliberately duplicated in the two places rather than
shared: the template file gets copied *out* of this repo into others, so it has
to stand alone with no import from here. Two copies that agree is the cost of
the template being self-contained; if you change one, change both.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
RULESET_TEMPLATE = REPO_ROOT / "rulesets" / "main.json"

# The keys the rulesets API accepts on a write. Anything else the API returns is
# server-assigned identity or viewer-relative state; see rulesets/README.md.
SETTABLE = ("name", "target", "enforcement", "bypass_actors", "conditions", "rules")


# -- gh plumbing ---------------------------------------------------------------

def _gh(args: list[str], body: str | None = None) -> tuple[int, str, str]:
    result = subprocess.run(
        ["gh", "api", *args],
        capture_output=True,
        text=True,
        check=False,
        input=body,
    )
    return result.returncode, result.stdout, result.stderr


class RulesetsUnavailable(Exception):
    """
    The rulesets API is gated by plan **and** visibility, not just by permission.

    On a free personal account a *private* repository cannot have rulesets, and
    cannot have classic protected branches either: both are paid features there.
    The API answers 403 with "Upgrade to GitHub Pro or make this repository
    public", which is a statement about the plan rather than about the token — so
    re-authenticating, adding scopes, or checking admin rights will not fix it,
    and this is raised to say so instead of letting a raw error suggest otherwise.

    It is not fatal to the whole run. The repository settings and the Dependabot
    toggles are unaffected and still apply, so those steps stand and only this one
    is skipped — loudly, and with a non-zero exit, because a repo that silently
    ends up unprotected is the failure this tool exists to prevent.
    """


# The rulesets endpoints, matched by shape rather than by substring.
#
# `"rulesets" in path` also matches `repos/OWNER/rulesets` -- the plain repository
# GET for a repo that happens to be named `rulesets`. That call is made from
# step_repo_settings, outside the try/except in main that exists to handle this
# exception, so raising there is an uncaught traceback rather than the [skip] it
# was meant to produce.
#
# Suffix matching does not separate the two: `repos/OWNER/rulesets` and
# `repos/OWNER/REPO/rulesets` both end in `/rulesets`. The segment count is what
# distinguishes them, so the whole shape is matched. The optional trailing id is
# the by-id read in _find_ruleset and the PUT that updates one.
_RULESETS_ENDPOINT = re.compile(r"repos/[^/]+/[^/]+/rulesets(?:/\d+)?")


def _raise_if_plan_gated(path: str, err: str) -> None:
    """
    Translate the plan/visibility 403 into ``RulesetsUnavailable``.

    Called on reads *and* writes. Detecting it on reads alone leaves the case
    where the list endpoint answers but the write is refused: the run exits 1
    with a raw 403 instead of the [skip] and exit 2 that say what to do about it.
    """
    if _RULESETS_ENDPOINT.fullmatch(path) and "Upgrade to GitHub Pro" in err:
        raise RulesetsUnavailable(err.strip())


def _gh_json(path: str) -> object:
    code, out, err = _gh([path])
    if code != 0:
        _raise_if_plan_gated(path, err)
        sys.exit(f"gh api {path} failed:\n{err.strip()}")
    return json.loads(out)


def _status(path: str) -> int:
    """
    The HTTP status of a GET, for the two endpoints that answer in status codes
    rather than bodies.

    ``vulnerability-alerts`` returns 204 when enabled and 404 when not -- there
    is no body to read either way, so ``gh``'s exit code alone cannot separate
    "disabled" from "no such repo" or "token lacks the scope". Reading the
    status line does.
    """
    code, out, err = _gh(["--include", path])
    match = re.search(r"^HTTP/[\d.]+\s+(\d{3})", out, re.MULTILINE)
    if match:
        return int(match.group(1))
    if code != 0:
        sys.exit(f"gh api {path} failed with no parseable status:\n{err.strip()}")
    sys.exit(f"gh api --include {path} returned no status line:\n{out[:400]}")


# -- reading job names out of a workflow --------------------------------------

def _strip_comment(value: str) -> str:
    """YAML comment rule: `#` opens a comment at line start or after whitespace."""
    return re.split(r"(?:^|\s)#", value, maxsplit=1)[0].strip()


def _scalar(value: str) -> str | None:
    """
    What a job's ``name:`` denotes, or ``None`` when it denotes nothing and the
    job id stands as the context.

    Stripping comments unconditionally is wrong for three shapes, and wrong here
    is silent: a context that does not match what the job reports never arrives,
    and a rule waiting on a check that never arrives leaves the pull request
    pending rather than red.

    ``name: 'Build #1'``
        A quoted scalar. ``#`` opens a comment only outside quotes, so the value
        is read to its closing quote instead of truncated at the hash -- which
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
        right -- only prove equal. A job name is a one-line label, so the shape is
        vanishingly rare and stopping costs nothing.

        The two indicators may appear in **either** order -- ``|2-`` and ``|-2``
        are both valid headers -- so both are matched. Matching only one order
        left ``|2-`` deriving the literal context ``|2-``, which is the silent
        failure this function exists to prevent rather than the loud one.

    ``name: null`` / ``~``
        YAML's null tokens, so the job id stands exactly as it does for a
        comment-only name. Quoted, they are ordinary strings and keep their
        value -- which is why this is checked after the quoted branch, not before.

    Raised as ``ValueError`` rather than exiting, because the copy of this in the
    guard template is a test and has no process to exit. Each caller says what to
    do about it.
    """
    value = value.strip()

    if re.match(r"[|>][-+]?\d*[-+]?(?:\s|$)", value):
        raise ValueError(
            f"`name:` is a block scalar ({value.split()[0]!r}), whose value is on "
            "the lines below it.\nThis parser reads one-line names only. Write "
            "the name inline, or pass the context explicitly with --check."
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


def _job_contexts(workflow: Path) -> list[str]:
    """
    The status-check context every job in ``workflow`` will report.

    Indentation-based, and it has to be: parsing this with PyYAML would make
    the equivalent guard in ``templates/tests/`` need PyYAML in whatever
    manifest the target repo's CI installs, for the benefit of one test. The job
    header is a fixed, shallow shape.

    Step names are ``- name:`` at six spaces and are therefore excluded
    structurally rather than by pattern.

    The copy in ``templates/tests/test_required_checks.py`` returns
    ``{job id: context}`` where this returns just the contexts — it needs the id
    to name the job whose ``name:`` moved, and this only needs the strings to
    require. The parsing is what must stay identical between the two, not the
    return type.
    """
    contexts: dict[str, str] = {}
    lines = workflow.read_text(encoding="utf-8").splitlines()

    try:
        start = next(i for i, line in enumerate(lines) if line.rstrip() == "jobs:")
    except StopIteration:
        sys.exit(f"{workflow} has no top-level `jobs:` block.")

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
            try:
                scalar = _scalar(name.group(1))
            except ValueError as exc:
                sys.exit(f"{workflow}, job {current!r}: {exc}")
            # None means the name denotes nothing, so the job id already recorded
            # above stands -- which is what GitHub reports for such a job.
            if scalar is not None:
                contexts[current] = scalar

    if not contexts:
        sys.exit(
            f"{workflow} has a `jobs:` block but no jobs were collected.\n"
            "Deriving zero contexts would silently produce a ruleset that "
            "requires nothing, so this stops instead."
        )
    return list(contexts.values())


# -- the ruleset body ---------------------------------------------------------

def _ruleset_body(branch: str, contexts: list[str]) -> dict:
    body = json.loads(RULESET_TEMPLATE.read_text(encoding="utf-8"))

    # The template is written for `main`; retarget it rather than making the
    # caller edit the file. Both the name and the condition move, so a ruleset
    # protecting `release` is not called "main" in the settings UI.
    ref = f"refs/heads/{branch}"
    body["name"] = branch
    body["conditions"]["ref_name"]["include"] = [ref]

    if contexts:
        body["rules"].append({
            "type": "required_status_checks",
            "parameters": {
                "do_not_enforce_on_create": False,
                "required_status_checks": [{"context": c} for c in contexts],
                # Without this a pull request merges on checks that ran against
                # an older base. That is how a job added to CI while a branch
                # was already open goes un-run on the very branch it was added
                # to protect.
                "strict_required_status_checks_policy": True,
            },
        })
    return body


def _canonical(value):
    """
    Recursively order-insensitive. **For comparison only** — never for a body
    that gets written.

    Every list in a ruleset is a set as far as GitHub is concerned, and the API
    does not return them in the order they were sent. Measured, not assumed:
    a live ruleset returns its required checks in the order the workflow declares
    the jobs, so a tool that sends them in any other order compares unequal to
    what it just wrote, reports a change on every run, and rewrites the ruleset
    forever. That defeats the idempotency this whole script is built on.

    The tradeoff, stated because it is a real one: if a ruleset field ever *does*
    have significant order, this would call two different rulesets equal and skip
    a legitimate update. That direction is the safe one — a missed update leaves
    the previous, working ruleset in place — whereas comparing raw would keep
    rewriting config that was already correct.
    """
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return sorted(
            (_canonical(v) for v in value),
            key=lambda v: json.dumps(v, sort_keys=True),
        )
    return value


def _settable(ruleset: dict) -> str:
    """A stable rendering of the writable fields, for comparison only."""
    return json.dumps(_canonical({k: ruleset.get(k) for k in SETTABLE}), sort_keys=True)


def _find_ruleset(repo: str, name: str) -> dict | None:
    """
    Looked up by name, never by a remembered id. An id is assigned at creation,
    so a ruleset deleted and recreated -- which is how a bad one gets rolled
    back -- comes back with a different one.
    """
    matches = [r for r in _gh_json(f"repos/{repo}/rulesets") if r["name"] == name]
    if len(matches) > 1:
        sys.exit(
            f"{len(matches)} rulesets on {repo} are named {name!r}. This script "
            "cannot tell which one it is meant to be updating; delete or rename "
            "the duplicates."
        )
    # The list endpoint returns a summary. Re-read the one match in full, since
    # the comparison below needs its rules and bypass actors.
    return _gh_json(f"repos/{repo}/rulesets/{matches[0]['id']}") if matches else None


# -- the steps ----------------------------------------------------------------

class Reporter:
    def __init__(self, dry_run: bool):
        self.dry_run = dry_run
        self.changed = 0

    def ok(self, what: str) -> None:
        print(f"  [ok]      {what}")

    def did(self, what: str) -> None:
        self.changed += 1
        verb = "would set" if self.dry_run else "set"
        print(f"  [{verb}] {what}")


def step_repo_settings(repo: str, report: Reporter) -> None:
    """
    ``delete_branch_on_merge`` only, and the omissions are deliberate.

    Merge methods are not set here even though the repo accepts them, because
    the ruleset declares ``allowed_merge_methods`` and the two intersect --
    setting both is two places to keep in sync for one decision.
    """
    live = _gh_json(f"repos/{repo}")
    if live.get("delete_branch_on_merge"):
        report.ok("delete_branch_on_merge already true")
        return
    report.did("delete_branch_on_merge -> true")
    if not report.dry_run:
        code, _, err = _gh(
            ["-X", "PATCH", f"repos/{repo}", "-F", "delete_branch_on_merge=true"]
        )
        if code != 0:
            sys.exit(f"Could not set delete_branch_on_merge:\n{err.strip()}")


def step_dependabot_alerts(repo: str, report: Reporter) -> None:
    """
    Alerts, and then the security updates that act on them. Two separate
    toggles: alerts alone tell you about a CVE, and do not open the pull
    request that fixes it.

    Neither is expressible in ``dependabot.yml``. That file configures the
    scheduled *version* sweep; the immediate *security* pull request is a
    repository setting, which is exactly why a template repository leaves this
    half off and why it is scripted here.
    """
    if _status(f"repos/{repo}/vulnerability-alerts") == 204:
        report.ok("Dependabot alerts already enabled")
    else:
        report.did("Dependabot alerts -> enabled")
        if not report.dry_run:
            code, _, err = _gh(["-X", "PUT", f"repos/{repo}/vulnerability-alerts"])
            if code != 0:
                sys.exit(f"Could not enable Dependabot alerts:\n{err.strip()}")

    fixes = _gh_json(f"repos/{repo}/automated-security-fixes")
    if isinstance(fixes, dict) and fixes.get("enabled") and not fixes.get("paused"):
        report.ok("Dependabot security updates already enabled")
        return
    report.did("Dependabot security updates -> enabled")
    if not report.dry_run:
        code, _, err = _gh(["-X", "PUT", f"repos/{repo}/automated-security-fixes"])
        if code != 0:
            sys.exit(f"Could not enable Dependabot security updates:\n{err.strip()}")


def step_ruleset(repo: str, branch: str, contexts: list[str], report: Reporter) -> None:
    desired = _ruleset_body(branch, contexts)
    existing = _find_ruleset(repo, branch)

    if contexts:
        print(f"  required checks: {', '.join(contexts)}")
    else:
        print(
            "  required checks: none supplied, so the required_status_checks "
            "rule is omitted.\n"
            "                   Re-run with --check/--checks-from once CI "
            "exists; an empty rule\n"
            "                   would look like protection and gate nothing."
        )

    if existing and _settable(existing) == _settable(desired):
        report.ok(f"ruleset {branch!r} already matches (id {existing['id']})")
        return

    if existing:
        what = f"ruleset {branch!r} -> updated (id {existing['id']})"
        path = f"repos/{repo}/rulesets/{existing['id']}"
        method = "PUT"
    else:
        what = f"ruleset {branch!r} -> created"
        path = f"repos/{repo}/rulesets"
        method = "POST"

    if report.dry_run:
        # A dry run reaches here having only read, so it cannot prove the write
        # would be permitted -- see the note on --dry-run in main.
        report.did(what)
        return

    code, _, err = _gh(["-X", method, path, "--input", "-"], body=json.dumps(desired))
    if code != 0:
        _raise_if_plan_gated(path, err)
        sys.exit(f"Could not apply the ruleset:\n{err.strip()}")

    # Reported only once the write has landed. Reporting before it meant a
    # refused write printed `[set] ruleset created` and then died -- a claim that
    # the thing happened, one line above the error saying it had not.
    report.did(what)


# -- entry point --------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply baseline repository configuration to one GitHub repo.",
        epilog="Requires `gh` authenticated with admin rights on the target repo; "
               "reading and writing rulesets both need them.",
    )
    parser.add_argument("repo", metavar="OWNER/REPO")
    parser.add_argument(
        "--branch", default="main",
        help="the branch to protect, and the ruleset's name (default: main)",
    )
    parser.add_argument(
        "--check", action="append", default=[], metavar="CONTEXT",
        help="a required status check, repeatable",
    )
    parser.add_argument(
        "--checks-from", metavar="WORKFLOW",
        help="derive required checks from a workflow file's job names",
    )
    parser.add_argument(
        "--no-ruleset", action="store_true",
        help="apply the repository settings only",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report what would change and write nothing. Performs only GETs, so "
             "it reports what the API permits reading -- a repo whose ruleset "
             "list reads but whose writes are refused still reports pending "
             "changes here",
    )
    args = parser.parse_args()

    if "/" not in args.repo:
        parser.error("repo must be OWNER/REPO")

    contexts = list(args.check)
    if args.checks_from:
        contexts += _job_contexts(Path(args.checks_from))
    # Deduped, order preserved: a newly created ruleset then lists its checks in
    # the order the workflow declares the jobs, which is how the API returns them
    # anyway and makes the committed record read top-to-bottom like CI runs.
    # Order carries no meaning to GitHub -- see _canonical.
    contexts = list(dict.fromkeys(contexts))

    report = Reporter(args.dry_run)
    print(f"{args.repo}{'  (dry run)' if args.dry_run else ''}")

    print("repository settings")
    step_repo_settings(args.repo, report)
    print("code security")
    step_dependabot_alerts(args.repo, report)

    unprotected = False
    if not args.no_ruleset:
        print(f"ruleset on {args.branch}")
        try:
            step_ruleset(args.repo, args.branch, contexts, report)
        except RulesetsUnavailable:
            unprotected = True
            print(
                "  [skip]    rulesets are unavailable on this repository.\n"
                "\n"
                "            GitHub gates them by plan AND visibility: a private "
                "repo on a free\n"
                "            personal account can have neither a ruleset nor a "
                "classic protected\n"
                "            branch. This is not a token problem: more scopes "
                "will not fix it.\n"
                "\n"
                "            Three ways forward:\n"
                "              * make the repository public  "
                "(gh repo edit --visibility public)\n"
                "              * upgrade the account to GitHub Pro\n"
                "              * accept an unprotected branch and pass "
                "--no-ruleset to say so\n"
                "\n"
                "            Everything above this line was applied and is "
                "unaffected."
            )

    if report.changed == 0 and not unprotected:
        print("\nNothing to change.")
    elif args.dry_run:
        print(f"\n{report.changed} change(s) pending. Re-run without --dry-run.")
        # Said in the output rather than only in --help, because this line is
        # what a person reads before believing the run would succeed. A dry run
        # performs no writes, so it cannot establish that the writes are allowed.
        print("Reads only, so this does not prove the writes would be permitted.")
    else:
        print(f"\n{report.changed} change(s) applied.")
        if not unprotected:
            print(
                "Copy templates/tests/test_required_checks.py into the target "
                "repo and commit the\nruleset alongside it, or nothing there will "
                "notice when these strings drift."
            )

    if unprotected:
        # Non-zero even though most of the run succeeded: the branch is not
        # protected, and a bootstrap tool that exits 0 on that is how a repo ends
        # up looking configured while anyone can push to its default branch.
        print(f"\n{args.branch} is NOT protected.")
        sys.exit(2)


if __name__ == "__main__":
    main()
