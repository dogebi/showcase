#!/usr/bin/env python3
"""showcase 카드의 저장소를 dogebi 계정으로 fork 하고 Pages 를 켠다.

- 대상: index.html 에서 https://github.com/dogebi/<repo> 로 참조되는 저장소 전체
- 각 단계는 재개 가능 (/tmp/mirror_state.json)
- 사용법:
    python3 tools/mirror_to_dogebi.py list      # 대상 출력
    python3 tools/mirror_to_dogebi.py fork      # fork (병렬)
    python3 tools/mirror_to_dogebi.py pages     # 원본 Pages 설정 복제 + 활성화
    python3 tools/mirror_to_dogebi.py verify    # 빌드 상태 확인
    python3 tools/mirror_to_dogebi.py all       # fork → pages → verify
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = "/tmp/mirror_state.json"
SRC = "sigco3111"
DST = "dogebi"
WORKERS = 1
FORK_DELAY = 40.0       # GitHub 은 fork 생성을 강하게 제한한다(403 'too quickly') → 40초 간격
_lock = threading.Lock()


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
        if "too quickly" in low or "secondary rate" in low:
            time.sleep(110)                        # fork 폭주 제한 → 길게 쉬고 다음 repo 로
            continue
        if "rate limit" in low or " 403" in low or " 429" in low or "abuse" in low:
            time.sleep(30 * (attempt + 1))
            continue
        return False, err
    return False, "rate limited"


def repo_list() -> list[str]:
    """index.html 참조 저장소 전체. 대표 카드(featured)를 앞에 둔다."""
    import json as _json
    html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    allr = sorted(set(re.findall(r"https://github\.com/dogebi/([A-Za-z0-9._-]+)", html)))
    feat_path = "/tmp/featured_repos.json"
    feat = _json.load(open(feat_path)) if os.path.exists(feat_path) else []
    head = [r for r in feat if r in allr]
    return head + [r for r in allr if r not in head]


def exists(owner: str, name: str) -> bool:
    ok, out = gh([f"repos/{owner}/{name}", "--jq", ".name"])
    return ok


def log(msg: str) -> None:
    with _lock:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)


def do_fork(name: str, st: dict) -> str:
    if exists(DST, name):
        return "exists"
    ok, out = gh([f"repos/{SRC}/{name}", "--jq", ".name"])
    if not ok:
        return "source-missing"
    time.sleep(FORK_DELAY)
    ok, err = gh(["-X", "POST", f"repos/{SRC}/{name}/forks", "--jq", ".full_name"])
    if not ok:
        return f"fork-fail:{err[:60]}"
    # fork 생성 완료 대기
    for _ in range(30):
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
    log(f"[{phase}] 대상 {len(todo)} / 전체 {len(names)}")
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit({"fork": do_fork, "pages": do_pages, "verify": do_verify}[phase], n, st): n for n in todo}
        for fut in as_completed(futs):
            n = futs[fut]
            try:
                res = fut.result()
            except Exception as e:                      # noqa: BLE001
                res = f"error:{type(e).__name__}"
            with _lock:
                st["repos"].setdefault(n, {})[phase if phase != "verify" else "status"] = res
                done += 1
                save_state(st)
            log(f"[{phase}] {n} → {res}")
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
    from collections import Counter
    for key in ("fork", "pages", "status"):
        c = Counter(r.get(key) for r in recs.values())
        if any(k for k in c if k):
            log(f"요약 {key}: {dict(c)}")


if __name__ == "__main__":
    main()
