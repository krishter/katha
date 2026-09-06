#!/usr/bin/env bash
#
# Nightly pg_dump to S3, with retention.
#
#     ./scripts/backup.sh          # take one backup now and exit
#     ./scripts/backup.sh --loop   # sleep until the next run time, forever
#
# The --loop form is what the `backup` service in docker-compose.prod.yml
# runs. A container that sleeps is used rather than host cron so the backup
# ships with the stack instead of living in a crontab that only exists on
# one box and is not in git.
#
# DPDP note: a family that exercises deletion is still inside the most
# recent dumps until those age out. BACKUP_RETENTION_DAYS is therefore not
# an ops preference — it is the number the privacy copy has to be able to
# state. Keep it finite and keep it written down.

set -euo pipefail

: "${POSTGRES_USER:?}"
: "${POSTGRES_DB:?}"
: "${AWS_S3_BUCKET:?}"
: "${AWS_ACCESS_KEY_ID:?}"
: "${AWS_SECRET_ACCESS_KEY:?}"
AWS_S3_REGION="${AWS_S3_REGION:-ap-south-1}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
BACKUP_HOUR_UTC="${BACKUP_HOUR_UTC:-20}" # 01:30 IST-ish; off-peak for families
PGHOST="${PGHOST:-db}"

PREFIX="backups/postgres"

log() { echo "[backup $(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

ensure_awscli() {
	# Installed at container start rather than baked into an image, so the
	# backup rides on the same pgvector image as the database and pg_dump
	# always matches the server version. Costs ~30s on each container start,
	# which in --loop mode is once per restart, not once per backup.
	#
	# `set -e` means a failed install exits the container; compose's
	# `restart: unless-stopped` then retries. That is deliberate — a backup
	# container that silently kept running without aws would produce a
	# reassuring log and no backups.
	command -v aws >/dev/null 2>&1 && return 0
	log "installing awscli"
	apt-get update -qq && apt-get install -y -qq --no-install-recommends awscli
}

take_backup() {
	local stamp key tmp
	stamp="$(date -u +%Y%m%dT%H%M%SZ)"
	key="${PREFIX}/katha-${stamp}.sql.gz"
	tmp="$(mktemp)"

	log "dumping ${POSTGRES_DB}"
	# --clean --if-exists so the dump can be restored over an existing
	# database without hand-editing it at 3am.
	PGPASSWORD="${POSTGRES_PASSWORD}" pg_dump \
		--host="${PGHOST}" \
		--username="${POSTGRES_USER}" \
		--dbname="${POSTGRES_DB}" \
		--clean --if-exists --no-owner |
		gzip -9 >"${tmp}"

	local size
	size=$(stat -c %s "${tmp}" 2>/dev/null || stat -f %z "${tmp}")
	# A dump of a live Katha database is never a few hundred bytes. Catching
	# this here beats discovering it during a restore.
	if [[ "${size}" -lt 1024 ]]; then
		log "ERROR: dump is only ${size} bytes — refusing to upload"
		rm -f "${tmp}"
		return 1
	fi

	log "uploading s3://${AWS_S3_BUCKET}/${key} (${size} bytes)"
	aws s3 cp "${tmp}" "s3://${AWS_S3_BUCKET}/${key}" \
		--region "${AWS_S3_REGION}" \
		--only-show-errors
	rm -f "${tmp}"

	prune
	log "done"
}

prune() {
	local cutoff
	cutoff=$(date -u -d "${BACKUP_RETENTION_DAYS} days ago" +%Y-%m-%d 2>/dev/null ||
		date -u -v-"${BACKUP_RETENTION_DAYS}"d +%Y-%m-%d)
	log "pruning backups older than ${cutoff} (retention ${BACKUP_RETENTION_DAYS}d)"

	aws s3 ls "s3://${AWS_S3_BUCKET}/${PREFIX}/" --region "${AWS_S3_REGION}" |
		while read -r date_part _ _ name; do
			[[ -z "${name:-}" ]] && continue
			if [[ "${date_part}" < "${cutoff}" ]]; then
				log "  removing ${name}"
				aws s3 rm "s3://${AWS_S3_BUCKET}/${PREFIX}/${name}" \
					--region "${AWS_S3_REGION}" --only-show-errors
			fi
		done
}

ensure_awscli

if [[ "${1:-}" != "--loop" ]]; then
	take_backup
	exit 0
fi

log "loop mode: nightly at ${BACKUP_HOUR_UTC}:00 UTC, retention ${BACKUP_RETENTION_DAYS}d"
while true; do
	now_hour=$(date -u +%H)
	if [[ "${now_hour}" == "${BACKUP_HOUR_UTC}" ]]; then
		take_backup || log "ERROR: backup failed; will retry tomorrow"
		sleep 3600 # past the trigger hour so it fires once, not sixty times
	fi
	sleep 300
done
