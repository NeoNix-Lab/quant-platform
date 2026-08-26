#!/usr/bin/env bash
#
# 002_roles.sh — crea gli utenti applicativi del catalogo.
#
# Gira una sola volta, dentro docker-entrypoint-initdb.d, subito dopo
# 001_catalog.sql. Esiste separato dal SQL per una ragione precisa: le password
# arrivano dall'environment del container (quindi dal .env, che non e' in git) e
# non devono comparire in nessun file versionato.
#
# POSTGRES_USER resta il superuser del cluster e NON va usato dai servizi.
#
set -euo pipefail

miss=0
for v in CATALOG_ADMIN_PASSWORD CATALOG_WRITER_PASSWORD CATALOG_READER_PASSWORD; do
    if [[ -z "${!v:-}" ]]; then
        echo "002_roles.sh: FATAL — variabile $v non impostata" >&2
        miss=1
    fi
done
# Fallire qui e' voluto: meglio un init interrotto che tre account applicativi
# senza password su un catalogo che poi qualcuno espone.
(( miss == 0 )) || exit 1

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v apw="$CATALOG_ADMIN_PASSWORD" \
     -v wpw="$CATALOG_WRITER_PASSWORD" \
     -v rpw="$CATALOG_READER_PASSWORD" \
     -v db="$POSTGRES_DB" <<'EOSQL'

-- amministrazione del catalogo: migra lo schema, NON e' superuser.
-- Membro di catalog_owner, che possiede ogni oggetto creato da 001_catalog.sql:
-- senza questa membership un ALTER TABLE su una tabella esistente fallirebbe,
-- perche' il DDL richiede ownership e nessun GRANT la conferisce.
CREATE ROLE market_catalog_admin  LOGIN PASSWORD :'apw' IN ROLE catalog_owner;
-- collector, canonicalizer, feature builder: scrivono l'indice
CREATE ROLE market_catalog_writer LOGIN PASSWORD :'wpw' IN ROLE catalog_writer;
-- DataGateway, Quant, dashboard: sola lettura
CREATE ROLE market_catalog_reader LOGIN PASSWORD :'rpw' IN ROLE catalog_reader;

-- Ogni sessione dell'admin assume catalog_owner all'ingresso. Due effetti:
--   1. le tabelle create da una migrazione appartengono a catalog_owner, non
--      a market_catalog_admin, quindi ricadono sotto gli ALTER DEFAULT
--      PRIVILEGES FOR ROLE catalog_owner e reader/writer le vedono subito;
--   2. un secondo amministratore aggiunto domani eredita lo stesso ownership
--      invece di creare oggetti orfani che solo lui puo' toccare.
ALTER ROLE market_catalog_admin SET role = catalog_owner;

-- Il database 'postgres' concede CONNECT a PUBLIC per default, e i privilegi
-- sono ADDITIVI: revocarlo ai singoli ruoli non annulla quello che ricevono
-- via PUBLIC, quindi si collegherebbero lo stesso. La revoca va fatta a PUBLIC.
--
-- Questo cluster e' dedicato al catalogo: nessuno deve usare 'postgres' come
-- database di lavoro. I superuser non sono soggetti al controllo e continuano
-- ad accedervi per la manutenzione.
--
-- NON e' un isolamento generale: un database creato in futuro nascerebbe di
-- nuovo con CONNECT a PUBLIC e andrebbe ristretto a parte.
REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;

-- Stessa logica sul catalogo stesso: si toglie l'accesso implicito di PUBLIC e
-- lo si concede NOMINALMENTE ai tre utenti applicativi. Senza questo, un ruolo
-- creato domani per tutt'altro scopo potrebbe collegarsi al catalogo senza che
-- nessuno gliene abbia dato il permesso.
--
-- :"db" e' il nome del database quotato come IDENTIFICATORE (da POSTGRES_DB),
-- non come stringa: GRANT/REVOKE vogliono un identificatore. Nessun nome
-- hardcodato, cosi' rinominare il database non spezza silenziosamente i
-- privilegi.
REVOKE CONNECT ON DATABASE :"db" FROM PUBLIC;
GRANT  CONNECT ON DATABASE :"db"
    TO market_catalog_admin, market_catalog_writer, market_catalog_reader;

EOSQL

echo "002_roles.sh: creati market_catalog_admin / _writer / _reader"
