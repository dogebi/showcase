#!/usr/bin/env python3
"""fork 처리율 실측: 간격을 조금씩 줄여가며 403('too quickly'/'rate') 이 나오는 지점을 찾는다."""
import json, os, subprocess, sys, time

ROOT = "/mnt/d/dev/showcase"
SRC, DST = "sigco3111", "dogebi"
ENV = dict(os.environ, HTTPS_PROXY="http://127.0.0.1:1099", HTTP_PROXY="http://127.0.0.1:1099")

plan = json.load(open("/tmp/mirror_plan.json"))
missing = plan["miss"]
print(f"미러 필요 {len(missing)}개 중 상위 {sys.argv[1] if len(sys.argv)>1 else 6}개로 실측", flush=True)


def api(args):
    p = subprocess.run(["gh", "api", *args], capture_output=True, text=True, env=ENV)
    return p.returncode == 0, (p.stdout if p.returncode == 0 else p.stderr).strip()


n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
gap = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
ok_n = 0
for name in missing[:n]:
    ok, out = api([f"repos/{DST}/{name}", "--jq", ".name"])
    if ok:
        print(f"{time.strftime('%H:%M:%S')} {name} -> already exists", flush=True)
        continue
    ok2, out2 = api([f"repos/{SRC}/{name}", "--jq", ".name"])
    if not ok2:
        print(f"{time.strftime('%H:%M:%S')} {name} -> source-missing", flush=True)
        continue
    t0 = time.time()
    ok3, out3 = api(["-X", "POST", f"repos/{SRC}/{name}/forks", "--jq", ".full_name"])
    dt = time.time() - t0
    print(f"{time.strftime('%H:%M:%S')} {name} -> fork {'OK' if ok3 else 'FAIL'} ({dt:.1f}s) {out3[:100]}", flush=True)
    if ok3:
        ok_n += 1
    else:
        low = out3.lower()
        if "too quickly" in low or "secondary rate" in low or "abuse" in low:
            print("BLOCKED: secondary rate limit 도달 — 여기서 페이싱이 필요", flush=True)
            break
    time.sleep(gap)
print(f"RESULT created={ok_n}/{n} gap={gap}s", flush=True)
