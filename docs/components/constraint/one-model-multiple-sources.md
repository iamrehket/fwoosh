---
id: constraint-one-model-multiple-sources
type: constraint
status: current
date: 2026-08-27
---

# One model, multiple sources

A single Ossie semantic model describes a data set whose tables span
Snowflake, PostgreSQL, and other data sources. fwoosh must never assume a
model resolves to one warehouse or one SQL dialect; dialect and source are
properties of the datasets and expressions being compiled, not of the model
as a whole. This comes from the data sets fwoosh is being built to serve,
which already live across several systems.
