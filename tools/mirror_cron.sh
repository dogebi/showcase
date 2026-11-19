#!/usr/bin/env bash
# 쇼케이스 dogebi 미러/링크 자동 정비 (크론 전용).
#   1) 남은 저장소 fork + Pages 켜기 (레이트리밋이면 스스로 멈추고 다음 실행에서 이어받음)
#   2) 링크 재작성 (dogebi 저장소/Pages 가 실제 200 인 것만 dogebi 로)
#   3) 바뀐 게 있으면 커밋 + 푸시
set -uo pipefail
cd /mnt/d/dev/showcase || exit 1
export HTTPS_PROXY=http://127.0.0.1:1099
export HTTP_PROXY=http://127.0.0.1:1099
LOG=/tmp/showcase_sync.log

exec 9>/tmp/showcase_sync.lock
flock -n 9 || { echo "$(date '+%F %T') 이미 실행 중 — 건너뜀" >>"$LOG"; exit 0; }

{
  echo "=== $(date '+%F %T') 시작 ==="
  python3 tools/mirror_sweep.py --minutes 18 --gap 6
  python3 tools/relink.py
  if ! git diff --quiet -- index.html sync-state.json README.md; then
    git add index.html sync-state.json README.md
    git commit -q -m "mirror: dogebi 링크 자동 갱신 ($(date '+%m-%d %H:%M'))"
    git push -q origin HEAD && echo "push OK" || echo "push FAIL"
  else
    echo "변경 없음"
  fi
  echo "=== $(date '+%F %T') 끝 ==="
} >>"$LOG" 2>&1
