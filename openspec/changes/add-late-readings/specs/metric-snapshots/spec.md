# Spec Delta

## ADDED Requirements

### Requirement: Posts Past The Horizon Are Read At Late Ages

The system SHALL store a snapshot of a post past the tracking horizon and younger than the late horizon (35 days by default) when a poll's window holds it and the post has passed a configured late age (by default 3, 4, 5, 7, 10, 14, 21 and 30 days) at which it was not yet read. Such a reading SHALL be taken from the window the poll already fetched, at most one per post per poll.

#### Scenario: A post that has passed a late age is read

- **GIVEN** a post 3.5 days old whose newest snapshot was taken at 48 hours
- **AND** a poll of its channel whose window holds it
- **WHEN** the poll is stored
- **THEN** a snapshot of the post is stored with the moment of the poll

#### Scenario: Several late ages passed at once give one reading

- **GIVEN** a post 6 days old whose newest snapshot was taken at 48 hours
- **WHEN** a poll's window holds it
- **THEN** one snapshot of it is stored
- **AND** the next poll stores none for it before it is 7 days old

#### Scenario: A post already read at its latest late age is not read again

- **GIVEN** a post 3.8 days old with a snapshot taken at 3.2 days
- **WHEN** a poll's window holds it
- **THEN** no snapshot of it is stored

#### Scenario: Past the late horizon a post is not read

- **GIVEN** a post 40 days old
- **WHEN** a poll's window holds it
- **THEN** no snapshot of it is stored

#### Scenario: A post the window does not reach is not read

- **GIVEN** a post 10 days old that is older than every message the poll's window holds
- **WHEN** the channel is polled
- **THEN** no snapshot of it is stored
- **AND** no request is made for it

#### Scenario: Late ages that could never be reached are refused

- **GIVEN** configured late ages not in ascending order, or a first late age inside the tracking horizon, or a late horizon that does not lie past the last late age
- **WHEN** settings are loaded
- **THEN** loading fails, naming the settings involved

### Requirement: Late Readings Cost No Request

The system SHALL take late readings only from polls the schedule makes anyway. A post past the tracking horizon MUST NOT make its channel due, MUST NOT shorten a channel's interval, and MUST NOT enlarge the window a poll asks for.

#### Scenario: A post due a late reading does not make its channel due

- **GIVEN** a channel with no post inside the tracking horizon and one post past a late age it was not read at
- **WHEN** its next-due moment is computed
- **THEN** it is the moment the channel's own posting rate gives, as it would be without that post

#### Scenario: The window is sized as before

- **GIVEN** a channel whose posting rate gives a window of ten messages
- **WHEN** it is polled with posts due late readings
- **THEN** the history request asks for ten messages

#### Scenario: A late reading counts the age it was taken at

- **GIVEN** a post whose 7-day late age passed while no poll was made
- **WHEN** a poll at 9 days stores its late reading
- **THEN** the snapshot carries the moment of that poll
- **AND** its age is obtained from that moment and the post's publication date alone

## MODIFIED Requirements

### Requirement: Per-Channel Poll Schedule

The system SHALL hold a next-due moment per channel and poll only channels that are due. The interval SHALL be derived from the age of the channel's youngest tracked post and, where it has none, from the channel's own posting rate, and MUST stay within configured bounds.

#### Scenario: A young post is sampled densely and then less often

- **GIVEN** a channel that has just published
- **WHEN** the post ages
- **THEN** the channel is polled on a schedule that starts within minutes of publication and lengthens as the post gets older

#### Scenario: A channel with nothing live falls back to its own rate

- **GIVEN** a channel whose posts are all older than the tracking horizon
- **WHEN** its next-due moment is computed
- **THEN** it is derived from how often that channel publishes
- **AND** it lies within the configured idle bounds

#### Scenario: A channel with several live posts is polled once

- **GIVEN** a channel carrying more than one post within the tracking horizon
- **WHEN** its next-due moment is computed
- **THEN** it is the earliest next sample over those posts
- **AND** one poll produces a snapshot for each of them

#### Scenario: A burst does not produce a burst of polls

- **GIVEN** a channel that publishes several messages within a few minutes
- **WHEN** its next-due moment is computed
- **THEN** it is no sooner than the configured minimum gap after the last poll

#### Scenario: A channel with no schedule yet is due immediately

- **GIVEN** an in-scope channel with no recorded next-due moment
- **WHEN** the loop selects work
- **THEN** the channel is treated as due
- **AND** polling it records a next-due moment

#### Scenario: Tracking ends at the horizon

- **GIVEN** a post older than the tracking horizon
- **WHEN** the channel is polled
- **THEN** no snapshot of the tracking schedule is recorded for that post
- **AND** a snapshot is recorded only where the post has passed a late age it was not read at, and is younger than the late horizon

### Requirement: Missed Samples Are Skipped, Not Replayed

The system SHALL compute a channel's next sample from the current age of its posts. Samples whose moment has passed while the loop was not running MUST be abandoned rather than taken late or queued. A late reading of a post past the tracking horizon is not a sample of this schedule: it schedules no poll and is taken at the first poll past its age.

#### Scenario: A long interruption produces no backlog

- **GIVEN** a loop that has not run for several hours
- **AND** channels whose next-due moments have all passed
- **WHEN** the loop starts again
- **THEN** each channel is polled once
- **AND** no channel is polled repeatedly to make up for the samples it missed

#### Scenario: A post that slept through its early samples resumes at its current age

- **GIVEN** a post whose first samples were missed
- **WHEN** its channel is next polled
- **THEN** the snapshot taken is the one appropriate to the post's current age
- **AND** the missed samples are not recorded at any later moment

#### Scenario: The age of a snapshot is a fact about the row

- **WHEN** a snapshot is stored
- **THEN** the moment of observation is recorded on it
- **AND** the age of the post at observation is obtainable from the snapshot and the post's publication date alone

#### Scenario: A late reading does not replay a missed sample

- **GIVEN** a post whose 24- and 48-hour samples fell inside an outage
- **WHEN** its channel is polled at 3.5 days
- **THEN** one late reading is stored
- **AND** no snapshot is recorded for 24 or 48 hours
