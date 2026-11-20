#!/usr/bin/env bash
# showcase 최종 마감 + 배포 검증
#   1) 미러 대기(pending)가 0 이 될 때까지 기다린다 (90분간 진척이 없으면 중단하고 그대로 마감)
#   2) relink.py 로 링크를 최종 정렬(포크 완료분 → dogebi repo, Pages 200 → dogebi live)
#   3) 커밋·푸시 → Pages 재빌드 대기 → 배포본을 실제로 받아 "전부 dogebi" 인지 검증
#   결과: /tmp/showcase_final.txt (요약), /tmp/showcase_finish.log (경과)
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="/home/nttcom/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:1099}"
export HTTP_PROXY="${HTTP_PROXY:-http://127.0.0.1:1099}"
export NO_PROXY="127.0.0.1,localhost"

MAXH="${1:-10}"
END=$(( $(date +%s) + MAXH * 3600 ))
STATE="/home/nttcom/.hermes/cache/showcase_mirror_state.json"
LOG=/tmp/showcase_finish.log
OUT=/tmp/showcase_final.txt

pending() {
  python3 -c "
import json,os
p='$STATE'
r=json.load(open(p)).get('repos',{}) if os.path.exists(p) else {}
print(sum(1 for x in r.values() if x.get('fork') not in ('forked','exists','source-missing')))
" 2>/dev/null || echo 999
}

echo "=== $(date '+%F %T') finish-watch start (pending $(pending)) ===" >> "$LOG"
last=-1; since=$(date +%s)
while [ "$(date +%s)" -lt "$END" ]; do
  L=$(pending)
  echo "$(date '+%H:%M:%S') pending=$L" >> "$LOG"
  if [ "$L" != "$last" ]; then last=$L; since=$(date +%s); fi
  [ "$L" -le 0 ] && break
  if [ $(( $(date +%s) - since )) -gt 5400 ]; then
    echo "$(date '+%H:%M:%S') 90분 무진척 — 마감 단계로 진행 (남은 $L)" >> "$LOG"
    break
  fi
  sleep 120
done

echo "$(date '+%H:%M:%S') relink 시작" >> "$LOG"
# 배치 러너와 겹치지 않게 같은 flock 을 잡고 relink (index.html 동시 쓰기 방지)
exec 9>/tmp/mirror_batch.lock
if flock -w 2400 9; then
  python3 tools/relink.py >> "$LOG" 2>&1
else
  echo "$(date '+%H:%M:%S') flock 대기 실패 — 그대로 relink" >> "$LOG"
  python3 tools/relink.py >> "$LOG" 2>&1
fi
echo "$(date '+%H:%M:%S') relink 완료" >> "$LOG"

if ! git diff --quiet -- index.html sync-state.json README.md; then
  git -c user.name=dogebi -c user.email=dogebi@users.noreply.github.com \
      commit -q -am "mirror: 전 카드 dogebi 링크 확정 ($(date '+%m-%d %H:%M'))" \
    && git push -q origin main && echo "$(date '+%H:%M:%S') pushed" >> "$LOG" \
    || echo "$(date '+%H:%M:%S') commit/push 실패" >> "$LOG"
else
  echo "$(date '+%H:%M:%S') 변경 없음" >> "$LOG"
fi

# Pages 반영 대기 후 검증 (최대 6회)
for i in 1 2 3 4 5 6; do
  sleep 90
  RES=$(python3 tools/verify_deployed.py 14 2>&1)
  echo "$RES" | tail -1 | grep -q '"ok": true' && { echo "$RES" | tail -1 > "$OUT"; echo "$RES" >> "$LOG"; echo "$(date '+%H:%M:%S') 검증 OK" >> "$LOG"; exit 0; }
  echo "--- 검증 시도 $i 실패" >> "$LOG"
  echo "$RES" >> "$LOG"
done
python3 tools/verify_deployed.py 14 2>&1 | tail -1 > "$OUT"
echo "$(date '+%H:%M:%S') 검증 미완료 — $OUT 확인" >> "$LOG"
