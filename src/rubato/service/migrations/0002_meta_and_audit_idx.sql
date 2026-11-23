-- 0002: keep the planning metadata adapters send (e.g. {"ref_kind": "job"}) and speed up audit scans.
ALTER TABLE jobs ADD COLUMN meta jsonb NOT NULL DEFAULT '{}';
CREATE INDEX events_t_idx ON events (t);
CREATE INDEX decisions_t_idx ON decisions (t);
