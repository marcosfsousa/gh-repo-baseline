# The ruleset template

`main.json` is a request body, not a file GitHub reads. Nothing in a repository
tree configures branch protection — the ruleset lives in repository settings and
is applied through the API. `scripts/bootstrap-repo.py` is what applies this
one. JSON cannot carry comments, so the reasoning lives here.

Read this before editing `main.json`. Every field in it was chosen, and three of
them are easy to get wrong in a way that looks correct in a diff.

## What is in the file, and why it ports

**`deletion` and `non_fast_forward`.** Protect the branch itself. These are
frequently mistaken for a constraint on feature branches; they are not. A
ruleset scoped to one ref does not touch anything else, so rebasing and
force-pushing a pull request branch is unaffected. Friction on a feature branch
is never a reason to loosen these.

**`pull_request` with `required_approving_review_count: 0`.** The zero is
load-bearing on a solo repository and is not an oversight. GitHub does not let
an author approve their own pull request, so any positive count on a repo with
one maintainer means no human pull request can ever merge, and every Dependabot
pull request needs a bypass. The rule is here for the pull request *requirement*
— the diff, the checks, and the record — not for the approval.

Raise it the moment there is a second person. It is the one parameter in this
file whose right value is a function of the team, not of the repo.

**`bypass_actors`: role 5, mode `pull_request`.** `5` is GitHub's built-in
Repository admin role, and the built-in role ids are the same on every
repository, which is what makes this line portable at all. It was read back from
the API rather than guessed: the ids are undocumented, and a wrong one is a
silent widening — `2` is Write, which on a solo repo is the same person and
would look identical in a diff.

The mode matters more than the actor. `pull_request` buys exactly the emergency
path a bypass is granted for: open a pull request, merge it past a red or stuck
required check, leave the trail in the pull request and the audit log. `always`
would additionally permit pushing straight to the protected branch with no pull
request and no diff, and would skip the deletion and force-push rules with it.
That is a strictly larger grant than the argument for having a bypass at all.

Having no bypass is worse than either. With none, the only way past a required
check that is stuck rather than failing is to delete or disable the ruleset — a
change that tends to leave protection off after the emergency, instead of
leaving a bypass in the log.

**`enforcement: "active"`.** The other values are `disabled` and `evaluate`.
`evaluate` reports what *would* have been blocked without blocking it, which is
useful for one afternoon and indistinguishable from protection thereafter.

## What is deliberately absent

**`required_status_checks`.** Added by `bootstrap-repo.py` from `--check` /
`--checks-from`, because the contexts are the names of one repo's CI jobs and
cannot be baseline.

Required checks are matched by string, and GitHub does not verify that a
required context corresponds to anything. A context naming a job that does not
exist never reports, and a rule waiting on a check that never arrives is
indistinguishable from one that has not run yet — the pull request sits
*pending*, not red, which reads as "still working" rather than
"misconfigured". That is why the script omits the rule entirely when given no
contexts rather than writing it empty, and why a repo that gets this ruleset
should also get `templates/tests/test_required_checks.py`.

`strict_required_status_checks_policy` is set to `true` by the script when the
rule is written at all. Without it, a pull request merges on checks that ran
against an older base commit — so a job added to CI while a branch was already
open does not run on that branch, including when that branch is the one the job
was added to protect.

## Server-assigned fields

The API returns more than it accepts. If you ever pull a live ruleset down to
compare against this file, these are not configuration and must not be
committed into it:

| | |
|---|---|
| identity | `id`, `node_id`, `source`, `source_type`, `created_at`, `updated_at`, `_links` |
| viewer-relative | `current_user_can_bypass` |

The second is the one worth naming. It answers "can *the caller* bypass this",
so it is a property of the token, not of the ruleset — committing it records one
admin's view as if it were config.

## Applying and updating

`bootstrap-repo.py` looks the ruleset up **by name** and `PUT`s it if found,
`POST`s it if not. Never hardcode an id anywhere: ids are assigned at creation,
so a ruleset deleted and recreated — which is how a bad one gets rolled back —
comes back with a different one, and a remembered id then updates a ruleset that
no longer exists, or a different one that does.

## Whether you can use this at all

Rulesets are gated by **plan and visibility together**, which is the first thing
to check when this file appears not to work:

| | free personal account | GitHub Pro |
|---|---|---|
| public repo | rulesets work | rulesets work |
| private repo | **nothing available** | rulesets work |

The bottom-left cell is absolute — a private repo on a free account can have no
ruleset and no classic protected branch either. The 403 reads "Upgrade to GitHub
Pro or make this repository public", which is a statement about the plan, not
about the token, so re-authenticating or widening scopes achieves nothing.

The consequence for a rollout is worth sitting with: on a free account it is a
repo's *visibility* that decides whether it can be protected, not how much the
protection matters. A private repo holding something sensitive is precisely the
one that cannot have a gate.

## If the repos are in an organization

Stop using this. Org-level rulesets target many repositories at once by name
pattern or custom property, which makes a per-repo script unnecessary and
removes the drift it exists to correct. This file and that script are the
personal-account substitute for a feature organizations already have.
