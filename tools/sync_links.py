#!/usr/bin/env python3
"""showcase 링크를 '실제로 존재하는 대상'으로 맞춘다 (멱등).

규칙
  저장소 버튼 : dogebi/<r> 가 존재하면 github.com/dogebi/<r>, 아니면 github.com/<원본>/<r>
  라이브 버튼 : dogebi/<r> 의 Pages 가 built 면 dogebi.github.io/<r>/, 아니면 원본 github.io/<r>/

멱등이라 여러 번 실행해도 안전하고, 미러가 진행될수록 자동으로 dogebi 쪽으로 옮겨간다.
  python3 tools/sync_links.py [--dry]
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = "sigco3111"
DST = "dogebi"
FILES = ["index.html", "sync-state.json", "README.md"]
ENV = dict(os.environ, HTTPS_PROXY=os.environ.get("HTTPS_PROXY", "http://127.0.0.1:1099"),
           HTTP_PROXY=os.environ.get("HTTP_PROXY", "http://127.0.0.1:1099"))


def gh_jq(path: str, jq: str) -> str | None:
    p = subprocess.run(["gh", "api", path, "--jq", jq], capture_output=True, text=True, env=ENV)
    return p.stdout.strip() if p.returncode == 0 else None


def dogebi_repos() -> set[str]:
    out: set[str] = set()
    page = 1
    while True:
        p = subprocess.run(
            ["gh", "api", f"user/repos?per_page=100&page={page}&affiliation=owner",
             "--jq", ".[].name"],
            capture_output=True, text=True, env=ENV)
        if p.returncode != 0:
            break
        names = [x for x in p.stdout.split() if x]
        if not names:
            break
        out.update(names)
        page += 1
        if page > 20:
            break
    return out


def pages_built(repos: set[str]) -> set[str]:
    """dogebi/<r> 의 Pages 가 built 인 저장소. API 결과를 캐시한다."""
    from concurrent.futures import ThreadPoolExecutor
    cache_path = "/tmp/pages_built.json"
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    todo = [r for r in sorted(repos) if r not in cache]
    if todo:
        def probe(r: str) -> tuple[str, str]:
            st2 = gh_jq(f"repos/{DST}/{r}/pages", ".status")
            return r, (st2 or "none")
        with ThreadPoolExecutor(max_workers=6) as ex:
            for r, st2 in ex.map(probe, todo):
                cache[r] = st2
        json.dump(cache, open(cache_path, "w"))
    return {r for r in repos if cache.get(r) in ("built", "building")}


def main() -> None:
    dry = "--dry" in sys.argv
    have = dogebi_repos()
    built = pages_built(have)
    print(f"dogebi 저장소 {len(have)}개 · Pages built {len(built)}개")

    stats = {"repo→dogebi": 0, "repo→원본": 0, "live→dogebi": 0, "live→원본": 0}
    for f in FILES:
        path = os.path.join(ROOT, f)
        if not os.path.exists(path):
            continue
        s = open(path, encoding="utf-8").read()
        orig = s

        # 1) 저장소 링크: dogebi/<r> 가 없으면 원본으로 되돌린다
        def fix_gh(m: re.Match) -> str:
            r = m.group(1)
            if r in have:
                stats["repo→dogebi"] += 1
                return f"https://github.com/{DST}/{r}"
            stats["repo→원본"] += 1
            return f"https://github.com/{SRC}/{r}"

        s = re.sub(r"https://github\.com/dogebi/([A-Za-z0-9._-]+)", fix_gh, s)
        s = re.sub(r"https://github\.com/sigco3111/([A-Za-z0-9._-]+)", fix_gh, s)

        # 2) 라이브 링크: dogebi Pages 가 built 인 앱만 dogebi 로
        def fix_live(m: re.Match) -> str:
            r = m.group(1)
            if r in built:
                stats["live→dogebi"] += 1
                return f"https://{DST}.github.io/{r}/"
            stats["live→원본"] += 1
            return f"https://{SRC}.github.io/{r}/"

        s = re.sub(r"https://(?:dogebi|sigco3111)\.github\.io/([A-Za-z0-9._-]+)/", fix_live, s)

        if s != orig and not dry:
            open(path, "w", encoding="utf-8").write(s)
    print(json.dumps(stats, ensure_ascii=False), "dry-run=" + str(dry))


if __name__ == "__main__":
    main()
