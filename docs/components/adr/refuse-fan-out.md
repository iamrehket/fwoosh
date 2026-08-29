---
id: adr-refuse-fan-out
type: adr
status: draft
paths: [src/fwoosh/compiler/]
date: 2026-08-28
---

# Refuse fan-out, name the metric and hop

fwoosh verifies join cardinality against keys rather than trusting a
relationship's declared from/to direction: Ossie declares `to` as the one
side, and fwoosh accepts that only when the to_columns cover the target
dataset's primary_key or one entry of its unique_keys; a `to` dataset with
no declared key makes the hop unknown, and unknown is treated as fan-out. Keys and relationship columns are physical column
names and are compared as declared; only the COUNT(DISTINCT ...)
carve-out below looks at a metric's arguments after field substitution.
The verdict
is taken per metric and per side, orientation-relative to the join tree:
cutting any hop splits the tree in two, and every dataset the metric
aggregates over must be protected from multiplication across that cut -
the far component must be reached through a verified one side, and a
metric with an unprotected aggregated dataset on either side of a cut is
refused. A chain of hops that all fan in is safe; a metric that sums a
dimension-side column across a fact is refused even though the same hop
is safe for a fact-side metric.
One carve-out: COUNT(DISTINCT ...) over bare columns of a single dataset
that cover its primary_key or a unique_keys entry is fan-out-immune by
construction, whichever side that dataset sits on, and is treated as
safe. When any metric in a request would be
computed across an unsafe hop, fwoosh refuses and names the metric and the
hop, with the fix ("declare unique_keys on X, restructure, or split the
request") in the message. Rejected for now: compiling each metric at its
own grain in a CTE and joining the results - correct for multi-fact
questions but a much larger compiler. Trade-off accepted: some legitimate
multi-fact questions are rejected until the model is fixed or per-grain
compilation lands in a later spec.
