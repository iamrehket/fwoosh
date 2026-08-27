# CI recipe: grim check

Wire `grim check` into an adopting project's CI. It fails on structural
violations, on rendered output that does not byte-match a fresh render,
and on unwaived touched-path hits; waivers appear in lint output as `W071`
warnings for reviewer visibility.

## GitHub Actions

```yaml
name: grim
on:
  pull_request:
  push:
    branches: [main]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # required: grim check fails closed without full history
      - uses: astral-sh/setup-uv@v5
      - run: uv run tools/grim.py check
```

Adjust the `tools/grim.py` path to wherever the adopting project keeps or
references the script, and `main` to its default branch.

## Merge discipline (why both triggers)

1. Enable "require branches up to date before merge" (branch protection or
   merge queue). After updating a branch that touched docs, re-run
   `grim lint --fix && grim render` and commit — one command, mechanical.
2. `grim check` on pull requests catches violations before merge; CI must
   fetch full history (`fetch-depth: 0`) because the transition check fails
   closed when the merge-base is unresolvable.
3. `grim check` on the default branch is the backstop: a red default branch
   means the discipline was bypassed; fix with `grim lint --fix && grim render`
   on a follow-up commit.

Doc-touching merges are serialized by rule 1; accepted for small teams,
revisit if it becomes a bottleneck.
