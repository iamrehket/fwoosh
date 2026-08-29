---
components: [adr-structured-request-by-name, adr-shortest-path-pin-relationships, adr-refuse-fan-out, adr-transpile-ansi-sql, adr-left-join-request-inner, adr-dialect-per-request, adr-substitute-field-expressions]
# implemented: "YYYY-MM-DD (PR #N)"   — MUST be quoted; added once by finish-docs, never by hand
---

<!-- grim:status -->
> **Not yet implemented.**
> Not fully realized: adr-dialect-per-request, adr-left-join-request-inner, adr-refuse-fan-out, adr-shortest-path-pin-relationships, adr-structured-request-by-name, adr-substitute-field-expressions, adr-transpile-ansi-sql still draft.
<!-- /grim:status -->

# Query compiler - Design

Date: 2026-08-28

## Problem

An LLM-driven tool answers a person's question by building SQL over a data
set that spans Snowflake, PostgreSQL, and other sources. Given only the
physical schema the LLM already writes workable SQL; what it cannot be
trusted with is (1) choosing join paths and preserving grain - a metric
summed across a one-to-many join is silently wrong - and (2) faithfulness:
every answer must use the model's own metric definitions, not the LLM's
reconstruction of them. The Ossie semantic model carries the datasets,
relationships, keys, and metric expressions needed to make both guarantees,
but Ossie expressions are scalar fragments - the spec leaves
SELECT/FROM/JOIN/GROUP BY to "the semantic layer". fwoosh is that layer: it
turns a request against the model into a runnable SELECT for PostgreSQL and
Snowflake. Dialect correctness matters but is secondary; the compiler is
primarily the trust boundary for joins and metric math.

## Approach

**Plan-then-emit.** Phase 1 (resolve) turns a request plus one semantic
model into a `QueryPlan`: the required datasets, the chosen join edges each
with a per-metric cardinality verdict, every metric and field expression
resolved to a sqlglot AST for the target dialect, filters split into WHERE
and HAVING, time grains, order and limit. Every refusal is raised here,
before any SQL exists, from one place. Phase 2 (emit) renders the
`QueryPlan` into a single sqlglot `exp.Select` and generates it for the
target. The plan is inspectable and golden-testable without SQL, and it is
the seam that derived metrics, per-grain CTEs, and a future `explain` plug
into.

*Single-pass sqlglot builder* (rejected): build the `exp.Select` directly
while walking the request. Less code and a faster first result, but
refusals and construction interleave, the only testable artifact is SQL
text, and per-grain CTEs later would mean restructuring the builder.

*Templates plus sqlglot validation* (rejected baseline): assemble SQL from
per-dialect string templates and parse to validate. No real transpilation,
every dialect difference becomes a template branch, and it breaks the
project rule that SQL is never built by string concatenation.

## Request contract

The request is a named Pydantic model (JSON-schema exportable) - the same
artifact a later model-presentation surface hands to the LLM. Only names
from the model are accepted; fwoosh owns every byte of SQL.

- `dialect`: the target, `postgres` or `snowflake`, supplied by the caller
  per request. Ossie datasets carry only a free-text `source`, so fwoosh
  cannot know which engine a dataset lives in; a request that joins
  datasets from different engines produces SQL that runs nowhere, and
  detecting that is out of scope (see below).
- `model`: the semantic model name, required when the Ossie document holds
  more than one `semantic_model` entry; relationships are scoped within
  one model. Ossie's schema does not enforce unique names, so fwoosh
  validates up front that semantic-model names are unique within the
  document, that dataset, metric, and relationship names are unique within
  the model, and that field names are unique within their dataset,
  refusing the document otherwise - the by-name contract rests on it.
  Dataset, field, and metric names are also required to be legal
  unquoted identifiers in both targets and at most 63 bytes (PostgreSQL's
  truncation limit; the alias-collision check below runs on aliases
  truncated to 63 bytes so a long `<field>_<grain>` cannot collide
  silently), since they are emitted unquoted as join and column aliases
  (sqlglot would auto-quote an unsafe name, which would contradict the
  identifier rule below); the bound is PostgreSQL's and is applied
  uniformly, not per dialect. Relationships are validated too: `from` and
  `to` must name declared datasets, and `from_columns` and `to_columns`
  must be the same length and non-empty - a shorter `from_columns`
  against a composite key would otherwise be scored as a safe hop while
  the emitted ON joins on half the key (invalid relationship). Relationship
  columns and keys live in the physical-column namespace, not the field
  namespace, and are not checked against `fields`.
- `metrics`: metric names, may be empty; a request with neither metrics
  nor dimensions is refused. Order is load-bearing: the first metric
  chooses the anchor, and under the LEFT default the anchor decides which
  unmatched rows survive, so `[total_sales, store_count]` and the reverse
  are different requests.
- `dimensions`: field references `dataset.field`, optionally with `grain`.
- `filters`: predicates on a field, referenced as `dataset.field` (WHERE),
  or on a metric by name (HAVING). All filters are ANDed. A dataset
  reached only by a filter still joins the tree. Operators
  and value arity: `=`, `<>`, `<`, `<=`, `>`, `>=`, `LIKE`, `NOT LIKE`
  take one value; `IN`, `NOT IN` take a non-empty list; `BETWEEN` takes
  exactly two; `IS NULL`, `IS NOT NULL` take none. Wrong arity, including
  an empty list, is refused (filter arity). Value type versus the field's
  `datatype` is not checked in this cut; mismatches fall through to the
  engine. Values are bound literal nodes, never
  interpolated text. A metric named in a filter must also appear in
  `metrics`; otherwise refused.
- `relationships`: optional relationship names to pin; semantics in
  adr-shortest-path-pin-relationships (a pin is the only way into its
  far-end dataset; unusable pins are refused). A name that is not a
  relationship in the model is an unknown name; a self-relationship
  (`from` equals `to`) is inert for path-finding and cannot be pinned.
- `inner`: optional boolean, default false; true joins with INNER instead
  of LEFT (adr-left-join-request-inner).
- `order_by`: entries naming a metric (sorted on its alias; it must also
  appear in `metrics`, else refused) or a dimension (by its emitted
  alias; it must also appear in `dimensions`, else refused - never
  resolved as a fresh field reference), each with a direction. The plan always carries `NULLS LAST`
  regardless of direction - a top-N must never lead with the unknowns;
  sqlglot spells it out for `DESC` and omits it for `ASC`, where it is
  both engines' default, and the goldens reflect that.
- `limit`: refused unless `order_by` is present - an unordered top-N is a
  different N rows each run.

## Compilation rules

The seven ADRs under Decisions carry the load-bearing choices; the rules
below are what the spec adds on top of them.

**Expressions.** Every field and metric expression is parsed with sqlglot
the way upstream's validator does: `parse_one(expr)`, falling back to
`parse_one(f"SELECT {expr}")`, so fwoosh accepts exactly what upstream
parses (the fallback yields a `Select`; the expression is lifted from
`expressions[0]`); an expression that fails both parses is refused as
unparseable. Shape is checked on the parsed AST: a field expression that
contains an aggregation function is refused, and a metric expression
that contains none is refused, because either breaks the GROUP BY
(the glossary defines a metric as an aggregating expression; CLAUDE.md
says field expressions are scalar). Dialect selection per expression
follows adr-transpile-ansi-sql;
an expression with neither a native target entry nor an `ANSI_SQL` entry
(for example `SNOWFLAKE`-only compiled for postgres) is refused - cross-
dialect transpilation between two non-ANSI entries is not attempted.

**References.** A qualified reference `dataset.name` inside a metric names
a *field* of that dataset, not a physical column; a name that is not a
declared field of that dataset is refused as unknown name, never passed
through as a physical column. The field's resolved expression is
substituted in place, and unqualified column references inside any
substituted field expression - in metrics, dimensions, and filters alike
(the fixture's `customer.customer_full_name` is `c_first_name || ' ' ||
c_last_name`) - are qualified with the owning dataset's join alias.
Field expressions reference physical columns only, never other fields;
a qualified reference inside a *field* expression must name the field's
own dataset, else refused as unknown name. An unqualified reference inside a metric is resolved to the
single field of that name across the model's datasets; no match or more
than one match is refused as unresolvable. The datasets a metric requires
are the owners of the fields it resolves to; a metric never declares a home
dataset; a metric that references no field at all (`COUNT(*)`) is
refused as having no dataset - it can neither anchor the tree nor be
protected from fan-out; the message suggests counting a key column. Each
metric resolves to one AST kept on the plan - the seam a
later derived-metrics feature substitutes into. The fan-out carve-out for
`COUNT(DISTINCT ...)` is evaluated on this post-substitution AST: the
arguments must be bare columns of one dataset that cover its
`primary_key` or a `unique_keys` entry; a field that substitutes to
anything other than a bare column fails closed and the metric takes the
ordinary per-side verdict.

**Join paths and fan-out** are adr-shortest-path-pin-relationships and
adr-refuse-fan-out; the spec adds nothing to them beyond naming the
refusal for a required dataset with no path from the anchor
(unreachable dataset). Note what they imply
for the default fixture: two of TPC-DS's five metrics reach into a
one-side dataset across `store_sales` - `store_productivity` sums
`store.s_number_employees` and is refused; `customer_lifetime_value`
counts distinct `customer.c_customer_sk`, the one-side key, and compiles
under the carve-out.

**Query shape.** The anchor dataset is the FROM, as `<source> AS
<dataset name>` like every other dataset; every other dataset in the
tree is joined outward from it per adr-left-join-request-inner (LEFT
JOIN unless the request sets `inner: true`), in breadth-first order from
the anchor with siblings in the model's relationship declaration order,
so the same request always emits the same text. Each join is ON the
pairwise equality of the relationship's ordered `from_columns` and
`to_columns` - physical column names as declared, never substituted
field expressions, and the fan-out key comparison uses the same physical
names - qualified by the two join aliases and ANDed. Every emitted
dimension expression is listed in GROUP BY, in request order; metrics are
the aggregated select items; field filters go to WHERE, metric filters to
HAVING; a metrics-only request with no dimensions omits GROUP BY
entirely. The select list is dimensions first, then metrics, each in
request order. A HAVING predicate repeats the metric's resolved expression
rather than its alias - Snowflake accepts an alias there, PostgreSQL does
not. A request with no metrics emits `SELECT DISTINCT` over its
dimensions with no GROUP BY; fan-out is moot there (nothing is summed)
and DISTINCT makes row multiplication invisible.

**Sources.** Ossie allows `dataset.source` to be a physical table reference or a query. <!-- grim:ok -->
This cut supports physical references only, and a `source` that does not parse as a
three-part-or-shorter name is refused as an unsupported source (wrapping
a query source as a subquery is a later addition).

**Aliases.** The dataset name is the join alias used to qualify columns. An ungrained
dimension is emitted under its field name; a grained one under
`<field>_<grain>`; a metric under its name. `order_by` refers to these
emitted aliases. Any two emitted aliases in one request that collide -
including the same dimension named twice - are refused. An emitted alias
that merely shares a name with a field, physical column, or dataset is
deliberately *not* refused: output-column names win in ORDER BY, GROUP
BY and HAVING carry full expressions, so there is nothing for such a
name to break.

**Time grains.** A dimension may carry `grain` in `day`, `month`,
`quarter`, `year`, `week`; `week` is an accepted enum member that the
compiler refuses as unsupported pending the time-conventions session, so
the message can say so. A grain is legal only when the vendored
`OssieField.is_time_dimension()` is true - no `dimension` block means
not a time dimension; an explicit `dimension.is_time` wins; otherwise a
temporal `datatype` decides - *and* the field's `datatype` is one of
`Date`, `DateTime`, `DateTimeTz` (not the vendored `_TEMPORAL_DATA_TYPES`,
which also holds `Time`: PostgreSQL has no `date_trunc` over a time of day
and truncating one to a month is meaningless); a field with `is_time:
true` but any other or absent datatype (TPC-DS's `d_year`) is refused
because `DATE_TRUNC` over it fails at run time. The message names which
leg failed - "not a time dimension" (a `Date` field with no `dimension`
block; fix: add `dimension: {}`) versus "not a truncatable datatype"
(fix: change the datatype, or use the field ungrained - Ossie itself
blesses integer year fields as time dimensions). A grained dimension is
emitted as an `exp.DateTrunc` (sqlglot
generates the unit uppercase, `DATE_TRUNC('MONTH', ...)`, and the goldens
are written that way) under the alias `<field>_<grain>`. This is the only
place fwoosh emits a column the model did not declare, and its type is
engine-defined: `DATE_TRUNC` over a `Date` returns `timestamp` on
PostgreSQL and `date` on Snowflake.

**Identifiers.** fwoosh emits identifiers - dataset, field, and metric
names - exactly as the model writes them, unquoted; the one alias it
composes, `<field>_<grain>`, uses the field name as written plus a
lowercase grain.
Result-column case is therefore engine-defined (Snowflake upper-folds,
PostgreSQL lower-folds); the execution tests compare column names
case-insensitively.

**Transpilation artifacts.** sqlglot preserves ANSI semantics when it
generates for a target: on 30.17, `customer_lifetime_value`'s ANSI
expression generates for postgres as `CAST(SUM(store_sales.ss_ext_sales_price)
AS DOUBLE PRECISION) / COUNT(DISTINCT customer.c_customer_sk)` (float
division), while for snowflake it is emitted unchanged - and a native
`SNOWFLAKE` entry would bypass the rewrite entirely. That is
adr-transpile-ansi-sql's "trusted less than a native entry" clause in
practice; the goldens record whatever sqlglot emits, and the execution
tests are what prove the numbers.

**Refusals** are errors that name the offending request element and the
model-side or request-side fix; the compiler never guesses. The refusal
classes are: empty request, duplicate name, illegal identifier, invalid
relationship,
unparseable expression, expression shape (field aggregates / metric does
not), unsupported source, unknown name, unresolvable reference, filter
arity, unusable pin, metric
with no dataset, unreachable dataset, ambiguous path, fan-out, no usable
dialect entry, illegal grain (with `week` as its own tested message),
alias collision, unselected metric (in a filter or order_by) or
unselected dimension (in order_by), limit without order.

## Success criteria

- A fixed set of requests against the TPC-DS fixture compiles to
  checked-in expected SQL for PostgreSQL and Snowflake. The goldens pin
  sqlglot's output, so `sqlglot` is capped to the tested major series
  (`<31`) and the goldens are regenerated deliberately on any upgrade,
  minor included; the execution tests are the invariant.
- Every refusal class has a test asserting its message. The TPC-DS fixture
  supplies fan-out (`store_productivity`), illegal grain (`d_year`), and
  the request-side classes (unknown name, unselected metric, limit
  without order, `week`, alias collision by naming one dimension twice).
  It is a
  keyed four-edge star with only `ANSI_SQL` entries, so a second,
  fwoosh-authored fixture supplies the rest: a diamond in its relationship
  graph (ambiguous path), a keyless dataset (fan-out via an unknown hop),
  a `SNOWFLAKE`-only expression (no usable dialect entry), a query-valued
  `source`, an unparseable expression, a field that aggregates and a
  metric that does not (both halves of expression shape), a metric with
  an unmatched and one with an ambiguous bare reference (unresolvable
  reference), a `COUNT(*)` metric (no dataset), and a dataset with no
  relationships (unreachable). The diamond also yields two goldens where a
  pin resolves the tie each way, a golden where a pinned
  dimension-to-dimension relationship with equidistant endpoints enters
  its `to` dataset via the pin, plus refusals for a pin that reaches no
  required dataset and for two pins contending for one dataset (both
  unusable pin). Document-level refusals - duplicate
  name, illegal identifier (one fixture each for a dataset, a field, and a
  metric name), invalid relationship (one fixture for mismatched
  column-list lengths, one for `to` naming an undeclared dataset) -
  reject the whole file at load, so each gets its own single-purpose
  fixture. Filter arity, unselected dimension, and empty request are
  request-side and need no fixture.
- The PostgreSQL output executes against a real PostgreSQL started in a
  container, loaded with a small TPC-DS sample, and returns known numbers -
  proving the join and grain math, not only the text. The expected numbers
  assume the LEFT default: rows with NULL `ss_customer_sk` count toward
  `customer_lifetime_value`'s numerator but not its denominator, which
  differs from the INNER answer.
- Snowflake is covered by golden SQL only in this cut.

## Decisions

- Structured request by name, never LLM-written SQL: adr-structured-request-by-name
- Shortest join path from a fan-out-derived anchor, ties refused, request may pin relationships: adr-shortest-path-pin-relationships
- Refuse fan-out, cardinality from keys, verdict per metric and side, COUNT DISTINCT key carve-out: adr-refuse-fan-out
- Transpile ANSI_SQL via sqlglot when no native entry: adr-transpile-ansi-sql
- LEFT JOIN outward from the anchor, request may opt into INNER: adr-left-join-request-inner
- Target dialect supplied per request; cross-engine joins undetectable: adr-dialect-per-request
- A dataset.name reference inside a metric names a field whose expression is substituted: adr-substitute-field-expressions

## Out of scope

- **Derived metrics** (a metric referencing another metric by name).
  Excluded because Ossie's expression language has no such rule - a file
  using it would mean something only fwoosh understands. Roadmapped, not
  abandoned: too convenient to leave; pursue as an upstream proposal or a
  `custom_extensions` marker so the model stays portable. The per-metric
  resolved AST on the plan is the seam it will use.
- **Time conventions**: `week`, week start, fiscal calendars, and other
  reporting conventions get their own design session; only
  day/month/quarter/year ship here.
- **Per-grain pre-aggregation** (compiling each metric at its own grain in
  a CTE and joining the results) - the alternative to refusing fan-out; a
  later spec.
- **Cross-engine detection.** A model may describe datasets in several
  engines (charter constraint), but Ossie gives no per-dataset engine
  field, so fwoosh cannot refuse a request that joins across engines; the
  caller chooses the target dialect and is responsible for asking only
  co-located questions until Ossie or a `custom_extensions` convention
  carries engine identity.
- **Cross-dialect transpilation** between two non-ANSI entries (e.g.
  `SNOWFLAKE` to postgres) - sqlglot could attempt it; not promised.
- **Model presentation to the LLM** (`describe`) - a separate spec after
  the compiler; the request contract's schema is its input.
- **Dialects beyond PostgreSQL and Snowflake** - sqlglot could emit them,
  but they are neither tested nor promised.
- **Snowflake integration tests** - later; golden SQL only for now.
- Views/DDL emission and cross-dialect expression transpilation as
  products in their own right, per CLAUDE.md.
