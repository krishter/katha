#!/usr/bin/env bash
# Rebuild backend/requirements.txt from requirements.in (SH-12).
#
#   ./scripts/lock_requirements.sh              # releases at least 14 days old
#   ./scripts/lock_requirements.sh 2026-11-07   # releases before this date
#
# requirements.in holds what Katha asks for, with the reasons for each bound.
# requirements.txt is the exact set every build installs, with hashes, so the
# image, CI and a laptop all get the same packages. Edit the .in file and run
# this; never edit the .txt by hand.
#
# It resolves inside python:3.12-slim, the image production runs, and skips
# any release newer than the cut-off, so a package published yesterday (or
# hijacked yesterday) does not reach the box on the next rebuild.
set -euo pipefail

cd "$(dirname "$0")/.."
CUTOFF="${1:-}"

docker run --rm -v "$PWD":/w -w /w -e CUTOFF="$CUTOFF" python:3.12-slim sh -ec '
  pip install -q --root-user-action=ignore --disable-pip-version-check "uv==0.9.5"
  if [ -z "$CUTOFF" ]; then
    CUTOFF=$(python -c "import datetime as d; print((d.date.today() - d.timedelta(days=14)).isoformat())")
  fi
  echo "Resolving with releases before $CUTOFF"
  uv pip compile requirements.in \
    --generate-hashes \
    --python-version 3.12 \
    --python-platform x86_64-unknown-linux-gnu \
    --exclude-newer "${CUTOFF}T00:00:00Z" \
    --custom-compile-command "./scripts/lock_requirements.sh $CUTOFF" \
    -o requirements.txt --quiet
'
echo "Wrote requirements.txt. Run the test suite and pip-audit before committing."
