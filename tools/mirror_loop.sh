#!/usr/bin/env bash
# showcase 미러 이어달리기 러너
#   크론(30분 간격, 12분 예산)은 사이클 사이에 18분을 놀린다.
#   레이트리밋 창이 열려 있는 동안에는 쉬지 않고 다음 배치를 붙여 처리율을 최대화한다.
#   mirror_batch.sh 가 flock 을 잡으므로 크론과 겹쳐도 중복 실행되지 않는다(skip).
# 사용법: tools/mirror_loop.sh [최대시간(시간)] [배치분]
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="/home/nttcom/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

MAXH="${1:-6}"
BATCH="${2:-12}"
END=$(( $(date +%s) + MAXH * 3600 ))
LOG=/tmp/mirror_loop.log
STATE="/home/nttcom/.hermes/cache/showcase_mirror_state.json"

pending() {
  python3 -c "
import json,os
p='$STATE'
r=json.load(open(p)).get('repos',{}) if os.path.exists(p) else {}
print(sum(1 for x in r.values() if x.get('fork') not in ('forked','exists','source-missing')))
" 2>/dev/null || echo 999
}

echo "=== $(date '+%F %T') loop start (max ${MAXH}h, batch ${BATCH}m, pending $(pending)) ==="
while [ "$(date +%s)" -lt "$END" ]; do
  L=$(pending)
  if [ "$L" -le 0 ]; then echo "$(date '+%H:%M:%S') 대기 0 — 미러 완료"; break; fi
  echo "$(date '+%H:%M:%S') 배치 시작 (남은 $L)"
  tools/mirror_batch.sh "$BATCH"
  sleep 5
done
echo "=== $(date '+%F %T') loop end (pending $(pending)) ==="
