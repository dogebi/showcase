#!/usr/bin/env python3
"""showcase → dogebi 미러 러너 (시간예산형, 크론 30분 배치에 맞춤).

왜 별도 러너인가
  fork 는 GitHub 2차 레이트리밋이 강해서 12개 안팎을 만들면 403('was submitted too quickly')가 뜬다.
  한 번에 다 돌리면 수백 건이 실패로 기록되고, 40초 고정 간격은 처리율이 시간당 30개로 떨어진다.
  이 러너는 (1) 성공하면 짧은 간격(기본 6초)으로 계속, (2) 403 이면 길게 쉬고 같은 항목 재시도,
  (3) 예산(--minutes) 안에서만 일하고 종료 → 다음 크론 실행이 이어받는다. 상태는 항상 재개 가능.

사용법
  python3 tools/mirror_sweep.py --minutes 22      # 크론 배치용
  python3 tools/mirror_sweep.py --minutes 60 --gap 6
종료코드
  0 = 정상 진행(또는 완료), 3 = 레이트리밋으로 조기 종료(다음 배치에서 재개)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections import OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.expanduser("~/.hermes/cache/showcase_mirror_state.json")
STATE_MIRROR = "/tmp/mirror_state.json"
SRC, DST = "sigco3111", "dogebi"
ENV = dict(os.environ, HTTPS_PROXY="http://127.0.0.1:1099", HTTP_PROXY="http://127.0.0.1:1099")
BLOCK_SLEEP = 240.0          # 'too quickly' 후 대기
MAX_RETRY = 4                # 같은 항목 재시도 한도


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def load_state() -> dict:
    st = {"repos": {}, "started": time.time()}
    for path in (STATE_MIRROR, STATE):          # 옛 /tmp 상태도 흡수
        if os.path.exists(path):
            try:
                old = json.load(open(path))
                st["repos"].update(old.get("repos", {}))
                st.setdefault("started", old.get("started", time.time()))
            except Exception:                    # noqa: BLE001
                pass
    return st


def save_state(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    for path in (STATE, STATE_MIRROR):
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)


def gh(args: list[str]) -> tuple[bool, str]:
    p = subprocess.run(["gh", "api", *args], capture_output=True, text=True, env=ENV)
    return p.returncode == 0, (p.stdout if p.returncode == 0 else (p.stderr or "")).strip()


def blocked(err: str) -> str | None:
    low = err.lower()
    if "content creation" in low:
        return "hard"
    if "too quickly" in low or "secondary rate" in low or "abuse" in low:
        return "soft"
    if "rate limit" in low or " 429" in low:
        return "soft"
    return None


def targets() -> list[str]:
    names: "OrderedDict[str, None]" = OrderedDict()
    st_path = os.path.join(ROOT, "sync-state.json")
    if os.path.exists(st_path):
        for rec in json.load(open(st_path, encoding="utf-8")).get("entries", {}).values():
            if rec.get("repo"):
                names[rec["repo"]] = None
    html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    for r in re.findall(r"https://github\.com/(?:sigco3111|dogebi)/([A-Za-z0-9._-]+)", html):
        names[r] = None
    allr = sorted(names)
    feat_path = "/tmp/featured_repos.json"
    feat = json.load(open(feat_path)) if os.path.exists(feat_path) else []
    head = [r for r in feat if r in names]
    return head + [r for r in allr if r not in head]


class Runner:
    def __init__(self, budget_s: float, gap: float):
        self.deadline = time.time() + budget_s
        self.gap = gap
        self.st = load_state()
        self.created = self.pages_on = self.errors = 0
        self.stop_reason = ""

    # ---------- helpers ----------
    def rec(self, name: str) -> dict:
        return self.st["repos"].setdefault(name, {})

    def left(self) -> float:
        return self.deadline - time.time()

    def api_retry(self, args: list[str], name: str, what: str) -> tuple[bool, str]:
        """레이트리밋이면 길게 쉬고 같은 호출을 재시도. 하드 블록이면 러너를 멈춘다."""
        for attempt in range(MAX_RETRY):
            ok, out = gh(args)
            if ok:
                return True, out
            kind = blocked(out)
            if not kind:
                return False, out
            if kind == "hard":
                self.stop_reason = f"하드 블록({what}) {out[:80]}"
                return False, out
            wait = BLOCK_SLEEP
            if wait > self.left():
                self.stop_reason = f"레이트리밋 대기({wait:.0f}s)가 예산 초과 — 다음 배치로"
                return False, out
            log(f"  403({what}) → {wait:.0f}s 대기 후 재시도 {attempt + 1}/{MAX_RETRY}")
            time.sleep(wait)
        return False, "rate limited"

    # ---------- actions ----------
    def do_fork(self, name: str) -> bool:
        ok, _ = self.api_retry([f"repos/{DST}/{name}", "--jq", ".name"], name, "exists")
        if ok:
            self.rec(name)["fork"] = "exists"
            save_state(self.st)
            return True
        ok, out = self.api_retry([f"repos/{SRC}/{name}", "--jq", ".name"], name, "src")
        if not ok:
            self.rec(name)["fork"] = "source-missing"
            save_state(self.st)
            return True
        ok, out = self.api_retry(["-X", "POST", f"repos/{SRC}/{name}/forks", "--jq", ".full_name"],
                                 name, "fork")
        if not ok:
            self.rec(name)["fork"] = f"fork-fail:{out[:60]}"
            save_state(self.st)
            self.errors += 1
            return False
        self.rec(name)["fork"] = "forked"
        save_state(self.st)
        self.created += 1
        log(f"  fork OK  {name}  (누적 생성 {self.created})")
        return True

    def do_pages(self, name: str) -> None:
        r = self.rec(name)
        ok, out = gh([f"repos/{SRC}/{name}/pages", "--jq", '.source.branch + "|" + .source.path'])
        if not ok:
            r["pages"] = "no-source-pages"
            save_state(self.st)
            return
        branch, _, path = out.strip().strip('"').partition("|")
        ok2, err2 = self.api_retry(["-X", "POST", f"repos/{DST}/{name}/pages",
                                    "-f", f"source[branch]={branch}",
                                    "-f", f"source[path]={path}"], name, "pages")
        if ok2 or "already enabled" in err2.lower() or "409" in err2:
            r["pages"] = f"pages-on:{branch}{path}"
            self.pages_on += 1
        else:
            r["pages"] = f"pages-fail:{err2[:60]}"
        save_state(self.st)

    # ---------- main loop ----------
    def run(self) -> int:
        names = targets()
        pending = [n for n in names if self.rec(n).get("fork") not in ("forked", "exists", "source-missing")]
        log(f"대상 {len(names)} · 미러 대기 {len(pending)} · 예산 {self.left():.0f}s · gap {self.gap:.0f}s")

        # 1) 이미 fork 된 것 중 Pages 미설정 → 먼저 정리
        for n in names:
            if self.left() < 60 or self.stop_reason:
                break
            r = self.rec(n)
            if r.get("fork") in ("forked", "exists") and r.get("pages") is None:
                self.do_pages(n)
                time.sleep(1.0)

        # 2) fork 신규
        for i, n in enumerate(pending, 1):
            if self.left() < 30 or self.stop_reason:
                break
            self.do_fork(n)
            if not self.left() < 0:
                time.sleep(self.gap)

        # 3) 방금 만든 것들 Pages 켜기 (예산 남으면)
        for n in names:
            if self.left() < 15 or self.stop_reason:
                break
            r = self.rec(n)
            if r.get("fork") in ("forked", "exists") and r.get("pages") is None:
                self.do_pages(n)

        done = sum(1 for r in self.st["repos"].values() if r.get("fork") in ("forked", "exists"))
        pages = sum(1 for r in self.st["repos"].values() if r.get("pages"))
        save_state(self.st)
        log(f"요약: 미러 완료 {done}/{len(names)} · Pages {pages} · 이번 생성 {self.created} · 오류 {self.errors}"
            + (f" · 중단사유: {self.stop_reason}" if self.stop_reason else ""))
        return 3 if self.stop_reason else 0


def main() -> int:
    minutes, gap = 22.0, 6.0
    for i, a in enumerate(sys.argv):
        if a == "--minutes" and i + 1 < len(sys.argv):
            minutes = float(sys.argv[i + 1])
        if a == "--gap" and i + 1 < len(sys.argv):
            gap = float(sys.argv[i + 1])
    return Runner(minutes * 60, gap).run()


if __name__ == "__main__":
    sys.exit(main())
