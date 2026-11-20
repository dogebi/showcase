#!/usr/bin/env python3
"""쇼케이스의 저장소/라이브 링크를 dogebi 기준으로 재작성한다 (멱등).

규칙
  저장소  : dogebi/<r> 이 존재하면  https://github.com/dogebi/<r>
            없으면 원본 https://github.com/sigco3111/<r> 유지 (404 방지)
  라이브  : 라이브 URL 이 *.github.io/<r>/... 형태이고
            https://dogebi.github.io/<r>/... 가 실제로 200 이면 dogebi 로 교체.
            아니면 원본 유지 (데모가 죽지 않게)
  썸네일(raw.githubusercontent.com/...)은 원본 그대로 둔다.

index.html 의 `const REPOS = [...]` 는 JSON 파싱해서 구조적으로 고친다
(경로가 붙은 라이브 URL, 끝 공백 같은 것도 정확히 다룬다).

  python3 tools/relink.py [--dry] [--no-check]
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC, DST = "sigco3111", "dogebi"
ENV = dict(os.environ)
ENV.setdefault("HTTPS_PROXY", "http://127.0.0.1:1099")
ENV.setdefault("HTTP_PROXY", "http://127.0.0.1:1099")
GHIO = re.compile(r"^https://(?:sigco3111|dogebi)\.github\.io/([A-Za-z0-9._-]+)(/.*)?$")


def gh(args: list[str]) -> str | None:
    p = subprocess.run(["gh", *args], capture_output=True, text=True, env=ENV)
    return p.stdout if p.returncode == 0 else None


def dogebi_repos() -> set[str]:
    """방문자가 클릭할 수 있는(=공개) dogebi 저장소만. 비공개는 404 가 되므로 제외."""
    out: set[str] = set()
    for page in range(1, 21):
        s = gh(["api", f"user/repos?per_page=100&page={page}&affiliation=owner",
                "--jq", ".[] | select(.private==false) | .name"])
        if not s:
            break
        names = [x for x in s.split() if x]
        if not names:
            break
        out.update(names)
    return out


def http_ok(url: str) -> bool:
    p = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-L",
                        "--max-time", "20", url], capture_output=True, text=True, env=ENV)
    return p.stdout.strip() == "200"


def live_target(url: str | None) -> tuple[str, str] | None:
    """(repo, rest) 또는 None"""
    if not url:
        return None
    m = GHIO.match(url.strip())
    if not m:
        return None
    return m.group(1), m.group(2) or "/"


def main() -> None:
    dry = "--dry" in sys.argv
    check = "--no-check" not in sys.argv
    have = dogebi_repos()
    print(f"dogebi 저장소 {len(have)}개")

    path = os.path.join(ROOT, "index.html")
    html = open(path, encoding="utf-8").read()
    m = re.search(r"(const REPOS = )(\[.*?\])(;\n)", html, re.S)
    if not m:
        sys.exit("REPOS 배열을 찾지 못했습니다")
    data = json.loads(m.group(2))

    # 1) 저장소 링크
    st = {"repo→dogebi": 0, "repo→원본": 0, "live→dogebi": 0, "live→원본": 0, "live→검증실패": 0}
    for d in data:
        r = (d.get("repo") or "").rstrip("/").rsplit("/", 1)[-1]
        if r in have:
            d["repo"] = f"https://github.com/{DST}/{r}"
            st["repo→dogebi"] += 1
        else:
            d["repo"] = f"https://github.com/{SRC}/{r}"
            st["repo→원본"] += 1

    # 2) 라이브 링크 — 양방향 검증: dogebi 가 살아 있으면 dogebi, 죽어 있으면 원본으로 폴백
    want: dict[str, str] = {}           # 현재 url -> 반대편 url
    for d in data:
        raw = d.get("live")
        t = live_target(raw)
        if not t:
            continue
        r, rest = t
        src = raw.strip()
        if src.startswith(f"https://{SRC}.github.io/"):
            want[src] = f"https://{DST}.github.io/{r}{rest}"
        else:
            want[src] = f"https://{SRC}.github.io/{r}{rest}"

    ok: set[str] = set()
    if check and want:
        probe_urls = sorted(set(want) | set(want.values()))
        with ThreadPoolExecutor(max_workers=12) as ex:
            for u, good in ex.map(lambda u: (u, http_ok(u)), probe_urls):
                if good:
                    ok.add(u)
        print(f"라이브 URL {len(probe_urls)}개 검증 · 200 OK {len(ok)}개")

    for d in data:
        raw = d.get("live")
        if not raw:
            continue
        src = raw.strip()
        alt = want.get(src)
        if not alt:
            continue
        currently_dogebi = src.startswith(f"https://{DST}.github.io/")
        if not check:
            if not currently_dogebi:
                d["live"] = alt
                st["live→dogebi"] += 1
            continue
        if currently_dogebi and src in ok:
            d["live"] = src
        elif alt in ok:
            d["live"] = alt
            if currently_dogebi:
                st["live→원본"] += 1
            else:
                st["live→dogebi"] += 1
        elif src in ok:
            d["live"] = src
        else:
            d["live"] = src
            st["live→검증실패"] += 1

    new_block = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    new_html = html[:m.start(2)] + new_block + html[m.end(2):]

    # 2b) 썸네일(raw.githubusercontent) 도 dogebi 로 — 포크는 이력·파일이 동일하므로 같은 경로가 200.
    #     단 원본 호스트를 지울 때 깨지지 않도록 dogebi 쪽 URL 을 실제로 확인한다.
    RAW = re.compile(r"raw\.githubusercontent\.com/" + SRC + r"/([A-Za-z0-9._-]+)/")
    cand: dict[str, str] = {}          # 원본 raw URL -> dogebi raw URL
    for d in data:
        u = d.get("screenshot") or ""
        mm = RAW.search(u)
        if mm and mm.group(1) in have:
            cand[u] = u.replace(f"raw.githubusercontent.com/{SRC}/",
                                f"raw.githubusercontent.com/{DST}/", 1)
    if cand:
        probe = sorted(cand.values())
        okraw: set[str] = set()
        if check:
            with ThreadPoolExecutor(max_workers=12) as ex:
                for u, good in ex.map(lambda u: (u, http_ok(u)), probe):
                    if good:
                        okraw.add(u)
        else:
            okraw = set(probe)
        st["thumb→dogebi"] = 0
        st["thumb→원본"] = 0
        for d in data:
            u = d.get("screenshot") or ""
            alt = cand.get(u)
            if not alt:
                continue
            if alt in okraw:
                d["screenshot"] = alt
                st["thumb→dogebi"] += 1
            else:
                st["thumb→원본"] += 1
        new_block = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        new_html = html[:m.start(2)] + new_block + html[m.end(2):]

    # 2c) REPOS 밖(배너·설명문 등)에 남은 원본 저장소 링크 중 dogebi 에 실제로 있는 것만 치환
    def _swap(mo: re.Match) -> str:
        return mo.group(0) if mo.group(1) not in have else f"https://github.com/{DST}/{mo.group(1)}"

    new_html = re.sub(r"https://github\.com/" + SRC + r"/([A-Za-z0-9._-]+)", _swap, new_html)

    if new_html != html and not dry:
        open(path, "w", encoding="utf-8").write(new_html)

    # 3) sync-state.json / README.md 의 호스트 표기 정리
    for f in ("sync-state.json", "README.md"):
        p = os.path.join(ROOT, f)
        if not os.path.exists(p):
            continue
        s = open(p, encoding="utf-8").read()
        o = s
        if f == "sync-state.json":
            s = re.sub(r"https://github\.com/" + SRC + r"/", f"https://github.com/{DST}/", s)
            s = re.sub(r"https://" + SRC + r"\.github\.io/", f"https://{DST}.github.io/", s)
        else:
            s = s.replace(f"https://github.com/{SRC}/showcase", f"https://github.com/{DST}/showcase")
        if s != o and not dry:
            open(p, "w", encoding="utf-8").write(s)

    print(json.dumps(st, ensure_ascii=False), "dry-run=" + str(dry))


if __name__ == "__main__":
    main()
