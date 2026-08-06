## ADDED Requirements

### Requirement: A Post Is Measured Against Its Own Channel At Its Own Age

The system SHALL compute an expected value for each metric from the channel's own history scaled to the post's age, and score the observed value as its deviation from that expectation in units of the measured spread. Absolute values MUST NOT be compared across channels, and values at different ages MUST NOT be compared without the age correction.

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

### Requirement: A Channel Without Enough History Is Not Scored, And Is Reported

The system SHALL score only channels with at least the configured number of mature posts, and SHALL report how many channels were excluded for want of history.

"No alerts from this channel" and "this channel is not scored at all" are different facts, and only one of them means the channel is quiet.

#### Scenario: A thin channel produces no score

- **GIVEN** a channel with fewer mature posts than the configured minimum
- **WHEN** its posts are scored
- **THEN** no score is produced for them

#### Scenario: The excluded channels are counted

- **WHEN** a scoring run finishes
- **THEN** it reports how many in-scope channels have no baseline

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
- **THEN** a further alert may be raised for that metric

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

The system SHALL be able to score stored snapshots as of a past moment, report what would have been raised, and write no alert and send no message while doing so. The replay MUST use the same scoring code as the live pass.

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

### Requirement: Scoring Reads Only Collected Data

The system SHALL score from stored snapshots and stored history alone. It MUST issue no Telegram request, MUST NOT acquire the session lease, and MUST NOT modify the snapshots, the raw layer, the edges or the inventory.

#### Scenario: No request is made

- **WHEN** the scoring pass runs
- **THEN** no Telegram request is issued
- **AND** it does not acquire the session lease

#### Scenario: Collected data is untouched

- **WHEN** the scoring pass runs
- **THEN** no snapshot, raw message, edge or channel record is modified
