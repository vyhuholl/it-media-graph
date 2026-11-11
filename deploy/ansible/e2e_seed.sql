-- Rows of every shape the restore neutralises, none of them real. An empty table
-- proves nothing. The people in the payloads are for the revision that clears them,
-- which the restore runs: the suite dumps this as the previous release left it.
INSERT INTO channels (tg_id, username, status, discovered_via, kind, kind_note, reject_reason, reject_note) VALUES
  (1, 'fake_seed_mine', 'seed', 'own_subscriptions', 'personal', 'a fake note', NULL, NULL),
  (2, 'fake_rejected_mine', 'rejected', 'own_subscriptions', NULL, NULL, 'not_it', 'a fake reason'),
  (3, 'fake_candidate_mine', 'candidate', 'own_subscriptions', NULL, NULL, NULL, NULL),
  (4, 'fake_seed_other', 'seed', 'manual', 'media', NULL, NULL, NULL),
  (5, 'fake_rejected_other', 'rejected', 'forward', NULL, NULL, 'not_it', 'a fake reason');
INSERT INTO yt_channels (channel_id, status, reject_reason, reject_note, kind_note, discovered_via)
  VALUES ('UCfake000000000000000000', 'rejected', 'not_it', 'a fake reason', 'a fake kind note', 'manual');
-- A seed of each language a YouTube topic takes, for the restore's trial of both.
INSERT INTO yt_channels (channel_id, status, kind, lang, discovered_via) VALUES
  ('UCfakeSeedEnglish0000000', 'seed', 'personal', 'en', 'manual'),
  ('UCfakeSeedRussian0000000', 'seed', 'personal', 'ru', 'manual');
INSERT INTO affiliation_candidates (channel_a, channel_b, score, decision_note) VALUES (4, 5, 0.5, 'a fake note on a pair');
INSERT INTO pending_mentions (username) VALUES ('fake_person');
INSERT INTO raw_messages (channel_id, msg_id, payload) VALUES
  (4, 10, '{"id": 10, "_": "Message", "date": "2026-11-05T09:00:00+00:00", "message": "a public post", "views": 123,
    "replies": {"replies": 4, "recent_repliers": [{"_": "PeerUser", "user_id": 777000111}]},
    "reactions": {"results": [], "recent_reactions": [{"peer_id": {"_": "PeerUser", "user_id": 777000111}}],
                  "top_reactors": [{"_": "MessageReactor", "count": 10, "peer_id": {"_": "PeerUser", "user_id": 777000111}},
                                   {"_": "MessageReactor", "count": 7, "peer_id": {"_": "PeerChannel", "channel_id": 4}}]},
    "post_author": "A Fake Author",
    "from_id": {"_": "PeerUser", "user_id": 777000111},
    "entities": [{"_": "MessageEntityMentionName", "offset": 0, "length": 1, "user_id": 777000111},
                 {"_": "MessageEntityUrl", "offset": 2, "length": 3}],
    "fwd_from": {"from_id": {"_": "PeerUser", "user_id": 777000111}, "from_name": "A Fake Sender", "post_author": "Another Fake Author"}}'),
  (4, 11, '{"id": 11, "_": "Message", "date": "2026-11-05T09:30:00+00:00", "message": "a forward", "views": 45,
    "from_id": {"_": "PeerChannel", "channel_id": 4},
    "fwd_from": {"from_id": {"_": "PeerChannel", "channel_id": 4}, "channel_post": 99}}');
INSERT INTO message_metrics (channel_id, msg_id, observed_at, views) VALUES (4, 10, now() - interval '1 day', 123);
-- Enough that a dump's end is real data, whose loss the restore has to notice.
INSERT INTO message_metrics (channel_id, msg_id, observed_at, views)
  SELECT 4, 10, now() - interval '2 days' - g * interval '1 second', (random() * 1000000)::int
  FROM generate_series(1, 5000) g;
INSERT INTO alerts (kind, channel_id, msg_id, band, value, raised_at) VALUES ('views_spike', 4, 10, 1, 4.2, now() - interval '1 hour');
-- YouTube's data: full dumps carry it, and a copy keeps it.
INSERT INTO yt_raw_channels (channel_id, payload, fetched_at) VALUES ('UCfake000000000000000000', '{"snippet": {"title": "a fake title"}}', now());
