-- Generated from LocalDb schema v58 (edge 0.11.3) via exportCopy().
-- Regenerate when the app's schemaVersion changes.

CREATE TABLE decoded_onehz (
        device_id TEXT NOT NULL DEFAULT '',
        ts_ms INTEGER NOT NULL DEFAULT 0,
        -- NOT re-asserted NOT NULL, deliberately: [_rekeyTableByDevice] copies
        -- this column verbatim out of a table where SQLite never enforced it
        -- (a declared PRIMARY KEY on a legacy rowid table does not imply NOT
        -- NULL), and a constraint failure inside onUpgrade's one exclusive
        -- transaction quarantines the whole database (invariant 11).
        rec_ts INTEGER,
        counter INTEGER NOT NULL,
        hr INTEGER,
        ax REAL,
        ay REAL,
        az REAL,
        spo2_red_raw INTEGER,
        spo2_ir_raw INTEGER,
        skin_temp_raw INTEGER,
        step_count INTEGER,
        step_cadence INTEGER,
        activity_class INTEGER,
        skin_temp_c REAL,
        on_wrist INTEGER,
        hr_valid INTEGER,
        hr_alt INTEGER,
        ambient_raw INTEGER,
        device_family TEXT,
        source TEXT,
        temp_ch2_c REAL,
        temp_ch3_c REAL,
        signal_quality_logvar REAL,
        dyn_accel_g REAL, ts_subsec INTEGER, band_sleep_state INTEGER,
        PRIMARY KEY (device_id, ts_ms)
      );

CREATE TABLE decoded_rr (
        device_id TEXT NOT NULL DEFAULT '',
        ts_ms INTEGER NOT NULL DEFAULT 0,
        rec_ts INTEGER NOT NULL,
        beat_index INTEGER NOT NULL,
        rr_ts_ms INTEGER NOT NULL,
        rr_ms INTEGER NOT NULL,
        device_family TEXT,
        source TEXT, beat_ts_ms INTEGER,
        PRIMARY KEY (device_id, ts_ms, beat_index)
      );

CREATE TABLE day_result (
        day_id TEXT NOT NULL,
        algo_version INTEGER NOT NULL,
        payload_json TEXT NOT NULL,
        window_json TEXT NOT NULL DEFAULT '{}',
        computed_at INTEGER NOT NULL,
        finalized INTEGER NOT NULL DEFAULT 0,
        rhr REAL,
        rmssd REAL,
        readiness REAL,
        skipped INTEGER NOT NULL DEFAULT 0,
        partial INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (day_id, algo_version)
      );

CREATE TABLE metric_series (
        date TEXT NOT NULL,
        key TEXT NOT NULL,
        value REAL,
        PRIMARY KEY (date, key)
      );
