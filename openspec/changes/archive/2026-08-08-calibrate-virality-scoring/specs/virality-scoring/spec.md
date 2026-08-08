## MODIFIED Requirements

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

### Requirement: Baselines Are Stored, Refreshed, And Carry Their Parameters

The system SHALL store the channel baselines, the growth curves and the spread each was measured against, refresh them on a configured cadence, and record the parameters under which each was computed.

The spread SHALL be measured per age band rather than once per metric, because the dispersion of the residual is not constant across a post's life, and one figure makes a threshold stricter at some ages than at others without saying so.

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
