---
id: adr-transpile-ansi-sql
type: adr
status: draft
paths: [src/fwoosh/compiler/]
date: 2026-08-28
---

# Transpile ANSI_SQL via sqlglot

When a field or metric has no expression in the target dialect, fwoosh
parses its ANSI_SQL expression with sqlglot's default dialect and generates
it for the target (sqlglot "snowflake" or "postgres"); a native
target-dialect entry always wins when present. This is the only route to
PostgreSQL at all - it is absent from Ossie's dialect enum - and it is what
lets an ANSI-only model such as the TPC-DS example compile anywhere.
Rejected: emitting ANSI_SQL text verbatim (breaks on any function the
target spells differently) and refusing without a native entry (every
ANSI-only model fails every request). Trade-off accepted: sqlglot's default
dialect is a permissive superset of ANSI, not a strict checker, and it
occasionally misrenders exotic functions, so a transpiled expression is
trusted less than a native one and should be testable against a real
engine later.
