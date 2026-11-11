## ADDED Requirements

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

## MODIFIED Requirements

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

### Requirement: References Live With Their Payloads

The system SHALL work references out of the payloads and featured lists stored at the moment they are asked for, and MUST NOT store a reference, an edge, a pending target or anything else read out of a title, a description or a featured list.

#### Scenario: A description edited upstream

- **GIVEN** a seed's video whose description linked a channel
- **WHEN** its payload is refreshed with a description that no longer links it, and references are read
- **THEN** that reference is not recorded
