## MODIFIED Requirements

### Requirement: A Post Is Measured Against Its Own Channel At Its Own Age

The system SHALL compute an expected value for each metric from the channel's own history scaled to the post's age, and score the observed value as its deviation from that expectation in units of the measured spread. Absolute values MUST NOT be compared across channels, and values at different ages MUST NOT be compared without the age correction.

The channel's reference SHALL be drawn from its own newest posts, read at the age the growth curve is normalised to: the median, over a configured number of the channel's most recent posts within a configured maximum age, of the value each reached at that age. It MUST be recomputed from posts as they are collected, so that it describes the channel as it is now. A reference that does not move measures a channel against the version of itself that existed when it was fixed, which for a growing channel means its ordinary posts read as remarkable, and for a shrinking one that nothing it publishes can.

Where a channel has too few such posts, the system SHALL fall back to the median of its settled posts scaled by its kind's factor, bounded at both ends as before: posts settled enough to have stopped moving, and recent enough to describe the channel. A channel scored against this fallback SHALL be distinguishable from one scored against its newest posts, because the fallback is only as recent as the history it was taken from.

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

- **GIVEN** a channel whose newest posts reach far more than its posts did some months ago
- **WHEN** its baseline is computed
- **THEN** the reference is taken from its newest posts
- **AND** an ordinary recent post on that channel scores near zero rather than high

#### Scenario: A channel that has shrunk is measured against what it is now

- **GIVEN** a channel whose newest posts reach far less than its posts did some months ago
- **WHEN** an ordinary recent post on it is scored
- **THEN** its score is near zero rather than far below it

#### Scenario: The reference follows the channel as posts are collected

- **GIVEN** a channel whose reference was computed at one refresh
- **WHEN** it publishes posts that reach a different level and baselines are refreshed again
- **THEN** the new reference reflects those posts

#### Scenario: A channel too quiet for a recent reference falls back to its settled posts

- **GIVEN** a channel with fewer recent posts than the configured minimum but enough settled history
- **WHEN** its baseline is computed
- **THEN** it is given the median of its settled posts scaled by its kind's factor
- **AND** the baseline records that it is the fallback

#### Scenario: A reading between sample offsets is still scored

- **GIVEN** a post whose only reading inside the window falls between two collection offsets
- **WHEN** it is scored
- **THEN** a score is produced for that reading

#### Scenario: Publication time does not decide whether a post can be scored

- **GIVEN** two posts of comparable channels, one published during the day and one during the quiet hours
- **WHEN** both are read at least once inside the alerting window
- **THEN** both are scored

### Requirement: Baselines Are Stored, Refreshed, And Carry Their Parameters

The system SHALL store the channel baselines, the growth curves and the spread each was measured against, refresh them on a configured cadence, and record the parameters under which each was computed, including which kind of reference each channel was given.

The spread SHALL be measured per age band rather than once per metric, because the dispersion of the residual is not constant across a post's life, and one figure makes a threshold stricter at some ages than at others without saying so.

The spread SHALL be measured against the reference each post would actually have been scored against: one built from the channel's posts whose reference age had passed before that post was published. A post MUST NOT take part in the reference its own residual is measured from, because a reference containing the posts it is measured on fits them, and a ruler fitted to its own readings is too narrow — the direction that over-alerts.

A band's own spread SHALL be used only where it was measured on at least a configured number of distinct posts, as well as a configured number of readings. A post is read several times inside one band, so readings overstate how much a band has seen; a spread measured on a handful of posts is too narrow as often as not, and too narrow is the direction that over-alerts.

Where a channel kind has too little data to fit a curve of its own, the system SHALL fall back to a curve pooled across kinds rather than leaving that kind unscoreable, and SHALL record on the stored baseline that the curve was borrowed. A borrowed estimate that reports itself is not the same as a partial baseline assembled silently.

#### Scenario: Scoring reads stored baselines

- **WHEN** the scoring pass runs
- **THEN** it reads baselines that were computed earlier
- **AND** does not recompute them per post

#### Scenario: Baselines record what they were computed under

- **WHEN** baselines are refreshed
- **THEN** the parameters used are stored with them
- **AND** each channel's baseline records whether it is drawn from the channel's newest posts or from its settled history

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

#### Scenario: A post does not narrow its own ruler

- **GIVEN** a post whose readings contribute to the spread
- **WHEN** baselines are refreshed
- **THEN** its residuals are measured against a reference built only from posts whose reference age had passed before it was published

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

The system SHALL score only channels that have either enough recent posts for a reference of their own, or at least the configured number of mature posts inside the mature window, and SHALL report how many channels were excluded for want of history.

It SHALL also report how many scored channels are measured against the fallback rather than their newest posts. "No alerts from this channel" and "this channel is not scored at all" are different facts, and only one of them means the channel is quiet; "scored against what it is now" and "scored against what it was when its history was collected" are a second such pair, and MUST be reported rather than inferred.

#### Scenario: A thin channel produces no score

- **GIVEN** a channel with fewer recent posts than the configured minimum and fewer mature posts than the configured minimum
- **WHEN** its posts are scored
- **THEN** no score is produced for them

#### Scenario: The excluded channels are counted

- **WHEN** a scoring run finishes
- **THEN** it reports how many in-scope channels have no baseline

#### Scenario: The channels on the fallback are counted

- **WHEN** baselines are refreshed, or a scoring run finishes
- **THEN** it reports how many channels are measured against their newest posts and how many against their settled history

#### Scenario: A channel with recent posts but no settled history is scored

- **GIVEN** a channel added after its history could be collected, with enough recent posts
- **WHEN** baselines are refreshed
- **THEN** it is given a reference from its newest posts
- **AND** it is not counted among the channels excluded for want of history

#### Scenario: A channel with history only outside the window is excluded

- **GIVEN** a channel whose posts are all older than both the recent and the mature window
- **WHEN** baselines are refreshed
- **THEN** it is given no baseline
- **AND** it is counted among the channels excluded for want of history
