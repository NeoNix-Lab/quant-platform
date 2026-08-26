-- test_catalog_ddl.sql — verifica empirica del DDL del catalogo.
--
-- Va eseguito con psql -v ON_ERROR_STOP=1 su un PostgreSQL 17 USA-E-GETTA,
-- gia' inizializzato da 001_catalog.sql + 002_roles.sh. Non tocca mai il
-- catalogo di produzione.
--
-- I dataset vengono identificati per rel_root, derivato dall'identita'
-- naturale: non esiste piu' una colonna slug.
--
-- Ogni asserzione fallisce rumorosamente. Il test e' scritto per fallire.
--
-- RIESEGUIBILE: tutto gira dentro una transazione chiusa da ROLLBACK, funzioni
-- di supporto comprese. Dopo l'esecuzione il database torna esattamente allo
-- stato precedente, quindi due run consecutivi sulla stessa istanza danno lo
-- stesso risultato. Se un'asserzione fallisce, ON_ERROR_STOP aborta e la
-- transazione viene comunque annullata: nessun residuo neanche in caso di
-- errore.

\set ON_ERROR_STOP on

BEGIN;
SET LOCAL search_path TO catalog, public;

CREATE OR REPLACE FUNCTION assert(cond boolean, what text) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    IF cond IS NOT TRUE THEN
        RAISE EXCEPTION 'ASSERZIONE FALLITA: %', what;
    END IF;
    RAISE NOTICE '  ok  %', what;
END; $$;

CREATE OR REPLACE FUNCTION rifiuta(stmt text) RETURNS boolean
LANGUAGE plpgsql AS $$
BEGIN
    EXECUTE stmt;
    RETURN false;
EXCEPTION
    WHEN integrity_constraint_violation THEN RETURN true;
    WHEN invalid_text_representation  THEN RETURN true;
    WHEN datatype_mismatch            THEN RETURN true;
END; $$;

CREATE OR REPLACE FUNCTION ds(root text) RETURNS uuid
LANGUAGE sql STABLE AS $$ SELECT dataset_id FROM datasets WHERE rel_root = root $$;

\echo ''
\echo '=== 1. ownership ==='
SELECT assert(count(*) = 0, 'nessuna tabella con owner diverso da catalog_owner')
FROM pg_tables WHERE schemaname = 'catalog' AND tableowner <> 'catalog_owner';
SELECT assert(pg_get_userbyid(nspowner) = 'catalog_owner', 'schema catalog di catalog_owner')
FROM pg_namespace WHERE nspname = 'catalog';
SELECT assert(count(*) = 3, 'tre utenti applicativi, tutti non-superuser')
FROM pg_roles WHERE rolname LIKE 'market_catalog_%' AND rolcanlogin AND NOT rolsuper;

\echo ''
\echo '=== 2. forma di datasets: instrument, niente symbol, niente slug ==='
SELECT assert(count(*) = 1, 'datasets.instrument esiste')
FROM information_schema.columns
WHERE table_schema='catalog' AND table_name='datasets' AND column_name='instrument';
SELECT assert(count(*) = 0, 'datasets.symbol non esiste piu')
FROM information_schema.columns
WHERE table_schema='catalog' AND table_name='datasets' AND column_name='symbol';
SELECT assert(count(*) = 0, 'datasets.slug rimosso: l identita e la tupla, il path e rel_root')
FROM information_schema.columns
WHERE table_schema='catalog' AND table_name='datasets' AND column_name='slug';
SELECT assert(count(*) = 0, 'v_partitions non espone piu dataset_slug')
FROM information_schema.columns
WHERE table_schema='catalog' AND table_name='v_partitions' AND column_name='dataset_slug';
SELECT assert(count(*) = 1, 'v_partitions espone dataset_rel_root')
FROM information_schema.columns
WHERE table_schema='catalog' AND table_name='v_partitions' AND column_name='dataset_rel_root';

\echo ''
\echo '=== 3. colonne obbligatorie: parita col manifest ==='
SELECT assert(count(*) = 3, 'datasets: layer, venue, instrument NOT NULL')
FROM information_schema.columns
WHERE table_schema='catalog' AND table_name='datasets'
  AND column_name IN ('layer','venue','instrument') AND is_nullable='NO';
SELECT assert(is_nullable = 'NO', 'datasets.schema_id NOT NULL: fa parte dell identita')
FROM information_schema.columns
WHERE table_schema='catalog' AND table_name='datasets' AND column_name='schema_id';
SELECT assert(count(*) = 2, 'partitions: row_count e byte_size NOT NULL')
FROM information_schema.columns
WHERE table_schema='catalog' AND table_name='partitions'
  AND column_name IN ('row_count','byte_size') AND is_nullable='NO';

\echo ''
\echo '=== 4. dati di appoggio ==='
INSERT INTO schema_registry (schema_id, name, version, json_sha256, body)
VALUES ('trade-v1','trade',1,repeat('a',64),'{}'),
       ('trade-v2','trade',2,repeat('b',64),'{}');
INSERT INTO feature_set_definitions (slug, version, definition_ref)
VALUES ('trade_microstructure', 1, 'd92aa10'),
       ('trade_microstructure', 2, 'd92aa11');

INSERT INTO datasets (layer, kind, venue, instrument, rel_root, schema_id, manifest_sha256)
VALUES ('raw','trades','bybit','BTCUSDT',
        'raw/trades/bybit/BTCUSDT/trade-v1','trade-v1',repeat('1',64)),
       ('canonical','trades','bybit','BTCUSDT',
        'canonical/trades/bybit/BTCUSDT/trade-v1','trade-v1',repeat('9',64));

\echo ''
\echo '=== 5. natural key ==='
SELECT assert(rifiuta($q$
  INSERT INTO datasets (layer, kind, venue, instrument, rel_root, schema_id, manifest_sha256)
  VALUES ('raw','trades','bybit','BTCUSDT',
          'raw/trades/bybit/BTCUSDT/altro','trade-v1',repeat('2',64))
$q$), 'tupla naturale duplicata respinta anche con rel_root diverso');

INSERT INTO datasets (layer, kind, venue, instrument, rel_root, schema_id, manifest_sha256)
VALUES ('raw','trades','bybit','BTCUSDT',
        'raw/trades/bybit/BTCUSDT/trade-v2','trade-v2',repeat('3',64));
SELECT assert(count(*) = 2, 'record_schema_id diverso produce un dataset distinto')
FROM datasets WHERE layer='raw' AND venue='bybit' AND instrument='BTCUSDT';

SELECT assert(rifiuta($q$
  INSERT INTO datasets (layer, kind, venue, instrument, rel_root, schema_id, manifest_sha256)
  VALUES ('canonical','trades','bybit','BTCUSDT',
          'canonical/trades/bybit/BTCUSDT/bis','trade-v1',repeat('6',64))
$q$), 'NULLS NOT DISTINCT: due feature_set_def_id NULL collidono come devono');

INSERT INTO datasets (layer, kind, venue, instrument, rel_root, schema_id,
                      feature_set_def_id, manifest_sha256)
SELECT 'features','trade_microstructure','bybit','BTCUSDT',
       'features/trade_microstructure/bybit/BTCUSDT/trade_microstructure/v'||version||'/trade-v1',
       'trade-v1', feature_set_def_id, repeat('6',64)
FROM feature_set_definitions WHERE slug='trade_microstructure';
SELECT assert(count(*) = 2, 'due versioni di feature set = due dataset distinti')
FROM datasets WHERE layer='features';

\echo ''
\echo '=== 6. NOT NULL e foreign key su datasets ==='
SELECT assert(rifiuta($q$
  INSERT INTO datasets (layer, kind, venue, instrument, rel_root, schema_id, manifest_sha256)
  VALUES ('raw','trades',NULL,'X','raw/trades/x/X/trade-v1','trade-v1',repeat('7',64))
$q$), 'venue NULL respinta');
SELECT assert(rifiuta($q$
  INSERT INTO datasets (layer, kind, venue, instrument, rel_root, schema_id, manifest_sha256)
  VALUES ('canonical','trades','bybit',NULL,'canonical/trades/bybit/X/trade-v1','trade-v1',repeat('7',64))
$q$), 'instrument NULL respinto anche nel layer canonical');
SELECT assert(rifiuta($q$
  INSERT INTO datasets (layer, kind, venue, instrument, rel_root, schema_id, manifest_sha256)
  VALUES ('raw','trades','bybit','Y','raw/trades/bybit/Y/none',NULL,repeat('7',64))
$q$), 'schema_id NULL respinto: senza, l identita naturale sarebbe incompleta');
SELECT assert(rifiuta($q$
  INSERT INTO datasets (layer, kind, venue, instrument, rel_root, schema_id, manifest_sha256)
  VALUES ('raw','trades','bybit','Z','raw/trades/bybit/Z/ghost','ghost-v1',repeat('7',64))
$q$), 'schema_id inesistente respinto dalla foreign key');

\echo ''
\echo '=== 7. rel_root distingue le versioni di record schema ==='
SELECT assert(count(DISTINCT rel_root) = 2,
              'due dataset identici salvo record_schema_id hanno rel_root diversi')
FROM datasets WHERE layer='raw' AND venue='bybit' AND instrument='BTCUSDT';

\echo ''
\echo '=== 8. sequence oltre 2^53 ==='
INSERT INTO partitions (dataset_id, partition_key, storage_root_id, rel_path,
                        ts_start, ts_end, row_count, byte_size, content_sha256,
                        manifest_sha256, state, created_at, closed_at,
                        first_sequence, last_sequence, producer, code_ref)
VALUES (ds('raw/trades/bybit/BTCUSDT/trade-v1'), 'dt=2026-08-25', 'hot',
        'dt=2026-08-25/part-000.parquet',
        '2026-08-25T00:00:00Z','2026-08-25T23:59:59Z', 1842776, 48211934,
        repeat('a',64), repeat('b',64), 'valid',
        '2026-08-25T00:00:00Z', '2026-08-26T00:00:04Z',
        9007199254740993, 99999999999999999999999999999999, 'bybit-collector','4f1a9c3');
SELECT assert(first_sequence = 9007199254740993::numeric,
              'sequence 2^53+1 conservata senza arrotondamento')
FROM partitions WHERE partition_key='dt=2026-08-25';
SELECT assert(last_sequence = 99999999999999999999999999999999::numeric,
              'sequence a 32 cifre conservata esattamente')
FROM partitions WHERE partition_key='dt=2026-08-25';

\echo ''
\echo '=== 9. ordinamento NUMERICO, non lessicografico ==='
SELECT assert((SELECT min(v) FROM (VALUES (9::numeric),(10::numeric),(100::numeric)) t(v)) = 9,
              'numeric ordina 9 < 10 < 100');
SELECT assert((SELECT min(v) FROM (VALUES ('9'),('10'),('100')) t(v)) = '10',
              'text ordinerebbe 10 < 100 < 9 — ecco perche numeric e non text');

\echo ''
\echo '=== 10. vincoli sulle sequence ==='
SELECT assert(rifiuta($q$UPDATE partitions SET first_sequence = -1$q$), 'sequence negativa respinta');
SELECT assert(rifiuta($q$UPDATE partitions SET first_sequence = 1.5$q$), 'sequence non intera respinta');
SELECT assert(rifiuta($q$UPDATE partitions SET last_sequence = NULL$q$), 'co-nullita: un solo confine respinto');
SELECT assert(rifiuta($q$UPDATE partitions SET first_sequence = 10, last_sequence = 5$q$),
              'first_sequence > last_sequence respinto');

\echo ''
\echo '=== 11. writing vs sealed ==='
SELECT assert(rifiuta($q$UPDATE partitions SET closed_at = NULL$q$), 'sigillata senza closed_at respinta');
SELECT assert(rifiuta($q$UPDATE partitions SET content_sha256 = NULL$q$), 'sigillata senza content_sha256 respinta');
SELECT assert(rifiuta($q$UPDATE partitions SET manifest_sha256 = NULL$q$), 'sigillata senza manifest_sha256 respinta');
INSERT INTO partitions (dataset_id, partition_key, storage_root_id, rel_path,
                        row_count, byte_size, state, producer, code_ref)
VALUES (ds('raw/trades/bybit/BTCUSDT/trade-v1'), 'dt=2026-08-26', 'hot',
        'dt=2026-08-26/part-000.parquet', 0, 0, 'writing', 'bybit-collector', '4f1a9c3');
SELECT assert(count(*) = 1, 'writing accettata senza sha256/closed_at/sequence')
FROM partitions WHERE state='writing';

\echo ''
\echo '=== 12. row_count e byte_size obbligatori ==='
SELECT assert(rifiuta($q$
  INSERT INTO partitions (dataset_id, partition_key, storage_root_id, rel_path,
                          byte_size, state, producer, code_ref)
  VALUES (ds('raw/trades/bybit/BTCUSDT/trade-v1'), 'dt=2026-09-01', 'hot',
          'dt=2026-09-01/p.parquet', 0, 'writing', 'p', 'c')
$q$), 'row_count mancante respinto: il manifest lo ha sempre');
SELECT assert(rifiuta($q$
  INSERT INTO partitions (dataset_id, partition_key, storage_root_id, rel_path,
                          row_count, state, producer, code_ref)
  VALUES (ds('raw/trades/bybit/BTCUSDT/trade-v1'), 'dt=2026-09-02', 'hot',
          'dt=2026-09-02/p.parquet', 0, 'writing', 'p', 'c')
$q$), 'byte_size mancante respinto');
SELECT assert(rifiuta($q$
  INSERT INTO partitions (dataset_id, partition_key, storage_root_id, rel_path,
                          row_count, byte_size, state, producer, code_ref)
  VALUES (ds('raw/trades/bybit/BTCUSDT/trade-v1'), 'dt=2026-09-03', 'hot',
          'dt=2026-09-03/p.parquet', 42, 100, 'writing', 'p', 'c')
$q$), 'row_count > 0 senza copertura temporale respinto');

\echo ''
\echo '=== 13. producer / code_ref ==='
SELECT assert(rifiuta($q$UPDATE partitions SET producer = NULL$q$), 'producer NULL respinto');
SELECT assert(rifiuta($q$UPDATE partitions SET code_ref = '   '$q$), 'code_ref di soli spazi respinto');

\echo ''
\echo '=== 14. una sola revisione viva ==='
SELECT assert(rifiuta($q$
  INSERT INTO partitions (dataset_id, partition_key, revision, storage_root_id, rel_path,
                          ts_start, ts_end, row_count, byte_size, content_sha256,
                          manifest_sha256, state, closed_at, producer, code_ref)
  VALUES (ds('raw/trades/bybit/BTCUSDT/trade-v1'), 'dt=2026-08-25', 2, 'hot',
          'dt=2026-08-25/part-001.parquet', '2026-08-25T00:00:00Z','2026-08-25T23:59:59Z',
          1, 1, repeat('c',64), repeat('d',64), 'valid', now(), 'p', 'c')
$q$), 'seconda revisione viva respinta finche la prima non e superseded');

UPDATE partitions SET state='superseded' WHERE partition_key='dt=2026-08-25';
INSERT INTO partitions (dataset_id, partition_key, revision, storage_root_id, rel_path,
                        ts_start, ts_end, row_count, byte_size, content_sha256,
                        manifest_sha256, state, closed_at, producer, code_ref)
VALUES (ds('raw/trades/bybit/BTCUSDT/trade-v1'), 'dt=2026-08-25', 2, 'hot',
        'dt=2026-08-25/part-001.parquet', '2026-08-25T00:00:00Z','2026-08-25T23:59:59Z',
        1, 1, repeat('c',64), repeat('d',64), 'valid', now(), 'p', 'c');
SELECT assert(count(*) = 2, 'revisione 2 accettata dopo il supersede')
FROM partitions WHERE partition_key='dt=2026-08-25';

\echo ''
\echo '=== 15. v_partitions restituisce un FILE path ==='
SELECT assert(
  file_abs_path = '/srv/marketdata/raw/trades/bybit/BTCUSDT/trade-v1/dt=2026-08-25/part-001.parquet',
  'file_abs_path risolve al data file, non alla directory')
FROM v_partitions WHERE partition_key='dt=2026-08-25' AND revision=2;
SELECT assert(file_abs_path LIKE '%.parquet', 'file_abs_path termina col file')
FROM v_partitions WHERE partition_key='dt=2026-08-25' AND revision=2;

\echo ''
\echo '=== 16. tiering: sposta il file, non l identita ==='
UPDATE partitions SET storage_root_id='cold', tiered_at=now()
WHERE partition_key='dt=2026-08-25' AND revision=2;
SELECT assert(file_abs_path LIKE '/archive/marketdata-cold/%', 'UPDATE dello storage root sposta il path')
FROM v_partitions WHERE partition_key='dt=2026-08-25' AND revision=2;
SELECT assert(count(*) = 1, 'il dataset non si e duplicato')
FROM datasets WHERE rel_root='raw/trades/bybit/BTCUSDT/trade-v1';

\echo ''
\echo '=== 17. lineage: identita semantica, nessun code_ref ==='
SELECT assert(count(*) = 0, 'dataset_lineage.code_ref rimosso dallo schema')
FROM information_schema.columns
WHERE table_schema='catalog' AND table_name='dataset_lineage' AND column_name='code_ref';
SELECT assert(rifiuta($q$
  INSERT INTO dataset_lineage (child_id, parent_id, transform)
  VALUES (ds('canonical/trades/bybit/BTCUSDT/trade-v1'),
          ds('canonical/trades/bybit/BTCUSDT/trade-v1'), 'canonicalize-trades-v1')
$q$), 'lineage su se stesso respinta');
SELECT assert(rifiuta($q$
  INSERT INTO dataset_lineage (child_id, parent_id, transform)
  VALUES (ds('canonical/trades/bybit/BTCUSDT/trade-v1'),
          ds('raw/trades/bybit/BTCUSDT/trade-v1'), '   ')
$q$), 'transform vuoto respinto');
INSERT INTO dataset_lineage (child_id, parent_id, transform)
VALUES (ds('canonical/trades/bybit/BTCUSDT/trade-v1'),
        ds('raw/trades/bybit/BTCUSDT/trade-v1'), 'canonicalize-trades-v1');
SELECT assert(count(*) = 1, 'arco di lineage con sola identita semantica accettato')
FROM dataset_lineage WHERE transform='canonicalize-trades-v1';

\echo ''
\echo '=== 18. partition_key: grammatica key=value ==='
SELECT assert(rifiuta($q$
  INSERT INTO partitions (dataset_id, partition_key, storage_root_id, rel_path,
                          row_count, byte_size, state, producer, code_ref)
  VALUES (ds('raw/trades/bybit/BTCUSDT/trade-v1'), '2026-08-25', 'hot',
          'x/p.parquet', 0, 0, 'writing', 'p', 'c')
$q$), 'partition_key data nuda respinta');
SELECT assert(rifiuta($q$
  INSERT INTO partitions (dataset_id, partition_key, storage_root_id, rel_path,
                          row_count, byte_size, state, producer, code_ref)
  VALUES (ds('raw/trades/bybit/BTCUSDT/trade-v1'), '/dt=2026-08-25', 'hot',
          'x/p.parquet', 0, 0, 'writing', 'p', 'c')
$q$), 'partition_key con slash iniziale respinta');
SELECT assert(rifiuta($q$
  INSERT INTO partitions (dataset_id, partition_key, storage_root_id, rel_path,
                          row_count, byte_size, state, producer, code_ref)
  VALUES (ds('raw/trades/bybit/BTCUSDT/trade-v1'), 'dt=', 'hot',
          'x/p.parquet', 0, 0, 'writing', 'p', 'c')
$q$), 'partition_key con valore vuoto respinta');
SELECT assert(rifiuta($q$
  INSERT INTO partitions (dataset_id, partition_key, storage_root_id, rel_path,
                          row_count, byte_size, state, producer, code_ref)
  VALUES (ds('raw/trades/bybit/BTCUSDT/trade-v1'), 'DT=2026-08-25', 'hot',
          'x/p.parquet', 0, 0, 'writing', 'p', 'c')
$q$), 'partition_key con chiave maiuscola respinta');
INSERT INTO partitions (dataset_id, partition_key, storage_root_id, rel_path,
                        row_count, byte_size, state, producer, code_ref)
VALUES (ds('raw/trades/bybit/BTCUSDT/trade-v1'), 'dt=2026-08-27/hour=14', 'hot',
        'dt=2026-08-27/hour=14/part-000.parquet', 0, 0, 'writing', 'p', 'c');
SELECT assert(count(*) = 1, 'partition_key composta dt=.../hour=... accettata')
FROM partitions WHERE partition_key = 'dt=2026-08-27/hour=14';

\echo ''
\echo '=== 19. rel_path_safe: encoding canonico e anti-traversal ==='
SELECT assert(rifiuta($q$
  INSERT INTO datasets (layer, kind, venue, instrument, rel_root, schema_id, manifest_sha256)
  VALUES ('raw','trades','bybit','X','raw/trades/bybit/%2E%2E/trade-v1','trade-v1',repeat('c',64))
$q$), 'rel_root con %2E%2E respinto: il traversal encodato non passa');
SELECT assert(rifiuta($q$
  INSERT INTO datasets (layer, kind, venue, instrument, rel_root, schema_id, manifest_sha256)
  VALUES ('raw','trades','bybit','X','raw/trades/bybit/BTC%2fUSD/trade-v1','trade-v1',repeat('c',64))
$q$), 'escape minuscolo respinto: non e la forma canonica');
SELECT assert(rifiuta($q$
  INSERT INTO datasets (layer, kind, venue, instrument, rel_root, schema_id, manifest_sha256)
  VALUES ('raw','trades','bybit','X','raw/trades/bybit/BTC%/trade-v1','trade-v1',repeat('c',64))
$q$), 'percent isolato respinto');
INSERT INTO datasets (layer, kind, venue, instrument, rel_root, schema_id, manifest_sha256)
VALUES ('raw','trades','kraken','XBT/USD','raw/trades/kraken/XBT%2FUSD/trade-v1','trade-v1',repeat('d',64)),
       ('raw','trades','kraken','XBT-USD','raw/trades/kraken/XBT-USD/trade-v1','trade-v1',repeat('e',64));
SELECT assert(count(DISTINCT rel_root) = 2, 'XBT/USD e XBT-USD coesistono con rel_root diversi')
FROM datasets WHERE venue='kraken';

\echo ''
\echo '=== 20. partitions.rel_path: nessun percent-encoding ==='
SELECT assert(rifiuta($q$
  INSERT INTO partitions (dataset_id, partition_key, storage_root_id, rel_path,
                          row_count, byte_size, state, producer, code_ref)
  VALUES (ds('raw/trades/bybit/BTCUSDT/trade-v1'), 'dt=2026-09-10', 'hot',
          'dt=2026-09-10/part%2F000.parquet', 0, 0, 'writing', 'p', 'c')
$q$), 'rel_path con percent respinto: qui non serve encoding');
SELECT assert(rifiuta($q$
  INSERT INTO partitions (dataset_id, partition_key, storage_root_id, rel_path,
                          row_count, byte_size, state, producer, code_ref)
  VALUES (ds('raw/trades/bybit/BTCUSDT/trade-v1'), 'dt=2026-09-11', 'hot',
          'dt=2026-09-11/%2E%2E.parquet', 0, 0, 'writing', 'p', 'c')
$q$), 'rel_path con traversal encodato respinto');
SELECT assert(count(*) = 0, 'nessun rel_path contiene percent')
FROM partitions WHERE position('%' in rel_path) > 0;

\echo ''
\echo '=== 21. rel_path deve stare dentro la propria partizione ==='
SELECT assert(rifiuta($q$
  INSERT INTO partitions (dataset_id, partition_key, storage_root_id, rel_path,
                          row_count, byte_size, state, producer, code_ref)
  VALUES (ds('raw/trades/bybit/BTCUSDT/trade-v1'), 'dt=2026-10-01', 'hot',
          'dt=2026-10-02/part-000.parquet', 0, 0, 'writing', 'p', 'c')
$q$), 'rel_path di un altro giorno respinto');
SELECT assert(rifiuta($q$
  INSERT INTO partitions (dataset_id, partition_key, storage_root_id, rel_path,
                          row_count, byte_size, state, producer, code_ref)
  VALUES (ds('raw/trades/bybit/BTCUSDT/trade-v1'), 'dt=2026-10-03', 'hot',
          'part-000.parquet', 0, 0, 'writing', 'p', 'c')
$q$), 'rel_path senza prefisso di partizione respinto');
SELECT assert(rifiuta($q$
  INSERT INTO partitions (dataset_id, partition_key, storage_root_id, rel_path,
                          row_count, byte_size, state, producer, code_ref)
  VALUES (ds('raw/trades/bybit/BTCUSDT/trade-v1'), 'dt=2026-10-04', 'hot',
          'dt=2026-10-04-bis/part-000.parquet', 0, 0, 'writing', 'p', 'c')
$q$), 'prefisso parziale senza separatore respinto');
-- starts_with e non LIKE: un partition_key con '_' sarebbe un wildcard
SELECT assert(rifiuta($q$
  INSERT INTO partitions (dataset_id, partition_key, storage_root_id, rel_path,
                          row_count, byte_size, state, producer, code_ref)
  VALUES (ds('raw/trades/bybit/BTCUSDT/trade-v1'), 'a_b=1', 'hot',
          'axb=1/part-000.parquet', 0, 0, 'writing', 'p', 'c')
$q$), 'underscore del partition_key non e trattato come wildcard');
INSERT INTO partitions (dataset_id, partition_key, storage_root_id, rel_path,
                        row_count, byte_size, state, producer, code_ref)
VALUES (ds('raw/trades/bybit/BTCUSDT/trade-v1'), 'dt=2026-10-05', 'hot',
        'dt=2026-10-05/part-000.parquet', 0, 0, 'writing', 'p', 'c');
SELECT assert(count(*) = 1, 'rel_path dentro la partizione accettato')
FROM partitions WHERE partition_key = 'dt=2026-10-05';

\echo ''
\echo '=== 22. closed_at non puo precedere created_at ==='
SELECT assert(rifiuta($q$
  INSERT INTO partitions (dataset_id, partition_key, storage_root_id, rel_path,
                          ts_start, ts_end, row_count, byte_size, content_sha256,
                          manifest_sha256, state, created_at, closed_at, producer, code_ref)
  VALUES (ds('raw/trades/bybit/BTCUSDT/trade-v1'), 'dt=2026-10-06', 'hot',
          'dt=2026-10-06/part-000.parquet', '2026-10-06T00:00:00Z','2026-10-06T23:59:59Z',
          1, 1, repeat('a',64), repeat('b',64), 'valid',
          '2026-10-07T00:00:00Z', '2026-10-06T00:00:00Z', 'p', 'c')
$q$), 'sigillata prima di essere aperta respinta');
INSERT INTO partitions (dataset_id, partition_key, storage_root_id, rel_path,
                        ts_start, ts_end, row_count, byte_size, content_sha256,
                        manifest_sha256, state, created_at, closed_at, producer, code_ref)
VALUES (ds('raw/trades/bybit/BTCUSDT/trade-v1'), 'dt=2026-10-08', 'hot',
        'dt=2026-10-08/part-000.parquet', '2026-10-08T00:00:00Z','2026-10-08T23:59:59Z',
        1, 1, repeat('a',64), repeat('b',64), 'valid',
        '2026-10-08T00:00:00Z', '2026-10-08T00:00:00Z', 'p', 'c');
SELECT assert(count(*) = 1, 'closed_at uguale a created_at accettato')
FROM partitions WHERE partition_key = 'dt=2026-10-08';

\echo ''
\echo '=== 23. feature_set_definitions.definition_ref ==='
SELECT assert(count(*) = 1, 'la colonna si chiama definition_ref')
FROM information_schema.columns
WHERE table_schema='catalog' AND table_name='feature_set_definitions'
  AND column_name='definition_ref';
SELECT assert(count(*) = 0, 'feature_set_definitions.code_ref non esiste piu')
FROM information_schema.columns
WHERE table_schema='catalog' AND table_name='feature_set_definitions'
  AND column_name='code_ref';
SELECT assert(rifiuta($q$
  INSERT INTO feature_set_definitions (slug, version, definition_ref)
  VALUES ('vuoto', 1, '   ')
$q$), 'definition_ref di soli spazi respinto');

\echo ''
\echo '=== 24. il test non lascia residui ==='
-- Si guarda PRIMA del rollback che i dati di prova ci siano davvero: se fossero
-- gia' zero, il ROLLBACK sotto non proverebbe nulla.
SELECT assert(count(*) > 0, 'dentro la transazione i dati di prova esistono')
FROM datasets;

ROLLBACK;

\echo ''
DO $$
DECLARE
    n_datasets   int;
    n_partitions int;
    n_lineage    int;
    n_registry   int;
    n_helpers    int;
    n_roots      int;
BEGIN
    SELECT count(*) INTO n_datasets   FROM catalog.datasets;
    SELECT count(*) INTO n_partitions FROM catalog.partitions;
    SELECT count(*) INTO n_lineage    FROM catalog.dataset_lineage;
    SELECT count(*) INTO n_registry   FROM catalog.schema_registry;
    SELECT count(*) INTO n_roots      FROM catalog.storage_roots;
    SELECT count(*) INTO n_helpers
      FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
     WHERE ns.nspname = 'catalog' AND p.proname IN ('assert','rifiuta','ds');

    IF n_datasets <> 0 OR n_partitions <> 0 OR n_lineage <> 0 OR n_registry <> 0 THEN
        RAISE EXCEPTION 'ROLLBACK incompleto: datasets=% partitions=% lineage=% registry=%',
              n_datasets, n_partitions, n_lineage, n_registry;
    END IF;
    IF n_helpers <> 0 THEN
        RAISE EXCEPTION 'ROLLBACK incompleto: % funzioni di supporto sopravvissute', n_helpers;
    END IF;
    IF n_roots <> 2 THEN
        RAISE EXCEPTION 'storage_roots alterata: attese 2 righe seminate dal DDL, trovate %', n_roots;
    END IF;
    RAISE NOTICE '  ok  dopo il ROLLBACK il database e tornato allo stato iniziale';
    RAISE NOTICE '      (0 datasets, 0 partitions, 0 lineage, 0 schema_registry,';
    RAISE NOTICE '       0 funzioni di supporto, 2 storage_roots del DDL)';
END $$;

\echo ''
\echo 'TUTTE LE ASSERZIONI SUPERATE'
