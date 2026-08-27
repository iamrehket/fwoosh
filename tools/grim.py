# Vendored from iamrehket/grimore by adopt-docs on 2026-08-27. Source: grimore plugin v1.4.0.
#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""grim - doc-components tooling. Ships the lint, render, and check verbs.

Requirements doc: doc-components/SCHEMA.md.
Spec: docs/superpowers/specs/2026-07-24-doc-components-design.md.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import yaml

COMPONENT_TYPES = ("adr", "term", "usecase", "constraint", "nongoal", "note")
STATUSES = ("draft", "current", "superseded")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ESCAPE_MARKER = "<!-- grim:ok -->"
AVOID_LINE_RE = re.compile(r"^_Avoid_:\s*(.+?)\.?\s*$")
RESERVED_OUTPUTS = ("charter", "decisions", "glossary", "general")
FIELD_ORDER = ("id", "type", "status", "supersedes", "subsystem", "paths", "date")
REQUIRED_FIELDS = ("id", "type", "status", "date")
PATHS_TYPES = {"note", "adr"}
LEGAL_TRANSITIONS = {
    ("draft", "current"),
    ("draft", "superseded"),
    ("current", "superseded"),
}

DEFAULTS = {
    "components": "docs/components",
    "current": "docs/current",
    "specs": "docs/specs",
    "plans": "docs/plans",
    "default_branch": "main",
}


class ConfigError(Exception):
    """Unusable .grimore.toml or config values."""


@dataclasses.dataclass(frozen=True)
class StandingWaiver:
    """A permanent, path-scoped bypass of the touched-path guard.

    Scoped to one component AND a subset of its declared paths, so the
    component still gates on everything else it declares. That is the whole
    difference from a Grim-Waive trailer, which is component-wide but expires
    with the branch: a standing waiver is permanently deaf where a trailer is
    deaf once and makes you re-justify it. Keep the list short.
    """

    component: str
    paths: tuple[str, ...]
    reason: str


@dataclasses.dataclass
class Config:
    root: Path
    components: Path
    current: Path
    specs: Path
    plans: Path
    default_branch: str
    types: tuple[str, ...]
    standing_waivers: tuple[StandingWaiver, ...] = ()


def load_config(root: Path) -> Config:
    raw: dict = {}
    cfg_path = root / ".grimore.toml"
    if cfg_path.is_file():
        try:
            raw = tomllib.loads(cfg_path.read_text(encoding="utf-8")).get("grimore", {})
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f".grimore.toml: {exc}") from None
    for key in ("components", "current", "specs", "plans", "default_branch"):
        if key in raw and not isinstance(raw[key], str):
            raise ConfigError(f".grimore.toml: {key} must be a string")
    raw_types = raw.get("types", COMPONENT_TYPES)
    if not isinstance(raw_types, (list, tuple)) or not all(
        isinstance(t, str) for t in raw_types
    ):
        raise ConfigError(".grimore.toml: types must be a list of strings")
    types = tuple(raw_types)
    unknown = set(types) - set(COMPONENT_TYPES)
    if unknown:
        raise ConfigError(f".grimore.toml: unknown component types: {sorted(unknown)}")
    resolved_root = root.resolve()
    paths = {
        key: root / raw.get(key, DEFAULTS[key])
        for key in ("components", "current", "specs", "plans")
    }
    for key, p in paths.items():
        if not p.resolve().is_relative_to(resolved_root):
            raise ConfigError(
                f".grimore.toml: {key} must resolve inside the project root, got {p}"
            )
    # Specs and plans are walked as one pass and each file takes a single role,
    # so an overlap would silently classify every plan as a spec: plan-specific
    # validation and banner inheritance would never run. Fail loudly instead.
    spec_dir, plan_dir = paths["specs"].resolve(), paths["plans"].resolve()
    if spec_dir == plan_dir or spec_dir.is_relative_to(plan_dir) or plan_dir.is_relative_to(spec_dir):
        raise ConfigError(
            f".grimore.toml: specs and plans must not overlap, got {paths['specs']} and {paths['plans']}"
        )
    return Config(
        root=root,
        components=paths["components"],
        current=paths["current"],
        specs=paths["specs"],
        plans=paths["plans"],
        default_branch=raw.get("default_branch", DEFAULTS["default_branch"]),
        types=types,
        standing_waivers=parse_standing_waivers(raw),
    )


def parse_standing_waivers(raw: dict) -> tuple[StandingWaiver, ...]:
    """[[grimore.standing_waiver]] entries, validated for shape.

    Shape errors raise rather than being dropped: a malformed entry that
    silently vanished would leave the operator staring at the E070 the waiver
    was written to answer, with nothing pointing at the typo. Whether the named
    component exists is a store question, so it is E073's job, not this one's.
    """
    entries = raw.get("standing_waiver", [])
    if not isinstance(entries, list):
        raise ConfigError(".grimore.toml: standing_waiver must be a list of tables")
    out: list[StandingWaiver] = []
    for i, entry in enumerate(entries):
        where = f".grimore.toml: standing_waiver[{i}]"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where} must be a table")
        unknown = sorted(set(entry) - {"component", "paths", "reason"})
        if unknown:
            raise ConfigError(f"{where}: unknown key(s) {', '.join(unknown)}")
        component, reason = entry.get("component"), entry.get("reason")
        globs = entry.get("paths")
        if not isinstance(component, str) or not component.strip():
            raise ConfigError(f"{where}: component must be a non-empty string")
        # Required, not optional. A waiver without a stated reason is an
        # unreviewable bypass, and W071 already established that the
        # component-plus-reason pairing is what makes one auditable.
        if not isinstance(reason, str) or not reason.strip():
            raise ConfigError(f"{where}: reason is required and must be non-empty")
        if (
            not isinstance(globs, list)
            or not globs
            or not all(isinstance(g, str) and g.strip() for g in globs)
        ):
            raise ConfigError(f"{where}: paths must be a non-empty list of glob strings")
        out.append(StandingWaiver(component.strip(), tuple(globs), reason.strip()))
    return tuple(out)


@dataclasses.dataclass
class Finding:
    level: str  # "error" or "warning"
    code: str
    path: str  # config-root-relative posix path ("." when store-wide)
    message: str
    component: str | None = None


def error(code: str, path: str, message: str, component: str | None = None) -> Finding:
    return Finding("error", code, path, message, component)


def warning(code: str, path: str, message: str, component: str | None = None) -> Finding:
    return Finding("warning", code, path, message, component)


@dataclasses.dataclass
class Component:
    path: Path  # absolute
    rel: str  # config-root-relative posix path
    dir_type: str  # parent directory name
    fm: dict
    body: str

    @property
    def cid(self):
        return self.fm.get("id")

    @property
    def ctype(self):
        return self.fm.get("type")

    @property
    def status(self):
        return self.fm.get("status")

    @property
    def supersedes(self) -> list:
        value = self.fm.get("supersedes")
        return value if isinstance(value, list) else []


FM_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)


def parse_component(path: Path, root: Path) -> tuple[Component | None, list[Finding]]:
    rel = path.relative_to(root).as_posix()
    text = path.read_text(encoding="utf-8")
    m = FM_RE.match(text)
    if not m:
        return None, [error("E001", rel, "missing or unterminated frontmatter block")]
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        return None, [error("E002", rel, f"invalid YAML frontmatter: {exc}")]
    if not isinstance(fm, dict):
        return None, [error("E003", rel, "frontmatter is not a mapping")]
    coerce_fm(fm)
    return (
        Component(path=path, rel=rel, dir_type=path.parent.name, fm=fm, body=m.group(2)),
        [],
    )


def coerce_fm(fm: dict) -> dict:
    """Normalize parsed frontmatter in place. Every reader must use this.

    YAML resolves an unquoted `date: 2026-07-24` to a datetime.date, so a
    caller that parses frontmatter itself and compares against a Component's fm
    would see str != date and report a change nobody made. E043 learned that
    the hard way, on all 48 components at once.
    """
    if isinstance(fm.get("date"), datetime.date):
        fm["date"] = fm["date"].isoformat()
    return fm


@dataclasses.dataclass
class Store:
    components: list[Component]
    findings: list[Finding]


def load_store(cfg: Config) -> Store:
    components: list[Component] = []
    findings: list[Finding] = []
    if not cfg.components.is_dir():
        return Store(components, findings)
    for path in sorted(cfg.components.rglob("*.md")):
        rel = path.relative_to(cfg.root).as_posix()
        parent = path.parent
        if parent == cfg.components or parent.parent != cfg.components:
            findings.append(
                error("E004", rel, "component files must live at <components>/<type>/<slug>.md")
            )
            continue
        if parent.name not in cfg.types:
            findings.append(
                error("E005", rel, f"unknown component type directory {parent.name!r}")
            )
            continue
        try:
            comp, errs = parse_component(path, cfg.root)
        except UnicodeDecodeError:
            findings.append(error("E006", rel, "file is not valid UTF-8"))
            continue
        findings.extend(errs)
        if comp is not None:
            components.append(comp)
    return Store(components, findings)


def _valid_date(value: str) -> bool:
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def check_schema(store: Store, cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    for c in store.components:
        rel, fm = c.rel, c.fm
        for key in fm:
            if key not in FIELD_ORDER:
                out.append(error("E011", rel, f"unknown frontmatter field {key!r}", c.cid))
        missing = [k for k in REQUIRED_FIELDS if k not in fm]
        if missing:
            out.append(
                error("E010", rel, f"missing required fields: {', '.join(missing)}", c.cid)
            )
            continue
        ctype, status, cid, date = fm["type"], fm["status"], fm["id"], fm["date"]
        if ctype not in cfg.types:
            out.append(
                error("E012", rel, f"type must be one of {list(cfg.types)}, got {ctype!r}", cid)
            )
        elif ctype != c.dir_type:
            out.append(
                error("E013", rel, f"type {ctype!r} does not match directory {c.dir_type!r}", cid)
            )
        if status not in STATUSES:
            out.append(
                error("E014", rel, f"status must be one of {list(STATUSES)}, got {status!r}", cid)
            )
        slug = c.path.stem
        if not SLUG_RE.fullmatch(slug):
            out.append(
                error("E015", rel, f"filename slug {slug!r} must match [a-z0-9][a-z0-9-]*", cid)
            )
        if isinstance(ctype, str):
            expected = f"{ctype}-{slug}"
            if cid != expected:
                out.append(
                    error("E016", rel, f"id must be {expected!r} (type + filename slug), got {cid!r}", cid)
                )
        if not (isinstance(date, str) and DATE_RE.fullmatch(date) and _valid_date(date)):
            out.append(
                error("E017", rel, f"date must be a valid ISO YYYY-MM-DD date, got {date!r}", cid)
            )
        if "paths" in fm and ctype not in PATHS_TYPES:
            out.append(error("E018", rel, "paths: is only allowed on note and adr components", cid))
        if "supersedes" in fm and not (
            isinstance(fm["supersedes"], list)
            and all(isinstance(x, str) for x in fm["supersedes"])
        ):
            out.append(error("E019", rel, "supersedes must be a list of component IDs", cid))
        if "paths" in fm and not (
            isinstance(fm["paths"], list) and all(isinstance(x, str) for x in fm["paths"])
        ):
            out.append(error("E019", rel, "paths must be a list of glob strings", cid))
        if "subsystem" in fm:
            if not isinstance(fm["subsystem"], str):
                out.append(error("E019", rel, "subsystem must be a string", cid))
            elif ctype != "note":
                out.append(
                    warning("W061", rel, "subsystem has no effect on non-note components", cid)
                )
            elif not SLUG_RE.fullmatch(fm["subsystem"]):
                out.append(error("E062", rel, f"subsystem {fm['subsystem']!r} must match [a-z0-9][a-z0-9-]*", cid))
            elif fm["subsystem"] in RESERVED_OUTPUTS:
                out.append(error("E063", rel, f"subsystem {fm['subsystem']!r} collides with a fixed render output", cid))
    return out


def check_ids(store: Store) -> list[Finding]:
    out: list[Finding] = []
    seen: dict[str, str] = {}
    for c in store.components:
        cid = c.cid
        if not isinstance(cid, str):
            continue
        if cid in seen:
            out.append(error("E020", c.rel, f"duplicate id {cid!r} (also in {seen[cid]})", cid))
        else:
            seen[cid] = c.rel
    return out


def check_edges(store: Store) -> list[Finding]:
    out: list[Finding] = []
    ids = {c.cid for c in store.components if isinstance(c.cid, str)}
    rel_by_id = {c.cid: c.rel for c in store.components if isinstance(c.cid, str)}
    status_by_id = {
        c.cid: c.status for c in store.components if isinstance(c.cid, str)
    }
    live_successors: dict[str, list[str]] = {}
    for c in store.components:
        for target in c.supersedes:
            if not isinstance(target, str):
                continue  # E019 already reported by check_schema
            if target == c.cid:
                out.append(error("E030", c.rel, "component supersedes itself", c.cid))
                continue
            if target not in ids:
                out.append(
                    error("E030", c.rel, f"supersedes target {target!r} does not exist in the store", c.cid)
                )
                continue
            if c.status == "current" and isinstance(c.cid, str):
                # setdefault(...).append would count a component listing the
                # same target twice as two live successors and report
                # "'x' has 2 live successors (adr-n, adr-n)" - a duplicate
                # list entry is idempotent, not a fork. Dedupe so E031 means
                # what it says.
                succs = live_successors.setdefault(target, [])
                if c.cid not in succs:
                    succs.append(c.cid)
                # SCHEMA: the edge takes effect at promotion, when the target
                # flips to superseded in the same pass. Nothing enforced that,
                # so a promotion whose cascade never ran left both decisions
                # live and rendered both into the consumer view as current,
                # passing lint and check in silence.
                if status_by_id.get(target) == "current":
                    out.append(
                        error(
                            "E032",
                            rel_by_id.get(target, "."),
                            f"still current, but {c.cid!r} supersedes it; the cascade "
                            f"did not run - set this component's status to superseded "
                            f"in the same pass that promoted {c.cid!r}",
                            target,
                        )
                    )
    for target, succs in sorted(live_successors.items()):
        if len(succs) >= 2:
            out.append(
                error(
                    "E031",
                    rel_by_id.get(target, "."),
                    f"{target!r} has {len(succs)} live successors ({', '.join(sorted(succs))}); reconcile which stands",
                    target,
                )
            )
    return out


def _git(cfg: Config, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cfg.root, capture_output=True, text=True
    )


def resolve_merge_base(cfg: Config, strict: bool) -> tuple[str | None, list[Finding]]:
    top = _git(cfg, "rev-parse", "--show-toplevel")
    # origin/<default_branch> is what the PR will actually merge into, so it
    # is the authoritative base whenever it resolves. The local ref is only
    # a fallback for repos with no remote (or no fetch of it yet) -- a
    # stale or divergent local ref must never be preferred over origin, or
    # commits/waivers that will genuinely land in the PR diff (or that sit
    # on an upstream branch the guard has no business seeing) get excluded
    # or wrongly included.
    refs_tried = [f"origin/{cfg.default_branch}", cfg.default_branch]
    base = None
    if top.returncode == 0:
        for ref in refs_tried:
            mb = _git(cfg, "merge-base", "HEAD", ref)
            if mb.returncode == 0:
                base = mb.stdout.strip()
                break
    if base is not None:
        return base, []
    if not cfg.components.is_dir():
        return None, []
    if strict:
        return None, [error("E042", ".", f"cannot resolve git merge-base with any of {refs_tried}; failing closed (fix CI: fetch-depth: 0)")]
    return None, [warning("W042", ".", f"cannot resolve git merge-base with any of {refs_tried}; skipping transition and touched-path checks")]


WAIVE_VALUE_RE = re.compile(r"^(\S+)\s+(\S.*?)\s*$")


def collect_waivers(cfg: Config, base: str) -> dict[str, list[str]]:
    # Let git identify the trailer block: %(trailers:key=...) only reads the
    # final trailer paragraph, so a "Grim-Waive:" quoted or discussed in the
    # commit body prose is NOT a waiver.
    log = _git(
        cfg, "log", "--reverse",
        "--format=%(trailers:key=Grim-Waive,valueonly=true,unfold=true)%x00",
        f"{base}..HEAD",
    )
    waivers: dict[str, list[str]] = {}
    if log.returncode != 0:
        return waivers
    for block in log.stdout.split("\0"):
        for line in block.splitlines():
            m = WAIVE_VALUE_RE.match(line.strip())
            if m:
                waivers.setdefault(m.group(1), []).append(m.group(2))
    return waivers


def check_transitions(store: Store, cfg: Config, base: str | None, strict: bool) -> list[Finding]:
    out: list[Finding] = []
    if base is None:
        return out
    top = _git(cfg, "rev-parse", "--show-toplevel")
    git_root = Path(top.stdout.strip()).resolve()
    try:
        comp_prefix = cfg.components.resolve().relative_to(git_root).as_posix()
    except ValueError:
        raise ConfigError(
            f"components dir {cfg.components} is outside the git repository {git_root}"
        ) from None
    ls = _git(cfg, "ls-tree", "--full-tree", "-r", "-z", "--name-only", base, "--", comp_prefix)
    if ls.returncode != 0:
        if strict:
            return [
                error(
                    "E042",
                    ".",
                    "cannot list components at merge-base; "
                    "failing closed (fix CI: fetch-depth: 0)",
                )
            ]
        return [
            warning(
                "W042",
                ".",
                "cannot list components at merge-base; skipping transition check",
            )
        ]
    old_paths = {p for p in ls.stdout.split("\0") if p}
    present_paths = (
        {
            p.resolve().relative_to(git_root).as_posix()
            for p in cfg.components.rglob("*.md")
        }
        if cfg.components.is_dir()
        else set()
    )
    by_git_rel = {
        c.path.resolve().relative_to(git_root).as_posix(): c for c in store.components
    }
    root_resolved = cfg.root.resolve()
    for old in sorted(old_paths):
        if old.endswith(".md") and old not in present_paths:
            abs_old = git_root / old
            try:
                rel = abs_old.relative_to(root_resolved).as_posix()
            except ValueError:
                rel = old
            out.append(error("E041", rel, "component deleted; components are never deleted"))
    for git_rel, c in sorted(by_git_rel.items()):
        if git_rel not in old_paths:
            continue  # new on this branch; any initial status is legal
        show = _git(cfg, "show", f"{base}:{git_rel}")
        old_status = None
        old_fm = None
        old_body = None
        if show.returncode == 0:
            m = FM_RE.match(show.stdout)
            if m:
                try:
                    old_fm = yaml.safe_load(m.group(1))
                except yaml.YAMLError:
                    old_fm = None
                if isinstance(old_fm, dict):
                    coerce_fm(old_fm)
                    old_status = old_fm.get("status")
                    old_body = m.group(2)
        if old_status is None:
            out.append(
                warning("W043", c.rel, "could not read status at merge-base; transition skipped", c.cid)
            )
            continue
        # SCHEMA: drafts are the only place in-place edits are allowed, and the
        # rule had no enforcement. E040 below compares status alone, and the
        # touched-path guard deliberately skips a component whose own file
        # changed - so rewriting a current component's body was invisible to
        # every check. Gated on the status at the merge-base, not at HEAD, so
        # amending a draft and promoting it in the same branch stays legal.
        if old_status != "draft" and old_body is not None and isinstance(old_fm, dict):
            changed = sorted(_nonstatus_changes(old_fm, old_body, c))
            if changed:
                out.append(
                    error(
                        "E043",
                        c.rel,
                        f"was {old_status!r} at the merge-base, so only its status may "
                        f"change; {', '.join(changed)} also changed. Revert the edit and "
                        f"supersede this component with a new one instead",
                        c.cid,
                    )
                )
        new_status = c.status
        if new_status == old_status or (old_status, new_status) in LEGAL_TRANSITIONS:
            continue
        out.append(
            error(
                "E040",
                c.rel,
                f"illegal status transition {old_status!r} -> {new_status!r} since merge-base",
                c.cid,
            )
        )
    return out


def _nonstatus_changes(old_fm: dict, old_body: str, c: Component) -> set[str]:
    """What changed besides `status`, as field names plus maybe "the body".

    Compares the *parsed* frontmatter and the newline-stripped body rather than
    raw bytes. `lint --fix` reorders frontmatter to FIELD_ORDER, reformats
    values, and re-spaces the body, so a byte comparison would report every
    normalized file as an illegal edit.
    """
    changed = {
        key
        for key in (set(old_fm) | set(c.fm)) - {"status"}
        if old_fm.get(key) != c.fm.get(key)
    }
    if old_body.strip("\n") != c.body.strip("\n"):
        changed.add("the body")
    return changed


def _glob_hit(path: str, globs: list) -> bool:
    for g in globs:
        if g.endswith("/"):
            if path.startswith(g):
                return True
        elif fnmatch.fnmatchcase(path, g):
            # fnmatchcase, not fnmatch: no platform-dependent case folding.
            return True
    return False


def check_touched_paths(store: Store, cfg: Config, base: str | None, strict: bool) -> list[Finding]:
    out: list[Finding] = []
    gating = [
        c for c in store.components
        if c.status == "current"
        and isinstance(c.cid, str)
        and c.ctype in PATHS_TYPES
        and isinstance(c.fm.get("paths"), list)
        and c.fm["paths"]
        and all(isinstance(g, str) for g in c.fm["paths"])
        # anything shaped otherwise is already E018/E019 territory in check_schema
    ]
    if base is None or not gating:
        return out
    top = _git(cfg, "rev-parse", "--show-toplevel")
    # --no-renames: a plain `diff --name-only` collapses a detected rename to
    # only its destination path, so `git mv` out of a guarded paths: prefix
    # would report zero touched paths and silently bypass the guard.
    # -c diff.relative=false: a user-level `git config diff.relative true`
    # would make git print paths relative to cwd instead of the repo root
    # when cfg.root is nested under the git root, silently emptying the
    # touched set. Override in the same invocation (git >= 2.22; avoid
    # --no-relative, which needs 2.28).
    diff = _git(cfg, "-c", "diff.relative=false", "diff", "--no-renames", "--name-only", "-z", base)
    if top.returncode != 0 or diff.returncode != 0:
        # Do not silently skip: in CI a skipped guard is a bypassed guard.
        if strict:
            return [error("E072", ".", "touched-path guard could not compute the branch diff; failing closed")]
        return [warning("W072", ".", "touched-path guard could not compute the branch diff; skipping")]
    git_root = Path(top.stdout.strip()).resolve()
    touched = {name for name in diff.stdout.split("\0") if name}
    waivers = collect_waivers(cfg, base)
    standing: dict[str, list[StandingWaiver]] = {}
    known_ids = {c.cid for c in store.components if isinstance(c.cid, str)}
    for sw in cfg.standing_waivers:
        # Components are never deleted, so a name that resolves to nothing is
        # always a typo - and a silent one, because the waiver simply fails to
        # apply and the operator gets a bare E070 with no hint that a waiver
        # was meant to cover it.
        if sw.component not in known_ids:
            out.append(error(
                "E073", ".",
                f"standing waiver names {sw.component!r}, which is not a component "
                f"in the store", sw.component,
            ))
            continue
        standing.setdefault(sw.component, []).append(sw)
    for c in gating:
        own = c.path.resolve().relative_to(git_root).as_posix()
        hits = sorted(p for p in touched if _glob_hit(p, c.fm["paths"]))
        # Path-scoped, so the subtraction happens per path rather than per
        # component: everything this component declares and the waiver does not
        # cover still gates. That scoping is the whole difference from a
        # Grim-Waive trailer, which is why standing waivers stay out of the
        # collect_waivers dict.
        cid = c.cid if isinstance(c.cid, str) else ""
        covered = standing.get(cid, [])
        waived = sorted(p for p in hits if any(_glob_hit(p, list(sw.paths)) for sw in covered))
        hits = [p for p in hits if p not in set(waived)]
        if not hits and not waived:
            continue
        if own in touched:
            continue
        if waived:
            # Emitted whenever anything was waived, not only when nothing is
            # left: on a mixed hit the unwaived path raises E070 and the
            # permanently-deaf subset would otherwise vanish from the output
            # that reviewers read.
            reasons = "; ".join(sw.reason for sw in covered)
            out.append(warning(
                "W073", c.rel,
                f"standing waiver covers {', '.join(repr(p) for p in waived)}: {reasons}",
                c.cid,
            ))
        if not hits:
            continue
        if c.cid in waivers:
            reasons = "; ".join(waivers[c.cid])
            out.append(warning(
                "W071", c.rel,
                f"touched-path hit on {hits[0]!r} waived: {reasons}", c.cid,
            ))
        else:
            # Not "update it": SCHEMA forbids editing anything but a draft, so
            # for the current components this guard gates on, amending the
            # component is never a legal remedy.
            out.append(error(
                "E070", c.rel,
                f"branch touches {hits[0]!r}, declared in this component's paths:, "
                f"without changing the component; supersede it with a new component, "
                f"record a waiver with commit trailer 'Grim-Waive: {c.cid} <reason>', "
                f"or add a [[grimore.standing_waiver]] if this path churns for reasons "
                f"the decision does not govern", c.cid,
            ))
    return out


def check_avoid_terms(store: Store) -> list[Finding]:
    out: list[Finding] = []
    avoid: dict[str, str] = {}  # lowercased term -> defining component id
    for c in store.components:
        if c.ctype == "term" and c.status == "current":
            for line in c.body.splitlines():
                m = AVOID_LINE_RE.match(line.strip())
                if m:
                    for raw_term in m.group(1).split(","):
                        term = raw_term.strip().rstrip(".").strip()
                        if term:
                            avoid.setdefault(term.lower(), c.cid)
    if not avoid:
        return out
    patterns = {
        term: re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
        for term in avoid
    }
    for c in store.components:
        if c.status == "superseded":
            continue
        for lineno, line in enumerate(c.body.splitlines(), start=1):
            if AVOID_LINE_RE.match(line.strip()) or ESCAPE_MARKER in line:
                continue
            for term, pattern in patterns.items():
                if pattern.search(line):
                    out.append(
                        error(
                            "E050",
                            c.rel,
                            f"body line {lineno} uses avoided term {term!r} (defined by {avoid[term]}); "
                            f"append {ESCAPE_MARKER} to the line if the mention is deliberate",
                            c.cid,
                        )
                    )
    return out


BANNER_OPEN = "<!-- grim:status -->"
BANNER_CLOSE = "<!-- /grim:status -->"
IMPLEMENTED_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:\s+\(PR #(\d+)\))?$")

# Errors that make a derived banner untrustworthy. Spec-local ones mean this
# file's own inputs are unreadable; the store-level ones (duplicate ids, bad
# edges, two live successors) mean the graph the banner summarizes is already
# known-wrong, and writing a summary of it would bake that into a frozen
# document. Same reasoning run_check gives for refusing to byte-compare a
# broken store. E090 is deliberately absent: it is the drift this repairs.
BANNER_BLOCKING_CODES = frozenset(
    {"E020", "E030", "E031", "E032", "E091", "E092", "E093", "E094"}
)


@dataclasses.dataclass
class WorkingDoc:
    path: Path
    rel: str
    kind: str  # "spec" or "plan"
    text: str
    fm: dict | None  # None when frontmatter is absent or unparseable


def supersede_index(store: Store) -> dict[str, list[str]]:
    """target id -> ids of components superseding it, whatever their status.

    Distinct from check_edges' live-successor map, which keys only when the
    successor is itself current and so cannot see past an intermediate link.
    """
    out: dict[str, list[str]] = {}
    for c in store.components:
        if not isinstance(c.cid, str):
            continue
        for target in c.supersedes:
            if isinstance(target, str):
                out.setdefault(target, []).append(c.cid)
    return {k: sorted(v) for k, v in out.items()}


def resolve_live_successors(
    cid: str, index: dict[str, list[str]], status_by_id: dict[str, str]
) -> list[str]:
    """Every current component reachable by following supersede edges forward.

    Returns ALL of them, sorted, rather than the nearest one. A forked chain
    can reach two live endpoints without tripping E031, which only inspects a
    target's immediate successors - so returning the first match would state
    one successor as authoritative while silently dropping the other.

    The visited set is mandatory, not defensive: check_edges rejects only
    self-supersede and missing targets, so two mutually-superseding components
    lint clean and would otherwise loop here forever.
    """
    seen = {cid}
    found: set[str] = set()
    frontier = list(index.get(cid, ()))
    while frontier:
        nxt: list[str] = []
        for succ in sorted(frontier):
            if succ in seen:
                continue
            seen.add(succ)
            if status_by_id.get(succ) == "current":
                found.add(succ)  # an endpoint; nothing supersedes a live node
                continue
            nxt.extend(index.get(succ, ()))
        frontier = nxt
    return sorted(found)


def abandoned_references(
    components: list[str], status_by_id: dict[str, str], index: dict[str, list[str]]
) -> list[str]:
    """Referenced components that were superseded with nothing live replacing them.

    The distinction the store cannot otherwise express. `superseded` is written
    both by "this decision was replaced" and by "this was never built", and the
    rendered banner collapses them: with every reference superseded,
    derive_banner takes its all-gone branch and prints a bare "Superseded.",
    so the 'abandoned' wording in the partial-supersession path is unreachable
    in exactly the case where it would matter. Reachability is the signal that
    survives - a replacement leaves a live successor behind, an abandonment
    does not.
    """
    return sorted(
        cid
        for cid in set(components)
        if status_by_id.get(cid) == "superseded"
        and not resolve_live_successors(cid, index, status_by_id)
    )


def parse_implemented(value) -> tuple[str, str | None]:
    """('2026-07-24', '14') from a stamp. Raises ValueError on any other shape.

    The canonical on-disk form is quoted. Unquoted, YAML reads ' #' as a comment
    and 'implemented: 2026-07-24 (PR #14)' silently becomes '2026-07-24 (PR'.
    A bare date is accepted because YAML hands it back as a date object.
    """
    if isinstance(value, datetime.datetime):
        raise ValueError("timestamp, not a date")
    if isinstance(value, datetime.date):
        return value.isoformat(), None
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError(f"{type(value).__name__}, not a string")
    m = IMPLEMENTED_RE.match(value.strip())
    if not m:
        raise ValueError(f"{value!r} is not 'YYYY-MM-DD' or 'YYYY-MM-DD (PR #N)'")
    date = m.group(1)
    # DATE_RE is load-bearing: fromisoformat alone accepts forms it rejects.
    if not (DATE_RE.fullmatch(date) and _valid_date(date)):
        raise ValueError(f"{date!r} is not a real calendar date")
    return date, m.group(2)


def _banner_span(text: str) -> tuple[int, int] | None:
    """Character span of the block interior, exclusive of both markers."""
    open_at = text.find(BANNER_OPEN)
    if open_at == -1:
        return None
    line_end = text.find("\n", open_at)
    if line_end == -1:
        return None
    close_at = text.find(BANNER_CLOSE, line_end)
    if close_at == -1:
        return None
    return line_end + 1, close_at


def derive_banner(
    components: list[str],
    implemented: tuple[str, str | None] | None,
    status_by_id: dict[str, str],
    index: dict[str, list[str]],
) -> str:
    """The exact block interior. Never empty - see adr-never-empty-banner.

    Composed rather than enumerated (adr-banner-qualifier-clauses): one
    provenance line, then qualifier clauses in fixed order, so an unmatched
    combination degrades to the provenance line rather than to silence.
    """
    if implemented is None:
        lines = ["> **Not yet implemented.**"]
    else:
        date, pr = implemented
        suffix = f" (PR #{pr})" if pr else ""
        lines = [f"> **Implemented {date}{suffix}.**"]

    known = [c for c in components if c in status_by_id]
    unknown = sorted(c for c in components if c not in status_by_id)
    drafts = sorted(c for c in known if status_by_id[c] == "draft")
    gone = sorted(c for c in known if status_by_id[c] == "superseded")

    qualified = False
    if not components:
        lines.append("> References no components.")
        qualified = True
    if unknown:
        lines.append(f"> Unknown references: {', '.join(unknown)}.")
        qualified = True
    if drafts:
        lines.append(f"> Not fully realized: {', '.join(drafts)} still draft.")
        qualified = True
    if gone:
        if known and len(gone) == len(known):
            lines.append("> Superseded.")
        else:
            pairs = ", ".join(
                f"{c} -> {' or '.join(resolve_live_successors(c, index, status_by_id)) or 'abandoned'}"
                for c in gone
            )
            lines.append(f"> Superseded in part: {pairs}")
        qualified = True
    # "References current." is the nothing-to-report line, and the design ties
    # it to the stamped case. On an unstamped spec the provenance line already
    # carries the news and already satisfies the never-empty rule, so adding it
    # there is noise. Notable qualifiers above still fire either way.
    if components and not qualified and implemented is not None:
        lines.append("> References current.")
    return "\n".join(lines) + "\n"


def _load_working_docs(cfg: Config) -> tuple[list[WorkingDoc], list[Finding]]:
    docs: list[WorkingDoc] = []
    out: list[Finding] = []
    seen: set[Path] = set()
    for kind, base in (("spec", cfg.specs), ("plan", cfg.plans)):
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            resolved = path.resolve()
            if resolved in seen:
                continue  # specs and plans configured to the same tree
            seen.add(resolved)
            rel = path.relative_to(cfg.root).as_posix()
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                out.append(warning("W060", rel, f"{kind} is not valid UTF-8; skipped"))
                continue
            m = FM_RE.match(text)
            fm = None
            if m:
                try:
                    parsed = yaml.safe_load(m.group(1))
                except yaml.YAMLError:
                    parsed = None
                if isinstance(parsed, dict):
                    fm = parsed
            docs.append(WorkingDoc(path=path, rel=rel, kind=kind, text=text, fm=fm))
    return docs, out


def _resolve_spec_path(cfg: Config, raw: str) -> Path | None:
    """Project-root-relative, refusing anything that escapes the root."""
    candidate = (cfg.root / raw).resolve()
    if not candidate.is_relative_to(cfg.root.resolve()):
        return None
    return candidate if candidate.is_file() else None


def analyze_working_layer(
    cfg: Config, store: Store
) -> tuple[list[Finding], dict[str, tuple[Path, str]]]:
    """Findings plus, per file, the banner interior it should carry."""
    out: list[Finding] = []
    docs, load_findings = _load_working_docs(cfg)
    out.extend(load_findings)

    status_by_id = {
        c.cid: c.status
        for c in store.components
        if isinstance(c.cid, str) and isinstance(c.status, str)
    }
    index = supersede_index(store)

    derived_by_path: dict[Path, str] = {}
    desired: dict[str, tuple[Path, str]] = {}

    for doc in (d for d in docs if d.kind == "spec"):
        fm = doc.fm
        if fm is None:
            out.append(error("E093", doc.rel, "spec frontmatter is missing or unparseable"))
            continue
        raw_components = fm.get("components", [])
        if raw_components is None:
            raw_components = []
        if not isinstance(raw_components, list) or not all(
            isinstance(c, str) for c in raw_components
        ):
            # An error, not a warning: derivation is skipped below, so no E090
            # can fire for this file, and a warning would let grim check pass
            # over a stale or empty banner. Same reasoning as E091.
            out.append(error("E094", doc.rel, "components: is not a list of strings"))
            continue
        implemented = None
        if "implemented" in fm:
            try:
                implemented = parse_implemented(fm["implemented"])
            except ValueError as exc:
                out.append(error("E091", doc.rel, f"malformed implemented: stamp: {exc}"))
                continue
        for cid in sorted(set(raw_components) - set(status_by_id)):
            out.append(warning("W092", doc.rel, f"components: names {cid!r}, not in the store"))
        known = [c for c in raw_components if c in status_by_id]
        abandoned = abandoned_references(known, status_by_id, index)
        # A stamp asserts the spec was implemented. Abandoning every component
        # it created says the opposite, and the two states are indistinguishable
        # downstream: a superseded component does not block a stamp (correctly,
        # since a decision later replaced was still implemented) and the banner
        # renders a plain "Superseded." either way. So the claim has to be
        # refused where it is made.
        if implemented is not None and known and len(abandoned) == len(known):
            out.append(error(
                "E095", doc.rel,
                f"stamped implemented, but every component it created was abandoned "
                f"with no live successor ({', '.join(abandoned)}); nothing was built, "
                f"so remove the stamp or supersede those components with what shipped",
            ))
        derived_by_path[doc.path.resolve()] = derive_banner(
            raw_components, implemented, status_by_id, index
        )

    for doc in docs:
        if doc.kind == "plan":
            fm = doc.fm or {}
            if "implemented" in fm:
                out.append(
                    error("E092", doc.rel, "plan carries implemented:; the stamp is spec-level")
                )
                continue
            raw = fm.get("spec")
            if not (isinstance(raw, str) and raw.strip()):
                out.append(warning("W060", doc.rel, "plan is missing spec: frontmatter"))
                interior = "> **Status unavailable: no spec declared.**\n"
            else:
                target = _resolve_spec_path(cfg, raw.strip())
                if target is None:
                    out.append(warning("W093", doc.rel, f"spec: {raw.strip()!r} does not resolve"))
                    interior = "> **Status unavailable: spec not found.**\n"
                elif target not in derived_by_path:
                    out.append(
                        warning("W093", doc.rel, f"spec: {raw.strip()!r} is not a governed spec")
                    )
                    interior = "> **Status unavailable: spec not governed.**\n"
                else:
                    interior = derived_by_path[target]
        else:
            resolved = doc.path.resolve()
            if resolved not in derived_by_path:
                continue  # a finding above already explains why
            interior = derived_by_path[resolved]

        span = _banner_span(doc.text)
        if span is None:
            code = "W090" if doc.kind == "spec" else "W091"
            out.append(
                warning(
                    code, doc.rel,
                    f"{doc.kind} has no {BANNER_OPEN} block; add one from the template",
                )
            )
            continue
        if doc.text[span[0] : span[1]] != interior:
            out.append(error("E090", doc.rel, f"banner block is out of date; {FIX_HINT}"))
            desired[doc.rel] = (doc.path, interior)
    return out, desired


def apply_banner_fixes(
    desired: dict[str, tuple[Path, str]], findings: list[Finding]
) -> list[str]:
    """Rewrite stale block interiors. Returns the rels actually repaired.

    Skips a file only on a BLOCKING error, never on E090 itself: apply_fixes'
    rule of skipping every file carrying any error would make --fix a no-op on
    exactly the files this exists to repair.
    """
    blocked = {f.path for f in findings if f.code in BANNER_BLOCKING_CODES}
    store_wide = any(f.code in BANNER_BLOCKING_CODES and f.path == "." for f in findings)
    # These are graph-level, so membership in BANNER_BLOCKING_CODES alone does
    # not stop them: that set is matched against the finding's path, which for
    # a graph error is a *component*, while the files rewritten here are
    # *specs*. E032 belongs here for the same reason as E031 - an un-cascaded
    # edge makes the derived banner actively wrong (it renders
    # "References current." about a component the store says was replaced),
    # and writing that into a frozen document bakes the error in.
    if store_wide or any(f.code in {"E020", "E030", "E031", "E032"} for f in findings):
        return []
    fixed: list[str] = []
    for rel, (path, interior) in sorted(desired.items()):
        if rel in blocked:
            continue
        if path.is_symlink():
            raise ConfigError(f"{rel} is a symlink; refusing to write through it")
        # Byte-level, not read_text/write_text: text mode normalizes CRLF to LF
        # on read and would rewrite the whole file with translated endings,
        # changing frozen bytes outside the block. Decoding read_bytes()
        # performs no newline translation, so the splice touches only the span.
        raw = path.read_bytes().decode("utf-8")
        span = _banner_span(raw)
        if span is None:
            continue
        path.write_bytes((raw[: span[0]] + interior + raw[span[1] :]).encode("utf-8"))
        fixed.append(rel)
    return fixed


def _yaml_scalar(value, *, key: str | None = None, in_flow: bool = False) -> str:
    """Emit a scalar that YAML re-parses to the same string, quoting only
    when the plain form would change meaning (e.g. yes -> bool) or break
    flow-sequence syntax (commas, brackets, braces)."""
    s = str(value)
    try:
        reparsed = yaml.safe_load(s)
    except yaml.YAMLError:
        reparsed = object()
    if key == "date" and isinstance(reparsed, datetime.date):
        reparsed = reparsed.isoformat()
    needs_quotes = reparsed != s
    if in_flow and any(ch in s for ch in ",[]{}"):
        needs_quotes = True
    return json.dumps(s) if needs_quotes else s


def _format_fm_value(key: str, value) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_yaml_scalar(v, key=key, in_flow=True) for v in value) + "]"
    return _yaml_scalar(value, key=key)


def normalize_component(c: Component) -> str:
    lines = ["---"]
    for key in FIELD_ORDER:
        if key in c.fm:
            lines.append(f"{key}: {_format_fm_value(key, c.fm[key])}")
    lines.append("---")
    body = c.body.strip("\n")
    if not body:
        return "\n".join(lines) + "\n"
    return "\n".join(lines) + "\n\n" + body + "\n"


HEADING_RE = re.compile(r"^(#{1,6})(?=\s)", re.MULTILINE)


def demote_headings(body: str, levels: int) -> str:
    return HEADING_RE.sub(lambda m: "#" * min(6, len(m.group(1)) + levels), body)


def store_hash(store: Store) -> str:
    h = hashlib.sha256()
    pairs = sorted(
        (c.rel, normalize_component(c))
        for c in store.components
        if c.status == "current"
    )
    for rel, content in pairs:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(content.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def apply_fixes(store: Store, findings: list[Finding]) -> list[str]:
    fixed: list[str] = []
    error_rels = {f.path for f in findings if f.level == "error"}
    for c in store.components:
        if c.rel in error_rels:
            continue
        new_text = normalize_component(c)
        if new_text != c.path.read_text(encoding="utf-8"):
            if c.path.is_symlink():
                raise ConfigError(f"{c.rel} is a symlink; refusing to render through it")
            c.path.write_text(new_text, encoding="utf-8")
            fixed.append(c.rel)
    return fixed


CHARTER_SECTIONS = (("usecase", "Use cases"), ("constraint", "Constraints"), ("nongoal", "Non-goals"))


def _current_sorted(store: Store, *types: str) -> list[Component]:
    return sorted(
        (c for c in store.components if c.status == "current" and c.ctype in types),
        key=lambda c: (str(c.fm.get("date", "")), str(c.cid or "")),
    )


def render_store(store: Store) -> dict[str, str]:
    header = [
        f"<!-- grim:store-hash sha256:{store_hash(store)} -->",
        "<!-- generated by grim render; do not edit -->",
        "",
    ]
    out: dict[str, str] = {}

    def emit(name: str, title: str, chunks: list[str]) -> None:
        if chunks:
            out[name] = "\n".join(header + [f"# {title}", ""] + chunks).rstrip("\n") + "\n"

    def body(c: Component, levels: int) -> str:
        return demote_headings(c.body.strip("\n"), levels) + "\n"

    charter: list[str] = []
    for ctype, heading in CHARTER_SECTIONS:
        comps = _current_sorted(store, ctype)
        if comps:
            charter.append(f"## {heading}\n")
            charter += [body(c, 2) for c in comps]
    emit("charter.md", "Charter", charter)
    emit("decisions.md", "Decisions", [body(c, 1) for c in _current_sorted(store, "adr")])
    emit("glossary.md", "Glossary", [body(c, 1) for c in _current_sorted(store, "term")])
    by_subsystem: dict[str, list[Component]] = {}
    for c in _current_sorted(store, "note"):
        sub = c.fm.get("subsystem")
        by_subsystem.setdefault(sub if isinstance(sub, str) and sub else "general", []).append(c)
    for sub, comps in sorted(by_subsystem.items()):
        emit(f"{sub}.md", sub, [body(c, 1) for c in comps])
    return out


@dataclasses.dataclass
class LintResult:
    findings: list[Finding]
    fixed: list[str]

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]

    @property
    def exit_code(self) -> int:
        return 1 if self.errors else 0

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": not self.errors,
                "errors": [dataclasses.asdict(f) for f in self.errors],
                "warnings": [dataclasses.asdict(f) for f in self.warnings],
                "fixed": self.fixed,
            },
            indent=2,
        )


@dataclasses.dataclass
class CheckResult:
    lint: LintResult
    mismatches: list[Finding]

    @property
    def exit_code(self) -> int:
        return 1 if self.lint.errors or self.mismatches else 0

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": self.exit_code == 0,
                "errors": [dataclasses.asdict(f) for f in self.lint.errors],
                "warnings": [dataclasses.asdict(f) for f in self.lint.warnings],
                "mismatches": [dataclasses.asdict(f) for f in self.mismatches],
            },
            indent=2,
        )


FIX_HINT = "run: grim lint --fix && grim render, and commit the result"


def run_lint(root: Path, *, fix: bool = False, strict: bool = False) -> LintResult:
    root = root.resolve()
    cfg = load_config(root)
    store = load_store(cfg)
    findings = list(store.findings)
    findings += check_schema(store, cfg)
    findings += check_ids(store)
    findings += check_edges(store)
    base, mb_findings = resolve_merge_base(cfg, strict)
    findings += mb_findings
    findings += check_transitions(store, cfg, base, strict)
    findings += check_touched_paths(store, cfg, base, strict)
    findings += check_avoid_terms(store)
    wl_findings, desired = analyze_working_layer(cfg, store)
    findings += wl_findings
    fixed: list[str] = []
    if fix:
        fixed = apply_fixes(store, findings)
        repaired = apply_banner_fixes(desired, findings)
        fixed += repaired
        # Findings are computed once above and exit_code derives from that
        # list, so a repaired E090 would leave the fixing run exiting non-zero
        # carrying the very error it just fixed - breaking `lint --fix &&
        # render`, which the instruction files, the CI recipe and the adoption
        # skill all mandate, and which is also the documented remedy for a
        # banner merge conflict. Drop what we repaired.
        done = set(repaired)
        findings = [f for f in findings if not (f.code == "E090" and f.path in done)]
    return LintResult(findings=findings, fixed=fixed)


def write_render(cfg: Config, rendered: dict[str, str]) -> tuple[list[str], list[str]]:
    written: list[str] = []
    removed: list[str] = []
    for name in rendered:
        # Belt and suspenders: the lint gate (E062) already rejects
        # path-hostile subsystems; refuse to write outside cfg.current
        # even if a future caller skips the gate.
        if "/" in name or "\\" in name or name.startswith("."):
            raise ValueError(f"unsafe render output name {name!r}")
    if rendered:
        cfg.current.mkdir(parents=True, exist_ok=True)
    for name in sorted(rendered):
        path = cfg.current / name
        if path.is_symlink():
            raise ConfigError(f"{path.relative_to(cfg.root).as_posix()} is a symlink; refusing to render through it")
        content_bytes = rendered[name].encode("utf-8")
        if not path.is_file() or path.read_bytes() != content_bytes:
            path.write_bytes(content_bytes)
            written.append(path.relative_to(cfg.root).as_posix())
    if cfg.current.is_dir():
        for path in sorted(cfg.current.glob("*.md")):
            if path.name not in rendered:
                path.unlink()
                removed.append(path.relative_to(cfg.root).as_posix())
    return written, removed


@dataclasses.dataclass
class RenderResult:
    written: list[str]
    removed: list[str]
    findings: list[Finding]

    @property
    def exit_code(self) -> int:
        return 1 if any(f.level == "error" for f in self.findings) else 0

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": self.exit_code == 0,
                "written": self.written,
                "removed": self.removed,
                "errors": [
                    dataclasses.asdict(f) for f in self.findings if f.level == "error"
                ],
                "warnings": [
                    dataclasses.asdict(f) for f in self.findings if f.level == "warning"
                ],
            },
            indent=2,
        )


def run_render(root: Path) -> RenderResult:
    root = root.resolve()
    lint = run_lint(root, fix=False, strict=False)
    if lint.errors:
        return RenderResult(written=[], removed=[], findings=lint.findings)
    # Re-reads disk after the gate; assumes no concurrent writer (single-user CLI).
    cfg = load_config(root)
    store = load_store(cfg)
    written, removed = write_render(cfg, render_store(store))
    return RenderResult(written=written, removed=removed, findings=lint.findings)


def run_check(root: Path) -> CheckResult:
    root = root.resolve()
    cfg = load_config(root)
    lint = run_lint(root, fix=False, strict=True)
    if lint.errors:
        # Don't render or byte-compare against a store we already know is
        # broken: lint errors (e.g. a reserved-name subsystem colliding with
        # a fixed output key, or traversal-shaped paths) can make render_store
        # silently drop or overwrite entries, producing bogus mismatches.
        # Lint errors alone already fail the exit code; no CI signal is lost.
        return CheckResult(lint=lint, mismatches=[])
    store = load_store(cfg)
    rendered = {name: content.encode("utf-8") for name, content in render_store(store).items()}
    committed = (
        {p.name: p.read_bytes() for p in sorted(cfg.current.glob("*.md"))}
        if cfg.current.is_dir()
        else {}
    )
    mismatches: list[Finding] = []
    current_rel = cfg.current.relative_to(cfg.root).as_posix()
    for name in sorted(set(rendered) | set(committed)):
        rel = f"{current_rel}/{name}"
        if name not in committed:
            mismatches.append(error("E080", rel, f"rendered file missing from {current_rel}/; {FIX_HINT}"))
        elif name not in rendered:
            mismatches.append(error("E080", rel, f"stale file: fresh render does not produce it; {FIX_HINT}"))
        elif committed[name] != rendered[name]:
            mismatches.append(error("E080", rel, f"out of date: committed bytes differ from fresh render; {FIX_HINT}"))
    return CheckResult(lint=lint, mismatches=mismatches)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grim", description="doc-components tooling")
    sub = parser.add_subparsers(dest="verb", required=True)
    lint_p = sub.add_parser("lint", help="validate the component store")
    lint_p.add_argument("--fix", action="store_true", help="normalize component formatting")
    lint_p.add_argument(
        "--strict", action="store_true",
        help="fail closed when the merge-base is unresolvable (CI mode)",
    )
    lint_p.add_argument("--json", action="store_true", help="machine-readable output")
    lint_p.add_argument(
        "--root", type=Path, default=Path.cwd(), help="project root (default: cwd)"
    )
    render_p = sub.add_parser("render", help="compile docs/current/ from current components")
    render_p.add_argument("--json", action="store_true", help="machine-readable output")
    render_p.add_argument("--root", type=Path, default=Path.cwd(), help="project root (default: cwd)")
    check_p = sub.add_parser("check", help="verify committed docs/current/ matches fresh render")
    check_p.add_argument("--json", action="store_true", help="machine-readable output")
    check_p.add_argument("--root", type=Path, default=Path.cwd(), help="project root (default: cwd)")
    args = parser.parse_args(argv)
    try:
        if args.verb == "lint":
            result = run_lint(args.root, fix=args.fix, strict=args.strict)
            if args.json:
                print(result.to_json())
            else:
                for f in result.findings:
                    location = f.path + (f" [{f.component}]" if f.component else "")
                    print(f"{f.level.upper()} {f.code} {location}: {f.message}")
                for rel in result.fixed:
                    print(f"FIXED {rel}")
                summary = f"{len(result.errors)} error(s), {len(result.warnings)} warning(s)"
                if result.fixed:
                    summary += f", {len(result.fixed)} file(s) fixed"
                print(summary)
            return result.exit_code
        elif args.verb == "render":
            result = run_render(args.root)
            if args.json:
                print(result.to_json())
            else:
                for f in result.findings:
                    if f.level == "error":
                        location = f.path + (f" [{f.component}]" if f.component else "")
                        print(f"{f.level.upper()} {f.code} {location}: {f.message}")
                if result.exit_code != 0:
                    print("render refused: fix lint errors first")
                else:
                    for rel in result.written:
                        print(f"RENDERED {rel}")
                    for rel in result.removed:
                        print(f"REMOVED {rel}")
                    summary = f"{len(result.written)} file(s) written, {len(result.removed)} removed"
                    print(summary)
            return result.exit_code
        elif args.verb == "check":
            result = run_check(args.root)
            if args.json:
                print(result.to_json())
            else:
                for f in result.lint.findings:
                    location = f.path + (f" [{f.component}]" if f.component else "")
                    print(f"{f.level.upper()} {f.code} {location}: {f.message}")
                if result.lint.errors:
                    print("byte-compare skipped: fix lint errors first")
                else:
                    for f in result.mismatches:
                        location = f.path + (f" [{f.component}]" if f.component else "")
                        print(f"{f.level.upper()} {f.code} {location}: {f.message}")
                lint_summary = f"{len(result.lint.errors)} error(s), {len(result.lint.warnings)} warning(s)"
                if result.mismatches:
                    lint_summary += f", {len(result.mismatches)} mismatch(es)"
                print(lint_summary)
            return result.exit_code
        else:
            raise AssertionError(f"unknown verb {args.verb!r}")
    except ConfigError as exc:
        print(f"grim: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"grim: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
