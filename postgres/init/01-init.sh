#!/bin/bash
# Runs once, the first time the Postgres volume is created.
# Creates: n8n database (n8n's own storage), monitoring database (predictions + drift metrics),
# and a read-only role for Grafana.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE n8n LOGIN PASSWORD '${N8N_DB_PASSWORD}';
    CREATE DATABASE n8n OWNER n8n;

    CREATE ROLE monitor LOGIN PASSWORD '${MONITOR_DB_PASSWORD}';
    CREATE DATABASE monitoring OWNER monitor;

    CREATE ROLE grafana_reader LOGIN PASSWORD '${GRAFANA_DB_PASSWORD}';
    GRANT CONNECT ON DATABASE monitoring TO grafana_reader;
EOSQL

# Read-only access for Grafana to every table the monitor creates, now and later.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname monitoring <<-EOSQL
    GRANT USAGE ON SCHEMA public TO grafana_reader;
    ALTER DEFAULT PRIVILEGES FOR ROLE monitor IN SCHEMA public GRANT SELECT ON TABLES TO grafana_reader;
    GRANT CREATE ON SCHEMA public TO monitor;
EOSQL
