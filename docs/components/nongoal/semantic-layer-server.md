---
id: nongoal-semantic-layer-server
type: nongoal
status: current
date: 2026-08-27
---

# Semantic-layer server

fwoosh is not a long-running query service or BI semantic layer in the style
of Cube or MetricFlow. Those were evaluated and set aside for lack of an easy
integration path; fwoosh is a library and CLI that a caller - typically an
LLM-driven tool - invokes to validate a model and build a query, and nothing
more.
