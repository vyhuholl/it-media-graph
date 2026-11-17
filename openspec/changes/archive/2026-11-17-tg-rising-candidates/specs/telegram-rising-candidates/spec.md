# Spec Delta

## Purpose

Which Telegram channels, and which names the seeds mention, are rising: newly referred to by several seed families at once. How they rank and what is left out, the command that shows them, the poll loop's resolution of the ranking's head under a cap shared by every command, and the journal of resolutions that cap is read from.

## ADDED Requirements

### Requirement: What Is Rising

The system SHALL count as rising a channel in status `candidate` or `maybe`, or a pending username, that seeds which are not chats referred to — by forward or by mention — from at least `rising_min_families` distinct families within the trailing `rising_window_days`. A seed in the candidate's own family SHALL NOT count. The ranking SHALL be worked out when asked and never stored.

#### Scenario: Two families in the window

- **GIVEN** a candidate channel forwarded by a seed of one family and mentioned by a seed of another, both within the window
- **WHEN** the ranking is worked out
- **THEN** the channel is rising, with two families in the window

#### Scenario: One family is not enough

- **GIVEN** a candidate channel referred to within the window by two seeds of the same family
- **WHEN** the ranking is worked out
- **THEN** the channel is not rising

#### Scenario: Its own family does not count

- **GIVEN** a candidate channel confirmed in a family with a seed, referred to by that seed and by one seed of another family within the window
- **WHEN** the ranking is worked out
- **THEN** only the other family counts, and the channel is not rising

#### Scenario: Only seeds count

- **GIVEN** a candidate channel referred to within the window by a seed and by another candidate
- **WHEN** the ranking is worked out
- **THEN** only the seed's family counts

#### Scenario: Old references do not make it rising

- **GIVEN** a candidate channel referred to by three seed families, every reference older than the window
- **WHEN** the ranking is worked out
- **THEN** the channel is not rising

#### Scenario: A pending username rises by its sources' dates

- **GIVEN** a pending username whose sources include two seed families that last referred to it within the window
- **WHEN** the ranking is worked out
- **THEN** the username is rising, with two families in the window

#### Scenario: Nothing is stored

- **WHEN** the ranking is worked out
- **THEN** no row of the database is inserted, updated or deleted

### Requirement: How Rising Candidates Rank

The system SHALL rank rising candidates by the families that referred to them within the window, most first. A tie SHALL go to the one fewer families referred to before the window, as the newer, and then to a fixed order by key, so that the same data always gives the same ranking.

#### Scenario: More families first

- **GIVEN** a rising channel with three families in the window and one with two
- **WHEN** the ranking is worked out
- **THEN** the one with three ranks first

#### Scenario: The newer comes first

- **GIVEN** two rising channels with two families in the window each, one of them referred to by four families before the window and the other by none
- **WHEN** the ranking is worked out
- **THEN** the one with none before the window ranks first

### Requirement: What Is Left Out

The system SHALL leave out of the ranking a supergroup, a channel whose resolution found no public username, and a name or id whose resolution found a user, a bot or nothing. Each SHALL be counted as left out, and a channel already reviewed — a seed, or rejected — SHALL NOT appear at all.

#### Scenario: A supergroup

- **GIVEN** a rising chat
- **WHEN** the ranking is worked out
- **THEN** it is not ranked, and is counted among those left out

#### Scenario: A channel without a public username

- **GIVEN** a rising channel resolved by id, with no username
- **WHEN** the ranking is worked out
- **THEN** it is not ranked, and is counted among those left out

#### Scenario: A name that is not a channel

- **GIVEN** a rising pending username whose resolution found a user
- **WHEN** the ranking is worked out
- **THEN** it is not ranked, and is counted among those left out

#### Scenario: A reviewed channel

- **GIVEN** a seed and a rejected channel, each referred to by three seed families within the window
- **WHEN** the ranking is worked out
- **THEN** neither appears in it, nor among those left out

### Requirement: The Rising Report

The system SHALL provide `itgraph rising`, printing each rising channel that has a public username, in rank order. The report SHALL also print how many rising candidates await a resolution, how many were left out and why, and the resolutions of the last 24 hours against the cap. It SHALL make no request to Telegram, take no lease, write nothing, and never print a pending username.

#### Scenario: A ranked channel

- **GIVEN** a rising channel with a username
- **WHEN** the report runs
- **THEN** it prints the channel's rank, username, title, families in the window and before it, and when it was first referred to in the window

#### Scenario: A candidate awaiting a resolution

- **GIVEN** a rising pending username, and a rising channel never resolved
- **WHEN** the report runs
- **THEN** it says two await a resolution
- **AND** neither the username nor the id is printed

#### Scenario: The cap

- **GIVEN** three resolutions by `itgraph resolve` and two by the poll loop within the last 24 hours
- **WHEN** the report runs
- **THEN** it says five of the cap were spent, and by which commands

#### Scenario: No session

- **GIVEN** the poll loop holding the session lease
- **WHEN** the report runs
- **THEN** it runs, takes no lease, and makes no request

### Requirement: The Poll Loop Resolves The Head Of The Ranking

When no channel is due and no walk has a page to take, the poll loop SHALL resolve the highest-ranked rising candidate awaiting an identity, as the resolution pass resolves it — a channel by id, a pending username by name — at most once every `rising_resolve_gap_minutes`. It SHALL resolve nothing that is not rising, and SHALL NOT try again a resolution that failed.

#### Scenario: The head is resolved

- **GIVEN** two rising pending usernames, ranked first and second, and nothing due
- **WHEN** the loop ticks
- **THEN** it resolves the first, and the second waits for the gap

#### Scenario: A name that is a channel

- **GIVEN** the loop resolving a rising pending username that names a channel
- **WHEN** the lookup answers
- **THEN** a channel record is created with status `candidate` and discovery source `mention`, as the resolution pass creates one

#### Scenario: Polls and walks come first

- **GIVEN** a rising candidate awaiting an identity, a channel due a poll and a seed awaiting its walk
- **WHEN** the loop runs
- **THEN** the poll and the walk's page are asked for before any resolution

#### Scenario: Nothing that is not rising

- **GIVEN** pending usernames that are not rising, and no rising candidate awaiting an identity
- **WHEN** the loop ticks for a day
- **THEN** it resolves nothing

#### Scenario: A failed resolution is not tried again

- **GIVEN** a rising channel whose resolution by id failed
- **WHEN** the loop next looks for a candidate to resolve
- **THEN** it passes over that channel

### Requirement: Resolutions Are Capped Across Every Command

The poll loop SHALL NOT resolve when `resolution_cap` resolutions or more were made in the last 24 hours, counting those of `itgraph resolve`, `itgraph add` and the loop alike. The cap SHALL NOT stop a command the operator runs.

#### Scenario: The loop stops at the cap

- **GIVEN** ten resolutions by the loop in the last 24 hours, and a rising candidate awaiting an identity
- **WHEN** the loop ticks
- **THEN** it resolves nothing until the oldest of the ten is more than 24 hours old

#### Scenario: A manual run spends the loop's share

- **GIVEN** the operator's `itgraph resolve --limit 50` an hour ago
- **WHEN** the loop ticks with a rising candidate awaiting an identity
- **THEN** it resolves nothing

#### Scenario: The operator is not stopped

- **GIVEN** ten resolutions by the loop in the last 24 hours
- **WHEN** the operator runs `itgraph add` with a new username
- **THEN** it is resolved

### Requirement: A Rate Limit On Resolution Stops Resolution Alone

A rate limit on a resolution by the poll loop SHALL be recorded and SHALL hold the loop's resolution until the wait it names has ended, read from what was recorded so that a restart does not ask again. The loop SHALL NOT sleep it off, and SHALL go on polling and walking meanwhile.

#### Scenario: A day's wait

- **GIVEN** the loop's resolution answered with a wait of a day
- **WHEN** the loop goes on
- **THEN** the wait is recorded against the loop's command
- **AND** polls and walk pages continue at once
- **AND** no resolution is made until the day has passed

#### Scenario: A restart inside the wait

- **GIVEN** a recorded wait on resolution that has not ended
- **WHEN** the loop restarts
- **THEN** it makes no resolution before the wait ends

### Requirement: Every Resolution Is Journaled And Names Nothing

Every resolution by `itgraph resolve`, `itgraph add` or the poll loop SHALL be recorded when its request is made, with its time, its command, whether it asked by id or by username, and what it found: a channel, not a channel, or a failure. The record SHALL NOT hold the username or the id it asked about.

#### Scenario: Each command is journaled

- **WHEN** `itgraph resolve`, `itgraph add` and the loop each resolve one name
- **THEN** three rows are recorded, one per command

#### Scenario: A person is not recorded

- **GIVEN** a pending username that resolves to a user
- **WHEN** its resolution is recorded
- **THEN** the row says not a channel, and holds no username and no id
