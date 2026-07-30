# gh-repo-baseline

Repository configuration worth having on every repo, in a form that can be
applied to one that already exists.

Two things live here, and the split is the point:

| | |
|---|---|
| `scripts/bootstrap-repo.py` + `rulesets/main.json` | Config that is **not a file** — branch protection, Dependabot toggles, repo settings. Applied through the API. |
| `templates/` | Files to copy in, then edit. |

## Why not a template repository

A template repository copies files and carries no settings. A repo created from
one arrives with the workflows and none of the enforcement — which is the exact
failure worth preventing, because an unprotected branch with a green tick looks
identical to a protected one.

It also only works at creation time. Most repos that need this already exist.

## Usage

```bash
# See what would change. Writes nothing.
python scripts/bootstrap-repo.py OWNER/REPO --dry-run

# Apply, deriving the required checks from the target repo's CI workflow.
python scripts/bootstrap-repo.py OWNER/REPO \
    --checks-from ../OWNER-REPO/.github/workflows/ci.yml

# Or name them directly.
python scripts/bootstrap-repo.py OWNER/REPO --check "Backend tests (pytest)"

# A repo with no CI yet: wire the branch protection now, add checks later.
python scripts/bootstrap-repo.py OWNER/REPO
```

Needs `gh` authenticated with admin rights on the target — reading a ruleset
needs them, not just writing one.

Every step is idempotent and reports `[ok]` or `[set]`, so re-running is how you
correct drift rather than something to avoid. There is no bootstrap window this
has to land inside.

### What it sets

- `delete_branch_on_merge`
- Dependabot **alerts** and **security updates** — two separate toggles, neither
  expressible in `dependabot.yml`
- the `main` ruleset from `rulesets/main.json`, with the required-check contexts
  injected per repo

Not per-repo secrets, variables, or the Actions policy. Those are project
decisions, not baseline.

## It is applied to itself

This repo runs the baseline on its own `main`: `.github/rulesets/main.json` is the
committed record, `Tests (pytest)` is the required check, and
`tests/test_required_checks.py` — the live copy of the guard template — holds the
two together. Rename the CI job and the suite fails instead of the branch quietly
unprotecting itself.

```bash
pip install -r requirements-dev.txt
pytest                                    # 48 tests
```

What the suite actually asserts, since "the guard passes" is nearly worthless on
its own:

- **`tests/test_bootstrap.py`** — the parser across every shape YAML allows, the
  ruleset body (retargeting, strict mode, omit-vs-empty), and that comparison is
  order-insensitive. That last one is a regression test: the API returns required
  checks in workflow-declaration order, so comparing raw made the tool report a
  phantom change and rewrite the ruleset on every run.
- **`tests/test_guard.py`** — assembles a throwaway repo out of `templates/`,
  confirms the guard passes, then breaks it **eight ways** and confirms the guard
  fails on *exactly* the intended tests and no others: job renamed, seam added
  but not required, rule dropped, rule emptied, strict mode off, CI trigger moved
  off the protected branch, force-push unblocked, ruleset retargeted.

  "And no others" matters as much as the rest — a mutation that trips six tests
  means the message someone reads won't name what broke.
- **Both duplications held honest** — the two copies of the guard, and the two
  copies of the workflow parser.

Offline by construction: nothing touches the network, so no secrets are
configured. The tradeoff is explicit — a green run proves the script would send
the right body, not that it can authenticate. `--dry-run` against a real repo is
the other half, and stays manual because it needs admin credentials CI should not
have.

## Copying the templates

```
templates/ci.yml                        -> .github/workflows/ci.yml
templates/dependabot.yml                -> .github/dependabot.yml
templates/tests/test_required_checks.py -> tests/test_required_checks.py
rulesets/main.json                      -> .github/rulesets/main.json
```

Always copy the **template**, never `tests/test_required_checks.py`. That one is
this repo's live copy and differs only in its header; the two are held identical
below it by a test, so editing the live one and not the template ships the bug
outward while keeping this repo green.

All four need editing after the copy; none is drop-in. `ci.yml` ships one
placeholder job that asserts nothing, `dependabot.yml` has `REPLACE-` package
names, and the test file has three constants to check at the top.

The ruleset copy is a **record**, not enforcement — GitHub reads repository
settings, not a path in your tree. It is committed so the config is reviewable
and so the test has something to read.

## The one invariant worth understanding

Required status checks are matched by **string** against the names jobs report,
and GitHub does not verify that a required context corresponds to anything.

A context naming a job that does not exist never reports. A rule waiting on a
check that never arrives is indistinguishable from one that has not run yet, so
the pull request sits **pending** rather than failing — which reads as "still
working" instead of "misconfigured", at the moment it matters most.

Three things follow, and they are why this repo is shaped the way it is:

1. **Job `name:` values are a contract.** Renaming one silently detaches the rule.
2. **An empty `required_status_checks` rule is worse than none.** It looks like
   protection in the settings UI and in a diff. `bootstrap-repo.py` omits the
   rule rather than writing it empty.
3. **Copy the guard test.** `templates/tests/test_required_checks.py` holds the
   job names and the required contexts equal in both directions, so a rename
   fails the suite instead of quietly unprotecting the branch. Without it,
   nothing in the repo notices.

## Provenance

Extracted 2026-07-30 from `project-ironhack-scienceq`, which is where these
decisions were made and paid for. That repo is **not** a consumer of this one and
was not modified to produce it — the ~20 lines of shared API plumbing are
duplicated rather than factored out, deliberately, so neither repo depends on the
other. See `CLAUDE.md`.

Anything specific to that project — Cloud Build deploy triggers, a Dockerfile
COPY allowlist, an imports-are-declared guard — was left behind. Those are the
most valuable checks it has and the least portable.

## If these repos are ever in an organization

Stop using the script. Org-level rulesets target many repositories at once by
name pattern or custom property, which is strictly better than a per-repo tool
and removes the drift it exists to correct. The script is the personal-account
substitute for a feature organizations already have.
