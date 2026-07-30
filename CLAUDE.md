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

## Templates are not collected by pytest

`pytest.ini` excludes `templates/`. Files under it resolve `.github` paths
relative to their own grandparent, which is only a repo root after they have been
copied out. A bare `pytest` here would collect the guard test and fail on paths
that were never meant to exist.

If you add a real test suite, it goes in `tests/` at the root, not under
`templates/`.

## No remote yet

Local git only, by decision. Do not `gh repo create` or push without being asked.
