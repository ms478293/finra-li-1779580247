#!/bin/bash
# Auto-cycle: re-search all unfilled 2024+ FINRA brokers via GH matrix until saturated.
set -e
REPO=ms478293/finra-li-1779580247
WORKDIR=/tmp/finra-li-clone
CYCLES_MAX=50
PYBIN=/opt/homebrew/bin/python3
MIN_NOVEL=500  # stop loop if novel hits this low

cd "$WORKDIR"
LOG=/tmp/autocycle_2024.log
echo "=== AUTO 2024+ start $(date) ===" | tee -a $LOG

for cycle in $(seq 1 $CYCLES_MAX); do
  echo "--- CYCLE $cycle/$CYCLES_MAX $(date +%H:%M) ---" | tee -a $LOG

  # 1. Re-chunk unfilled 2024+ brokers; grab "unfilled: N" from stderr
  $PYBIN scripts/extract_recent.py 40 2024 chunks 2>&1 | tee -a $LOG > /tmp/extract.out
  REMAIN=$(grep -oE 'unfilled: [0-9]+' /tmp/extract.out | head -1 | grep -oE '[0-9]+')
  echo "unfilled 2024+: ${REMAIN:-0}" | tee -a $LOG
  if [ -z "$REMAIN" ] || [ "$REMAIN" -lt 500 ]; then
    echo "POOL too small ($REMAIN), stopping" | tee -a $LOG; break
  fi

  # 2. Push + trigger run
  touch .prevfirm
  git add -A
  git -c user.email="mkt@local" -c user.name="mkt" commit -m "Auto 2024+ cycle $cycle: $REMAIN unfilled" -q 2>&1 | tail -1
  git push origin main 2>&1 | tail -2 | tee -a $LOG
  sleep 12
  RUN=$(gh run list --repo="$REPO" --limit 1 --json databaseId --jq '.[0].databaseId')
  echo "run $RUN" | tee -a $LOG

  # 3. Poll until done
  while true; do
    s=$(gh run view --repo="$REPO" "$RUN" --json status --jq '.status' 2>/dev/null)
    if [ "$s" = "completed" ]; then break; fi
    sleep 60
  done
  echo "run $RUN done at $(date +%H:%M:%S)" | tee -a $LOG

  # 4. Download + diff novel
  rm -rf artifacts_auto && mkdir artifacts_auto
  gh run download --repo="$REPO" "$RUN" --dir artifacts_auto 2>&1 | tail -2 | tee -a $LOG

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
")
  echo "novel: $NOVEL" | tee -a $LOG

  # 5. Merge
  $PYBIN scripts/merge_results.py artifacts_auto 2>&1 | tail -1 | tee -a $LOG

  # 6. Stop if saturated
  if [ "$NOVEL" -lt "$MIN_NOVEL" ]; then
    echo "SATURATED (novel<$MIN_NOVEL), stopping" | tee -a $LOG; break
  fi
done

# 7. Final rebuild parquet
cd /Users/mkt/Desktop/broker_linkedin_swarm
$PYBIN rebuild_parquet.py 2>&1 | grep -E "cache|filled|wrote" | tail -5 | tee -a $LOG

# 8. Refresh xlsx
$PYBIN export_active_2024.py 2>&1 | tail -3 | tee -a $LOG
$PYBIN export_184k_enriched.py 2>&1 | tail -2 | tee -a $LOG

echo "=== AUTO 2024+ DONE $(date) ===" | tee -a $LOG
