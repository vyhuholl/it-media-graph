-- What a restored copy must not hold, one transaction: which channels came from
-- the operator's own subscriptions, what the operator wrote about any channel,
-- and what would act on the restored data.
--
-- Not what in a payload names a person: the schema's own revision removes that
-- (`clear people from the raw layer`), and a restore runs the migrations before
-- this file. A copy that still names someone at this point holds what production
-- holds, and readback.sql fails it rather than hide that.
--
-- Every value this file removes has a check in readback.sql, and the test suite
-- removes each statement here in turn and expects that read-back to fail.
BEGIN;

DELETE FROM channels WHERE discovered_via = 'own_subscriptions' AND status <> 'seed';
UPDATE channels SET discovered_via = 'manual' WHERE discovered_via = 'own_subscriptions' AND status = 'seed';
UPDATE channels SET kind_note = NULL WHERE kind_note IS NOT NULL;
UPDATE channels SET reject_note = NULL WHERE reject_note IS NOT NULL;
UPDATE yt_channels SET kind_note = NULL WHERE kind_note IS NOT NULL;
UPDATE yt_channels SET reject_note = NULL WHERE reject_note IS NOT NULL;
UPDATE affiliation_candidates SET decision_note = NULL WHERE decision_note IS NOT NULL;

-- Handles, many of them people's. Only `resolve` reads them, and it cannot run here.
DELETE FROM pending_mentions WHERE true;

-- Whatever was on its way out in production stays there.
UPDATE alerts SET delivered_at = now(), delivery = 'digest' WHERE delivered_at IS NULL;
UPDATE yt_alerts SET delivered_at = now(), delivery = 'digest' WHERE delivered_at IS NULL;

COMMIT;
