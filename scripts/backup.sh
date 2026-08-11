#!/usr/bin/env bash
# Back up the AI Electrician database and uploaded prints to a timestamped
# tarball. Run from the repo root:  ./scripts/backup.sh [output_dir]
set -euo pipefail

OUT_DIR="${1:-./backups}"
mkdir -p "$OUT_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"

# shellcheck disable=SC1091
set -a; [ -f .env ] && . ./.env; set +a
DB_NAME="${POSTGRES_DB:-aielectrician}"
DB_USER="${POSTGRES_USER:-aielec}"

echo "Dumping database '$DB_NAME' ..."
docker compose exec -T db pg_dump -U "$DB_USER" "$DB_NAME" > "$OUT_DIR/db-$STAMP.sql"

echo "Archiving uploaded prints + rendered pages ..."
docker compose cp api:/data "$OUT_DIR/data-$STAMP" >/dev/null
tar -czf "$OUT_DIR/data-$STAMP.tgz" -C "$OUT_DIR" "data-$STAMP"
rm -rf "$OUT_DIR/data-$STAMP"

echo "Backup complete:"
echo "  $OUT_DIR/db-$STAMP.sql"
echo "  $OUT_DIR/data-$STAMP.tgz"
