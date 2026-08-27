<!-- grimore:begin -->
This project uses grimore's doc-components system. At the start of every
session, read `docs/current/` - it is the current, agent-facing view of the
project's decisions, use cases, constraints, and glossary.
`docs/current/glossary.md` settles terminology; use its terms, not synonyms.

Specs and plans produced in this project go under `docs/specs/` and
`docs/plans/` respectively (not the upstream defaults). Every plan carries a
`spec:` frontmatter line pointing at the spec it implements.

Merge discipline (see `doc-components/CI.md`):
1. Require branches up to date before merge. After updating a branch that
   touched docs, re-run `uv run tools/grim.py lint --fix && uv run
   tools/grim.py render` and commit.
2. `grim check` runs in PR CI and fails on structural violations, a stale
   render, or unwaived touched-path guard hits.
3. `grim check` also runs on `main` as a backstop; a red
   default branch means the discipline was bypassed.

<!-- banner: wording provisional until IAM-41 lands -->
<!-- grimore:end -->
