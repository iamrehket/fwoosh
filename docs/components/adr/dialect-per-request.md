---
id: adr-dialect-per-request
type: adr
status: draft
paths: [src/fwoosh/request/, src/fwoosh/compiler/]
date: 2026-08-28
---

# Dialect is a per-request parameter

The caller supplies the target dialect (postgres or snowflake) on every
request, and fwoosh compiles the whole query for it. This sits against the
charter constraint that dialect and source are properties of datasets, not
of the model: fwoosh honors that constraint as far as Ossie lets it, but an
Ossie dataset carries only a free-text `source` and no engine identity, so
fwoosh cannot know which engine a dataset lives in and cannot refuse a
request that joins datasets from two engines. Rejected: inferring the
engine from `source` naming conventions (a guess with the model's
authority behind it) and requiring a fwoosh-specific engine annotation
(forks the model). Trade-off accepted: cross-engine joins compile to SQL
that runs nowhere, undetected; the caller asks only co-located questions
until Ossie or a `custom_extensions` convention carries engine identity,
at which point this decision should be superseded.
