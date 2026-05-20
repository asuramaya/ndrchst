CREATE TABLE IF NOT EXISTS servers (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    platform_id         TEXT NOT NULL,
    family              TEXT NOT NULL CHECK (family IN ('java', 'bedrock')),
    version             TEXT NOT NULL,
    port                INTEGER NOT NULL,
    memory_mb           INTEGER NOT NULL,
    status              TEXT NOT NULL DEFAULT 'created',
    container_id        TEXT,
    cross_play          INTEGER NOT NULL DEFAULT 0,
    bedrock_bridge_port INTEGER,
    cf_project_id       INTEGER,
    cf_file_id          INTEGER,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Additive column migrations for older databases. SQLite raises if the column
-- already exists; we swallow that via the wrapper.

CREATE TABLE IF NOT EXISTS installed_assets (
    server_id     TEXT NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    source_id     TEXT NOT NULL,
    asset_id      TEXT NOT NULL,
    kind          TEXT NOT NULL,
    version       TEXT,
    installed_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (server_id, source_id, asset_id)
);

-- Wallet identities (Solana). The public surface upserts a row when a wallet
-- signs in / links a pilot; the admin surface reads these to push whitelist +
-- rank to game servers over RCON. Identity = wallet pubkey; mc_name is the
-- deterministic in-game name; tier is the holdings-based rank key.
CREATE TABLE IF NOT EXISTS wallet_links (
    wallet        TEXT PRIMARY KEY,
    mc_name       TEXT NOT NULL,
    tier          TEXT,
    holdings_pct  REAL NOT NULL DEFAULT 0,
    linked_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    synced_at     TEXT
);
