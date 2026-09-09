-- seed_schema.sql - the metadata catalog for the extract engine.
--
-- RECONSTRUCTED SCHEMA. docs/extract-engine-mvp-prompt-v2-polars.md states
-- that "the metadata model, the three dataset modes, and the acceptance demo
-- carry over" from an earlier MVP prompt. That earlier prompt does not exist
-- anywhere in this repository. Everything below beyond the two explicit v2
-- additions (meta.feed.default_execution_mode; meta.run_log.execution_mode/
-- duration_ms/peak_rss_bytes) is reconstructed from context clues in v2 itself
-- (the "three data files" / dependent-mode / no-orphaned-references language)
-- and should be reviewed against real customer schema shape before production
-- use. See references/dataset-modes-and-metadata-schema.md.
--
-- Creates three NEW schemas only: src (synthetic source data for dev/test),
-- meta (this catalog), gen (execution-path-B generated views). Never touches
-- an existing schema or table. Run this against a database you control, never
-- against a production database that already has objects in it.
--
-- Run once per database:
--   sqlcmd -S (localdb)\MSSQLLocalDB -d ExtractEngineDev -E -i seed_schema.sql

IF SCHEMA_ID('src') IS NULL EXEC('CREATE SCHEMA src');
GO
IF SCHEMA_ID('meta') IS NULL EXEC('CREATE SCHEMA meta');
GO
IF SCHEMA_ID('gen') IS NULL EXEC('CREATE SCHEMA gen');
GO

-- ---------------------------------------------------------------------------
-- meta.feed - one row per named extract job.
-- ---------------------------------------------------------------------------
IF OBJECT_ID('meta.feed') IS NULL
CREATE TABLE meta.feed (
    feed_id                 INT IDENTITY(1,1) PRIMARY KEY,
    feed_name                VARCHAR(128)  NOT NULL UNIQUE,
    source_server             VARCHAR(256)  NOT NULL,
    source_database            VARCHAR(128)  NOT NULL,
    delimiter                  CHAR(1)       NOT NULL DEFAULT '|',
    collision_action            VARCHAR(10)   NOT NULL DEFAULT 'sanitize'
                                     CONSTRAINT ck_feed_collision_action
                                     CHECK (collision_action IN ('sanitize', 'fail')),
    collision_char                CHAR(1)       NOT NULL DEFAULT ' ',
    line_ending                     VARCHAR(4)    NOT NULL DEFAULT 'CRLF'
                                     CONSTRAINT ck_feed_line_ending
                                     CHECK (line_ending IN ('CRLF', 'LF')),
    null_sentinel                    VARCHAR(16)   NOT NULL DEFAULT '',
    max_rows_per_file                 BIGINT        NOT NULL DEFAULT 1000000,
    emit_header_row                    BIT           NOT NULL DEFAULT 0,
    emit_trailer_row                    BIT           NOT NULL DEFAULT 1,
    emit_concat_ws_line                  BIT           NOT NULL DEFAULT 0,
    -- v2 doc, explicit addition:
    default_execution_mode                VARCHAR(10)   NOT NULL DEFAULT 'polars'
                                     CONSTRAINT ck_feed_execution_mode
                                     CHECK (default_execution_mode IN ('polars', 'sql')),
    output_root                             VARCHAR(512)  NOT NULL,
    anchor_date_default                       DATE          NULL,
    window_years_default                       INT           NOT NULL DEFAULT 2,
    is_active                                   BIT           NOT NULL DEFAULT 1,
    created_at                                   DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at                                    DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT ck_feed_collision_char_ne_delim CHECK (collision_char <> delimiter)
);
GO

-- ---------------------------------------------------------------------------
-- meta.dataset - one row per dataset within a feed. dataset_mode is the
-- reconstructed three-mode design: primary | dependent | reference.
-- ---------------------------------------------------------------------------
IF OBJECT_ID('meta.dataset') IS NULL
CREATE TABLE meta.dataset (
    dataset_id                  INT IDENTITY(1,1) PRIMARY KEY,
    feed_id                       INT NOT NULL REFERENCES meta.feed(feed_id),
    dataset_name                   VARCHAR(128) NOT NULL,
    source_schema                    VARCHAR(128) NOT NULL,
    source_table                       VARCHAR(128) NOT NULL,
    dataset_mode                        VARCHAR(20)  NOT NULL
                                     CONSTRAINT ck_dataset_mode
                                     CHECK (dataset_mode IN ('primary', 'dependent', 'reference')),
    primary_key_columns                   VARCHAR(256) NOT NULL,  -- comma-separated source column names
    -- required (loader-enforced, not a CHECK) when dataset_mode = 'primary'
    window_date_column                      VARCHAR(128) NULL,
    -- required (loader-enforced) when dataset_mode = 'dependent'
    depends_on_dataset_id                     INT NULL REFERENCES meta.dataset(dataset_id),
    dependency_source_column                    VARCHAR(128) NULL,  -- FK column on the depends-on dataset's rows
    dependency_target_column                     VARCHAR(128) NULL, -- matching column on this dataset's own table
    include_own_window                             BIT NOT NULL DEFAULT 0,
    output_file_stem                                 VARCHAR(128) NOT NULL,
    run_ordinal                                        INT NOT NULL,
    is_active                                            BIT NOT NULL DEFAULT 1,
    CONSTRAINT uq_dataset_name UNIQUE (feed_id, dataset_name)
);
GO

-- ---------------------------------------------------------------------------
-- meta.dataset_lookup - "calculator table" join definitions.
-- ---------------------------------------------------------------------------
IF OBJECT_ID('meta.dataset_lookup') IS NULL
CREATE TABLE meta.dataset_lookup (
    dataset_lookup_id     INT IDENTITY(1,1) PRIMARY KEY,
    dataset_id              INT NOT NULL REFERENCES meta.dataset(dataset_id),
    lookup_alias              VARCHAR(64)  NOT NULL,
    lookup_schema                VARCHAR(128) NOT NULL,
    lookup_table                    VARCHAR(128) NOT NULL,
    join_source_column                 VARCHAR(128) NOT NULL,
    join_lookup_column                    VARCHAR(128) NOT NULL,
    join_type                                VARCHAR(10)  NOT NULL DEFAULT 'left'
                                     CONSTRAINT ck_lookup_join_type
                                     CHECK (join_type IN ('left', 'inner')),
    CONSTRAINT uq_lookup_alias UNIQUE (dataset_id, lookup_alias)
);
GO

-- ---------------------------------------------------------------------------
-- meta.field_map - one row per output column. rule_name must exist in the
-- Python rule registry (extract_engine.rules) - it is never eval'd from here.
-- ---------------------------------------------------------------------------
IF OBJECT_ID('meta.field_map') IS NULL
CREATE TABLE meta.field_map (
    field_map_id       INT IDENTITY(1,1) PRIMARY KEY,
    dataset_id            INT NOT NULL REFERENCES meta.dataset(dataset_id),
    ordinal                 INT NOT NULL,
    target_column             VARCHAR(128) NOT NULL,
    -- bare source column, or 'lookup:<alias>.<column>'
    source_expression           VARCHAR(256) NOT NULL,
    -- neutral token: varchar(n) | decimal(p,s) | int | bigint | date | datetime | bit
    data_type                     VARCHAR(30)  NOT NULL,
    rule_name                       VARCHAR(64)  NOT NULL DEFAULT 'passthrough',
    rule_params                        VARCHAR(MAX) NULL,  -- JSON object
    nullable                             BIT NOT NULL DEFAULT 1,
    default_value                          VARCHAR(256) NULL,
    is_active                                BIT NOT NULL DEFAULT 1,
    CONSTRAINT uq_field_ordinal UNIQUE (dataset_id, ordinal),
    CONSTRAINT uq_field_target UNIQUE (dataset_id, target_column)
);
GO

-- ---------------------------------------------------------------------------
-- meta.feed_config_version - Excel-load audit trail. Every run stamps its
-- feed_config_version_id, so output traces to an exact workbook version.
-- ---------------------------------------------------------------------------
IF OBJECT_ID('meta.feed_config_version') IS NULL
CREATE TABLE meta.feed_config_version (
    feed_config_version_id   INT IDENTITY(1,1) PRIMARY KEY,
    feed_id                    INT NOT NULL REFERENCES meta.feed(feed_id),
    workbook_filename            VARCHAR(260) NOT NULL,   -- filename only, never a full customer path
    workbook_sha256                 CHAR(64) NOT NULL,
    loaded_at                          DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
    loaded_by                            VARCHAR(128) NOT NULL,  -- SUSER_SNAME(), never a customer identity
    -- shape only: row counts per sheet, rule names referenced. Never values.
    sheet_summary_json                     VARCHAR(MAX) NOT NULL,
    is_current                               BIT NOT NULL DEFAULT 1,
    CONSTRAINT uq_config_hash UNIQUE (feed_id, workbook_sha256)
);
GO

-- ---------------------------------------------------------------------------
-- meta.run_log - one row per run.
-- ---------------------------------------------------------------------------
IF OBJECT_ID('meta.run_log') IS NULL
CREATE TABLE meta.run_log (
    run_id                     INT IDENTITY(1,1) PRIMARY KEY,
    feed_id                      INT NOT NULL REFERENCES meta.feed(feed_id),
    feed_config_version_id         INT NOT NULL REFERENCES meta.feed_config_version(feed_config_version_id),
    anchor_date                       DATE NOT NULL,
    window_years                        INT NOT NULL,
    -- v2 doc, explicit additions:
    execution_mode                        VARCHAR(10) NOT NULL
                                     CONSTRAINT ck_run_execution_mode
                                     CHECK (execution_mode IN ('polars', 'sql')),
    duration_ms                              BIGINT NULL,
    peak_rss_bytes                             BIGINT NULL,
    started_at                                   DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
    completed_at                                   DATETIME2(3) NULL,
    status                                           VARCHAR(16) NOT NULL DEFAULT 'running'
                                     CONSTRAINT ck_run_status
                                     CHECK (status IN ('running', 'completed', 'failed', 'aborted')),
    requested_by                                       VARCHAR(128) NOT NULL,  -- SUSER_SNAME()
    error_message                                        VARCHAR(2000) NULL     -- never row/key/parameter values
);
GO

-- ---------------------------------------------------------------------------
-- meta.run_detail - what --resume reads to skip completed datasets.
-- ---------------------------------------------------------------------------
IF OBJECT_ID('meta.run_detail') IS NULL
CREATE TABLE meta.run_detail (
    run_detail_id       INT IDENTITY(1,1) PRIMARY KEY,
    run_id                INT NOT NULL REFERENCES meta.run_log(run_id),
    dataset_id              INT NOT NULL REFERENCES meta.dataset(dataset_id),
    status                    VARCHAR(16) NOT NULL DEFAULT 'pending'
                                     CONSTRAINT ck_run_detail_status
                                     CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    row_count                    BIGINT NULL,
    part_count                     INT NULL,
    checksum                         CHAR(64) NULL,   -- SHA-256 over the concatenated file parts
    started_at                         DATETIME2(3) NULL,
    completed_at                         DATETIME2(3) NULL,
    CONSTRAINT uq_run_dataset UNIQUE (run_id, dataset_id)
);
GO

-- ---------------------------------------------------------------------------
-- meta.run_dataset_key - staged key set implementing dependent-mode pull-in.
-- After a primary dataset finishes, its FK-bearing column values are staged
-- here; a dependent dataset's query filters against this set.
-- ---------------------------------------------------------------------------
IF OBJECT_ID('meta.run_dataset_key') IS NULL
CREATE TABLE meta.run_dataset_key (
    run_id       INT NOT NULL REFERENCES meta.run_log(run_id),
    dataset_id     INT NOT NULL REFERENCES meta.dataset(dataset_id),  -- the PRIMARY dataset whose keys are staged
    key_value        VARCHAR(256) NOT NULL,
    CONSTRAINT pk_run_dataset_key PRIMARY KEY (run_id, dataset_id, key_value)
);
GO

PRINT 'extract-engine meta schema ready.';
GO
