#!/bin/sh
# Окрема база для кожного сервісу з власними даними (Media зберігає лише файли).
set -e
for db in kairo_content kairo_scheduler kairo_publishing kairo_telemetry kairo_trends; do
  exists=$(psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT 1 FROM pg_database WHERE datname = '$db'")
  if [ "$exists" != "1" ]; then
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE DATABASE $db"
  fi
done
