---
id: adr-shortest-path-pin-relationships
type: adr
status: draft
paths: [src/fwoosh/compiler/]
date: 2026-08-28
---

# Shortest path, request may pin relationships

fwoosh resolves joins by treating the model's relationships as an
undirected graph and taking the shortest path (fewest joins) from an
anchor dataset to every other dataset the request needs; the join tree is
the union of those paths. The anchor is the dataset the fan-out rule
requires at the many end for the first metric (a metric may span
several datasets; the fan-out rule may leave exactly one candidate),
falling back to the first dataset referenced in that metric's expression
in parse order whenever it does not leave exactly one, and
to the first dimension's dataset when a request has no metrics. A
required dataset with no path from the anchor is refused as unreachable;
a metric that references no field at all (COUNT(*)) is refused because it
has no dataset to anchor or to protect from fan-out - count a key column
instead. Ties are
refused with both candidate paths named rather than guessed, because the
same request must always compile to the same SQL; determinism is promised,
global minimality of the tree is not. A request may pin relationship names
to resolve a tie or force a specific path: a pinned relationship becomes
the only edge through which its far-end dataset may be entered -
competing edges into that dataset are removed before the search runs -
so a pin both breaks ties and forces longer paths. The far end is the
endpoint farther from the anchor on the unpruned graph (an endpoint the
anchor cannot reach counts as infinitely far), or the relationship's `to`
dataset when both endpoints are equidistant; all far
ends are computed before any edge is removed, so multiple pins are
order-independent. A pin whose far end is neither named by the request
nor a hop on the resulting tree, or two pins contending for the same
dataset, is refused as unusable rather than silently joining extra
datasets. Relationships already carry ai_context so an LLM can know
them. Rejected: anchor-plus-one-hop (cannot
answer multi-hop questions) and silent tie-breaking (wrong numbers with
the model's authority behind them). Trade-off accepted: edges are weighted
equally, so a model with a preferred default path must express it by
pinning or by later model-level weighting.
