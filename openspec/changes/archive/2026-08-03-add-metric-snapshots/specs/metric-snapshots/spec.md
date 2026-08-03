## ADDED Requirements

### Requirement: Metric Snapshots Are Observations

The system SHALL record engagement counters as append-only observations, one row per message per moment of observation. A recorded snapshot MUST NOT be updated or deleted by any later collection, and nothing derived from a snapshot may be stored alongside it.

#### Scenario: A snapshot is stored with the moment it was taken

- **WHEN** a message is observed during a poll
- **THEN** a snapshot is stored carrying the channel, the message, the moment of observation, and the views, forwards, reactions and comment count as they were
- **AND** the message's stored payload is left as it was

#### Scenario: A second observation is a second row

- **GIVEN** a message that already has a snapshot
- **WHEN** it is observed again
- **THEN** a new snapshot is stored
- **AND** the earlier snapshot is unchanged

#### Scenario: Absent is not zero

- **GIVEN** a channel that publishes no reactions object at all
- **WHEN** one of its messages is observed
- **THEN** the snapshot's reactions are recorded as absent rather than as zero
- **AND** a post carrying a reactions object with no reactions is recorded as zero

#### Scenario: Reactions are kept per emoji

- **GIVEN** a post carrying several kinds of reaction
- **WHEN** it is observed
- **THEN** the snapshot preserves the count of each kind separately
- **AND** no combined total is stored

#### Scenario: A snapshot requires the message it describes

- **WHEN** a message is observed for the first time
- **THEN** its payload is stored in the raw layer before or in the same transaction as its snapshot
- **AND** no snapshot exists for a message the raw layer does not hold

### Requirement: One Request Serves Both New Posts And Fresh Metrics

The system SHALL obtain new messages and refreshed counters from the same history request. A poll of a channel MUST NOT issue separate requests for the two.

#### Scenario: A poll stores new posts and refreshes live ones together

- **GIVEN** a channel with one message published since the last poll and two messages still within the tracking horizon
- **WHEN** the channel is polled
- **THEN** one history request is issued
- **AND** the new message is stored in the raw layer
- **AND** a snapshot is stored for every observed message within the horizon

#### Scenario: The high-water mark advances

- **GIVEN** a channel whose newest collected message is known
- **WHEN** a poll observes messages newer than it
- **THEN** the newest collected message recorded for that channel advances to the newest one stored
- **AND** the channel's newest post date is updated

#### Scenario: A poll that finds nothing new still refreshes

- **GIVEN** a channel with no new messages but a post still within the tracking horizon
- **WHEN** it is polled
- **THEN** no message is added to the raw layer
- **AND** a snapshot is still stored for the post within the horizon

#### Scenario: Re-observing a stored message stores no second payload

- **WHEN** a poll returns a message the raw layer already holds
- **THEN** the stored payload is left as it was
- **AND** a snapshot is still recorded

### Requirement: The Watch Loop Derives Nothing

The system SHALL confine the watch loop to fetching and storing. It MUST NOT write edges, scores, alerts or any other derived record, and MUST NOT modify the channel inventory's review state.

#### Scenario: No derived record is written

- **WHEN** a poll stores messages and snapshots
- **THEN** no edge is written
- **AND** no channel's status, review or rejection is changed

#### Scenario: Derivation stays a separate pass

- **GIVEN** messages collected by the loop
- **WHEN** the derivation pass is run afterwards
- **THEN** it produces the same edges it would have produced had the messages arrived from a history walk

### Requirement: Polling Spends No Quota-Bearing Request

The system SHALL poll a channel using its peer from the session's entity cache alone. A poll MUST NOT resolve a username and MUST NOT request extended channel information.

#### Scenario: The peer comes from the session's own cache

- **GIVEN** an in-scope channel whose peer the session can supply
- **WHEN** it is polled
- **THEN** the peer is obtained from the session's entity cache
- **AND** no username is resolved

#### Scenario: A channel with no cached peer is skipped rather than resolved

- **GIVEN** an in-scope channel whose peer the session cannot supply
- **WHEN** the loop reaches it
- **THEN** no username resolution is requested
- **AND** the channel is skipped and counted as such
- **AND** the loop continues with the remaining channels

#### Scenario: A running loop resolves nothing, however long it runs

- **WHEN** the loop has polled every in-scope channel
- **THEN** it has issued no username resolution and no extended-information request

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
- **THEN** no snapshot is recorded for that post

### Requirement: Missed Samples Are Skipped, Not Replayed

The system SHALL compute a channel's next sample from the current age of its posts. Samples whose moment has passed while the loop was not running MUST be abandoned rather than taken late or queued.

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

### Requirement: Polling Is Sequential And Paced

The system SHALL poll one channel at a time, with a gap before each request, and MUST NOT issue concurrent requests to Telegram.

#### Scenario: Channels are polled one at a time

- **GIVEN** several channels due at the same moment
- **WHEN** the loop polls them
- **THEN** their requests are issued sequentially
- **AND** no two requests are in flight at once

#### Scenario: Every request is preceded by a gap

- **WHEN** the loop issues a history request
- **THEN** it waits first, using the same pacing every other command uses

#### Scenario: An empty queue costs no requests

- **GIVEN** no channel is due
- **WHEN** the loop ticks
- **THEN** no request is issued
- **AND** the loop waits and checks again

### Requirement: The Loop Absorbs Rate Limits Rather Than Exiting

The system SHALL keep the watch loop running across rate limits. A wait short enough to sleep off SHALL be slept off and the request retried; a wait too long to sleep off SHALL postpone the schedule and MUST NOT stop the loop or be recorded as a channel failure.

#### Scenario: A short wait is slept off

- **WHEN** the loop encounters a rate limit short enough to wait out
- **THEN** it waits and retries the request
- **AND** the event is recorded against the watch command

#### Scenario: A long wait postpones the schedule

- **WHEN** the loop encounters a rate limit too long to wait out
- **THEN** every channel's next-due moment is moved past the end of the wait
- **AND** the loop continues running
- **AND** nothing is recorded as a failure against the channel that was being polled

#### Scenario: The watch command is distinguishable in the record

- **WHEN** the loop encounters a rate limit
- **THEN** the recorded event names the watch command
- **AND** it is distinguishable from the same method limited under another command

### Requirement: Failure Isolation

The system SHALL contain a failure to one channel. A channel that cannot be polled MUST NOT stop the loop, and repeated failures SHALL lengthen that channel's interval rather than retry it at the same rate.

#### Scenario: One inaccessible channel does not stop the loop

- **GIVEN** a channel that has become private or was deleted
- **WHEN** the loop polls it
- **THEN** the error is recorded against that channel
- **AND** the loop proceeds to the next due channel

#### Scenario: A repeatedly failing channel is tried less often

- **GIVEN** a channel that has failed several polls in a row
- **WHEN** its next-due moment is computed
- **THEN** it is further away than it would be for a channel that succeeded

#### Scenario: Process death loses at most the poll in flight

- **GIVEN** a poll whose messages and snapshots have been committed
- **WHEN** the process is killed
- **THEN** the committed rows are retained
- **AND** restarting the loop resumes from the recorded schedule without re-observing what it already stored

### Requirement: Quiet Hours

The system SHALL suspend polling during a configured window and resume afterwards without attempting to recover the samples that window cost.

#### Scenario: No request is issued during the window

- **GIVEN** the current time falls inside the configured quiet window
- **WHEN** the loop ticks
- **THEN** no history request is issued

#### Scenario: Work resumes without catch-up

- **WHEN** the quiet window ends
- **THEN** due channels are polled at the normal rate
- **AND** no channel is polled repeatedly to compensate for the window

#### Scenario: Quiet hours can be switched off

- **GIVEN** quiet hours configured as an empty window
- **WHEN** the loop runs
- **THEN** polling continues at every hour

### Requirement: Forward Collection Is Not Bounded By The History Ceiling

The system SHALL continue polling a channel that has reached the per-channel message ceiling, storing the messages it publishes from now on. The ceiling SHALL continue to bound the backward history walk.

#### Scenario: A capped channel is still polled

- **GIVEN** a channel that has reached its message ceiling
- **WHEN** it publishes a new post
- **THEN** the loop stores the post and snapshots it

#### Scenario: The backward walk stays closed

- **GIVEN** a channel that has reached its message ceiling
- **WHEN** a history walk is run with a deeper cutoff
- **THEN** the channel is not reopened for older history

### Requirement: A Run Reports What It Could Not Do

The system SHALL report the work the loop skipped, not only the work it completed, so that a degraded state is visible without reading logs.

#### Scenario: Skipped channels are counted

- **GIVEN** channels the session's entity cache cannot supply
- **WHEN** the loop has passed over them
- **THEN** their number is reported as a count
- **AND** the report distinguishes them from channels that failed

#### Scenario: The schedule's lag is visible

- **WHEN** the loop is asked for its state
- **THEN** it reports how many channels are overdue and by how long the oldest of them is
