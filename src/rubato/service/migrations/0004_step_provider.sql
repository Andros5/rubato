-- 0004: each step may name its provider, so resources scoped by provider (and by provider + client) apply to it.
ALTER TABLE steps ADD COLUMN provider text;
