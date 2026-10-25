# virality-scoring Specification

## Purpose
TBD - created by archiving change add-virality-scoring. Update Purpose after archive.
## Requirements
### Requirement: A Post Is Measured Against Its Own Channel At Its Own Age

The system SHALL compute an expected value for each metric from the channel's own history scaled to the post's age, and score the observed value as its deviation from that expectation in units of the measured spread. Absolute values MUST NOT be compared across channels, and values at different ages MUST NOT be compared without the age correction.

The channel's history SHALL be bounded at both ends: posts settled enough to have stopped moving, and recent enough to describe the channel as it is now. An unbounded history measures a channel against every version of itself it has ever been, which for a growing channel means its ordinary posts read as remarkable.

A post SHALL be scoreable at any age inside the alerting window, not only at ages near a collection sample offset. Sampling is irregular by design, so ages cluster differently for posts published at different times of day; scoring only near the offsets makes a post's chance of being measured depend on when it was published.

#### Scenario: The same reach scores differently on channels of different size

- **GIVEN** two channels whose typical post reaches very different numbers
- **WHEN** each publishes a post reaching the same absolute figure
- **THEN** they receive different scores
- **AND** the smaller channel's post scores higher

#### Scenario: The same reach scores the same at different ages

- **GIVEN** one channel and two posts reaching the same figure, one an hour old and one eight hours old
- **WHEN** both are scored
- **THEN** the younger post scores higher
- **AND** the difference reflects the age correction rather than the raw values

#### Scenario: A post at its channel's ordinary level does not score

- **GIVEN** a post reaching what its channel's posts of that age normally reach
- **WHEN** it is scored
- **THEN** its score is near zero

#### Scenario: A channel that has grown is measured against what it is now

- **GIVEN** a channel whose posts reach far more than they did a year ago
- **WHEN** its baseline is computed
- **THEN** posts older than the configured window do not contribute to it
- **AND** an ordinary recent post on that channel scores near zero rather than high

#### Scenario: A reading between sample offsets is still scored

- **GIVEN** a post whose only reading inside the window falls between two collection offsets
- **WHEN** it is scored
- **THEN** a score is produced for that reading

#### Scenario: Publication time does not decide whether a post can be scored

- **GIVEN** two posts of comparable channels, one published during the day and one during the quiet hours
- **WHEN** both are read at least once inside the alerting window
- **THEN** both are scored

### Requirement: Each Metric Is Scored Against Its Own Curve

The system SHALL score views, reactions, forwards and comments independently, each against the growth curve measured for that metric. It MUST NOT score a ratio of two metrics against a baseline taken at a different age, and MUST NOT combine the four into a single score.

Metrics accrue at different rates — forwards front-load relative to views — so a ratio of two of them carries the difference between their curves and reads high on young posts, which over-alerts in the direction that costs trust.

#### Scenario: Metrics are scored separately

- **WHEN** a post is scored
- **THEN** a score is produced for each metric the post carries
- **AND** no score depends on the value of another metric

#### Scenario: A metric the channel does not publish is not scored

- **GIVEN** a channel that publishes no reactions
- **WHEN** its posts are scored
- **THEN** no reaction score is produced
- **AND** this is not recorded as a reaction score of zero

### Requirement: Age Comes From The Observation

The system SHALL take a snapshot's age as the interval between when it was observed and when the post was published. It MUST NOT infer age from which sample in the collection schedule the snapshot was expected to be.

Samples are irregular by design: quiet hours, suspend and rate limits all cost readings, and a missed one is dropped rather than taken late. A scorer assuming the schedule was met would mis-age precisely the posts whose sampling was unusual.

#### Scenario: An irregularly sampled post is scored at its real age

- **GIVEN** a post whose early samples were missed and whose first reading is hours old
- **WHEN** it is scored
- **THEN** the expectation used is the one for its real age at that reading

#### Scenario: Two posts read at the same age score comparably

- **GIVEN** two posts of the same channel read at the same age but at different points in the schedule
- **WHEN** both are scored
- **THEN** neither is advantaged by which sample it happened to be

### Requirement: Baselines Are Stored, Refreshed, And Carry Their Parameters

The system SHALL store the channel baselines, the growth curves and the spread each was measured against, refresh them on a configured cadence, and record the parameters under which each was computed.

The spread SHALL be measured per age band rather than once per metric, because the dispersion of the residual is not constant across a post's life, and one figure makes a threshold stricter at some ages than at others without saying so.

A band's own spread SHALL be used only where it was measured on at least a configured number of distinct posts, as well as a configured number of readings. A post is read several times inside one band, so readings overstate how much a band has seen; a spread measured on a handful of posts is too narrow as often as not, and too narrow is the direction that over-alerts.

Where a channel kind has too little data to fit a curve of its own, the system SHALL fall back to a curve pooled across kinds rather than leaving that kind unscoreable, and SHALL record on the stored baseline that the curve was borrowed. A borrowed estimate that reports itself is not the same as a partial baseline assembled silently.

#### Scenario: Scoring reads stored baselines

- **WHEN** the scoring pass runs
- **THEN** it reads baselines that were computed earlier
- **AND** does not recompute them per post

#### Scenario: Baselines record what they were computed under

- **WHEN** baselines are refreshed
- **THEN** the parameters used are stored with them

#### Scenario: A refresh replaces rather than accumulates

- **WHEN** baselines are refreshed a second time
- **THEN** the current baselines are the newer ones
- **AND** nothing scores against a mixture of the two

#### Scenario: Scoring without baselines raises nothing and says so

- **GIVEN** baselines have never been computed
- **WHEN** the scoring pass runs
- **THEN** no alert is raised
- **AND** the run reports that there are no baselines rather than appearing to find nothing

#### Scenario: The spread is measured where the reading is

- **GIVEN** a metric whose residuals are more dispersed on young posts than on settled ones
- **WHEN** a reading is scored
- **THEN** it is divided by the spread measured for its own age band

#### Scenario: A band too thin to measure its own spread borrows the metric's

- **GIVEN** an age band with fewer residuals than the configured minimum
- **WHEN** a reading falls in it
- **THEN** it is scored against the metric's pooled spread
- **AND** it is not left unscored

#### Scenario: A band measured on few posts borrows the metric's spread however many readings it has

- **GIVEN** an age band with enough readings but fewer distinct posts than the configured minimum
- **WHEN** baselines are refreshed
- **THEN** no spread of its own is stored for that band
- **AND** a reading in it is scored against the metric's pooled spread

#### Scenario: A kind with no curve of its own is still scored

- **GIVEN** a channel kind with too few posts to fit its own growth curve
- **WHEN** baselines are refreshed
- **THEN** its channels are given the curve pooled across kinds
- **AND** the refresh reports which kinds and metrics took a borrowed curve

### Requirement: A Channel Without Enough History Is Not Scored, And Is Reported

The system SHALL score only channels with at least the configured number of mature posts inside the mature window, and SHALL report how many channels were excluded for want of history.

"No alerts from this channel" and "this channel is not scored at all" are different facts, and only one of them means the channel is quiet. Bounding the mature window necessarily excludes channels that publish too rarely to fill it, so the count of excluded channels is the measure of what the bound costs and MUST be reported rather than inferred.

#### Scenario: A thin channel produces no score

- **GIVEN** a channel with fewer mature posts than the configured minimum
- **WHEN** its posts are scored
- **THEN** no score is produced for them

#### Scenario: The excluded channels are counted

- **WHEN** a scoring run finishes
- **THEN** it reports how many in-scope channels have no baseline

#### Scenario: A channel with history only outside the window is excluded

- **GIVEN** a channel whose posts are all older than the mature window
- **WHEN** baselines are refreshed
- **THEN** it is given no baseline
- **AND** it is counted among the channels excluded for want of history

### Requirement: One Post Raises One Alert

The system SHALL raise at most one alert per post per threshold band, choosing the metric with the highest score, and SHALL record which metric that was. It MUST NOT raise one alert per metric for the same post at the same time.

A post that is genuinely unusual tends to be unusual on several metrics at once, so one alert per metric would produce the most messages about exactly the posts most worth reading.

#### Scenario: A post unusual on several metrics raises one alert

- **GIVEN** a post scoring above the threshold on more than one metric
- **WHEN** it is scored
- **THEN** one alert is raised
- **AND** it records the metric that scored highest

#### Scenario: A later spike on a different metric is a separate event

- **GIVEN** a post that already alerted on one metric
- **WHEN** a different metric crosses the threshold hours later
- **THEN** a further alert is recorded for that metric
- **AND** it is folded into the channel's head if it crossed inside the fold window, and raised as a new head otherwise

#### Scenario: Re-scoring raises nothing new

- **GIVEN** a completed scoring run
- **WHEN** it runs again over the same snapshots
- **THEN** no additional alert is raised

### Requirement: Thresholds Are Parameters With Measured Defaults

The system SHALL take the score threshold and the minimum history as configuration, and its defaults SHALL be derived from the observed alert rate rather than chosen for roundness.

#### Scenario: The threshold is settable

- **GIVEN** a configured threshold
- **WHEN** posts are scored
- **THEN** only those above it raise alerts

#### Scenario: Defaults produce a usable volume

- **WHEN** the pass runs with no threshold given
- **THEN** it uses a default whose measured rate is a readable number of alerts a day rather than the rate of noise

### Requirement: Scoring Can Be Replayed Over History Without Sending Anything

The system SHALL be able to score stored snapshots as of a past moment, report what would have been raised, and write no alert and send no message while doing so. The replay MUST use the same scoring code as the live pass, including the folding of a channel's spikes into one event, and SHALL report which spikes would have been folded and into which head.

A threshold that can only be tried in production costs a day per experiment and is therefore chosen once. A replay implemented separately would agree with the live pass on the cases anyone checks and diverge on the one that matters.

#### Scenario: A replay reports without raising

- **WHEN** the pass is run in replay over a past period
- **THEN** it reports what would have been raised
- **AND** no alert row is written
- **AND** nothing is delivered

#### Scenario: A replay of the present agrees with the live pass

- **GIVEN** a set of snapshots
- **WHEN** the pass is run live and then replayed over the same period and moment
- **THEN** the posts it names are the same

#### Scenario: A replay can vary the threshold

- **GIVEN** a replay run with a threshold other than the configured one
- **WHEN** it finishes
- **THEN** it reports the volume that threshold would have produced

#### Scenario: A replay reports folds

- **GIVEN** a past period in which several posts of one channel crossed the threshold together
- **WHEN** the pass is run in replay over it
- **THEN** it reports one head for that channel
- **AND** it names the other posts as folded into that head

### Requirement: Scoring Reads Only Collected Data

The system SHALL score from stored snapshots and stored history alone. It MUST issue no Telegram request, MUST NOT acquire the session lease, and MUST NOT modify the snapshots, the raw layer, the edges or the inventory.

#### Scenario: No request is made

- **WHEN** the scoring pass runs
- **THEN** no Telegram request is issued
- **AND** it does not acquire the session lease

#### Scenario: Collected data is untouched

- **WHEN** the scoring pass runs
- **THEN** no snapshot, raw message, edge or channel record is modified

### Requirement: A Channel's Spikes Inside A Window Are One Event

The system SHALL treat every spike of one channel that crosses the threshold within a configured window of that channel's first spike as part of one event. The first spike SHALL be raised as the event's head; each later spike of the same channel inside the window SHALL be recorded as folded into that head, and MUST NOT be delivered as a message of its own.

When a whole channel's audience grows at once — an advertisement, a broadcast, purchased views — every recent post crosses together, and one message per post tells one event many times. The window is measured from the head, so a channel raises at most one direct spike message per window, and an event on a later day starts a new head.

The order of spikes inside one pass SHALL be by the moment each crossed the threshold, and among spikes that crossed at the same moment by score, highest first, so the head is the earliest and then the most remarkable post.

A folded spike SHALL be recorded rather than discarded: a post folded into an event MUST NOT be raised again as a new head once the window has passed.

#### Scenario: Posts of one channel crossing together raise one head

- **GIVEN** a channel with several recent posts that all cross the threshold in the same pass
- **WHEN** the scoring pass runs
- **THEN** one alert is raised as the head
- **AND** every other crossing post of that channel is recorded as folded into it

#### Scenario: A later crossing inside the window folds into the existing head

- **GIVEN** a channel whose head spike was raised less than the configured window ago
- **WHEN** another of its posts crosses the threshold
- **THEN** that spike is recorded as folded into the existing head
- **AND** no new head is raised

#### Scenario: A crossing after the window starts a new event

- **GIVEN** a channel whose last head spike was raised longer ago than the configured window
- **WHEN** another of its posts crosses the threshold
- **THEN** that spike is raised as a new head

#### Scenario: Spikes of different channels do not fold together

- **GIVEN** two channels whose posts cross the threshold in the same pass
- **WHEN** the scoring pass runs
- **THEN** each channel has its own head

#### Scenario: A folded post is not raised again later

- **GIVEN** a post whose spike was folded into a head
- **WHEN** the scoring pass runs again after the window has passed while the post is still above the threshold
- **THEN** no further alert is raised for that post and metric

#### Scenario: The head is the earliest crossing

- **GIVEN** two posts of one channel, one of which crossed at an earlier reading than the other
- **WHEN** both are first scored in the same pass
- **THEN** the earlier one is the head, whatever their scores

### Requirement: A Burst Of Messages Is One Post

The system SHALL score consecutive messages of one channel published less than a configured interval apart as one post, under its first message, as an album is scored under its first part. It MUST NOT raise one alert per message of such a burst.

A post published as text followed by each attachment sent separately reaches its readers as one post; its messages are read together and their counters move together, so scoring each is one post alerting several times.

#### Scenario: Messages seconds apart raise one alert

- **GIVEN** three messages of one channel published twenty seconds apart, all far above their expectation
- **WHEN** the scoring pass runs
- **THEN** one alert is raised
- **AND** it names the first message

#### Scenario: Messages minutes apart are separate posts

- **GIVEN** two messages of one channel published several minutes apart
- **WHEN** the scoring pass runs
- **THEN** each is scored as a post of its own

