#!/usr/bin/env python3
"""Check the vendored apache/ossie files against upstream.

Two independent checks, both must pass:

1. integrity — each vendored file is byte-identical to the pinned upstream ref
   recorded in UPSTREAM.json (i.e. nobody edited the vendored copy locally).
2. drift     — each vendored file is byte-identical to the current upstream
   branch (i.e. upstream has not moved on since we vendored).

Exit status: 0 if both pass, 1 on any mismatch, 2 on a fetch/config error.
Stdlib only, so it runs under `uv run` or a bare `python3 >= 3.11`.

Usage:
    uv run scripts/check_ossie_drift.py            # check against UPSTREAM.json's branch
    uv run scripts/check_ossie_drift.py --ref v0.2.0 # check against another ref
    uv run scripts/check_ossie_drift.py --no-integrity
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PIN_FILE = REPO_ROOT / "src" / "fwoosh" / "_vendor" / "ossie" / "UPSTREAM.json"
RAW_URL = "https://raw.githubusercontent.com/{repo}/{ref}/{path}"
MAX_DIFF_LINES = 60


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def fetch(repo: str, ref: str, path: str) -> bytes:
    url = RAW_URL.format(repo=repo, ref=ref, path=path)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - fixed https host
            return resp.read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"error: HTTP {e.code} fetching {url}") from e
    except urllib.error.URLError as e:
        raise SystemExit(f"error: cannot reach {url}: {e.reason}") from e


def unified_diff(local: bytes, remote: bytes, label: str) -> str:
    lines = list(
        difflib.unified_diff(
            local.decode("utf-8", "replace").splitlines(keepends=True),
            remote.decode("utf-8", "replace").splitlines(keepends=True),
            fromfile=f"vendored/{label}",
            tofile=f"upstream/{label}",
            n=2,
        )
    )
    if len(lines) > MAX_DIFF_LINES:
        lines = lines[:MAX_DIFF_LINES] + [f"... ({len(lines) - MAX_DIFF_LINES} more lines)\n"]
    return "".join(lines)


def compare(kind: str, upstream_path: str, local: bytes, remote: bytes) -> bool:
    if local == remote:
        print(f"  ok    {kind:<9} {upstream_path} ({sha256(local)})")
        return True
    print(f"  FAIL  {kind:<9} {upstream_path} vendored={sha256(local)} upstream={sha256(remote)}")
    print(unified_diff(local, remote, Path(upstream_path).name), end="")
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--ref", help="upstream ref to check drift against (default: branch in UPSTREAM.json)"
    )
    parser.add_argument(
        "--no-integrity", action="store_true", help="skip the pinned-ref integrity check"
    )
    args = parser.parse_args(argv)

    pin = json.loads(PIN_FILE.read_text())
    repo, pinned_ref = pin["repo"], pin["ref"]
    drift_ref = args.ref or pin["branch"]

    print(f"vendored from {repo}@{pinned_ref[:12]}; checking drift against {drift_ref}")
    ok = True
    for upstream_path, local_rel in pin["files"].items():
        local_path = REPO_ROOT / local_rel
        if not local_path.exists():
            print(f"  FAIL  missing   {local_rel}")
            ok = False
            continue
        local = local_path.read_bytes()

        if not args.no_integrity:
            ok &= compare("integrity", upstream_path, local, fetch(repo, pinned_ref, upstream_path))
        ok &= compare("drift", upstream_path, local, fetch(repo, drift_ref, upstream_path))

    if ok:
        print("no drift.")
        return 0
    print(
        "\nvendored ossie files differ from upstream. To resync: re-copy the files listed in\n"
        f'{PIN_FILE.relative_to(REPO_ROOT)}, set "ref" to the upstream commit you copied from,\n'
        "review the diff for changes fwoosh must adapt to, then rerun this check.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
