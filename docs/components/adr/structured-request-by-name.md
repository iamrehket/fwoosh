---
id: adr-structured-request-by-name
type: adr
status: draft
paths: [src/fwoosh/request/, src/fwoosh/compiler/]
date: 2026-08-27
---

# Structured request by name

The LLM hands fwoosh a structured request that names metrics, dimensions,
and filters from the semantic model - never SQL. fwoosh owns every byte of
the emitted query, which is what lets it guarantee join paths, grain, and
faithfulness to the model's metric definitions. The alternative, accepting
LLM-drafted SQL and validating it, was rejected because validation can only
reject, not guarantee. Trade-off accepted: the LLM cannot express anything
the model lacks; a missing metric or dimension is a model change, not a
request-time workaround.
