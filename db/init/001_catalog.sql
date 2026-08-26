-- market_catalog (rev 2) — catalogo dei dataset market data
--
-- PRINCIPIO: questo database e' RICOSTRUIBILE. La verita' sta nei manifest.json
-- accanto ai dati; qui c'e' solo un indice interrogabile. Ogni riga che descrive
-- dati su disco porta lo sha256 del manifest da cui deriva, cosi' che un rebuild
-- possa verificare di essere allineato.
--
-- Nessuna tabella qui contiene market data: quelli stanno in Parquet.
--
-- Rev 2 recepisce l'audit:
--   * la LOCATION FISICA sta sulla partizione, non sul dataset (hot/cold tiering
--     senza spezzare l'identita' logica del dataset)
--   * la DEFINIZIONE di un feature set e' separata dalla sua MATERIALIZZAZIONE
--   * 'footprint' canonico e 'footprint_microstructure' derivato sono kind distinti
--   * lifecycle esplicito delle partizioni
--   * quality_reports punta a UN SOLO bersaglio (XOR)

BEGIN;

-- gen_random_uuid() e' nel core da PostgreSQL 13: nessuna extension necessaria.

-- ---------------------------------------------------------------------------
-- Ruoli portatori di privilegi (NOLOGIN). Gli utenti LOGIN che li ereditano li
-- crea 002_roles.sh, perche' le password arrivano dall'environment e non
-- devono comparire in un file versionato.
--
--   catalog_owner   proprietario dello schema e di OGNI oggetto
--   catalog_writer  DML sull'indice
--   catalog_reader  sola lettura
--
-- Perche' catalog_owner esiste: se le tabelle restassero di proprieta' del
-- superuser che esegue questo file, un market_catalog_admin non-superuser non
-- potrebbe fare ALTER TABLE su di esse — GRANT concede privilegi, non
-- ownership, e il DDL su un oggetto esistente richiede di essere owner (o
-- membro del ruolo owner). Con questo modello l'admin diventa membro di
-- catalog_owner e migra davvero, senza essere superuser.
-- ---------------------------------------------------------------------------
CREATE ROLE catalog_owner  NOLOGIN;
CREATE ROLE catalog_reader NOLOGIN;
CREATE ROLE catalog_writer NOLOGIN;

CREATE SCHEMA IF NOT EXISTS catalog AUTHORIZATION catalog_owner;

-- Tutto cio' che segue viene creato COME catalog_owner, non come superuser:
-- e' la riga che determina l'ownership di ogni tabella, vista e funzione.
SET ROLE catalog_owner;
SET search_path TO catalog, public;

-- ---------------------------------------------------------------------------
-- rel_path_safe — dominio dei percorsi RELATIVI.
--
-- Il path assoluto di una partizione nasce da una concatenazione:
--     storage_roots.abs_path / datasets.rel_root / partitions.rel_path
-- quindi un manifest malformato che contenga '..' potrebbe far uscire un
-- consumatore (DataGateway, Quant) dal proprio storage root. Il vincolo sta
-- qui, nel dominio, e non nelle singole colonne: non e' dimenticabile.
--
-- Ammesso: componenti di [A-Za-z0-9._=-] separati da singole '/'.
--   'raw/bybit/btcusdt/trades'   ok
--   'dt=2026-08-24'              ok  (il '=' serve alle chiavi di partizione)
-- Rifiutato: path assoluti, '..', '.', componenti vuoti ('//'), slash finale,
--            spazi e caratteri di controllo.
-- ---------------------------------------------------------------------------
CREATE DOMAIN rel_path_safe AS text
    CONSTRAINT well_formed  CHECK (VALUE ~ '^[A-Za-z0-9._=-]+(/[A-Za-z0-9._=-]+)*$')
    CONSTRAINT no_traversal CHECK (VALUE !~ '(^|/)\.\.?(/|$)');

-- ---------------------------------------------------------------------------
-- storage_roots — i punti di ancoraggio fisici. Aggiungerne uno (un terzo
-- disco, un NAS) non tocca nessun'altra tabella.
-- ---------------------------------------------------------------------------
CREATE TABLE storage_roots (
    storage_root_id text PRIMARY KEY,               -- 'hot', 'cold'
    tier            text        NOT NULL CHECK (tier IN ('hot','cold')),
    abs_path        text        NOT NULL UNIQUE,
    device_uuid     text,                           -- UUID del filesystem, per audit
    description     text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    -- l'unico path assoluto del catalogo: deve esserlo davvero, e senza '..'
    CONSTRAINT abs_path_is_absolute CHECK (abs_path ~ '^(/[A-Za-z0-9._-]+)+$'),
    CONSTRAINT abs_path_no_traversal CHECK (abs_path !~ '(^|/)\.\.?(/|$)')
);

INSERT INTO storage_roots (storage_root_id, tier, abs_path, device_uuid, description) VALUES
    ('hot',  'hot',  '/srv/marketdata',        '0b56b5ac-eb32-4e9a-a8b2-5aaba41f8676',
     'Samsung 870 EVO 500GB — dati caldi, write path del collector'),
    ('cold', 'cold', '/archive/marketdata-cold','4c63f36c-9885-40a6-89e0-a35a6271fc6f',
     'SanDisk SSD PLUS 480GB — partizioni invecchiate, read-mostly');

-- ---------------------------------------------------------------------------
-- schema_registry — quali JSON Schema erano in vigore, e con quale contenuto.
-- Sorgente: /opt/market-platform/schemas (git). Qui si registra l'hash cosi'
-- che un dataset possa provare contro quale versione e' stato validato.
-- ---------------------------------------------------------------------------
CREATE TABLE schema_registry (
    schema_id     text PRIMARY KEY,                 -- 'trade-v1', 'footprint-v1'
    name          text        NOT NULL,
    version       integer     NOT NULL CHECK (version > 0),
    json_sha256   char(64)    NOT NULL,
    git_commit    text,
    body          jsonb       NOT NULL,
    registered_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (name, version)
);

-- ---------------------------------------------------------------------------
-- feature_set_definitions — COSA misuro: la definizione pura, indipendente dai
-- dati su cui la applico. 'trade_microstructure-v1' esiste UNA volta, anche se
-- verra' materializzato su Bybit, Coinbase e Kraken.
-- La MATERIALIZZAZIONE e' un dataset con layer='features' che punta qui.
-- ---------------------------------------------------------------------------
CREATE TABLE feature_set_definitions (
    feature_set_def_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug               text        NOT NULL,        -- 'trade_microstructure'
    version            integer     NOT NULL CHECK (version > 0),

    -- params: SOLO i parametri che definiscono la misura stabile materializzata
    -- (es. l'ampiezza della finestra di aggregazione).
    --
    -- NON vanno qui i parametri di hypothesis, di event detection o di trade
    -- recipe: min_stack, absorption_threshold, trend_threshold e simili. Quelli
    -- appartengono alla strategia, non al dato, e materializzarli server-side
    -- e' esattamente il modo in cui il vecchio Quant ha confuso i due livelli.
    -- Il database non puo' farlo rispettare: e' un contratto, e va letto.
    params             jsonb       NOT NULL DEFAULT '{}'::jsonb,

    -- implementation identity: senza questo, fra un anno sapremmo QUALI
    -- parametri, ma non QUALE codice li ha materializzati. Obbligatorio.
    code_ref           text        NOT NULL CHECK (length(trim(code_ref)) > 0),

    schema_id          text        REFERENCES schema_registry(schema_id),
    description        text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (slug, version)
);

-- ---------------------------------------------------------------------------
-- datasets — un albero logico di dati. NON ha un path assoluto: ha un percorso
-- RELATIVO, valido sotto qualunque storage root. Sono le partizioni a sapere su
-- quale disco stanno fisicamente.
-- ---------------------------------------------------------------------------
CREATE TABLE datasets (
    dataset_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug               text     NOT NULL UNIQUE,    -- 'canonical.trades.coinbase.btc-usd'
    layer              text     NOT NULL CHECK (layer IN ('raw','canonical','features')),
    kind               text     NOT NULL,
    venue              text,
    symbol             text,
    rel_root           rel_path_safe NOT NULL UNIQUE, -- 'raw/coinbase/btc-usd/trades'
    schema_id          text     REFERENCES schema_registry(schema_id),
    feature_set_def_id uuid     REFERENCES feature_set_definitions ON DELETE RESTRICT,
    manifest_sha256    char(64) NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),

    -- raw e' venue-native: venue e symbol sono obbligatori li'
    CONSTRAINT raw_requires_venue
        CHECK (layer <> 'raw' OR (venue IS NOT NULL AND symbol IS NOT NULL)),

    -- ogni layer ammette solo i propri kind. 'footprint' e' il dato canonico
    -- ricostruito dai trade; 'footprint_microstructure' sono le misure derivate
    -- da quel footprint. Nomi distinti = nessuna ambiguita' semantica.
    CONSTRAINT kind_matches_layer CHECK (
        CASE layer
            WHEN 'raw'       THEN kind IN ('trades','l2')
            WHEN 'canonical' THEN kind IN ('trades','footprint','l2')
            WHEN 'features'  THEN kind IN ('trade_microstructure',
                                           'footprint_microstructure',
                                           'l2_microstructure')
        END
    ),

    -- un dataset di feature E' la materializzazione di una definizione, e solo
    -- un dataset di feature puo' esserlo
    CONSTRAINT features_need_definition
        CHECK ((layer = 'features') = (feature_set_def_id IS NOT NULL)),

    -- il rel_root deve stare nel ramo del proprio layer
    CONSTRAINT rel_root_matches_layer
        CHECK (rel_root LIKE layer || '/%')
);

CREATE INDEX ON datasets (layer, kind);
CREATE INDEX ON datasets (venue, symbol);
CREATE INDEX ON datasets (feature_set_def_id);

-- ---------------------------------------------------------------------------
-- partitions — l'unita' fisica, tipicamente un giorno, ed e' QUI che vive la
-- location. Spostare una partizione da hot a cold e' un UPDATE di
-- storage_root_id: il dataset non si accorge di nulla.
--
-- Path assoluto = storage_roots.abs_path / datasets.rel_root / partitions.rel_path
--
-- Lifecycle:
--   writing     il collector ci sta ancora scrivendo — NON leggibile
--   closed      chiusa, non ancora validata
--   valid       validata, pienamente utilizzabile
--   degraded    utilizzabile con riserva (gap noti, qualita' parziale)
--   invalid     da non usare, tenuta per diagnosi
--   superseded  rimpiazzata da una revisione successiva
-- ---------------------------------------------------------------------------
CREATE TABLE partitions (
    partition_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id      uuid        NOT NULL REFERENCES datasets ON DELETE CASCADE,
    partition_key   text        NOT NULL,           -- 'dt=2026-08-24'
    revision        integer     NOT NULL DEFAULT 1 CHECK (revision > 0),
    storage_root_id text        NOT NULL REFERENCES storage_roots ON DELETE RESTRICT,
    rel_path        rel_path_safe NOT NULL,         -- relativo a datasets.rel_root
    ts_start        timestamptz,
    ts_end          timestamptz,
    row_count       bigint      CHECK (row_count >= 0),
    byte_size       bigint      CHECK (byte_size >= 0),
    content_sha256  char(64),
    state           text        NOT NULL DEFAULT 'writing'
                                CHECK (state IN ('writing','closed','valid',
                                                 'degraded','invalid','superseded')),
    manifest_sha256 char(64),
    tiered_at       timestamptz,                    -- ultimo spostamento di tier
    created_at      timestamptz NOT NULL DEFAULT now(),

    UNIQUE (dataset_id, partition_key, revision),
    CONSTRAINT ts_ordered
        CHECK (ts_start IS NULL OR ts_end IS NULL OR ts_start <= ts_end),
    -- una partizione chiusa e' stata sigillata: hash e manifest sono obbligatori
    CONSTRAINT closed_is_hashed
        CHECK (state = 'writing'
               OR (content_sha256 IS NOT NULL AND manifest_sha256 IS NOT NULL))
);

-- al massimo una revisione viva per (dataset, partition_key): le altre sono
-- necessariamente superseded
CREATE UNIQUE INDEX partitions_one_live
    ON partitions (dataset_id, partition_key)
    WHERE state <> 'superseded';

CREATE INDEX ON partitions (dataset_id, ts_start);
CREATE INDEX ON partitions (storage_root_id);
CREATE INDEX ON partitions (state) WHERE state NOT IN ('valid','superseded');

-- ---------------------------------------------------------------------------
-- dataset_lineage — quale dataset deriva da quale, e per mano di quale codice.
-- Risponde a "se raw X e' sbagliato, cosa devo rigenerare".
-- ---------------------------------------------------------------------------
CREATE TABLE dataset_lineage (
    child_id    uuid NOT NULL REFERENCES datasets ON DELETE CASCADE,
    parent_id   uuid NOT NULL REFERENCES datasets ON DELETE RESTRICT,
    transform   text NOT NULL,          -- nome del job che l'ha prodotto
    -- implementation identity del passo di derivazione: sapere CHE canonical
    -- deriva da raw non basta, serve sapere QUALE codice l'ha derivato.
    code_ref    text NOT NULL CHECK (length(trim(code_ref)) > 0),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (child_id, parent_id, transform),
    CONSTRAINT no_self_lineage CHECK (child_id <> parent_id)
);

CREATE INDEX ON dataset_lineage (parent_id);

-- ---------------------------------------------------------------------------
-- artifacts — qualunque file prodotto con un manifest accanto che non sia una
-- partizione di dataset: export, report, modelli, fixture generate.
-- ---------------------------------------------------------------------------
CREATE TABLE artifacts (
    artifact_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind               text        NOT NULL,        -- 'export','model','report',...
    storage_root_id    text        NOT NULL REFERENCES storage_roots ON DELETE RESTRICT,
    rel_path           rel_path_safe NOT NULL,
    content_sha256     char(64)    NOT NULL,
    byte_size          bigint      CHECK (byte_size >= 0),
    produced_by        text,                        -- nome del job/servizio
    code_ref           text,
    dataset_id         uuid        REFERENCES datasets              ON DELETE SET NULL,
    feature_set_def_id uuid        REFERENCES feature_set_definitions ON DELETE SET NULL,
    manifest_sha256    char(64)    NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (storage_root_id, rel_path)
);

CREATE INDEX ON artifacts (kind, created_at DESC);

-- ---------------------------------------------------------------------------
-- quality_reports — esito dei controlli. Il bersaglio e' ESATTAMENTE UNO:
-- o una partizione (caso normale) o un dataset (controlli di continuita').
-- Ammettere entrambi permetterebbe di scrivere una partizione Bybit accanto a
-- un dataset Coinbase: una contraddizione silenziosa.
-- ---------------------------------------------------------------------------
CREATE TABLE quality_reports (
    report_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    partition_id uuid        REFERENCES partitions ON DELETE CASCADE,
    dataset_id   uuid        REFERENCES datasets   ON DELETE CASCADE,
    check_suite  text        NOT NULL,
    status       text        NOT NULL CHECK (status IN ('pass','warn','fail')),
    metrics      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    violations   jsonb       NOT NULL DEFAULT '[]'::jsonb,
    code_ref     text,
    ran_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT exactly_one_target
        CHECK ((partition_id IS NULL) <> (dataset_id IS NULL))
);

CREATE INDEX ON quality_reports (partition_id, ran_at DESC);
CREATE INDEX ON quality_reports (dataset_id, ran_at DESC);
CREATE INDEX ON quality_reports (status, ran_at DESC) WHERE status <> 'pass';

-- ---------------------------------------------------------------------------
-- rebuild_log — traccia di ogni ricostruzione del catalogo dai manifest.
-- E' cio' che rende verificabile la frase "Postgres non e' la source of truth".
-- ---------------------------------------------------------------------------
CREATE TABLE rebuild_log (
    rebuild_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    storage_root_id text        REFERENCES storage_roots ON DELETE SET NULL,
    manifests_seen  integer,
    rows_written    integer,
    discrepancies   jsonb       NOT NULL DEFAULT '[]'::jsonb,
    code_ref        text,
    CONSTRAINT finished_after_start
        CHECK (finished_at IS NULL OR finished_at >= started_at)
);

-- ---------------------------------------------------------------------------
-- v_partitions — ricompone il path assoluto dalle tre parti. E' l'unico posto
-- in cui il path completo esiste: nessuna tabella lo duplica.
-- ---------------------------------------------------------------------------
CREATE VIEW v_partitions AS
SELECT p.partition_id,
       d.slug        AS dataset_slug,
       d.layer, d.kind, d.venue, d.symbol,
       p.partition_key, p.revision, p.state,
       sr.tier,
       sr.abs_path || '/' || d.rel_root || '/' || p.rel_path AS abs_path,
       p.ts_start, p.ts_end, p.row_count, p.byte_size,
       p.content_sha256, p.tiered_at, p.created_at
FROM partitions p
JOIN datasets      d  ON d.dataset_id      = p.dataset_id
JOIN storage_roots sr ON sr.storage_root_id = p.storage_root_id;

-- ---------------------------------------------------------------------------
-- updated_at automatico su datasets
-- ---------------------------------------------------------------------------
CREATE FUNCTION touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER datasets_touch
    BEFORE UPDATE ON datasets
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- ---------------------------------------------------------------------------
-- Privilegi. Da qui in poi si torna al superuser: assegnare privilegi su
-- oggetti altrui e definire default privileges FOR ROLE non e' lavoro da owner.
-- ---------------------------------------------------------------------------
RESET ROLE;

GRANT USAGE ON SCHEMA catalog TO catalog_reader, catalog_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA catalog TO catalog_reader;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA catalog TO catalog_writer;

-- FOR ROLE catalog_owner e' il punto cruciale: i default privileges valgono
-- per gli oggetti creati DA QUEL RUOLO. Poiche' le migrazioni girano come
-- catalog_owner (002_roles.sh imposta 'role = catalog_owner' sull'admin), una
-- tabella aggiunta domani sara' automaticamente leggibile dal reader e
-- scrivibile dal writer, senza dover ricordarsi di fare GRANT a mano.
ALTER DEFAULT PRIVILEGES FOR ROLE catalog_owner IN SCHEMA catalog
    GRANT SELECT ON TABLES TO catalog_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE catalog_owner IN SCHEMA catalog
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO catalog_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE catalog_owner IN SCHEMA catalog
    GRANT USAGE, SELECT ON SEQUENCES TO catalog_writer;

-- lo schema public non serve a nessuno qui
REVOKE ALL ON SCHEMA public FROM PUBLIC;

COMMIT;
