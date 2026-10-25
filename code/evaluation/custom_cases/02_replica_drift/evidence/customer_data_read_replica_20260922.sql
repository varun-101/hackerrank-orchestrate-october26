-- Ghost Data Still Exists: point-in-time database snapshot
-- Dialect: SQLite-compatible SQL
-- Captured at: 2026-09-22T09:00:00Z
-- Database topology: read_replica
-- Compare the primary and any replica tables for replication drift.

BEGIN TRANSACTION;
CREATE TABLE customer_data_primary (
            resource_id TEXT PRIMARY KEY,
            origin_system TEXT NOT NULL,
            collection_name TEXT NOT NULL,
            record_json TEXT NOT NULL CHECK (json_valid(record_json)),
            updated_at TEXT NOT NULL
        );
CREATE TABLE customer_data_read_replica (
            resource_id TEXT PRIMARY KEY,
            origin_system TEXT NOT NULL,
            collection_name TEXT NOT NULL,
            record_json TEXT NOT NULL CHECK (json_valid(record_json)),
            updated_at TEXT NOT NULL
        );
COMMIT;
INSERT INTO "customer_data_primary" VALUES('audit_record_92011','security_service','security_audit_events','{"resource_id":"audit_record_92011","subject_reference":"user_9201","event_name":"account_deletion_reviewed","actor_type":"automated_privacy_workflow","integrity_seal":"sha256:0000000000000000000000000000000000000000000000000000000000009201"}','2026-09-22T09:00:00Z');
INSERT INTO "customer_data_read_replica" VALUES('account_92011','account_service','customer_profiles','{"resource_id":"account_92011","customer_reference":"user_9201","full_name":"Tomasz Wrobelski","email":"tomasz.wrobelski@polmail.example","phone":"+44 7700 900201","account_status":"ACTIVE","signup_channel":"web"}','2026-09-22T09:00:00Z');
INSERT INTO "customer_data_read_replica" VALUES('audit_record_92011','security_service','security_audit_events','{"resource_id":"audit_record_92011","subject_reference":"user_9201","event_name":"account_deletion_reviewed","actor_type":"automated_privacy_workflow","integrity_seal":"sha256:0000000000000000000000000000000000000000000000000000000000009201"}','2026-09-22T09:00:00Z');
