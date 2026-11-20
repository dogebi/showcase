#!/usr/bin/env python3
"""배포본(dogebi.github.io/showcase)을 실제로 받아 '모두 dogebi' 인지 검사한다.

검사 항목
  - REPOS 배열의 repo 링크가 전부 https://github.com/dogebi/<r> 인지
  - live 링크가 전부 https://dogebi.github.io/<r>/... 인지
  - 남은 sigco3111 문자열 개수 (카드 밖 포함)
  - 무작위 표본의 repo/live URL 이 실제 200 인지
결과는 JSON 한 줄로 출력(마지막 줄) + 사람이 읽는 요약을 stdout 에 낸다.
"""
from __future__ import annotations

import json
import random
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

URL = "https://dogebi.github.io/showcase/"
SRC, DST = "sigco3111", "dogebi"


def fetch() -> str:
    p = subprocess.run(["curl", "-sL", "--max-time", "40", URL], capture_output=True, text=True)
    return p.stdout


def http_ok(u: str) -> int:
    p = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-L",
                        "--max-time", "25", u], capture_output=True, text=True)
    return int(p.stdout.strip() or 0)


def main() -> int:
    sample = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    html = fetch()
    if len(html) < 10000:
        print(f"배포본 수신 실패(길이 {len(html)})")
        print(json.dumps({"ok": False, "reason": "fetch-failed"}))
        return 2

    m = re.search(r"(const REPOS = )(\[.*?\])(;)", html, re.S)
    if not m:
        print("REPOS 배열 없음")
        print(json.dumps({"ok": False, "reason": "no-repos"}))
        return 2
    data = json.loads(m.group(2))

    repo_dog = repo_src = live_dog = live_src = live_other = live_none = 0
    bad_repo: list[str] = []
    bad_live: list[str] = []
    for d in data:
        r = (d.get("repo") or "")
        if f"github.com/{DST}/" in r:
            repo_dog += 1
        elif f"github.com/{SRC}/" in r:
            repo_src += 1
            bad_repo.append(r)
        lv = (d.get("live") or "").strip()
        if not lv:
            live_none += 1
        elif f"://{DST}.github.io/" in lv:
            live_dog += 1
        elif f"://{SRC}.github.io/" in lv:
            live_src += 1
            bad_live.append(lv)
        else:
            live_other += 1

    sigco_strings = len(re.findall(SRC, html))

    # 표본 검증: 남은 것 우선 + 무작위
    cand = [d for d in data if f"github.com/{SRC}/" in (d.get("repo") or "")
            or f"://{SRC}.github.io/" in (d.get("live") or "").strip()]
    pick = cand[:sample]
    if not pick:
        pool = [d for d in data if d.get("repo") and d.get("live")]
        pick = random.sample(pool, min(sample, len(pool)))
    urls: list[str] = []
    for d in pick:
        urls.append(d["repo"])
        if (d.get("live") or "").strip():
            urls.append(d["live"].strip())
    with ThreadPoolExecutor(max_workers=10) as ex:
        codes = list(ex.map(http_ok, urls))
    broken = [(u, c) for u, c in zip(urls, codes) if c != 200]

    ok = (repo_src == 0 and live_src == 0 and not broken and sigco_strings == 0)
    print(f"카드 {len(data)}개 · repo dogebi {repo_dog} / sigco {repo_src}")
    print(f"live dogebi {live_dog} / sigco {live_src} / 외부 {live_other} / 없음 {live_none}")
    print(f"배포 HTML 내 '{SRC}' 문자열: {sigco_strings}")
    print(f"표본 {len(urls)}개 URL 검증 · 200 아닌 것 {len(broken)}")
    for u, c in broken[:10]:
        print(f"   {c} {u}")
    for u in bad_repo[:5]:
        print(f"   남은 repo: {u}")
    for u in bad_live[:5]:
        print(f"   남은 live: {u}")
    print(json.dumps({"ok": ok, "cards": len(data), "repo_dogebi": repo_dog, "repo_sigco": repo_src,
                      "live_dogebi": live_dog, "live_sigco": live_src, "live_other": live_other,
                      "live_none": live_none, "sigco_strings": sigco_strings,
                      "broken": len(broken), "checked": len(urls)}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
