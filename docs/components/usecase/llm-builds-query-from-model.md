---
id: usecase-llm-builds-query-from-model
type: usecase
status: current
date: 2026-08-27
---

# LLM builds a query from the semantic model

A person asks a question through an LLM-driven tool such as Claude. The LLM
reads the Ossie semantic model for a data set - a set of physical tables that <!-- grim:ok -->
may live across Snowflake, PostgreSQL, and other sources - and uses fwoosh to
build the query that returns the answer. fwoosh exists so the LLM works from
a validated, shared description of the data rather than re-deriving dataset
structure and metric definitions per question.
