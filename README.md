# fwoosh

Compile [Apache Ossie](https://github.com/apache/ossie) semantic models into SQL, with
[sqlglot](https://github.com/tobymao/sqlglot) as the SQL layer. First target: a query
compiler (metrics + dimensions → `SELECT`) for PostgreSQL and Snowflake.

```sh
uv sync
uv run pytest
uv run scripts/check_ossie_drift.py   # vendored ossie models vs. upstream main
```

Licensed under Apache-2.0. `src/fwoosh/_vendor/ossie/` contains unmodified files from
apache/ossie (see `UPSTREAM.json`).
