#!/bin/bash
# Auto-cycle: search all Active (BC or IA) unfilled brokers. NO set -e so transient
# errors don't kill the loop. Each command wrapped w/ retries.
REPO=ms478293/finra-li-1779580247
WORKDIR=/tmp/finra-li-clone
CYCLES_MAX=60
PYBIN=/opt/homebrew/bin/python3
MIN_NOVEL=200

cd "$WORKDIR" || exit 1
LOG=/tmp/autocycle_active.log
echo "=== AUTO Active start $(date) ===" | tee -a $LOG

retry_cmd() {
  local n=0
  local max=$1; shift
  while [ $n -lt $max ]; do
    "$@" && return 0
    n=$((n+1))
    echo "  retry $n/$max: $*" | tee -a $LOG
    sleep $((n * 10))
  done
  return 1
}

for cycle in $(seq 1 $CYCLES_MAX); do
  echo "--- CYCLE $cycle/$CYCLES_MAX $(date +%H:%M) ---" | tee -a $LOG

  $PYBIN scripts/extract_active.py 100 chunks 2>&1 | tee -a $LOG > /tmp/extract.out
  REMAIN=$(grep -oE 'unfilled: [0-9]+' /tmp/extract.out | head -1 | grep -oE '[0-9]+')
  echo "active unfilled: ${REMAIN:-0}" | tee -a $LOG
  if [ -z "$REMAIN" ] || [ "$REMAIN" -lt 200 ]; then
    echo "POOL too small ($REMAIN), stopping" | tee -a $LOG; break
  fi

  touch .prevfirm
  git add -A 2>&1 | tee -a $LOG
  git -c user.email="mkt@local" -c user.name="mkt" commit -m "Auto Active cycle $cycle: $REMAIN unfilled" -q 2>&1 | tail -1

  if ! retry_cmd 5 git push origin main 2>&1 | tail -2 | tee -a $LOG; then
    echo "git push failed after retries; sleeping 60 + continuing" | tee -a $LOG
    sleep 60
    continue
  fi

  sleep 12
  RUN=$(gh run list --repo="$REPO" --limit 1 --json databaseId --jq '.[0].databaseId' 2>/dev/null)
  echo "run $RUN" | tee -a $LOG
  if [ -z "$RUN" ]; then
    echo "no RUN id, skip cycle" | tee -a $LOG; sleep 30; continue
  fi

  POLL_FAIL=0
  while true; do
    s=$(gh run view --repo="$REPO" "$RUN" --json status --jq '.status' 2>/dev/null)
    if [ -z "$s" ]; then
      POLL_FAIL=$((POLL_FAIL+1))
      if [ $POLL_FAIL -gt 30 ]; then
        echo "poll repeatedly failed, abandon cycle" | tee -a $LOG
        break
      fi
      sleep 30; continue
    fi
    POLL_FAIL=0
    if [ "$s" = "completed" ]; then break; fi
    sleep 60
  done
  echo "run $RUN done at $(date +%H:%M:%S)" | tee -a $LOG

  rm -rf artifacts_auto
  mkdir artifacts_auto
  if ! retry_cmd 5 gh run download --repo="$REPO" "$RUN" --dir artifacts_auto 2>&1 | tail -2 | tee -a $LOG; then
    echo "download failed, skip cycle" | tee -a $LOG; continue
  fi

  NOVEL=$($PYBIN -c "
import csv, sqlite3, glob
conn = sqlite3.connect('/Users/mkt/Desktop/broker_linkedin_swarm/output/full_finra_dump/linkedin_cache.db')
existing = set(r[0] for r in conn.execute(\"SELECT crd FROM linkedin WHERE url LIKE 'http%'\"))
n=0
for f in glob.glob('artifacts_auto/*/out_*.csv'):
    for r in csv.DictReader(open(f)):
        u=r.get('linkedin_url') or ''
        if u.startswith('http') and r['crd'] not in existing: n+=1
print(n)
" 2>/dev/null)
  echo "novel: ${NOVEL:-0}" | tee -a $LOG
  $PYBIN scripts/merge_results.py artifacts_auto 2>&1 | tail -1 | tee -a $LOG

  if [ -n "$NOVEL" ] && [ "$NOVEL" -lt "$MIN_NOVEL" ]; then
    echo "SATURATED (novel<$MIN_NOVEL), stopping" | tee -a $LOG; break
  fi
done

cd /Users/mkt/Desktop/broker_linkedin_swarm
$PYBIN rebuild_parquet.py 2>&1 | grep -E "cache|filled|wrote" | tail -5 | tee -a $LOG
echo "=== AUTO Active DONE $(date) ===" | tee -a $LOG
