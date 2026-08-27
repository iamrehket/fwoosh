# Component Store Schema

Normative reference for the doc components system. Source of authority:
docs/superpowers/specs/2026-07-24-doc-components-design.md; on conflict the
spec wins and this file gets fixed.

A component is one small markdown file holding one documentation idea —
a decision, a term, a use case, a constraint, a non-goal, or a subsystem
fact — with YAML frontmatter carrying identity and lifecycle. Consumer
documentation is compiled from components; components are appended and
status-flipped, never surgically edited.

## Directory layout

Defaults; an adopting project overrides paths in `.grimore.toml`.

    docs/
      components/          # source of truth
        adr/<slug>.md
        term/<slug>.md
        usecase/<slug>.md
        constraint/<slug>.md
        nongoal/<slug>.md
        note/<slug>.md
      current/             # rendered agent view — written ONLY by grim render
      specs/               # working layer, dated session specs
      plans/               # working layer, dated implementation plans

## Frontmatter

| Field | Required | Rules |
|---|---|---|
| `id` | yes | `<type>-<slug>`; slug `[a-z0-9][a-z0-9-]*` and equals the filename minus `.md`; unique across the store; never reused |
| `type` | yes | one of `adr`, `term`, `usecase`, `constraint`, `nongoal`, `note`; equals the parent directory name |
| `status` | yes | `draft`, `current`, or `superseded` |
| `supersedes` | no | list of component IDs this replaces; every target must exist in the store |
| `subsystem` | no | routes `note` into a render target |
| `paths` | no | `note`/`adr` only; list of git-root-relative path globs the component describes; a trailing `/` matches the directory prefix, otherwise case-sensitive fnmatch, where `*` matches across `/`. Drives the touched-path guard: a branch that touches a matching path must change this component or record a `Grim-Waive: <id> <reason>` commit trailer (the trailer must sit in the commit's trailer block, and the reason is mandatory). Example: write `src/render/`, not `src/render` - without the trailing slash the pattern matches only a file literally named `src/render` and gates nothing. |
| `date` | yes | ISO `YYYY-MM-DD` creation date; never updated |

Frontmatter field order is not significant.

## Lifecycle

- **Live means `current`.** Drafts and superseded components are not live.
- Legal transitions: `draft -> current` (promotion), `draft -> superseded`
  (abandonment), `current -> superseded`. Nothing else. No deletion.
- A `supersedes:` edge is authored on the new component (usually while it
  is a draft) and **takes effect at promotion**: when the new component
  becomes `current`, each edge target flips to `superseded` in the same
  pass. `E032` enforces this — a `current` component whose target is still
  `current` is an error, because both decisions then render into the
  consumer view as live and nothing says which one holds. It is not
  auto-fixable: which decision stands is a judgement, not formatting.
- Drafts are the only place in-place edits are allowed, and an amendment
  must never reverse a decision's substance — if the decision itself
  changed, abandon the draft and write a new component. `E043` enforces
  this, comparing against the merge-base: a component that was not `draft`
  there may change its `status` and nothing else. Amending a draft and
  promoting it on the same branch stays legal, which is why the gate is the
  status at the merge-base rather than at HEAD.
- **Abandoned is not superseded-with-a-successor.** Both write status
  `superseded`, and the derived banner renders "Superseded." for either, so
  the distinction lives in reachability: a replaced decision leaves a live
  successor, an abandoned one does not. This matters because a replaced
  decision *was* implemented while an abandoned one never was — see the
  `implemented:` rules below.
- A branch's view of the store is the branch's own. Reconciliation flips a
  component to `superseded` before the pull request merges, so the branch
  asserts a replacement the default branch has not yet accepted. That is
  intended; `E031` catches two branches racing the same target once both
  land.
- **Reconcile error:** one component with two or more live successors is
  invalid; the branch that surfaces it must resolve which successor
  stands.
- Supersede targets must already exist in the branch's view of the store;
  reversing a decision in an unmerged sibling branch requires landing
  order.

### Touched-path guard

Only `current` components gate. The guard sees tracked changes vs the merge-base. Waivers are echoed in lint output (`W071`) so reviewers see every bypass. Coverage grows as `paths:` get declared. A waiver covers its component for the remainder of the branch (the whole merge-base..HEAD range), not just the change it was written for - if the branch grows after a waiver lands, re-review it.

A hit has three legal remedies, and amending the component is not one of
them — only drafts may be edited in place. Supersede it with a new
component; record a `Grim-Waive:` trailer; or, when a declared path churns
for reasons the decision does not govern, declare a **standing waiver** in
`.grimore.toml`:

    [[grimore.standing_waiver]]
    component = "adr-dual-plugin-manifests"
    paths = [".claude-plugin/plugin.json", ".codex-plugin/plugin.json"]
    reason = "shared metadata churn; the manifest contract is unchanged"

All three keys are required and `reason` must be non-empty — a bypass
nobody has to justify is not reviewable. It is scoped to one component
**and** a subset of its declared paths, so the component still gates on
everything else it declares; `E073` catches a name that resolves to no
component, which is always a typo since components are never deleted.
`W073` echoes every standing waiver that suppressed something, including
when other paths on the same component still raise `E070` — otherwise the
permanently-ignored subset disappears behind the error.

Choose deliberately: a `Grim-Waive` trailer is deaf once and makes you
re-justify it next time, while a standing waiver is **permanently deaf**
for the paths it names. A genuine restructure of a standing-waived file
fires nothing. Keep the list short.

## Body formats

- **adr** — record only decisions that are hard to reverse, surprising
  without context, and a real trade-off. One paragraph of
  context/decision/why; optional sections only when they add value.
- **term** — `**Term**: one-two sentence definition.` then
  `_Avoid_: rejected synonyms.` Opinionated; context-specific terms only.
  Lint enforces Avoid-terms from `current` terms against draft and current
  component bodies (word-boundary, case-insensitive); append
  `<!-- grim:ok -->` to a line to mark a deliberate mention.
- **usecase / constraint / nongoal** — `# Title` plus one terse prose
  paragraph. Non-goals state what is excluded and why.
- **note** — terse factual prose about one subsystem. No file paths or
  code snippets in prose; use `paths:` frontmatter.

## Working layer

Specs (session artifacts, frozen after implementation):

- Frontmatter: `components:` — list of component IDs the session created.
- Body includes a `## Decisions` section that references those IDs rather
  than restating them.
- `implemented:` stamp is added once by finish-docs; already-stamped
  specs are never re-stamped. **It must be quoted on disk** —
  `implemented: "2026-07-24 (PR #14)"`. Unquoted, YAML reads ` #` as the
  start of a comment and the value silently truncates to `2026-07-24 (PR`.
  A bare `implemented: 2026-07-24` is also accepted and coerced; every
  other shape is rejected rather than parsed into a fragment.
- **The stamp records the branch-finish event**: the date the work was
  completed and the pull request it was submitted in. It is not a merge
  date. Merge is implied by the file's presence on the default branch —
  anything you read on `main` got merged, and an abandoned branch's stamp
  never arrives there, so no reader meets a false claim. Accepted cost: a
  pull request that sits for three days stamps three days early. This is
  the definition, not an approximation of a merge date.
- **A spec whose components were all abandoned cannot be stamped.** The
  stamp asserts the work was implemented; abandoning everything the session
  created asserts the opposite. `E095` catches the state wherever it came
  from, and finish-docs refuses to write it in the first place. A spec
  implemented only in part still stamps — the banner reports the shortfall.
- The banner block delimited by `<!-- grim:status -->` and
  `<!-- /grim:status -->` is script-owned: grim lint --fix rewrites it;
  humans and agents never edit inside it. Everything outside the block is
  frozen after implementation.
- **The block is never empty.** An unstamped spec renders "Not yet
  implemented."; every other state renders at least a provenance line. A
  populated block is evidence the deriver ran, which an empty one cannot
  provide — it is indistinguishable from a script that never executed.
- Banner text is **composed**, not enumerated: a provenance line, then
  qualifier clauses in fixed order for an empty component list, draft
  references, partial supersession, and full supersession.
- Banner wording is **advisory**. Banners track explicit supersede edges
  only; a decision reversed by a fresh component carrying no edge fires
  nothing, and grim does not infer such reversals.
- A stale banner is an error, not a warning: `grim check` byte-compares
  only the rendered views, so working-layer drift is caught by lint or
  not at all.

Plans:

- Frontmatter: `spec:` — repo-relative path to the plan's spec. Lint
  warns when missing. Plans inherit their spec's banner.
- `spec:` resolves against the project root. A path escaping the root is
  rejected unread. A missing or ungoverned target yields a status line
  saying so rather than a blank block.
- A plan never carries `implemented:` — the stamp is a spec-level fact.

## Render mapping (informative)

Implemented by `grim render`; recorded here so authors know where
a component surfaces. Only `status: current` components render.

Rendered files carry a `grim:store-hash` provenance comment and a do-not-edit marker; `grim check` verifies by byte-compare, not by hash.

| Output | Source |
|---|---|
| `current/charter.md` | usecase, constraint, nongoal |
| `current/decisions.md` | adr |
| `current/glossary.md` | term |
| `current/<subsystem>.md` | note with matching `subsystem:` |
| `current/general.md` | note without `subsystem:` |

Ordering within every output: (`date`, `id`) ascending.
