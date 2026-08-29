---
id: adr-left-join-request-inner
type: adr
status: draft
paths: [src/fwoosh/compiler/, src/fwoosh/request/]
date: 2026-08-28
---

# LEFT JOIN by default, request may ask for inner

fwoosh joins every dataset outward from the anchor with LEFT JOIN, so a
metric's total is invariant to which dimensions a request adds: fact rows
with no matching dimension row (TPC-DS store_sales carries NULL customer
and store keys) land in a NULL group instead of silently disappearing.
This is the grain-faithfulness promise expressed as a join type. A request
may set `inner: true` to exclude unmatched rows when that is the question
being asked. Rejected: INNER JOIN as the default - the simplest SQL, but
adding a dimension could change a metric's total with nothing in the result
to show rows were dropped. Trade-off accepted: NULL groups appear in
results and must be understood by the caller, and a WHERE filter on a
dimension field still narrows to matched rows because a NULL comparison is
false.
