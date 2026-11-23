-- 0003: the coordination mode of each client is controlled from the service (off | shadow | enforce).
ALTER TABLE clients ADD COLUMN mode text NOT NULL DEFAULT 'off' CHECK (mode IN ('off', 'shadow', 'enforce'));
ALTER TABLE clients ADD COLUMN mode_changed_at bigint;
