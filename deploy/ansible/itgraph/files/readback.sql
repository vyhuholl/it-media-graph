-- Reads back, on the real rows, what neutralise.sql removed and what dumps leave
-- out, and fails on any that remains, naming it. An empty answer is the whole point
-- here, so a statement that did not run must show as a number that is not zero.
DO $readback$
DECLARE
  found text[] := '{}';
  checks constant text[][] := ARRAY[
    ['channels from the operator''s subscriptions',
     $q$SELECT count(*) FROM channels WHERE discovered_via = 'own_subscriptions'$q$],
    ['notes on channels',
     $q$SELECT count(*) FROM channels WHERE kind_note IS NOT NULL OR reject_note IS NOT NULL$q$],
    ['notes on YouTube channels',
     $q$SELECT count(*) FROM yt_channels WHERE kind_note IS NOT NULL OR reject_note IS NOT NULL$q$],
    ['notes on affiliation decisions',
     $q$SELECT count(*) FROM affiliation_candidates WHERE decision_note IS NOT NULL$q$],
    ['pending mentions',
     $q$SELECT count(*) FROM pending_mentions$q$],
    ['undelivered alerts',
     $q$SELECT count(*) FROM alerts WHERE delivered_at IS NULL$q$],
    ['recent repliers',
     $q$SELECT count(*) FROM raw_messages WHERE jsonb_typeof(payload->'replies'->'recent_repliers') = 'array'$q$],
    ['recent reactions',
     $q$SELECT count(*) FROM raw_messages WHERE jsonb_typeof(payload->'reactions'->'recent_reactions') = 'array'$q$],
    ['post signatures',
     $q$SELECT count(*) FROM raw_messages WHERE payload->>'post_author' IS NOT NULL$q$],
    ['forwarded signatures',
     $q$SELECT count(*) FROM raw_messages WHERE payload->'fwd_from'->>'post_author' IS NOT NULL$q$],
    ['hidden senders'' names',
     $q$SELECT count(*) FROM raw_messages WHERE payload->'fwd_from'->>'from_name' IS NOT NULL$q$],
    ['authors who are users',
     $q$SELECT count(*) FROM raw_messages WHERE payload->'from_id'->>'_' = 'PeerUser'$q$],
    ['forwards from users',
     $q$SELECT count(*) FROM raw_messages WHERE payload->'fwd_from'->'from_id'->>'_' = 'PeerUser'$q$],
    ['mentions by user id',
     $q$SELECT count(*) FROM raw_messages WHERE payload->'entities' @> '[{"_": "MessageEntityMentionName"}]'
          OR payload->'entities' @> '[{"_": "InputMessageEntityMentionName"}]'$q$],
    ['people among a channel''s users',
     $q$SELECT count(*) FROM raw_channels, jsonb_array_elements(
          CASE WHEN jsonb_typeof(payload->'users') = 'array' THEN payload->'users' ELSE '[]'::jsonb END) person
        WHERE (person->>'bot')::boolean IS NOT TRUE$q$],
    ['requesters of a channel',
     $q$SELECT count(*) FROM raw_channels WHERE jsonb_typeof(payload->'full_chat'->'recent_requesters') = 'array'
          AND jsonb_array_length(payload->'full_chat'->'recent_requesters') > 0$q$]
  ];
  outside text;
  counted bigint;
BEGIN
  FOR i IN 1 .. array_length(checks, 1) LOOP
    EXECUTE checks[i][2] INTO counted;
    IF counted > 0 THEN
      found := found || (checks[i][1] || ': ' || counted);
    END IF;
  END LOOP;

  -- What dumps leave out holds no row in a copy. By name, so that a table added
  -- to the glob later is covered, and a test holds these to db/backup.py.
  FOR outside IN
    SELECT table_name FROM information_schema.tables
     WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
       AND (table_name LIKE 'yt_raw_%' OR table_name IN ('yt_backfill_walks', 'yt_search_runs'))
  LOOP
    EXECUTE format('SELECT count(*) FROM %I', outside) INTO counted;
    IF counted > 0 THEN
      found := found || (outside || ': ' || counted);
    END IF;
  END LOOP;

  IF array_length(found, 1) > 0 THEN
    RAISE EXCEPTION 'a restored copy still holds %', array_to_string(found, '; ');
  END IF;
END
$readback$;
