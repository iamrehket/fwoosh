# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What fwoosh is

A Python library + CLI that compiles [Apache Ossie](https://github.com/apache/ossie) semantic models (YAML/JSON) into SQL. First deliverable is a **query compiler**: given metrics + dimensions (+ filters), emit `SELECT … FROM … JOIN … GROUP BY` for **PostgreSQL and Snowflake**. Views/DDL and cross-dialect expression transpilation are explicitly later. sqlglot is the SQL layer: build queries as `sqlglot.exp` nodes (never string concatenation) and validate every Ossie expression by parsing it.

## Commands

- `uv sync` — Python 3.14 (`.python-version`). Never use the system `python3` (3.9).
- `uv run pytest` — unit tests. `uv run pytest -m network` runs the upstream drift check too (needs internet; skipped by default via `addopts`).
- `uv run ruff check . && uv run ruff format .`
- `uv run scripts/check_ossie_drift.py` — fails if vendored Ossie files were edited locally or upstream `main` has moved. Also runs weekly in CI (`.github/workflows/ossie-drift.yml`).

## Ossie facts that are easy to get wrong

- `apache-ossie` is **not on PyPI**. Its Pydantic models and JSON schema are vendored byte-for-byte in `src/fwoosh/_vendor/ossie/` (`models.py`, `ossie-schema.json`; pin in `UPSTREAM.json`). Never edit those two files — extend by wrapping/subclassing in `src/fwoosh/`. The `__init__.py` there is ours (upstream's uses an absolute `ossie.` import). To update: re-copy the files, set `ref` in `UPSTREAM.json` to the copied commit, run the drift check, then adapt fwoosh to whatever changed.
- Import the models as `from fwoosh._vendor.ossie import OssieDocument, …` (`OssieSemanticModel`, `OssieDataset`, `OssieField`, `OssieMetric`, `OssieRelationship`, `OssieDialect`, …). All models are `frozen=True`; serialize with `OssieDocument.to_ossie_yaml()` / `to_ossie_json()`.
- **Dialect names disagree across upstream docs.** The authority is the vendored `OssieDialect` enum: `ANSI_SQL`, `SNOWFLAKE`, `DATABRICKS`, `BIGQUERY`, `MDX`, `MAQL`, `TABLEAU`. `core-spec/expression_language.md` also talks about `Ossie_SQL_2026` and `PostgreSQL` — neither exists in the enum or schema. Treat `ANSI_SQL` as the canonical/fallback expression dialect. sqlglot mapping (mirrors upstream `validation/validate.py`): `ANSI_SQL → None` (sqlglot default), `SNOWFLAKE → "snowflake"`, `DATABRICKS → "databricks"`, `BIGQUERY → "bigquery"`; `MDX`/`MAQL`/`TABLEAU` are not SQL and must be skipped, not parsed.
- **Ossie expressions are scalar fragments only** — no `SELECT`/`FROM`/`JOIN`/`WHERE`/`GROUP BY`, subqueries, or CTEs (the spec says those are "handled by the semantic layer" — fwoosh *is* that layer). Fields must not aggregate; metrics may. fwoosh builds the query skeleton from `dataset.source` (physical table), `relationships` (`from`/`to` dataset names + ordered `from_columns`/`to_columns`), and `primary_key`.
- Column references may be unqualified (`amount`) or qualified (`orders.amount`). Unquoted identifiers are case-insensitive; double-quoted ones are case-sensitive. A bare column name is not a parseable statement — upstream's validator tries `parse_one(expr)` then falls back to `parse_one(f"SELECT {expr}")`.
- `tests/fixtures/tpcds_semantic_model.yaml` is upstream's TPC-DS example (same pinned commit); use it as the default fixture rather than inventing models.

## Conventions

- Conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`).
- Apache-2.0. Keep the ASF license headers on vendored files.
- Design decisions go through `grimore:align`. Open questions to settle there before building the compiler: join-path selection when `relationships` form a graph with multiple paths; fan-out / symmetric-aggregate handling when metrics span one-to-many joins; how filters are expressed on the request side; whether to derive a missing target-dialect expression from `ANSI_SQL` via sqlglot transpile or refuse.

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
