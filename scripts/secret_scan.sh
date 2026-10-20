#!/usr/bin/env bash
#
# Fail if a tracked file holds a credential-shaped string.
#
#     ./scripts/secret_scan.sh              # every tracked file (what CI runs)
#     ./scripts/secret_scan.sh FILE...      # just these files
#
# The repository is public. A production env file once sat untracked in
# the repo root, one `git add -A` from being published
# (docs/PRE_PILOT_REVIEW.md M-09). This catches the shapes of the keys
# Katha actually holds: AWS access keys, Anthropic and Google API keys,
# and Twilio account SIDs. It prints file:line only, never the match.

set -euo pipefail
cd "$(dirname "$0")/.."

PATTERN='AKIA[0-9A-Z]{16}|sk-ant-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{35}|AC[0-9a-f]{32}'
# Documented placeholders that tests use on purpose: AWS's own example key.
ALLOWED='AKIAIOSFODNN7EXAMPLE'

if [[ $# -gt 0 ]]; then
	hits=$(grep -HnIE "${PATTERN}" -- "$@" 2>/dev/null | grep -vE "${ALLOWED}" || true)
else
	hits=$(git ls-files -z | xargs -0 grep -HnIE "${PATTERN}" -- 2>/dev/null |
		grep -vE "${ALLOWED}" || true)
fi

if [[ -n "${hits}" ]]; then
	echo "Credential-shaped strings found (file:line):" >&2
	echo "${hits}" | cut -d: -f1,2 >&2
	exit 1
fi
echo "secret scan: clean"
