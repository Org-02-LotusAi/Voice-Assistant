-- Phase 2 / P2 fix round 1: private template ids are namespaced per owner as `<owner_username>.<slug>`.
-- Existing private families WITHOUT bound identities are renamed (their immutable-version triggers are lifted for the move
-- and restored). A private family that identities are already bound to keeps its old id: renaming would break the identity
-- foreign keys and change what the identity was resolved from. Such ids stay valid and visible only to their owner.
CREATE TEMP TABLE _rename AS
SELECT t.template_id AS old_id, u.username || '.' || t.template_id AS new_id
FROM templates t JOIN users u ON u.user_id = t.owner_user_id
WHERE t.shared = 0 AND instr(t.template_id, '.') = 0
  AND NOT EXISTS (SELECT 1 FROM identities i WHERE i.template_id = t.template_id);

DROP TRIGGER template_versions_no_update;
DROP TRIGGER template_versions_no_delete;

INSERT INTO templates (template_id, created_at, owner_user_id, shared, disabled)
SELECT r.new_id, t.created_at, t.owner_user_id, t.shared, t.disabled FROM templates t JOIN _rename r ON r.old_id = t.template_id;
INSERT INTO template_versions (template_id, version, body_json, created_at)
SELECT r.new_id, v.version, json_set(v.body_json, '$.template_id', r.new_id), v.created_at
FROM template_versions v JOIN _rename r ON r.old_id = v.template_id;
DELETE FROM template_versions WHERE template_id IN (SELECT old_id FROM _rename);
DELETE FROM templates WHERE template_id IN (SELECT old_id FROM _rename);

CREATE TRIGGER template_versions_no_update BEFORE UPDATE ON template_versions
BEGIN
    SELECT RAISE(ABORT, 'template_versions rows are immutable');
END;
CREATE TRIGGER template_versions_no_delete BEFORE DELETE ON template_versions
BEGIN
    SELECT RAISE(ABORT, 'template_versions rows are immutable');
END;
DROP TABLE _rename;
