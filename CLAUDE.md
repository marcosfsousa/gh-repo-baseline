# CLAUDE.md

## What this repo is

Portable repository configuration, extracted from `project-ironhack-scienceq` on
2026-07-30. Read `README.md` first for what it does.

## Containment: the source repo is read-only

`project-ironhack-scienceq` is where these decisions were made. It is **not** a
consumer of this repo and must not become one as a side effect of work here.

- Never edit, branch, commit in, or add a worktree to that repo while working in
  this one. It is a copy source.
- Its `main` is protected and requires a pull request plus four green checks. Do
  not treat that as the safety net — the rule is not to touch it at all.

Two consequences that look like bugs and are not:

**The API plumbing is duplicated, not shared.** `scripts/bootstrap-repo.py`
reimplements the ruleset lookup that `scripts/export_ruleset.py` does over there.
Sharing it would mean refactoring a working file in that repo in service of this
one, and it would not work cleanly anyway: that script hardcodes its own repo and
is written around being the record of one named ruleset on one repo. Copy the ~20
lines and accept the fork.

**The workflow parser is duplicated within this repo too.** `bootstrap-repo.py`
and `templates/tests/test_required_checks.py` both parse job names by
indentation. The template gets copied *out* into other repos, so it has to stand
alone with no import from here. Change one, change both.

Making the source repo a consumer of this one — a pointer in its `CLAUDE.md`,
replacing its `export_ruleset.py` with the generic tool — is a separate,
explicitly-requested change, judged on its own. Never bundled into work here.

## Writing the templates

The value in these files is the comments. The reasoning was originally anchored
to the source repo's issue numbers (`#37`, `#87`, `#90`), and those anchors are
meaningless here and actively misleading — they read as authoritative and point
at nothing.

So: **keep the argument, drop the anchor.** State the failure mode in the general
case ("a required check matched by string that names no job leaves the pull
request pending, not red") rather than the incident. Where a specific incident is
the only convincing evidence, describe it without the number and tell the reader
to look for it in their own history.

Do not add issue references to this repo's own files either. Nothing here has an
issue tracker yet.

## The guard exists twice. Edit the template.

`templates/tests/test_required_checks.py` is the copyable one.
`tests/test_required_checks.py` is the live copy guarding this repo's own
ruleset — the baseline applied to itself.

They are byte-identical below their headers, and
`TestTheLiveGuardMatchesTheTemplate` in `tests/test_guard.py` enforces it. The
drift is asymmetric and that is why it is a test: a fix applied only to the live
copy leaves this repo green while shipping the broken version to every repo that
copies the template.

So the order is always **edit the template, re-copy over the live one, restore
only the header**.

`pytest.ini` excludes `templates/` from collection. Files under it resolve
`.github` paths relative to their own grandparent, which is a repo root only after
they have been copied out.

## The parser exists twice too

`scripts/bootstrap-repo.py` and the guard both parse workflow job names by
indentation. The template cannot import from `scripts/`, because it gets copied
into repos that have no `scripts/`. `TestBothParserCopiesAgree` holds them to the
same answer on the same input, including on this repo's real `ci.yml`. Only the
parsing must match — the return types differ deliberately.

## Remote

`https://github.com/marcosfsousa/gh-repo-baseline`, private, created 2026-07-30.
Not public: nothing in it is secret, but it encodes how these repos are protected
and the templates still carry `REPLACE-` placeholders. Going public is a decision
to ask about, not a default.

**`main` is NOT protected, and cannot be while the repo is private.** Rulesets are
gated by plan and visibility together: on a free personal account a private repo
can have neither a ruleset nor a classic protected branch. `.github/rulesets/main.json`
is committed and correct — it is the record and the body that would be applied —
but nothing enforces it.

So `Tests (pytest)` runs on every PR and can go red without blocking a merge. Treat
the discipline as manual until the repo goes public or the plan changes: read CI
before merging, because nothing will stop you.

`delete_branch_on_merge` and both Dependabot toggles *were* applied — those are
not plan-gated. The root commit was pushed directly, since there was no base to
open a PR against.

## Verifying a change to bootstrap-repo.py

The suite is offline and covers only the pure part: the parser, the ruleset body,
the comparison. It never exercises the `gh` wrappers, so a green run does not
prove the script can authenticate or that the endpoints still exist.

The other half is a read-only `--dry-run` against a real repo, which performs
only GETs. Against a repo already configured to the baseline it must print
`Nothing to change.` — if it reports a pending ruleset update on a repo that
already matches, that is the order-insensitivity bug in `_canonical`, and
`TestComparisonIsOrderInsensitive` is the regression test for it.
