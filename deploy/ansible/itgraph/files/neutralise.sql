-- What a restored copy must not hold, one transaction: which channels came from
-- the operator's own subscriptions, what the operator wrote about any channel,
-- what would act on the restored data, and what in a payload names a person.
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

UPDATE raw_messages SET payload = payload #- '{replies,recent_repliers}'
  WHERE jsonb_typeof(payload->'replies'->'recent_repliers') = 'array';
UPDATE raw_messages SET payload = payload #- '{reactions,recent_reactions}'
  WHERE jsonb_typeof(payload->'reactions'->'recent_reactions') = 'array';
UPDATE raw_messages SET payload = payload - 'post_author'
  WHERE payload->>'post_author' IS NOT NULL;
UPDATE raw_messages SET payload = payload #- '{fwd_from,post_author}'
  WHERE payload->'fwd_from'->>'post_author' IS NOT NULL;
UPDATE raw_messages SET payload = payload #- '{fwd_from,from_name}'
  WHERE payload->'fwd_from'->>'from_name' IS NOT NULL;
UPDATE raw_messages SET payload = jsonb_set(payload, '{from_id}', 'null')
  WHERE payload->'from_id'->>'_' = 'PeerUser';
UPDATE raw_messages SET payload = jsonb_set(payload, '{fwd_from,from_id}', 'null')
  WHERE payload->'fwd_from'->'from_id'->>'_' = 'PeerUser';
UPDATE raw_messages SET payload = jsonb_set(
    payload,
    '{entities}',
    (SELECT coalesce(jsonb_agg(entity), '[]'::jsonb)
       FROM jsonb_array_elements(payload->'entities') entity
      WHERE entity->>'_' NOT IN ('MessageEntityMentionName', 'InputMessageEntityMentionName'))
  )
  WHERE jsonb_typeof(payload->'entities') = 'array'
    AND (payload->'entities' @> '[{"_": "MessageEntityMentionName"}]'
      OR payload->'entities' @> '[{"_": "InputMessageEntityMentionName"}]');

COMMIT;
