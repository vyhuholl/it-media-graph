# Spec Delta

## ADDED Requirements

### Requirement: New Seeds Are Walked Without An Operator

The poll loop SHALL walk every seed in scope whose walk is not complete, back to the configured cutoff and under the per-channel ceiling, through the same per-channel progress the history walk keeps. How the channel became a seed SHALL NOT matter. A seed whose walk is complete SHALL NOT be walked again by the loop, whatever its depth.

#### Scenario: A seed marked from the command line is walked

- **GIVEN** a channel marked as a seed, never walked, whose peer the session holds
- **WHEN** the poll loop runs with no poll due
- **THEN** its history is fetched back to the configured cutoff, as `itgraph backfill` would fetch it
- **AND** its walk is recorded complete to that cutoff

#### Scenario: A seed added with its review is walked

- **GIVEN** a channel added by username and marked a seed in the same command
- **WHEN** the poll loop runs with no poll due
- **THEN** its history is fetched back to the configured cutoff

#### Scenario: The ceiling bounds a new seed

- **GIVEN** a new seed with more history above the cutoff than the ceiling allows
- **WHEN** the loop walks it
- **THEN** the walk stops at the ceiling
- **AND** the depth recorded is the date of the oldest message stored

#### Scenario: A completed walk is not repeated

- **GIVEN** a seed whose walk is complete to a cutoff later than the configured one
- **WHEN** the loop runs
- **THEN** no request is made to walk it

#### Scenario: Only what is in scope is walked

- **GIVEN** a rejected channel, a candidate, a discussion chat, and a seed whose walk failed permanently, none of them walked
- **WHEN** the loop runs
- **THEN** none of them is walked

#### Scenario: A walk interrupted by a restart resumes

- **GIVEN** a seed whose walk the loop had taken part of the way when it stopped
- **WHEN** the loop starts again
- **THEN** the walk continues from the oldest message already retrieved
- **AND** no message already stored is asked for again

#### Scenario: A seed that stops being one is not walked further

- **GIVEN** a seed part of the way through its walk
- **WHEN** it is rejected
- **THEN** the loop asks for no further page of it

### Requirement: A Walk Yields To Due Polls

The poll loop SHALL walk one page of history at a time, only when no poll is due, before each page waiting as the history walk waits. It SHALL make no walk request during quiet hours. A rate limit too long to wait out SHALL postpone the walk as it postpones the polls.

#### Scenario: Due polls come first

- **GIVEN** a seed awaiting its walk and channels due a poll
- **WHEN** the loop runs
- **THEN** the due channels are polled before any page of the walk is asked for

#### Scenario: One page, then the polls again

- **GIVEN** a seed awaiting a walk of several pages, and no poll due
- **WHEN** the loop walks a page
- **THEN** it looks for due polls before asking for the next page

#### Scenario: No walk in quiet hours

- **GIVEN** a seed awaiting its walk, and the current time inside quiet hours
- **WHEN** the loop ticks
- **THEN** no page is asked for

#### Scenario: A long rate limit stops the walk with the polls

- **GIVEN** a page of a walk answered with a rate limit longer than the halt threshold
- **WHEN** the loop handles it
- **THEN** the polls are postponed past the wait
- **AND** no page of any walk is asked for before the wait ends

### Requirement: A Walk That Could Not Run Is Tried Again An Hour Later

When the poll loop cannot walk a seed because the session holds no peer for it, or because a request failed transiently, it SHALL record why and SHALL NOT try that seed again for an hour, walking other seeds meanwhile. A permanent failure SHALL take the channel out of scope, as it does for any walk.

#### Scenario: A seed the session does not know

- **GIVEN** a seed awaiting its walk whose peer the session cannot supply
- **WHEN** the loop reaches it
- **THEN** no username resolution is requested
- **AND** it is recorded as skipped, with the reason
- **AND** it is not tried again within the hour, while another seed awaiting its walk is walked

#### Scenario: A transient failure

- **GIVEN** a page of a walk that fails with a network error
- **WHEN** an hour has passed
- **THEN** the loop tries the seed again from where it stopped

#### Scenario: A permanent failure

- **GIVEN** a seed whose channel turned private
- **WHEN** a page of its walk is refused for good
- **THEN** the failure is recorded as permanent
- **AND** the channel is neither walked nor polled again
