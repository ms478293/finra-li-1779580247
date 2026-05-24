#!/bin/bash
# Auto-cycle: wait for run finish -> download -> merge -> re-chunk -> push -> trigger next
set -e
REPO=ms478293/finra-li-1779580247
WORKDIR=/tmp/finra_li_matrix
CYCLES_MAX=8
PYBIN=/opt/homebrew/bin/python3

cd "$WORKDIR"

for cycle in $(seq 1 $CYCLES_MAX); do
  echo "=== CYCLE $cycle / $CYCLES_MAX ==="
  RUN=$(cat .run)
  echo "watching run $RUN..."

  # Poll until run finishes
  while true; do
    s=$(gh run view --repo="$REPO" "$RUN" --json status --jq '.status' 2>/dev/null)
    if [ "$s" = "completed" ]; then break; fi
    sleep 180
  done
  echo "run $RUN completed at $(date)"

  # Download artifacts
  rm -rf artifacts && mkdir artifacts
  gh run download --repo="$REPO" "$RUN" --dir artifacts 2>&1 | tail -3

  # Merge into linkedin_cache.db
  $PYBIN scripts/merge_results.py artifacts 2>&1

  # Check remaining
  REMAINING=$($PYBIN -c "
import sqlite3
li=sqlite3.connect('/Users/mkt/Desktop/broker_linkedin_swarm/output/full_finra_dump/linkedin_cache.db')
fi=sqlite3.connect('/Users/mkt/Desktop/broker_linkedin_swarm/output/full_finra_dump/finra_all_cache.db')
found=set(r[0] for r in li.execute('SELECT crd FROM linkedin WHERE url LIKE \"http%\"'))
total=sum(1 for r in fi.execute('SELECT crd FROM detail WHERE data IS NOT NULL'))
todo = total - len(found)
print(todo)
")
  echo "remaining brokers: $REMAINING"

  if [ "$REMAINING" -lt 5000 ]; then
    echo "FINISHED (remaining < 5000)"
    break
  fi

  # Re-chunk
  $PYBIN scripts/extract_remaining.py 40 chunks 2>&1 | tail -3

  # Push + trigger
  git add -A
  git -c user.email="mkt@local" -c user.name="mkt" commit -m "Cycle $cycle: $REMAINING remaining" -q 2>&1 | tail -2
  git push origin main 2>&1 | tail -3
  sleep 10
  NEW=$(gh run list --repo="$REPO" --limit 1 --json databaseId --jq '.[0].databaseId')
  echo "$NEW" > .run
  echo "new run: $NEW"
done

echo "=== AUTO-CYCLE DONE ==="
$PYBIN -c "
import sqlite3
li=sqlite3.connect('/Users/mkt/Desktop/broker_linkedin_swarm/output/full_finra_dump/linkedin_cache.db')
hits=li.execute('SELECT COUNT(*) FROM linkedin WHERE url LIKE \"http%\"').fetchone()[0]
print(f'TOTAL LinkedIn URLs found: {hits}')
"
