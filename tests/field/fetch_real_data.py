#!/usr/bin/env python3
"""Fetch real-world data from 4 popular Python repos for field test prompts.

Pulls GitHub issues (open + closed), PRs, and code snippets from:
  - fastapi/fastapi
  - pydantic/pydantic
  - encode/httpx      (issues disabled — PRs only)
  - Textualize/rich

Saves structured JSON to tests/field/realdata/ so the field test runner
can use real prompts instead of synthetic ones.

Usage:
    python tests/field/fetch_real_data.py                    # default: 50 per repo
    python tests/field/fetch_real_data.py --limit 200        # 200 issues + 200 PRs per repo
    python tests/field/fetch_real_data.py --repos fastapi pydantic
"""

import argparse
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent / "realdata"

REPOS = {
    "fastapi":   {"full": "fastapi/fastapi",       "code_paths": ["fastapi/applications.py", "fastapi/routing.py"]},
    "pydantic":  {"full": "pydantic/pydantic",     "code_paths": ["pydantic/main.py", "pydantic/types.py"]},
    "httpx":     {"full": "encode/httpx",           "code_paths": ["httpx/_client.py", "httpx/_models.py"]},
    "rich":      {"full": "Textualize/rich",        "code_paths": ["rich/table.py", "rich/console.py"]},
}

# gh paginates internally — supports --limit up to 1000
GH_MAX = 1000


def gh_run(args: list[str], timeout: int = 60) -> str:
    try:
        r = subprocess.run(
            ["gh"] + args,
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            print(f"  WARN: gh failed: {r.stderr.strip()[:120]}", file=sys.stderr)
            return ""
        return r.stdout
    except Exception as e:
        print(f"  WARN: {e}", file=sys.stderr)
        return ""


def fetch_issues(repo_full: str, limit: int) -> list[dict]:
    n = min(limit, GH_MAX)
    raw = gh_run([
        "issue", "list", "--repo", repo_full,
        "--limit", str(n),
        "--state", "all",
        "--json", "number,title,labels,body",
    ], timeout=120)
    if not raw.strip():
        return []
    items = json.loads(raw)
    return [
        {
            "repo": repo_full,
            "number": i.get("number"),
            "title": i.get("title", ""),
            "labels": [lb.get("name") for lb in i.get("labels", [])],
            "body": (i.get("body") or "")[:2000],
        }
        for i in items
    ]


def fetch_prs(repo_full: str, limit: int) -> list[dict]:
    n = min(limit, GH_MAX)
    raw = gh_run([
        "pr", "list", "--repo", repo_full,
        "--limit", str(n),
        "--state", "all",
        "--json", "number,title,body",
    ], timeout=120)
    if not raw.strip():
        return []
    items = json.loads(raw)
    return [
        {
            "repo": repo_full,
            "number": p.get("number"),
            "title": p.get("title", ""),
            "body": (p.get("body") or "")[:2000],
        }
        for p in items
    ]


def fetch_code(repo_full: str, paths: list[str]) -> list[dict]:
    snippets = []
    for path in paths:
        raw = gh_run([
            "api", f"repos/{repo_full}/contents/{path}", "--jq", ".content",
        ], timeout=15)
        if not raw.strip():
            continue
        try:
            decoded = base64.b64decode(raw).decode("utf-8", errors="replace")
            lines = decoded.split("\n")
            snippets.append({
                "repo": repo_full,
                "path": path,
                "lines": len(lines),
                "content": decoded[:4000],
            })
        except Exception as e:
            print(f"  WARN: decode {path}: {e}", file=sys.stderr)
    return snippets


def main():
    parser = argparse.ArgumentParser(
        description="Fetch real data from 4 popular Python repos for field test prompts"
    )
    parser.add_argument("--limit", type=int, default=50,
                        help="Max issues/PRs per repo (default 50, max 1000)")
    parser.add_argument("--repos", nargs="*",
                        choices=list(REPOS.keys()),
                        default=list(REPOS.keys()),
                        help="Which repos to fetch (default: all 4)")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_issues = []
    all_prs = []
    all_code = []
    per_repo = {}

    for name in args.repos:
        info = REPOS[name]
        full = info["full"]
        print(f"\n{'=' * 60}")
        print(f"  {name}  ({full})")
        print(f"{'=' * 60}")

        print(f"  Fetching up to {args.limit} issues...")
        issues = fetch_issues(full, args.limit)
        all_issues.extend(issues)
        print(f"    {len(issues)} issues")

        print(f"  Fetching up to {args.limit} PRs...")
        prs = fetch_prs(full, args.limit)
        all_prs.extend(prs)
        print(f"    {len(prs)} PRs")

        print("  Fetching code snippets...")
        code = fetch_code(full, info["code_paths"])
        all_code.extend(code)
        print(f"    {len(code)} files")

        per_repo[name] = {
            "issues": len(issues),
            "prs": len(prs),
            "code": len(code),
        }
        time.sleep(1)

    issues_path = OUTPUT_DIR / "issues.json"
    prs_path = OUTPUT_DIR / "prs.json"
    code_path = OUTPUT_DIR / "code_snippets.json"

    with open(issues_path, "w") as f:
        json.dump(all_issues, f, indent=2)
    with open(prs_path, "w") as f:
        json.dump(all_prs, f, indent=2)
    with open(code_path, "w") as f:
        json.dump(all_code, f, indent=2)

    index = {
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "limit_per_repo": args.limit,
        "repos": {name: REPOS[name]["full"] for name in args.repos},
        "per_repo": per_repo,
        "totals": {
            "issues": len(all_issues),
            "prs": len(all_prs),
            "code_snippets": len(all_code),
        },
        "files": {
            "issues": str(issues_path.name),
            "prs": str(prs_path.name),
            "code_snippets": str(code_path.name),
        },
    }
    index_path = OUTPUT_DIR / "index.json"
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)

    print(f"\n{'=' * 60}")
    print("  DONE")
    print(f"{'=' * 60}")
    print(f"  Saved to:  {OUTPUT_DIR}/")
    print(f"  Issues:    {len(all_issues)}")
    print(f"  PRs:       {len(all_prs)}")
    print(f"  Code:      {len(all_code)} files")
    print(f"  Index:     {index_path.name}")
    print()
    print("  Per repo:")
    for name, counts in per_repo.items():
        print(f"    {name:12s}  issues={counts['issues']:4d}  prs={counts['prs']:4d}  code={counts['code']}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
