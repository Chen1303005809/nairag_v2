#!/usr/bin/env bash
# Nightly production update and cold backup. The Compose stacks are stopped
# during the archive so no service can write into the backed-up volumes.
set -Eeuo pipefail

PROJECT_DIR="/mnt/data/RohonDev1/nairag_v2"
BACKUP_DIR="/mnt/data/RohonDev1/nairag_v2_backups"
RETENTION_DAYS=14
STOP_TIMEOUT_SECONDS=120
DOCKER_BIN="/usr/bin/docker"

mkdir -p "$BACKUP_DIR"
exec >>"$BACKUP_DIR/nightly-maintenance.log" 2>&1
exec 9>"$BACKUP_DIR/.nightly-maintenance.lock"
if ! /usr/bin/flock -n 9; then
  echo "$(date --iso-8601=seconds) another nightly maintenance run is active; skipping"
  exit 0
fi

cd "$PROJECT_DIR"

compose() {
  "$DOCKER_BIN" compose -f docker-compose.yaml -f docker-compose.prod.yaml "$@"
}

light_rag_compose() {
  "$DOCKER_BIN" compose -f lightrag/compose.yaml "$@"
}

restart_platform=0
restart_light_rag=0
backup_verified=0
temporary_archive=""

restore_services() {
  local status=$?
  trap - EXIT
  set +e

  if [[ -n "$temporary_archive" ]]; then
    rm -f "$temporary_archive" || status=1
  fi

  if (( restart_platform )); then
    if (( backup_verified )); then
      echo "$(date --iso-8601=seconds) starting the Nairag stack with updated images"
      if compose up -d; then
        echo "$(date --iso-8601=seconds) Nairag Compose start command completed"
      else
        echo "$(date --iso-8601=seconds) ERROR: failed to start the Nairag Compose stack"
        status=1
      fi
    else
      echo "$(date --iso-8601=seconds) backup was not verified; restarting existing Nairag containers"
      if compose start; then
        echo "$(date --iso-8601=seconds) existing Nairag containers restarted"
      else
        echo "$(date --iso-8601=seconds) ERROR: failed to restart existing Nairag containers"
        status=1
      fi
    fi
  fi

  if (( restart_light_rag )); then
    if (( backup_verified )); then
      echo "$(date --iso-8601=seconds) starting the LightRAG stack with updated images"
      if light_rag_compose up -d; then
        echo "$(date --iso-8601=seconds) LightRAG Compose start command completed"
      else
        echo "$(date --iso-8601=seconds) ERROR: failed to start the LightRAG Compose stack"
        status=1
      fi
    else
      echo "$(date --iso-8601=seconds) backup was not verified; restarting existing LightRAG containers"
      if light_rag_compose start; then
        echo "$(date --iso-8601=seconds) existing LightRAG containers restarted"
      else
        echo "$(date --iso-8601=seconds) ERROR: failed to restart existing LightRAG containers"
        status=1
      fi
    fi
  fi

  exit "$status"
}
trap restore_services EXIT

echo "$(date --iso-8601=seconds) nightly maintenance started"
git pull --ff-only

# Build before the outage so a failed build leaves the running stack untouched.
compose build

light_rag_exists=0
if [[ -f lightrag/.env ]]; then
  if [[ -n "$(light_rag_compose ps --quiet)" ]]; then
    light_rag_exists=1
    light_rag_compose build
  fi
fi

if (( light_rag_exists )); then
  restart_light_rag=1
  light_rag_compose stop --timeout "$STOP_TIMEOUT_SECONDS"
fi
restart_platform=1
compose stop --timeout "$STOP_TIMEOUT_SECONDS"
echo "$(date --iso-8601=seconds) Compose services stopped; archiving volumes"

timestamp="$(date +%Y%m%d-%H%M%S)"
temporary_archive="$BACKUP_DIR/.volumes-$timestamp.tar.gz.tmp"
final_archive="$BACKUP_DIR/volumes-$timestamp.tar.gz"
rm -f "$temporary_archive"
/usr/bin/tar --numeric-owner --acls --xattrs -czf "$temporary_archive" -C "$PROJECT_DIR" volumes
/usr/bin/gzip -t "$temporary_archive"
mv "$temporary_archive" "$final_archive"
temporary_archive=""
backup_verified=1
echo "$(date --iso-8601=seconds) verified backup created: $final_archive"

# Start the stack from the just-built images. Compose recreates services whose
# image or configuration changed and keeps their mounted data directories.
compose up -d
restart_platform=0
if (( restart_light_rag )); then
  light_rag_compose up -d
  restart_light_rag=0
fi
echo "$(date --iso-8601=seconds) Compose stack start commands completed"

/usr/bin/find "$BACKUP_DIR" -maxdepth 1 -type f \
  -name 'volumes-*.tar.gz' -mtime "+$RETENTION_DAYS" -delete
echo "$(date --iso-8601=seconds) nightly maintenance completed"
