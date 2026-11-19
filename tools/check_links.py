#!/usr/bin/env python3
"""쇼케이스 REPOS 의 모든 링크 상태를 점검해 JSON 으로 남긴다.

  python3 tools/check_links.py            # 기본: 저장소+라이브 전수
  python3 tools/check_links.py --repo-only
결과: /tmp/showcase_link_status.json
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = "/tmp/showcase_link_status.json"
ENV = dict(os.environ)
ENV.setdefault("HTTPS_PROXY", "http://127.0.0.1:1099")
ENV.setdefault("HTTP_PROXY", "http://127.0.0.1:1099")


def code(url: str) -> int:
    p = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-L",
                        "--max-time", "20", url], capture_output=True, text=True, env=ENV)
    try:
        return int(p.stdout.strip() or 0)
    except ValueError:
        return 0


def main() -> None:
    html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    m = re.search(r"const REPOS = (\[.*?\]);\n", html, re.S)
    data = json.loads(m.group(1))
    urls: dict[str, str] = {}
    for d in data:
        for k in ("repo", "live"):
            u = (d.get(k) or "").strip()
            if u.startswith("http"):
                urls[u] = k
    if "--repo-only" in sys.argv:
        urls = {u: k for u, k in urls.items() if k == "repo"}
    print(f"점검 URL {len(urls)}개")

    with ThreadPoolExecutor(max_workers=12) as ex:
        res = dict(zip(urls, ex.map(code, list(urls))))

    bad = {u: c for u, c in res.items() if c != 200}
    summary: dict[str, dict[str, int]] = {}
    for u, c in res.items():
        host = re.sub(r"^https://([^/]+).*", r"\1", u)
        summary.setdefault(host, {}).setdefault(str(c), 0)
        summary[host][str(c)] += 1
    json.dump({"summary": summary, "bad": bad}, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"200 아님 {len(bad)}개 → {OUT}")


if __name__ == "__main__":
    main()
