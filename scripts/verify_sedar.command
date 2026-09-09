#!/bin/bash
REPO="$HOME/closeology"; [ -d "$REPO" ] || REPO="$HOME/Downloads/closeology"
cd "$REPO" || exit 1
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/Library/Python/3.9/bin:$PATH"
LOG="$REPO/data/keep/sedar_verify.log"
{ echo "===== $(date) VERIFY (session-cycling, limit 6) ====="
  export GITHUB_TOKEN="$(git remote get-url origin | sed -E 's#https://([^@]+)@.*#\1#')"
  export GITHUB_REPOSITORY="jaydeepdive/closeology"
  PYTHONPATH=src python3 -m minemodelingpro.sedar_collect --chrome --limit 6 --max-pages 40 --throttle 5
  echo "===== $(date) VERIFY done ====="; } >> "$LOG" 2>&1
echo done
