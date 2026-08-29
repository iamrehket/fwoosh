---
id: adr-substitute-field-expressions
type: adr
status: draft
paths: [src/fwoosh/compiler/]
date: 2026-08-28
---

# Substitute field expressions for references

A qualified reference `dataset.name` inside a metric expression names a
field of that dataset, and the field's own expression is substituted in
place with its unqualified columns re-qualified by the dataset's join
alias; it is never emitted as a physical column. Ossie's wording calls
these "column references", and a consumer reading them literally would
emit `customer.customer_full_name` for a field defined as `c_first_name ||
' ' || c_last_name` - a column that does not exist. Substitution is what
makes computed fields usable inside metrics, and the fan-out carve-out and
unresolvable-reference rules are built on the substituted form. Rejected:
physical-column semantics (computed fields unusable in metrics) and
allowing fields to reference other fields (a resolution rule Ossie does
not have). Trade-off accepted: fwoosh's reading of a reference diverges
from the literal upstream wording, so a model that relies on it may mean
something different to another consumer; that is worth raising upstream.
