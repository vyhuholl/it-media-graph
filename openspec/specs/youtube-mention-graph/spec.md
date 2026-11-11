# youtube-mention-graph Specification

## Purpose
What a seed's YouTube videos reference — other channels and other videos — read from the stored payloads on demand, without the network and without storing anything, so that the graph and the queue of channels to review always reflect the payloads they come from.

## Requirements

### Requirement: Channel Links

The system SHALL take a link of the form `youtube.com/@handle`, `youtube.com/channel/<id>` or `youtube.com/user/<name>` in a video's title or description as a reference to that channel. The host may carry `www.`, `m.` or `music.`, the scheme may be absent, and any further path or query SHALL be ignored.

#### Scenario: A handle link with a trailing path and a query

- **GIVEN** a description containing `https://www.youtube.com/@fake-seed/videos?sub_confirmation=1`
- **WHEN** its references are read
- **THEN** it references the handle `fake-seed`

#### Scenario: A channel id link on another host

- **GIVEN** a description containing `music.youtube.com/channel/UCfakeChannel0000000000a`
- **WHEN** its references are read
- **THEN** it references that channel id

#### Scenario: A legacy name link

- **GIVEN** a description containing `youtube.com/user/FakeLegacy`
- **WHEN** its references are read
- **THEN** it references the legacy name `fakelegacy`

#### Scenario: A link ending a sentence

- **GIVEN** a description containing `see youtube.com/@fake.seed.two.`
- **WHEN** its references are read
- **THEN** it references the handle `fake.seed.two`

#### Scenario: A percent-encoded handle

- **GIVEN** a handle link whose handle is written percent-encoded
- **WHEN** its references are read
- **THEN** it references the decoded handle

### Requirement: Handle Grammar

The system SHALL read a handle as YouTube defines one: letters and digits of any script, with `_`, `-`, `.` and `·` allowed inside it but not first or last, 3 to 30 characters, compared without regard to letter case and after Unicode normalization. Separators at the end are punctuation after the handle. A handle link whose handle still breaks this SHALL reference nothing.

#### Scenario: Letter case does not matter

- **GIVEN** a link to `@Fake_Seed` and a channel whose handle is `@fake_seed`
- **WHEN** references are read
- **THEN** the link references that channel

#### Scenario: A separator cannot end a handle

- **GIVEN** a description containing `youtube.com/@fake-seed-`
- **WHEN** its references are read
- **THEN** it references the handle `fake-seed`

#### Scenario: A handle too short to exist

- **GIVEN** a description containing `youtube.com/@ab`
- **WHEN** its references are read
- **THEN** it references nothing

#### Scenario: A handle in another script

- **GIVEN** a handle link whose handle is written in Cyrillic letters
- **WHEN** its references are read
- **THEN** it references that handle

### Requirement: Video Links

The system SHALL take a link of the form `youtube.com/watch?v=<id>`, `youtu.be/<id>`, `youtube.com/shorts/<id>`, `youtube.com/live/<id>`, `youtube.com/embed/<id>` or `youtube.com/v/<id>` — the last two on `youtube-nocookie.com` too — as a reference to that video. A playlist, a start time or a share token in the link SHALL be ignored.

#### Scenario: A video inside a playlist

- **GIVEN** a description containing `youtube.com/watch?v=fakevideo02&list=PLfakefake&t=30`
- **WHEN** its references are read
- **THEN** it references the video `fakevideo02` and nothing else

#### Scenario: A short link with a share token

- **GIVEN** a description containing `youtu.be/fakevideo02?si=fake-share-token`
- **WHEN** its references are read
- **THEN** it references the video `fakevideo02`

#### Scenario: Every video form

- **GIVEN** a description linking one video in each of the forms
- **WHEN** its references are read
- **THEN** each link references that video

### Requirement: Bare Mentions Name Only Known Handles

The system SHALL take an `@handle` written in a video's title or description as a reference only when the handle is one a channel in the inventory carries in its stored data. An `@` directly after a letter, a digit or one of `_ . % + - / @`, or a handle followed by `/`, SHALL not be read as a mention.

#### Scenario: A mention of a known channel

- **GIVEN** a channel in the inventory whose stored handle is `@fake.seed.two`
- **AND** a seed's video title `Guest @fake.seed.two on caching`
- **WHEN** references are read
- **THEN** the video references that channel

#### Scenario: A mention of an unknown handle

- **GIVEN** a description mentioning `@fake_twitter_name`, which no channel in the inventory carries
- **WHEN** references are read
- **THEN** it references nothing
- **AND** the mention is counted as not followed

#### Scenario: An email address is no mention

- **GIVEN** a description containing `fake@fake-seed.example`
- **AND** a channel in the inventory whose handle is `@fake-seed`
- **WHEN** references are read
- **THEN** it references nothing

#### Scenario: A package scope is no mention

- **GIVEN** a description containing `@fakescope/fake-package`
- **AND** a channel in the inventory whose handle is `@fakescope`
- **WHEN** references are read
- **THEN** it references nothing

### Requirement: Forms Counted, Never Followed

The system SHALL count, and never turn into a reference, a custom URL (`youtube.com/c/<name>` or `youtube.com/<name>`), a playlist page, a link through a link shortener, a link to Telegram, and a bare mention of a handle the inventory does not carry. A Telegram link SHALL be counted by number only, its name never shown.

#### Scenario: A custom URL

- **GIVEN** a description containing `youtube.com/c/FakeCustom`
- **WHEN** references are read
- **THEN** it references nothing
- **AND** one custom URL is counted

#### Scenario: A shortened link

- **GIVEN** a description containing `bit.ly/fake-short`
- **WHEN** references are read
- **THEN** it references nothing
- **AND** one shortened link is counted

#### Scenario: A Telegram link

- **GIVEN** a description containing `t.me/fake_tg_channel`
- **WHEN** references are read
- **THEN** it references nothing
- **AND** one Telegram link is counted, and the name `fake_tg_channel` appears nowhere in what is reported

### Requirement: Sources Are Seeds' Videos

The system SHALL read references only from the titles and descriptions of stored videos whose channel has status `seed`, whichever command stored them, and from the stored featured lists of seeds. Videos and featured lists of channels with any other status, and channel descriptions, SHALL NOT be read as sources.

#### Scenario: Videos found by the poll loop and the walk

- **GIVEN** a seed with one video found by the poll loop and one found by the walk
- **WHEN** references are read
- **THEN** both videos are read

#### Scenario: Channels out of scope are not sources

- **GIVEN** stored videos of a channel with status `maybe`, one `rejected` and one `candidate`, each linking another channel
- **WHEN** references are read
- **THEN** none of those links is a reference

#### Scenario: A channel description is not a source

- **GIVEN** a seed whose stored channel description links another channel
- **WHEN** references are read
- **THEN** that link is not a reference

#### Scenario: A seed's featured list is a source

- **GIVEN** a seed whose stored featured list names another channel
- **WHEN** references are read
- **THEN** that channel is referenced by the seed

#### Scenario: A former seed's featured list is not a source

- **GIVEN** a channel whose featured list was stored while it was a seed, and which is now `rejected`
- **WHEN** references are read
- **THEN** its list is not read

### Requirement: Known Targets Become Edges

The system SHALL turn a reference into an edge when its target is known without the network: a channel id in the inventory, a handle a channel in the inventory carries in its stored data, or a video whose data is stored. An edge SHALL name the referencing channel, the referencing video where there is one, the target channel, the target video where there is one, and the form the reference took.

#### Scenario: A link to another seed

- **GIVEN** a seed's video linking `youtube.com/@fake-seed-two`, the handle of another seed
- **WHEN** references are read
- **THEN** an edge leads from the video's channel to that seed

#### Scenario: A link to a stored video

- **GIVEN** a seed's video linking a video of another channel whose data is stored
- **WHEN** references are read
- **THEN** an edge leads to that video and its channel

#### Scenario: A rejected channel is a target

- **GIVEN** a seed's video linking a channel the operator rejected
- **WHEN** references are read
- **THEN** the edge is recorded

#### Scenario: A channel known by id alone

- **GIVEN** a seed's video linking by id a channel in the inventory whose stored data is gone
- **WHEN** references are read
- **THEN** the edge is recorded

#### Scenario: An edge from a featured list names no video

- **GIVEN** a seed whose featured list names a channel in the inventory
- **WHEN** references are read
- **THEN** the edge names the seed and the target channel, and no referencing or target video

### Requirement: Unknown Targets Are Pending

The system SHALL hold a reference whose target is not known without the network — a handle link, a legacy name, a channel id or a video id — as a pending target, together with the set of distinct seeds whose videos or featured lists make it. It MUST NOT create an inventory record or make a request for one.

#### Scenario: An unknown handle link

- **GIVEN** a seed's video linking `youtube.com/@fake-unknown`, which no channel in the inventory carries
- **WHEN** references are read
- **THEN** `fake-unknown` is a pending handle, with that seed as its source
- **AND** no channel record is created

#### Scenario: Two seeds are two sources

- **GIVEN** two seeds each linking the same unknown channel id
- **WHEN** references are read
- **THEN** that id is pending with two sources

#### Scenario: One seed linking repeatedly is one source

- **GIVEN** one seed linking the same unknown video in five of its videos
- **WHEN** references are read
- **THEN** that video is pending with one source

#### Scenario: A legacy name is pending

- **GIVEN** a seed's video linking `youtube.com/user/fakelegacy`
- **WHEN** references are read
- **THEN** `fakelegacy` is a pending legacy name

#### Scenario: A featured id and a linked id are one target

- **GIVEN** one seed featuring an unknown channel id and another seed's video linking the same id
- **WHEN** references are read
- **THEN** that id is pending with two sources

### Requirement: One Reference Per Target Per Video

The system SHALL record one reference per referencing video and target — the target channel together with the target video, or with none. A channel both linked and mentioned in one video SHALL be recorded once, as a link.

#### Scenario: A channel linked and mentioned

- **GIVEN** a video that links `youtube.com/@fake-seed-two` and mentions `@fake-seed-two`
- **WHEN** references are read
- **THEN** one reference to that channel is recorded, as a link

#### Scenario: A channel and one of its videos

- **GIVEN** a video that links another seed's channel and one of that seed's stored videos
- **WHEN** references are read
- **THEN** two references are recorded: one to the channel, one to the video

#### Scenario: The same video linked twice

- **GIVEN** a video that links one other video by `youtu.be` and by `watch?v=`
- **WHEN** references are read
- **THEN** one reference to that video is recorded

### Requirement: No Self-References

The system SHALL NOT record a reference from a video to its own channel or to another video of its own channel, however it is written.

#### Scenario: A subscribe link to the own channel

- **GIVEN** a seed's video linking its own channel by id with `?sub_confirmation=1`
- **WHEN** references are read
- **THEN** no reference is recorded for it

#### Scenario: The own handle

- **GIVEN** a seed's video mentioning the seed's own handle
- **WHEN** references are read
- **THEN** no reference is recorded for it

#### Scenario: A link to an earlier episode

- **GIVEN** a seed's video linking another stored video of the same seed
- **WHEN** references are read
- **THEN** no reference is recorded for it

#### Scenario: A self-reference does not hide the others

- **GIVEN** a video linking its own channel and another seed
- **WHEN** references are read
- **THEN** the reference to the other seed is recorded

### Requirement: An Ambiguous Handle Names No Channel

When two channels in the inventory carry the same handle in their stored data, the system SHALL record no edge for a reference to that handle, and the report SHALL name both channel ids.

#### Scenario: Two records with one handle

- **GIVEN** two channels whose stored data carry the handle `@fake-dup`
- **AND** a seed's video linking `youtube.com/@fake-dup`
- **WHEN** the report runs
- **THEN** no edge is recorded for that link
- **AND** the report names both channel ids as carrying `@fake-dup`

### Requirement: References Live With Their Payloads

The system SHALL work references out of the payloads and featured lists stored at the moment they are asked for, and MUST NOT store a reference, an edge, a pending target or anything else read out of a title, a description or a featured list. A payload or list refreshed or removed SHALL change what is referenced from the next reading on.

#### Scenario: A description edited upstream

- **GIVEN** a seed's video whose description linked a channel
- **WHEN** its payload is refreshed with a description that no longer links it, and references are read
- **THEN** that reference is not recorded

#### Scenario: A removed video

- **GIVEN** a seed's video linking an unknown handle, which YouTube then stops returning
- **WHEN** references are read
- **THEN** that handle is not pending, unless another stored video names it

#### Scenario: A featured list no longer current

- **GIVEN** a seed whose featured list named an unknown channel id, and whose list is then older than `youtube_answers_fresh_days`
- **WHEN** references are read
- **THEN** that id is not pending, unless another stored source names it

#### Scenario: Reading writes nothing

- **WHEN** references are read, or the report runs
- **THEN** no row in the database is inserted, updated or deleted

### Requirement: No Network

Reading references and running the report SHALL make no request to YouTube and SHALL need no API key.

#### Scenario: Without a key

- **GIVEN** no API key configured
- **WHEN** the report runs
- **THEN** it completes and makes no request

### Requirement: No Measure Over The Graph

The system SHALL NOT compute or show a weight, a score, a rank value, a centrality or a cluster over YouTube references. The only number it SHALL derive per target is how many distinct seeds reference it.

#### Scenario: What the report shows per target

- **WHEN** the report runs
- **THEN** each target is shown with the count of distinct seeds referencing it and their ids
- **AND** with no other number derived from the references

### Requirement: Mentions Report

The system SHALL provide `itgraph yt mentions`. It reports the known targets and then the pending targets, each with the ids of the seeds referencing it, most distinct seeds first and by target after that. It then reports what was counted and not followed. A minimum number of seeds and a row limit may bound each list.

#### Scenario: The most-named target first

- **GIVEN** an unknown handle linked by two seeds and another linked by one
- **WHEN** the report runs
- **THEN** the handle linked by two seeds is listed first, with both seeds' ids

#### Scenario: Ties are ordered the same way every time

- **GIVEN** two pending targets with one source each
- **WHEN** the report runs twice over unchanged data
- **THEN** both runs list them in the same order

#### Scenario: Bounded by evidence

- **GIVEN** pending targets with one, two and three sources
- **WHEN** the report runs with a minimum of two seeds
- **THEN** only the targets with two and three sources are listed

#### Scenario: What was not followed

- **GIVEN** descriptions with custom URLs, shortened links, playlist pages, Telegram links and mentions of unknown handles
- **WHEN** the report runs
- **THEN** it states how many of each were counted

### Requirement: The Report Is Not Scheduled

No scheduled job SHALL run the report: its output names handles read from the API, and a job's output is kept in the cluster's logs.

#### Scenario: The deployed jobs

- **WHEN** the deploy's manifests are rendered with the walk enabled and an API key
- **THEN** no scheduled job runs `yt mentions`

### Requirement: Logs Name Ids Only

Log records written while reading references SHALL identify channels and videos by id only, and MUST NOT carry a handle, title, description or link read from the API.

#### Scenario: Reading at debug level

- **GIVEN** logging at debug level
- **WHEN** the report runs over videos mentioning handles
- **THEN** no log record contains a handle, a title or a link from those videos

### Requirement: Featured Channels Are References

The system SHALL take each channel id in a seed's stored featured list as a reference from that seed, of the form `featured`, with no referencing video: an edge when the channel is known, a pending channel id otherwise. One seed SHALL make one such reference per channel, and none to its own channel.

#### Scenario: A featured seed

- **GIVEN** a seed whose featured list names another seed
- **WHEN** references are read
- **THEN** an edge of the form `featured` leads from the first seed to the second, naming no video

#### Scenario: A featured channel outside the inventory

- **GIVEN** a seed whose featured list names a channel id with no record
- **WHEN** references are read
- **THEN** that id is a pending channel id, with that seed as its source

#### Scenario: Featured and linked

- **GIVEN** a seed whose featured list names another seed, and one of whose videos links it
- **WHEN** references are read
- **THEN** two references are recorded: one of the form `featured`, one from the video

#### Scenario: The seed's own channel in its list

- **GIVEN** a seed whose featured list names its own channel id
- **WHEN** references are read
- **THEN** no reference is recorded for it
