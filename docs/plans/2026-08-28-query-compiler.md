---
spec: docs/specs/2026-08-28-query-compiler.md
---

<!-- grim:status -->
> **Not yet implemented.**
> Not fully realized: adr-dialect-per-request, adr-left-join-request-inner, adr-refuse-fan-out, adr-shortest-path-pin-relationships, adr-structured-request-by-name, adr-substitute-field-expressions, adr-transpile-ansi-sql still draft.
<!-- /grim:status -->

# Query compiler - Implementation plan

Date: 2026-08-28

Implements `docs/specs/2026-08-28-query-compiler.md`. Every rule below is
stated there; this plan only sequences the work, fixes the module layout,
and names the tests. Where the plan and the spec disagree, the spec wins.

## Module layout

Fixed by the ADRs' `paths:` guards - changing it means amending them.

```
src/fwoosh/
  request/                 adr-structured-request-by-name, adr-dialect-per-request
    __init__.py            re-exports QueryRequest, Dialect, Grain, Filter, OrderBy
    model.py               Pydantic models; QueryRequest.model_json_schema() is public
  compiler/                the other five ADRs
    __init__.py            compile(document, request) -> str  (the one public entry point)
    errors.py              Refusal(Exception) with .cls: RefusalClass (enum of the 19 classes)
    model.py               LoadedModel: validated, indexed OssieSemanticModel
    expressions.py         parse-with-fallback, shape check, dialect pick, substitution
    graph.py               relationship graph, anchor, BFS paths, pins, cardinality
    plan.py                QueryPlan dataclass + resolve(model, request) -> QueryPlan
    emit.py                QueryPlan -> sqlglot exp.Select -> str
tests/
  fixtures/tpcds_semantic_model.yaml        (exists; upstream, pinned)
  fixtures/diamond.yaml                     fwoosh-authored second fixture
  fixtures/invalid/*.yaml                   one file per document-level refusal
  goldens/<request-name>.{postgres,snowflake}.sql
  postgres/                                 execution harness (schema, sample CSVs, expected)
```

Vendored `src/fwoosh/_vendor/ossie` is read-only throughout; `LoadedModel`
wraps `OssieSemanticModel`, never subclasses or edits it.

## Phase 0 - Contract and errors (no compilation yet)

1. `request/model.py`: `Dialect` (`postgres`, `snowflake`), `Grain`
   (`day`, `month`, `quarter`, `year`, `week`), `Filter` (field-or-metric
   target, operator enum, value with per-operator arity validated in
   Pydantic), `OrderBy`, `QueryRequest` (`dialect`, `model`, `metrics`,
   `dimensions` with optional grain, `filters`, `relationships`, `inner`,
   `order_by`, `limit`). Pydantic validators enforce: non-empty `IN` lists,
   `BETWEEN` arity two, no value for `IS [NOT] NULL`, `limit` requires
   `order_by`, at least one of metrics/dimensions.
2. `compiler/errors.py`: `RefusalClass` enum with exactly the spec's
   classes; `Refusal(cls, element, fix)` whose `str()` is
   `"<cls>: <element> - <fix>"`. Every refusal test asserts on `.cls` and
   on the presence of the named element in the message, not on exact
   prose.
3. Tests: request-model validation round-trips; JSON schema exports;
   request-side refusals raised by Pydantic (empty request, filter arity,
   limit without order) map to `RefusalClass` via one adapter.

Checkpoint: `uv run pytest` green; no compiler yet.

## Phase 1 - LoadedModel and document validation

1. `compiler/model.py`: `LoadedModel.from_document(doc, model_name)` -
   selects the semantic model (`model` required when >1), then validates
   per spec: unique model names; unique dataset/metric/relationship
   names; unique field names per dataset; legal unquoted identifiers
   (both targets, <=63 bytes) for dataset/field/metric names;
   relationships: `from`/`to` declared, equal-length non-empty column
   lists. Physical column names are *not* checked against fields.
2. Indexes: `datasets[name]`, `fields[(dataset, name)]`, `metrics[name]`,
   `relationships[name]`, adjacency list for `graph.py`.
3. Tests: TPC-DS loads; one `tests/fixtures/invalid/*.yaml` per
   document-level class - duplicate name (dataset, metric, model), illegal
   identifier (dataset, field, metric; one over 63 bytes), invalid
   relationship (length mismatch; `to` undeclared).

## Phase 2 - Expressions

1. `expressions.py`:
   - `parse(expr, dialect)`: `parse_one(expr)` then
     `parse_one(f"SELECT {expr}").expressions[0]`; failure -> unparseable
     expression.
   - `shape(ast, kind)`: field must contain no `exp.AggFunc`; metric must
     contain at least one -> expression shape.
   - `pick_dialect(ossie_expression, target)`: native (`SNOWFLAKE` for
     snowflake) wins; else `ANSI_SQL` parsed with sqlglot default dialect;
     neither -> no usable dialect entry. Returns (ast, source_dialect).
   - `substitute(metric_ast, model)`: `dataset.name` -> field AST with
     bare columns qualified by the dataset alias; unknown field -> unknown
     name; bare identifier -> unique field match across datasets else
     unresolvable reference; `COUNT(*)`-style zero references -> metric
     with no dataset. Field expressions: qualified refs must name own
     dataset. Returns (ast, referenced_datasets, aggregated_columns per
     dataset) - the last feeds the fan-out verdict and carve-out.
2. Tests: every TPC-DS field and metric parses and passes shape; CLV
   substitutes correctly; `customer_full_name` inlines and qualifies;
   diamond fixture supplies the SNOWFLAKE-only, unparseable, wrong-shape,
   unmatched-bare, ambiguous-bare, and `COUNT(*)` cases.

## Phase 3 - Graph, anchor, cardinality

1. `graph.py`:
   - `cardinality(rel, model)`: `to_columns` cover `to` dataset's
     `primary_key` or a `unique_keys` entry -> ONE; else UNKNOWN.
   - `anchor(metric_infos, dimensions)`: fan-out-derived many-end for the
     first metric when exactly one candidate; else first referenced
     dataset in parse order; else first dimension's dataset.
   - `pins(request, model, anchor)`: far end per pin on the unpruned
     graph (unreachable = infinite; tie -> `to`); self-relationships
     unpinnable; contention or no far-end use -> unusable pin; prune
     competing edges into each far end.
   - `join_tree(anchor, required, pruned_graph)`: BFS shortest paths;
     equal-length alternatives for any required dataset -> ambiguous
     path (message names both); no path -> unreachable dataset; edges
     ordered BFS-from-anchor, siblings in relationship declaration order.
   - `fan_out(tree, metric_infos)`: per metric, per hop cut: every
     aggregated dataset's far component must be reached through a ONE
     hop; carve-out for `COUNT(DISTINCT <bare key columns of one
     dataset>)`; violation -> fan-out naming metric and hop.
2. Tests: TPC-DS - `total_sales` by every dimension compiles;
   `store_productivity` refuses naming `store_sales_to_store`; CLV
   compiles via carve-out; `d_year` grain refuses. Diamond fixture -
   tie refuses naming both paths; each pin resolves it; equidistant
   dimension-to-dimension pin enters `to`; unusable pin (no required
   dataset; two contending); keyless dataset -> fan-out; disconnected
   dataset -> unreachable.

## Phase 4 - QueryPlan and resolve

1. `plan.py`: frozen dataclass `QueryPlan` - `dialect`, `anchor`,
   `joins: list[Join(rel, parent, child, inner: bool)]`, `select:
   list[SelectItem(alias, ast, is_metric)]`, `group_by: list[ast]`,
   `where: list[ast]`, `having: list[ast]`, `order_by: list[(alias,
   desc)]`, `limit`, `distinct: bool`.
2. `resolve(model, request) -> QueryPlan` runs phases 1-3 in order and
   then: grains (legality via `is_time_dimension()` and datatype in
   Date/DateTime/DateTimeTz; `week` -> illegal grain with its own
   message; alias `<field>_<grain>`; `exp.DateTrunc`); filters (field ->
   WHERE, metric -> HAVING with the resolved AST; unselected metric
   refused; bound literals via `exp.Literal`/`exp.Tuple`); order_by
   (metric/dimension must be selected; `NULLS LAST` on the plan);
   aliases (dimensions then metrics; duplicate emitted alias, on
   63-byte-truncated form, -> alias collision); `distinct` when no
   metrics; `inner` from the request.
3. Tests: plan-level golden per request as JSON (`tests/goldens/*.plan.json`)
   - these are the SQL-free artifacts the spec promises; every refusal
   class now has at least one test (matrix below).

## Phase 5 - Emit and SQL goldens

1. `emit.py`: `QueryPlan -> exp.Select` using the sqlglot builder API
   only - `select`, `from_`, `join(..., join_type="LEFT"|"INNER",
   on=...)`, `where`, `group_by`, `having`, `order_by`, `limit`,
   `distinct`; then `.sql(dialect=plan.dialect)`. Never string-concatenate.
2. `compiler/__init__.py`: `compile(doc, request) -> str` =
   `emit(resolve(LoadedModel.from_document(doc, request.model), request))`.
3. Goldens: for each named request in `tests/requests.yaml`, checked-in
   `tests/goldens/<name>.postgres.sql` and `.snowflake.sql`; a
   `--update-goldens` pytest option regenerates them (only on deliberate
   sqlglot upgrades). Initial set: sales by year; sales by state and
   month grain; CLV by state; top-5 stores by sales (order+limit); sales
   filtered by year with a HAVING on total_sales; dimensions-only
   distinct; inner-join variant; each diamond pin golden.

Checkpoint: both golden suites green; `uv run ruff check` clean.

## Phase 6 - PostgreSQL execution harness

1. `tests/postgres/schema.sql`: the five TPC-DS tables, columns the
   fixture touches only. `tests/postgres/data/*.csv`: a hand-authored
   sample small enough to compute expected numbers by hand - include
   rows with NULL `ss_customer_sk` / `ss_store_sk` so the LEFT default is
   observable.
2. Harness: `testcontainers[postgres]` as a dev dependency (Docker 29 is
   installed locally; `ci.yml` gets a `services: postgres` job instead).
   Session-scoped fixture starts postgres:16, loads schema and CSVs.
3. Tests marked `postgres` (opt-in like `network`): run each postgres
   golden, compare result sets to `tests/postgres/expected/<name>.json`
   with case-insensitive column names; assert the LEFT-vs-INNER
   difference on CLV explicitly.
4. `pyproject.toml`: add the `postgres` marker; `addopts` excludes it by
   default; CI runs it in its own job.

## Phase 7 - CLI and finish

1. `fwoosh compile <model.yaml> <request.json> --dialect postgres` via a
   `[project.scripts]` entry; prints SQL or the refusal, exit 1 on
   refusal. `fwoosh schema` prints the request JSON schema.
2. README usage; CLAUDE.md gains the `postgres` marker command.
3. Run `grimore:finish-docs` on the branch: reconcile the seven draft
   ADRs against the diff, write the `implemented:` stamp on the spec.

## Refusal-class test matrix

| Class | Fixture / source | Phase |
|---|---|---|
| empty request | request model | 0 |
| filter arity | request model | 0 |
| limit without order | request model | 0 |
| duplicate name | invalid/dup-*.yaml | 1 |
| illegal identifier | invalid/ident-*.yaml | 1 |
| invalid relationship | invalid/rel-*.yaml | 1 |
| unparseable expression | diamond | 2 |
| expression shape (both halves) | diamond | 2 |
| unsupported source | diamond | 2 |
| unknown name (field, metric, dataset, relationship) | tpcds | 2 |
| unresolvable reference (none, ambiguous) | diamond | 2 |
| metric with no dataset | diamond | 2 |
| no usable dialect entry | diamond | 2 |
| unreachable dataset | diamond | 3 |
| ambiguous path | diamond | 3 |
| unusable pin (no use, contention) | diamond | 3 |
| fan-out (one-side sum; unknown hop) | tpcds; diamond | 3 |
| illegal grain (`d_year`; `week`; no dimension block) | tpcds | 4 |
| alias collision | tpcds (dimension twice) | 4 |
| unselected metric / dimension | tpcds | 4 |

## Sequencing and risk

- Phases 0-4 are pure Python with no SQL and are where the spec's hard
  rules live; they should land and be reviewed before any emitter code.
- The golden suites are the regression net; the postgres harness is the
  correctness proof. Do not tune goldens by hand - regenerate.
- Open risk: sqlglot's ANSI parse being permissive means a model
  expression can pass Phase 2 and fail on the engine; the postgres
  harness is the only place that surfaces it in this cut.
