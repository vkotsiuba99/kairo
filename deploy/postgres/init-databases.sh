#!/bin/sh
# Окрема база для кожного сервісу з власними даними (Media зберігає лише файли).
set -e
for db in kairo_content kairo_scheduler kairo_publishing kairo_telemetry kairo_trends; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -c "SELECT 'CREATE DATABASE $db' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$db')\gexec"
done
