-- Reads back, on the real rows, what neutralise.sql removed and anything in a
-- payload that names a person, and fails on any that remains,
-- naming it. An empty answer is the whole point here, so a statement that did not
-- run must show as a number that is not zero.
--
-- People are looked for by type, anywhere in a payload, not at a list of paths: a
-- list of paths missed the paid reactors once. The types are those of the rule in
-- `tg/payload.py`, which the collector applies, and a test holds them to it.
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
    ['undelivered YouTube alerts',
     $q$SELECT count(*) FROM yt_alerts WHERE delivered_at IS NULL$q$],
    ['undelivered weekly lists',
     $q$SELECT count(*) FROM yt_weekly_lists WHERE delivered_at IS NULL$q$],
    ['undelivered weekly list entries',
     $q$SELECT count(*) FROM yt_weekly_entries WHERE delivered_at IS NULL$q$]
  ];
  -- What names a person, by type, in either kind of payload.
  people constant text[][] := ARRAY[
    ['users',
     $p$$.** ? (@."_" == "PeerUser" || @."_" == "InputPeerUser" || @."_" == "InputPeerUserFromMessage"
        || @."_" == "InputUser" || @."_" == "InputUserFromMessage" || @."_" == "UserEmpty"
        || (@."_" == "User" && !exists(@.bot ? (@ == true))))$p$],
    ['mentions by id',
     $p$$.** ? (@."_" == "MessageEntityMentionName" || @."_" == "InputMessageEntityMentionName")$p$],
    ['names',
     $p$$.** ? (@.post_author != null || @.from_name != null || @.saved_from_name != null)$p$],
    ['lists of user ids',
     $p$$.** ? ((@.winners.type() == "array" && exists(@.winners[0]))
        || (@.recent_requesters.type() == "array" && exists(@.recent_requesters[0])))$p$]
  ];
  payloads constant text[][] := ARRAY[['posts', 'raw_messages'], ['channel payloads', 'raw_channels']];
  counted bigint;
BEGIN
  FOR i IN 1 .. array_length(checks, 1) LOOP
    EXECUTE checks[i][2] INTO counted;
    IF counted > 0 THEN
      found := found || (checks[i][1] || ': ' || counted);
    END IF;
  END LOOP;

  -- One scan of a table per kind of person: jsonb_path_exists streams, one row at
  -- a time, which is what a 2 GiB database can afford.
  FOR t IN 1 .. array_length(payloads, 1) LOOP
    FOR i IN 1 .. array_length(people, 1) LOOP
      EXECUTE format('SELECT count(*) FROM %I WHERE jsonb_path_exists(payload, %L)', payloads[t][2], people[i][2])
        INTO counted;
      IF counted > 0 THEN
        found := found || (people[i][1] || ' in ' || payloads[t][1] || ': ' || counted);
      END IF;
    END LOOP;
  END LOOP;

  IF array_length(found, 1) > 0 THEN
    RAISE EXCEPTION 'a restored copy still holds %', array_to_string(found, '; ');
  END IF;
END
$readback$;
