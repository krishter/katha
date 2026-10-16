#!/usr/bin/env bash
#
# Deploy Katha on the production box. Run from the repo root.
#
#     ./scripts/deploy.sh
#
# Three things, in this order, because the order matters: get the code,
# build the image, migrate the database, then swap the running container.
# Migrating after the new container is already serving means a request can
# hit new code against an old schema.
#
# This is deliberately a script and not three commands you remember.
# `alembic upgrade head` is the step most likely to be skipped by hand, and
# skipping it is how a deploy half-works.

set -euo pipefail

COMPOSE="docker compose -f docker-compose.prod.yml"

cd "$(dirname "$0")/.."

echo "==> Checking configuration"
if [[ ! -f backend/.env ]]; then
	echo "backend/.env is missing. Copy .env.example and fill it in." >&2
	exit 1
fi
if ! grep -q '^ENVIRONMENT=production' backend/.env; then
	echo "backend/.env does not set ENVIRONMENT=production." >&2
	echo "validate_production_config only runs in production, so none of the" >&2
	echo "boot checks that protect a live deploy would fire." >&2
	exit 1
fi

if [[ ! -f deploy.env ]]; then
	echo "deploy.env is missing. Copy deploy.env.example and fill it in." >&2
	exit 1
fi

# The database is described in two files, in two syntaxes, because
# config.Settings forbids unknown keys and so cannot be handed POSTGRES_*.
# Nothing else notices when they drift: the db container comes up with one
# set of credentials and the backend fails to authenticate against it, which
# reads as a connection problem rather than a config problem.
echo "==> Checking the two env files describe the same database"

# Read KEY from FILE, stripping a trailing comment, surrounding quotes and
# surrounding whitespace.
#
# Both files are read through this, so the two sides of the comparison are
# parsed identically. They were not before: the deploy.env values went through
# `tr -d` (which also deleted every embedded quote and space) while DATABASE_URL
# went through a sed, so the same trailing comment survived on one side and not
# the other, and the comparison failed on values that actually agreed.
#
# A '#' only opens a comment when whitespace precedes it. '#' is a legal
# character mid-value, and the previous sed truncated there unconditionally —
# a password containing '#' was silently cut in half and then compared, which
# fails in a way that points at the wrong file.
read_env_value() {
	local file="$1" key="$2"
	grep -E "^${key}=" "$file" | tail -1 | cut -d= -f2- |
		sed -e 's/[[:space:]]\{1,\}#.*$//' \
			-e 's/^[[:space:]]*//' \
			-e 's/[[:space:]]*$//' \
			-e 's/^"\(.*\)"$/\1/' \
			-e "s/^'\(.*\)'\$/\1/"
}

pg_user=$(read_env_value deploy.env POSTGRES_USER)
pg_pass=$(read_env_value deploy.env POSTGRES_PASSWORD)
pg_db=$(read_env_value deploy.env POSTGRES_DB)
db_url=$(read_env_value backend/.env DATABASE_URL)

# The password is embedded in DATABASE_URL as a literal, and this script
# compares it there as a literal. Both break on the same characters, so reject
# them at the point the value is read rather than letting the mismatch surface
# later as an authentication failure against a database that looks fine.
# Empty is left to the loop below, which already words that case correctly.
if [[ -n "${pg_pass}" && ! "${pg_pass}" =~ ^[A-Za-z0-9_-]+$ ]]; then
	echo "POSTGRES_PASSWORD contains characters outside [A-Za-z0-9_-]." >&2
	echo >&2
	echo "It is embedded in backend/.env's DATABASE_URL, where / + = : @ ? and #" >&2
	echo "are URL syntax and break parsing. Percent-encoding it there is not a way" >&2
	echo "out either: the encoded form would no longer match the literal substring" >&2
	echo "comparison this script makes between the two files." >&2
	echo >&2
	echo "Generate one with: openssl rand -hex 32" >&2
	exit 1
fi

for pair in "user:${pg_user}" "password:${pg_pass}" "database:${pg_db}"; do
	label="${pair%%:*}"
	value="${pair#*:}"
	if [[ -z "${value}" ]]; then
		echo "deploy.env has no ${label}" >&2
		exit 1
	fi
	if [[ "${db_url}" != *"${value}"* ]]; then
		echo "backend/.env DATABASE_URL does not contain the ${label} from deploy.env." >&2
		echo "They must describe the same database — see deploy.env.example." >&2
		exit 1
	fi
done

if [[ "${db_url}" != *"@db:"* ]]; then
	echo "DATABASE_URL host is not 'db'. Inside compose the database is reached" >&2
	echo "by service name, not localhost." >&2
	exit 1
fi

echo "==> Pulling"
git pull --ff-only

echo "==> Building"
$COMPOSE build backend

echo "==> Migrating"
# Against the db service, in a throwaway container, before anything new
# starts serving. --rm so a failed migration does not leave a container
# behind to confuse the next run.
$COMPOSE run --rm backend alembic upgrade head

echo "==> Starting"
$COMPOSE up -d

echo "==> Waiting for health"
# From inside the container: the backend publishes no port of its own, so
# it is reachable only through Caddy on the public interface. Checking it
# here rather than through Caddy separates "the app is up" from "TLS and
# DNS are right", which are different problems with different fixes.
healthy=""
for _ in $(seq 1 30); do
	if $COMPOSE exec -T backend python -c \
		"import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
		>/dev/null 2>&1; then
		healthy="yes"
		echo "    healthy"
		break
	fi
	sleep 2
done
if [[ -z "${healthy}" ]]; then
	echo "backend never became healthy — check: $COMPOSE logs backend" >&2
	exit 1
fi

echo "==> Confirming exactly one backend process"
# The scheduler is in-process. Two backend containers means every family
# gets every opening voice note twice, and nothing else in the system will
# tell you that is happening.
running=$($COMPOSE ps --status running --format '{{.Service}}' | grep -c '^backend$' || true)
if [[ "$running" != "1" ]]; then
	echo "expected exactly 1 running backend, found $running" >&2
	exit 1
fi

echo "==> Scheduler"
# Match the app's own log line, not APScheduler's. Both say "Scheduler
# started" — a bare grep counts two on a perfectly healthy single-worker
# deploy, and a check that cries wolf on every deploy is worse than none.
starts=$($COMPOSE logs backend --since 5m 2>/dev/null | grep -c 'main:Scheduler started' || true)
echo "    scheduler started ${starts} time(s) in the last 5m"
if [[ "${starts}" -gt 1 ]]; then
	echo "MORE THAN ONE SCHEDULER. Every family would get every opening voice" >&2
	echo "note once per scheduler. Check for a stray backend container or a" >&2
	echo "--workers value above 1." >&2
	exit 1
fi

echo
echo "Deployed. Check: curl -fsS https://api.katha.life/health"
