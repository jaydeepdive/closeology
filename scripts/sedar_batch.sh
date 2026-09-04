#!/bin/bash
# SEDAR+ NI 43-101 batch collector — runs on Jordan's Mac via launchd, several
# times a day. Each run opens a fresh real-Chrome session (residential IP, no
# Claude, no extension), downloads up to 25 NEW technical reports, retains each
# PDF in the GitHub report-archive release, and commits the dedup ledger. It stops
# cleanly if SEDAR's per-session download limit kicks in; the next run resumes.
set -u
REPO="$HOME/Downloads/closeology"
cd "$REPO" || exit 1
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/Library/Python/3.9/bin:$PATH"
LOG="$REPO/data/keep/sedar_batch.log"
{
  echo "===== $(date) SEDAR batch start ====="
  rm -f .git/index.lock 2>/dev/null || true
  git pull --no-edit --quiet || true
  python3 -m pip install --user --quiet playwright >/dev/null 2>&1 || true
  python3 -m playwright install chromium >/dev/null 2>&1 || true
  export GITHUB_TOKEN="$(git remote get-url origin | sed -E 's#https://([^@]+)@.*#\1#')"
  export GITHUB_REPOSITORY="jaydeepdive/closeology"
  PYTHONPATH=src python3 -m minemodelingpro.sedar_collect --chrome --limit 25 --throttle 8
  git add data/keep/sedar_manifest.json
  git -c user.name=closeology -c user.email=jay@thedeepdive.ca \
      commit -m "SEDAR batch $(date -u +%Y-%m-%dT%H:%MZ)" || echo "nothing to commit"
  git push origin main || echo "push failed"
  echo "===== $(date) SEDAR batch done ====="
} >> "$LOG" 2>&1
