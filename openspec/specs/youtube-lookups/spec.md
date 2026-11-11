# youtube-lookups Specification

## Purpose
Finding which YouTube channel a linked video, handle or legacy name belongs to. It owns the requests that ask, how their spending is ordered and bounded, and the answers kept, so that whatever names channels by such links can be ranked by channel without asking twice.

## Requirements

### Requirement: Lookup Command

The system SHALL provide `itgraph yt tg-lookup`, finding the channel behind every video, handle and legacy name that the Telegram seeds link to and that nothing stored answers yet. It SHALL read no Telegram data over the network, and SHALL need no Telegram session.

#### Scenario: Names linked from Telegram are looked up

- **GIVEN** seeds' posts linking an unknown video and an unknown handle
- **WHEN** the command runs
- **THEN** the video and the handle are each looked up
- **AND** each answer is stored

#### Scenario: No Telegram session is needed

- **GIVEN** a running Telegram collection loop holding the session
- **WHEN** the command runs
- **THEN** it completes without refusing, and makes no Telegram request

#### Scenario: Nothing to look up

- **GIVEN** every name linked from the seeds already answered
- **WHEN** the command runs
- **THEN** no request is made
- **AND** it reports that nothing awaits a lookup

### Requirement: What Needs No Request

The system SHALL NOT make a request for a name that is already answered: a channel id, a video whose stored payload names its channel, a handle exactly one stored channel payload carries, or a name with a lookup still in date.

#### Scenario: A channel id is its own answer

- **GIVEN** a post linking `youtube.com/channel/UCfakeChannel0000000000a`
- **WHEN** the command runs
- **THEN** no request is made for it

#### Scenario: A stored video answers itself

- **GIVEN** a post linking a video whose stored payload names its channel
- **WHEN** the command runs
- **THEN** no request is made for it

#### Scenario: A handle the inventory carries

- **GIVEN** a post linking `youtube.com/@fake-seed`, a handle one stored channel payload carries
- **WHEN** the command runs
- **THEN** no request is made for it

#### Scenario: A handle two records carry is asked about

- **GIVEN** two stored channel payloads carrying the handle `@fake-dup`
- **AND** a post linking `youtube.com/@fake-dup`
- **WHEN** the command runs
- **THEN** the handle is looked up

#### Scenario: A lookup still in date

- **GIVEN** a video looked up 10 days ago
- **WHEN** the command runs
- **THEN** no request is made for it

### Requirement: Video Lookups

The system SHALL look up linked videos fifty to a request, asking for each video's id and its channel's id and nothing else. A video the answer does not hold SHALL be recorded as not found. A request answered not found as a whole SHALL be asked again id by id.

#### Scenario: Fifty videos to a request

- **GIVEN** a hundred linked videos, none answered
- **WHEN** the command runs
- **THEN** two video requests are made

#### Scenario: A deleted or private video

- **GIVEN** a request for fifty videos whose answer holds forty-nine
- **WHEN** the answer arrives
- **THEN** forty-nine videos are recorded with their channel
- **AND** the missing one is recorded as not found

#### Scenario: A batch answered not found

- **GIVEN** a request for several videos answered not found as a whole
- **WHEN** the command handles it
- **THEN** each video in it is asked for alone

### Requirement: Handle And Legacy Name Lookups

The system SHALL look up a linked handle or legacy name one to a request, asking for the channel's id and nothing else. A name YouTube returns no channel for SHALL be recorded as not found.

#### Scenario: A handle names a channel

- **GIVEN** a linked handle `fake-unknown`
- **WHEN** it is looked up
- **THEN** the request asks for the channel holding `@fake-unknown`, and for its id only
- **AND** the channel id is recorded for the handle

#### Scenario: A handle names nobody

- **GIVEN** a linked handle YouTube returns no channel for
- **WHEN** it is looked up
- **THEN** it is recorded as not found
- **AND** the run continues

#### Scenario: A legacy name

- **GIVEN** a linked `youtube.com/user/FakeLegacy`
- **WHEN** it is looked up
- **THEN** the request asks for the channel behind the legacy name `fakelegacy`, for its id only

### Requirement: Lookups Are Kept, Not Refreshed

The system SHALL store each answer — a channel id, or not found — with the moment it was fetched. An answer SHALL NOT be fetched again while it is in date, younger than `youtube_answers_fresh_days`. Past that, its name is looked up again by the next run that needs it, and the new answer replaces the old.

#### Scenario: A lookup no longer current is asked again

- **GIVEN** a video whose lookup was fetched 31 days ago
- **AND** a post still linking it
- **WHEN** the command runs
- **THEN** the video is looked up again

#### Scenario: Not found is remembered too

- **GIVEN** a handle recorded as not found 5 days ago
- **WHEN** the command runs
- **THEN** no request is made for it

### Requirement: Lookup Spending

The system SHALL book lookups against the general pool, leave no floor in it, and stop after the number of requests `--limit` gives. Names SHALL be looked up most evidence first — for `tg-lookup` by the number of distinct seed families linking them, for `mention-lookup` by the number of distinct seeds referencing them — then by kind and name. Each answer SHALL be committed before the next request.

#### Scenario: The best-evidenced names first

- **GIVEN** an unknown handle linked by three families and another linked by one
- **WHEN** the command runs with a limit of one request
- **THEN** the handle linked by three families is looked up
- **AND** the other is not

#### Scenario: The most-referenced names first

- **GIVEN** an unknown handle referenced by two seeds and another referenced by one
- **WHEN** `itgraph yt mention-lookup` runs with a limit of one request
- **THEN** the handle referenced by two seeds is looked up
- **AND** the other is not

#### Scenario: A stopped run resumes

- **GIVEN** a run that stopped at its limit part way through
- **WHEN** the command runs again
- **THEN** the names already answered cost no request

#### Scenario: Spent quota keeps what was done

- **WHEN** the general pool is spent, or YouTube answers `quotaExceeded`, during a run
- **THEN** the run stops
- **AND** the answers stored before it remain

#### Scenario: No floor is left

- **GIVEN** a general pool with fewer units left than the history walk's floor
- **WHEN** the command runs
- **THEN** it spends what is left

### Requirement: Lookup Report

Each lookup command SHALL report how many names it looked up by kind, how many were found and not found, how many requests it made, and how many names still await a lookup. It SHALL name no handle and no legacy name.

#### Scenario: What a run says

- **WHEN** a run ends
- **THEN** it reports videos, handles and legacy names looked up, found and not found, and requests made
- **AND** how many names still await a lookup

#### Scenario: Both commands report alike

- **WHEN** `itgraph yt mention-lookup` ends
- **THEN** its report has the same lines as `itgraph yt tg-lookup`'s

### Requirement: Lookup Logs Name Ids Only

Log records written while looking up SHALL identify videos and channels by id only, and MUST NOT carry a handle, a legacy name or a link.

#### Scenario: Looking up at debug level

- **GIVEN** logging at debug level
- **WHEN** a run looks up handles and videos
- **THEN** no log record contains a handle, a legacy name or a link

### Requirement: Mention Lookup Command

The system SHALL provide `itgraph yt mention-lookup`, finding the channel behind every handle, legacy name and video that seeds' videos reference and that nothing stored answers yet. A channel id needs no lookup, and nothing a featured list names is looked up.

#### Scenario: Names from descriptions are looked up

- **GIVEN** seeds' videos referencing an unknown video, an unknown handle and an unknown legacy name
- **WHEN** the command runs
- **THEN** each is looked up, and each answer is stored

#### Scenario: A name already looked up for Telegram

- **GIVEN** a handle looked up for a Telegram link 5 days ago
- **AND** a seed's video referencing the same handle
- **WHEN** the command runs
- **THEN** no request is made for it

#### Scenario: Nothing to look up

- **GIVEN** every name the seeds' videos reference already answered
- **WHEN** the command runs
- **THEN** no request is made
- **AND** it reports that nothing awaits a lookup
