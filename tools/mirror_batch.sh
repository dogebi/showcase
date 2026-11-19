#!/usr/bin/env bash
# showcase 저장소 미러 배치 (크론 30분용)
#   fork 는 GitHub 2차 레이트리밋이 강하다 → 시간예산 러너가 조금씩 진행하고,
#   이 스크립트가 이어서 링크 정렬 → 커밋·푸시까지 마무리한다.
# 사용법: tools/mirror_batch.sh [분]   (기본 12분)
set -uo pipefail
cd "$(dirname "$0")/.."

export PATH="/home/nttcom/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"   # 크론은 gh 를 못 찾는다
MIN="${1:-12}"
LOG=/tmp/mirror_batch.log
# 동시 실행 방지 (Hermes 크론과 수동 실행이 겹치면 같은 저장소를 두 번 포크해 403 만 늘어난다)
exec 3>&1                     # 원래 stdout 보관 → 크론이 이 출력을 폐하께 전달
exec 9>/tmp/mirror_batch.lock
flock -n 9 || { echo "[쇼케이스 미러] $(date '+%H:%M') 이전 사이클이 아직 실행 중 — 이번 회차 skip"; exit 0; }

exec >>"$LOG" 2>&1
echo "=== $(date '+%F %T') batch start (minutes=$MIN) ==="

export HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:1099}"
export HTTP_PROXY="${HTTP_PROXY:-http://127.0.0.1:1099}"
export NO_PROXY="127.0.0.1,localhost"
export PYTHONUNBUFFERED=1

python3 tools/mirror_sweep.py --minutes "$MIN"
RC=$?
echo "runner rc=$RC"

python3 tools/relink.py

if ! git diff --quiet -- index.html sync-state.json README.md; then
  D=$(python3 -c "
import re
s=open('index.html',encoding='utf-8').read()
print(len(set(re.findall(r'https://github\.com/dogebi/([A-Za-z0-9._-]+)',s))))")
  git -c user.name=dogebi -c user.email=dogebi@users.noreply.github.com \
      commit -q -am "mirror: dogebi 저장소 링크 갱신 ($D 개 참조)" && git push -q origin main \
      && echo "pushed: 참조 $D 개"
else
  echo "변경 없음"
fi

SUMMARY=$(python3 - <<'EOF'
import json, re, os, time
s = open('index.html', encoding='utf-8').read()
dog = len(set(re.findall(r'https://github\.com/dogebi/([A-Za-z0-9._-]+)', s)))
src = len(set(re.findall(r'https://github\.com/sigco3111/([A-Za-z0-9._-]+)', s)))
live_dog = len(set(re.findall(r'https://dogebi\.github\.io/([A-Za-z0-9._-]+)/', s)))
live_src = len(set(re.findall(r'https://sigco3111\.github\.io/([A-Za-z0-9._-]+)/', s)))
path = os.path.expanduser('~/.hermes/cache/showcase_mirror_state.json')
recs = json.load(open(path)).get('repos', {}) if os.path.exists(path) else {}
mirrored = sum(1 for r in recs.values() if r.get('fork') in ('forked', 'exists'))
pages = sum(1 for r in recs.values() if r.get('pages'))
total = mirrored + sum(1 for r in recs.values() if r.get('fork') is None)
pct = (mirrored / total * 100) if total else 0
left = total - mirrored
cycles = -(-left // 8) if left else 0          # 사이클당 평균 8개 가정
eta = f"{cycles * 0.5:.0f}시간" if left else "완료"
print(f"[쇼케이스 미러] {time.strftime('%H:%M')} · 진행 {mirrored}/{total} ({pct:.0f}%) · 남은 {left}개 (약 {eta})")
print(f"  저장소 dogebi {dog} / 원본 {src} · 미러 저장소 {mirrored} · Pages {pages}")
print(f"  라이브 dogebi {live_dog} / 원본 {live_src}")
EOF
)
echo "$SUMMARY"
echo "$SUMMARY" > /tmp/mirror_summary.txt
printf '%s\n' "$SUMMARY" >&3          # 크론 전달용 (fd3 = 원래 stdout)
echo "=== $(date '+%F %T') batch end ==="
