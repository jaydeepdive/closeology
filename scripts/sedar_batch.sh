#!/bin/bash
# SEDAR+ NI 43-101 batch collector — runs on Jordan's Mac via launchd, several
# times a day. Each run opens a fresh real-Chrome session (residential IP, no
# Claude, no extension), downloads up to 25 NEW technical reports, retains each
# PDF in the GitHub report-archive release, and commits the dedup ledger. It stops
# cleanly if SEDAR's per-session download limit kicks in; the next run resumes.
set -u
# Prefer the non-Downloads location (macOS blocks launchd from ~/Downloads);
# fall back to the old path during/after the move.
REPO="$HOME/closeology"; [ -d "$REPO" ] || REPO="$HOME/Downloads/closeology"
cd "$REPO" || exit 1
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/Library/Python/3.9/bin:$PATH"
LOG="$REPO/data/keep/sedar_batch.log"

# Because a batch can now run long (it cools down and retries through SEDAR's
# rate-limit window), guard against two launchd fires overlapping — they would
# fight over the same Chrome profile. If a batch is already running and still
# fresh (<90 min), skip this fire. A stale lock (crashed run) is ignored.
LOCK="$REPO/data/keep/.sedar_batch.lock"
if [ -f "$LOCK" ]; then
  AGE=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))
  if [ "$AGE" -lt 5400 ]; then
    echo "===== $(date) SEDAR batch SKIPPED (another run active, lock age ${AGE}s) =====" >> "$LOG"
    exit 0
  fi
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK" 2>/dev/null' EXIT

{
  echo "===== $(date) SEDAR batch start ====="
  rm -f .git/index.lock 2>/dev/null || true
  git pull --no-edit --quiet || true
  python3 -m pip install --user --quiet playwright >/dev/null 2>&1 || true
  python3 -m playwright install chromium >/dev/null 2>&1 || true
  export GITHUB_TOKEN="$(git remote get-url origin | sed -E 's#https://([^@]+)@.*#\1#')"
  export GITHUB_REPOSITORY="jaydeepdive/closeology"
  PYTHONPATH=src python3 -m minemodelingpro.sedar_collect --chrome --limit 60 --max-pages 150 --throttle 45
  git add data/keep/sedar_manifest.json
  git -c user.name=closeology -c user.email=jay@thedeepdive.ca \
      commit -m "SEDAR batch $(date -u +%Y-%m-%dT%H:%MZ)" || echo "nothing to commit"
  # Push with a rebase-retry loop: the GitHub Actions build and other batches
  # push to main too, so a plain push can be rejected ('fetch first'). Retry a
  # few times, rebasing our single ledger commit onto whatever landed.
  pushed=""
  for i in 1 2 3 4 5; do
    if git push origin main 2>&1; then pushed="yes"; break; fi
    echo "push rejected (try $i) — rebasing on origin/main and retrying"
    git fetch origin main --quiet || true
    git rebase origin/main || { git rebase --abort 2>/dev/null || true; git reset --soft origin/main; git add data/keep/sedar_manifest.json; git -c user.name=closeology -c user.email=jay@thedeepdive.ca commit -m "SEDAR batch $(date -u +%Y-%m-%dT%H:%MZ)" || true; }
    sleep 3
  done
  [ -n "$pushed" ] || echo "push failed after retries"
  echo "===== $(date) SEDAR batch done ====="
} >> "$LOG" 2>&1
