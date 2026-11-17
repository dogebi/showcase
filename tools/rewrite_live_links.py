#!/usr/bin/env python3
"""원본 Pages 가 dogebi fork 에서 정상 빌드된 앱만 라이브 링크를 dogebi 로 바꾼다.

- 미러 상태(/tmp/mirror_state.json)의 status == "built" 인 저장소만 대상
- 나머지는 원래 링크(sigco3111.github.io) 유지 → 데모가 죽지 않게
- 사용법: python3 tools/rewrite_live_links.py [--dry]
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = "/tmp/mirror_state.json"
SRC_HOST = "https://sigco3111.github.io/"
DST_HOST = "https://dogebi.github.io/"


def built_repos() -> set[str]:
    st = json.load(open(STATE))
    out = set()
    for name, rec in st.get("repos", {}).items():
        if rec.get("status") == "built":
            out.add(name)
    return out


def live_ok(url: str) -> bool:
    env = dict(os.environ)
    env.setdefault("HTTPS_PROXY", "http://127.0.0.1:1099")
    p = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                        "--max-time", "25", url], capture_output=True, text=True, env=env)
    return p.stdout.strip() == "200"


def main() -> None:
    dry = "--dry" in sys.argv
    ok_repos = built_repos()
    files = ["index.html", "sync-state.json", "README.md"]
    total = 0
    changed = {}
    for f in files:
        path = os.path.join(ROOT, f)
        if not os.path.exists(path):
            continue
        s = open(path, encoding="utf-8").read()

        def repl(m: re.Match) -> str:
            nonlocal total
            repo = m.group(1)
            if repo not in ok_repos:
                return m.group(0)
            total += 1
            changed[repo] = changed.get(repo, 0) + 1
            return DST_HOST + repo + "/"

        s2 = re.sub(r"https://sigco3111\.github\.io/([A-Za-z0-9._-]+)/", repl, s)
        if s2 != s and not dry:
            open(path, "w", encoding="utf-8").write(s2)
    print(f"dogebi 라이브로 전환한 링크 {total}개 · 앱 {len(changed)}개 (dry-run={dry})")
    sample = sorted(changed)[:5]
    for r in sample:
        url = DST_HOST + r + "/"
        print(f"  {url} → {'200 OK' if live_ok(url) else 'FAIL'}")


if __name__ == "__main__":
    main()
