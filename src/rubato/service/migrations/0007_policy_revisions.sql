CREATE TABLE policy_drafts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    base_revision bigint NOT NULL,
    body jsonb NOT NULL,
    created_at bigint NOT NULL,
    created_by text NOT NULL
);
ALTER TABLE config_versions ADD COLUMN based_on bigint;
ALTER TABLE config_versions ADD COLUMN source_draft bigint;
ALTER TABLE config_versions ADD COLUMN restored_from bigint;
ALTER TABLE config_versions ADD COLUMN reason text;
