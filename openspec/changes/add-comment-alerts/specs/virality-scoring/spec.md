# Spec Delta

## ADDED Requirements

### Requirement: Comments Are Scored As Counts

The system SHALL score a post's comments by how improbable its count is: negative binomial, with the channel's comment reference scaled by the comment curve as its mean and its kind's dispersion, the score being the z whose standard normal upper tail equals the probability of a count at least as large. It MUST NOT score comments by the logarithm of observed over expected, which cannot describe a counter that is zero in half its readings.

#### Scenario: A post without comments on a quiet channel

- **GIVEN** a channel whose posts usually have about one comment at this age
- **WHEN** a post of it has none
- **THEN** its comment score is below zero, and it raises nothing

#### Scenario: Many comments on a channel that rarely has any

- **GIVEN** a channel whose posts usually have about one comment at this age, in a kind of ordinary dispersion
- **WHEN** a post of it reaches thirty
- **THEN** its comment score is past the threshold

#### Scenario: A kind whose discussions vary widely needs more

- **GIVEN** two channels with the same expectation, in kinds of low and of high dispersion
- **WHEN** a post of each reaches the same count above it
- **THEN** the post in the kind of high dispersion scores lower

#### Scenario: The reference keeps the posts nobody commented on

- **GIVEN** a channel whose newest twenty posts had no comments but for two posts with ten each
- **WHEN** its comment reference is computed
- **THEN** it is one comment, not ten

#### Scenario: A channel with too few recent posts

- **GIVEN** a channel with fewer recent posts than the configured minimum
- **WHEN** its posts are scored
- **THEN** no comment score is produced for them, and its other metrics are scored as before

#### Scenario: A count far past what the arithmetic can tell apart

- **GIVEN** a post whose count is so improbable that its probability underflows
- **WHEN** it is scored
- **THEN** its score is a finite ceiling, not infinite and not missing

### Requirement: Comment Baselines Carry A Count Curve And A Dispersion

The baseline refresh SHALL fit the comment curve as a ratio of sums per age band, zeros included, and the comment dispersion per channel kind by maximum likelihood, against the reference each post would actually have been scored against. The dispersion SHALL be stored with the run, and borrowed from the pool for a kind too thin to fit its own, as curves are. A run that stores no comment dispersion SHALL score no comments.

#### Scenario: Zeros shape the curve

- **GIVEN** posts whose comments at fifteen minutes are mostly zero
- **WHEN** the comment curve is fitted
- **THEN** the fraction at fifteen minutes counts those zeros

#### Scenario: The dispersion is stored per kind

- **WHEN** baselines are refreshed
- **THEN** each kind's comment baseline carries a dispersion, and the run records which kinds borrowed it

#### Scenario: A post does not fit its own dispersion

- **GIVEN** a post whose readings take part in fitting the dispersion
- **WHEN** baselines are refreshed
- **THEN** its expectation is built only from posts whose reference age had passed before it was published

#### Scenario: A run measured before comments were counted

- **GIVEN** a current baseline run that stores no comment dispersion
- **WHEN** posts are scored
- **THEN** no comment score is produced, and the run says comments are not scored


## MODIFIED Requirements

### Requirement: A Post Is Measured Against Its Own Channel At Its Own Age

The system SHALL compute an expected value for each metric from the channel's own history scaled to the post's age, and score the observed value as its deviation from that expectation in units of the measured spread. Absolute values MUST NOT be compared across channels, and values at different ages MUST NOT be compared without the age correction. Comments are the one exception to how the deviation is measured: they are scored as counts ("Comments Are Scored As Counts").

The channel's reference SHALL be drawn from its own newest posts, read at the age the growth curve is normalised to: the median, over a configured number of the channel's most recent posts within a configured maximum age, of the value each reached at that age. For comments it is the mean of those values, zeros included. It MUST be recomputed from posts as they are collected, so that it describes the channel as it is now. A reference that does not move measures a channel against the version of itself that existed when it was fixed, which for a growing channel means its ordinary posts read as remarkable, and for a shrinking one that nothing it publishes can.

Where a channel has too few such posts, its comments SHALL NOT be scored, and for the other metrics the system SHALL fall back to the median of its settled posts scaled by its kind's factor, bounded at both ends as before: posts settled enough to have stopped moving, and recent enough to describe the channel. A channel scored against this fallback SHALL be distinguishable from one scored against its newest posts, because the fallback is only as recent as the history it was taken from.

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

### Requirement: Thresholds Are Parameters With Measured Defaults

The system SHALL take the score threshold, the minimum history and the minimum comment count as configuration, and its defaults SHALL be derived from the observed alert rate rather than chosen for roundness.

#### Scenario: The threshold is settable

- **GIVEN** a configured threshold
- **WHEN** posts are scored
- **THEN** only those above it raise alerts

#### Scenario: Defaults produce a usable volume

- **WHEN** the pass runs with no threshold given
- **THEN** it uses a default whose measured rate is a readable number of alerts a day rather than the rate of noise

#### Scenario: Comments under the minimum raise nothing

- **GIVEN** a configured minimum comment count
- **WHEN** a post's comments are fewer than that, however improbable for its channel
- **THEN** no comment alert is raised for that reading
