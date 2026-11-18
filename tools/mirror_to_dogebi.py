#!/usr/bin/env python3
"""showcase 카드의 저장소를 dogebi 계정으로 fork 하고 Pages 를 켠다.

- 대상: sync-state.json 의 전체 프로젝트 + index.html 의 원본(sigco3111) 참조
  (index.html 의 dogebi 참조만 읽으면 '이미 미러된 것'만 대상이 되는 순환 논리가 된다)
- 각 단계는 재개 가능 (/tmp/mirror_state.json) — fork 완료 여부는 항상 GitHub 실상태로 재확인한다
- 사용법:
    python3 tools/mirror_to_dogebi.py list      # 대상 출력
    python3 tools/mirror_to_dogebi.py fork      # fork (순차, 적응형 간격)
    python3 tools/mirror_to_dogebi.py pages     # 원본 Pages 설정 복제 + 활성화
    python3 tools/mirror_to_dogebi.py verify    # 빌드 상태 확인
    python3 tools/mirror_to_dogebi.py all       # fork → pages → verify
- 환경변수: FORK_GAP (기본 8초) — 최소 간격. 403 이 뜨면 자동으로 2배씩 늘린다(최대 120초).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = "/tmp/mirror_state.json"
SRC = "sigco3111"
DST = "dogebi"
WORKERS = 1
FORK_GAP = float(os.environ.get("FORK_GAP", "8"))
MAX_GAP = 120.0
_lock = threading.Lock()
_STOP = threading.Event()          # 하드 블록(콘텐츠 생성 차단) 감지 시 이번 배치만 중단
_gap = [FORK_GAP]


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"repos": {}, "started": time.time()}


def save_state(st: dict) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


def log(msg: str) -> None:
    with _lock:
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def current_gap() -> float:
    with _lock:
        return _gap[0]


def bump_gap(reason: str) -> float:
    with _lock:
        _gap[0] = min(_gap[0] * 2, MAX_GAP)
        g = _gap[0]
    log(f"레이트리밋 신호({reason[:40]}) → fork 간격 {g:.0f}s")
    return g


def gh(args: list[str], retries: int = 3) -> tuple[bool, str]:
    """gh api 호출. (ok, stdout|error)"""
    env = dict(os.environ)
    env.setdefault("HTTPS_PROXY", "http://127.0.0.1:1099")
    env.setdefault("HTTP_PROXY", "http://127.0.0.1:1099")
    for attempt in range(retries):
        p = subprocess.run(["gh", "api", *args], capture_output=True, text=True, env=env)
        if p.returncode == 0:
            return True, p.stdout
        err = (p.stderr or "").strip()
        low = err.lower()
        if "too quickly" in low or "secondary rate" in low or "abuse" in low \
                or "content creation" in low:
            g = bump_gap(err)
            if "content creation" in low:
                _STOP.set()            # 이 상태에서는 fork·repo 생성이 전부 403 → 다음 배치로
            time.sleep(min(g * 2, 120.0))
            continue
        if "rate limit" in low or " 403" in low or " 429" in low or "abuse" in low:
            time.sleep(30 * (attempt + 1))
            continue
        return False, err
    return False, "rate limited"


def repo_list() -> list[str]:
    """미러 대상 저장소 전체. 대표 카드(featured)를 앞에 둔다."""
    from collections import OrderedDict

    st_path = os.path.join(ROOT, "sync-state.json")
    names: "OrderedDict[str, None]" = OrderedDict()
    if os.path.exists(st_path):
        st = json.load(open(st_path, encoding="utf-8"))
        for rec in st.get("entries", {}).values():
            r = rec.get("repo")
            if r:
                names[r] = None
    html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    for r in re.findall(r"https://github\.com/(?:sigco3111|dogebi)/([A-Za-z0-9._-]+)", html):
        names[r] = None

    allr = sorted(names)
    feat_path = "/tmp/featured_repos.json"
    feat = json.load(open(feat_path)) if os.path.exists(feat_path) else []
    head = [r for r in feat if r in names]
    return head + [r for r in allr if r not in head]


def exists(owner: str, name: str) -> bool:
    ok, out = gh([f"repos/{owner}/{name}", "--jq", ".name"])
    return ok


def do_fork(name: str, st: dict) -> str:
    if exists(DST, name):
        return "exists"
    ok, out = gh([f"repos/{SRC}/{name}", "--jq", ".name"])
    if not ok:
        return "source-missing"
    time.sleep(current_gap())
    ok, err = gh(["-X", "POST", f"repos/{SRC}/{name}/forks", "--jq", ".full_name"])
    if not ok:
        return f"fork-fail:{err[:60]}"
    for _ in range(15):
        if exists(DST, name):
            return "forked"
        time.sleep(4)
    return "fork-pending"


def do_pages(name: str, st: dict) -> str:
    ok, out = gh([f"repos/{SRC}/{name}/pages", "--jq", '.source.branch + "|" + .source.path'])
    if not ok:
        return "no-source-pages"          # 원본에 Pages 없음 → 라이브 링크는 외부일 것
    branch, _, path = out.strip().strip('"').partition("|")
    ok2, err2 = gh(["-X", "POST", f"repos/{DST}/{name}/pages",
                    "-f", f"source[branch]={branch}", "-f", f"source[path]={path}"])
    if ok2:
        return f"pages-on:{branch}{path}"
    if "already enabled" in err2.lower() or "409" in err2:
        return f"pages-on:{branch}{path}"
    return f"pages-fail:{err2[:70]}"


def do_verify(name: str, st: dict) -> str:
    ok, out = gh([f"repos/{DST}/{name}/pages", "--jq", ".status"])
    return out.strip() if ok else "no-pages"


PHASES = {"fork": do_fork, "pages": do_pages, "verify": do_verify}


def run_phase(phase: str, names: list[str]) -> None:
    st = load_state()
    st.setdefault("repos", {})
    todo = []
    for n in names:
        rec = st["repos"].setdefault(n, {})
        if phase == "fork" and rec.get("fork") in ("forked", "exists"):
            continue
        if phase == "pages" and rec.get("pages"):
            continue
        if phase == "verify" and rec.get("status") in ("built", "no-pages"):
            continue
        todo.append(n)
    log(f"[{phase}] 대상 {len(todo)} / 전체 {len(names)} (gap {current_gap():.0f}s)")
    fn = PHASES[phase]
    for i, n in enumerate(todo, 1):
        if _STOP.is_set():
            log(f"[{phase}] 하드 블록 감지 → {len(todo) - i + 1}건은 다음 배치로 이월")
            break
        try:
            res = fn(n, st)
        except Exception as e:                      # noqa: BLE001
            res = f"error:{type(e).__name__}"
        with _lock:
            st["repos"].setdefault(n, {})[phase if phase != "verify" else "status"] = res
            save_state(st)
        log(f"[{phase}] {i}/{len(todo)} {n} → {res}")
    save_state(st)
    log(f"[{phase}] 완료")


def _limit(names: list[str]) -> list[str]:
    for i, a in enumerate(sys.argv):
        if a == "--limit" and i + 1 < len(sys.argv):
            return names[: int(sys.argv[i + 1])]
    return names


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    names = repo_list()
    log(f"참조 저장소 {len(names)}개")
    if cmd in ("list",):
        print("\n".join(names))
        return
    phases = ["fork", "pages", "verify"] if cmd == "all" else [cmd]
    for ph in phases:
        run_phase(ph, _limit(names))
    st = load_state()
    recs = st["repos"]
    for key in ("fork", "pages", "status"):
        c = Counter(r.get(key) for r in recs.values())
        if any(k for k in c if k):
            log(f"요약 {key}: {dict(c)}")


if __name__ == "__main__":
    main()
