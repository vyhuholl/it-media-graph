## ADDED Requirements

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

## MODIFIED Requirements

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
