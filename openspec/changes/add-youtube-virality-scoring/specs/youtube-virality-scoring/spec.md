## Purpose

How a YouTube video is judged to be doing unusually well for its own channel, its format and its age: what is scored, against which reference and curve, how a threshold is crossed and folded into an event, and how the judgement is replayed.

## ADDED Requirements

### Requirement: A Video Is Measured Against Its Own Channel, Format And Age

The system SHALL compute each signal's expected value as the channel's reference for the video's format, scaled by a factor and by the growth curve at the reading's age, and score the reading as its deviation from that expectation in units of the measured spread. Absolute values MUST NOT be compared across channels, nor values at different ages without the age correction.

#### Scenario: The same reach on channels of different size

- **GIVEN** two channels whose typical long-form video reaches very different numbers
- **WHEN** each publishes a video reaching the same views at the same age
- **THEN** the smaller channel's video scores higher

#### Scenario: The same reach at different ages

- **GIVEN** one channel and two of its videos with the same views, one eight hours old and one three days old
- **WHEN** both are scored
- **THEN** the younger scores higher

#### Scenario: An ordinary video

- **GIVEN** a video reaching what its channel's videos of that format normally reach at that age
- **WHEN** it is scored
- **THEN** its score is near zero

### Requirement: Formats Are Scored Apart

The system SHALL score short-form videos — no broadcast details, a duration of at most a configured number of seconds, 180 by default — and long-form videos — no broadcast details, longer — as separate populations, each with its own references, curves and spreads. The format SHALL be worked out from the stored video data when scoring and MUST NOT be stored per video.

#### Scenario: A channel's Shorts and its talks

- **GIVEN** a channel publishing both 40-second videos and hour-long talks
- **WHEN** baselines are refreshed
- **THEN** the channel has one reference for its short-form videos and one for its long-form ones

#### Scenario: A two-minute video

- **GIVEN** a video of 150 seconds with no broadcast details
- **WHEN** it is scored
- **THEN** it is scored against the channel's short-form reference

#### Scenario: Nothing records a video's format

- **WHEN** a video is scored
- **THEN** nothing stored about that video says which format it was scored as

### Requirement: Broadcasts Are Not Scored

The system SHALL NOT score a video whose data carries broadcast details — a stream or a premiere — nor a video whose duration cannot be read, and the scoring pass SHALL report how many it left out for each reason.

#### Scenario: A finished stream

- **GIVEN** a video with broadcast details and readings far above its channel's long-form videos
- **WHEN** the scoring pass runs
- **THEN** no score is produced for it and no alert is raised
- **AND** the pass counts it among the broadcasts not scored

### Requirement: Each Signal Is Scored Against Its Own Curve

The system SHALL score views, likes and comments independently, each against the growth curve measured for that signal. It MUST NOT score a ratio of two signals, nor combine them into one score. A counter YouTube did not report SHALL be left unscored and MUST NOT be treated as zero.

#### Scenario: Hidden likes

- **GIVEN** a video whose readings carry views and no like count
- **WHEN** it is scored
- **THEN** its views are scored, no like score is produced, and nothing records a like score of zero

#### Scenario: A channel that hides likes everywhere

- **GIVEN** a channel none of whose videos reports likes
- **WHEN** baselines are refreshed
- **THEN** the channel has no like reference, and its views are still scored

### Requirement: Comments Are Scored As Counts

The system SHALL score a video's comments by how improbable the count is: negative binomial, with the channel's comment reference scaled by the comment curve as its mean, and the dispersion of its kind and format, the score being the z with the same upper tail. A count under a configured minimum SHALL raise nothing.

#### Scenario: Many comments where there are usually none

- **GIVEN** a channel whose long-form videos usually have about one comment at a day old
- **WHEN** one of them has forty at a day old
- **THEN** its comment score is past the threshold

#### Scenario: Under the minimum

- **GIVEN** a configured minimum of ten comments
- **WHEN** a video's comments are fewer, however improbable for its channel
- **THEN** no comment alert is raised for that reading

### Requirement: Age Comes From The Reading, In Contiguous Bands

The system SHALL take a reading's age as the interval between when it was taken and the video's publication, and score it in the contiguous age band it falls in, from three hours to eight and a half days. A reading outside every band SHALL raise nothing.

#### Scenario: A reading taken late

- **GIVEN** a video whose four-hour reading was taken at five hours and twenty minutes
- **WHEN** it is scored
- **THEN** the expectation used is the one for its real age

#### Scenario: Too early

- **GIVEN** a reading of a video two hours old
- **WHEN** the scoring pass runs
- **THEN** no score is produced for that reading

#### Scenario: Too late

- **GIVEN** a reading of a video ten days old
- **WHEN** the scoring pass runs
- **THEN** no alert is raised from it

### Requirement: The Reference Is The Channel's Newest Videos Where It Has Enough

The system SHALL take a channel's reference for a format and signal as the median of the value reached at the reference age, 21 to 30 hours, by its configured number of newest videos of that format, needing a configured minimum and none older than a configured age; for comments the mean, zeros included. It SHALL be recomputed at every refresh.

#### Scenario: A channel that has grown

- **GIVEN** a channel whose newest twenty long-form videos reach far more than its videos did half a year ago
- **WHEN** baselines are refreshed
- **THEN** its reference is taken from those twenty, and an ordinary new video scores near zero

#### Scenario: A video does not measure itself

- **GIVEN** a video whose readings take part in fitting a spread
- **WHEN** baselines are refreshed
- **THEN** its residuals are measured against a reference built only from videos past the reference age before it was published

### Requirement: Too Few Newest Videos Fall Back To The Settled Ones

Where a channel has too few newest videos of a format, the system SHALL use for views and likes the median of its settled videos of that format — one reading each, the latest taken between 29 and 120 days of age by default — needing a configured minimum, scaled by its kind's factor. Comments SHALL take no fallback. A channel on the fallback SHALL be recorded as such.

#### Scenario: A channel uploading twice a month

- **GIVEN** a channel with three long-form videos watched from publication and fifteen read at 29 to 120 days of age
- **WHEN** baselines are refreshed
- **THEN** its long-form views and likes are referenced to the settled median times its kind's factor
- **AND** the baseline records that it is the fallback
- **AND** no comment reference is given

#### Scenario: One reading per settled video

- **GIVEN** a settled video read at 40, 65 and 90 days of age
- **WHEN** the settled median is taken
- **THEN** the video contributes its reading at 90 days alone

### Requirement: A Channel Without Enough History Is Not Scored, And Is Reported

The system SHALL score only seeds labelled English or Russian with a kind, and of those only channels with a reference for the video's format. Every refresh and every scoring pass SHALL report how many channels are in scope, how many have a baseline, and how many are on the fallback.

#### Scenario: Another language

- **GIVEN** a seed labelled `other` with a video far above its channel's usual
- **WHEN** the scoring pass runs
- **THEN** the video is not scored and no alert is raised

#### Scenario: A thin channel

- **GIVEN** a channel with too few newest videos and too few settled ones of a format
- **WHEN** its videos of that format are scored
- **THEN** no score is produced, and it is counted among the channels without a baseline

### Requirement: Baselines Are Stored Per Run And Carry Their Parameters

The system SHALL store each refresh's references, curves, factors, spreads and dispersions as one run with the parameters it used, readable only once complete, the newer run replacing the older for scoring. Spreads SHALL be per age band, a band keeping its own only past configured numbers of readings and of distinct videos; a kind too thin SHALL borrow its format's pooled fit, recorded as borrowed.

#### Scenario: A refresh that dies half way

- **GIVEN** a refresh interrupted before it completes
- **WHEN** the scoring pass runs
- **THEN** it scores against the previous complete run

#### Scenario: A band seen on few videos

- **GIVEN** an age band with enough readings but fewer distinct videos than the configured minimum
- **WHEN** baselines are refreshed
- **THEN** no spread of its own is stored, and a reading in it is divided by the pooled spread

#### Scenario: A thin kind

- **GIVEN** a channel kind with too few short-form videos for a curve of its own
- **WHEN** baselines are refreshed
- **THEN** its channels take the short-form curve pooled across kinds, and the run records it as borrowed

### Requirement: One Video Raises One Alert Per Signal

The system SHALL raise at most one alert per video and signal, under the signal that scored highest across the video's readings, naming the reading that score came from. A later crossing on another signal SHALL be an alert of its own; a second pass over the same readings SHALL raise nothing new.

#### Scenario: Views and likes together

- **GIVEN** a video past the threshold on views and on likes, likes the higher
- **WHEN** the scoring pass runs
- **THEN** one alert is raised, a like alert, naming the reading with the highest like score

#### Scenario: Scoring again

- **GIVEN** a completed scoring pass
- **WHEN** it runs again over the same readings
- **THEN** no alert is added

### Requirement: A Channel's Spikes Within A Window Are One Event

The system SHALL treat every spike of one channel that crosses within a configured window of that channel's first, 24 hours by default, as one event: the first raised as its head, the others recorded as folded into it and not sent on their own. Spikes SHALL be ordered by when they first crossed, then by score.

#### Scenario: A batch of talks

- **GIVEN** a conference channel whose six talks published together all cross the threshold within a day
- **WHEN** the scoring pass runs
- **THEN** one alert is raised as the head and the five others are folded into it

#### Scenario: Two days apart

- **GIVEN** a channel whose head was raised 30 hours ago
- **WHEN** another of its videos crosses
- **THEN** it is raised as a new head

### Requirement: Thresholds Are YouTube's Own Settings

The system SHALL take YouTube's score threshold, comment minimum and fold window as settings of their own, apart from Telegram's, with defaults chosen from the volume a replay of YouTube's data produced. One threshold SHALL apply to both languages.

#### Scenario: Telegram's threshold moves

- **WHEN** Telegram's score threshold is changed
- **THEN** no YouTube score crosses or fails to cross because of it

### Requirement: Scoring Can Be Replayed Without Writing

The system SHALL score stored readings as of past moments, refitting the baselines in memory as of the start of each day it scores, and report what would have been raised and folded, per language, writing nothing and sending nothing. It MUST use the scoring pass's own code, and a replay of the present SHALL name the videos the live pass names.

#### Scenario: A replay of two weeks

- **WHEN** a replay runs over the last fourteen days with a threshold other than the configured one
- **THEN** it reports per language and day what would have been raised and folded at that threshold
- **AND** no baseline run and no alert is written

#### Scenario: A crossing followed to its newest reading

- **GIVEN** a replayed video that crossed at eight hours old
- **WHEN** its newest reading, five days old, is still three times its expectation
- **THEN** the replay reports it as held
- **AND** one whose newest reading that old is back near its expectation as faded, and one with no reading three days old as too early

#### Scenario: Several thresholds in one run

- **WHEN** a replay is given several thresholds and a comment minimum
- **THEN** it reports each threshold's volume and outcomes, the baselines fitted once

#### Scenario: Settled readings taken after the day, on request

- **GIVEN** settled readings first taken yesterday, and a video that crossed five days ago
- **WHEN** a replay is asked to let each day's fit take settled readings taken later
- **THEN** the video crosses on its own day, and the report says settled readings taken later were used
- **AND** without the request it cannot cross before those readings exist

#### Scenario: The day's own baselines

- **GIVEN** a video published on the fifth day of a replay
- **WHEN** it is replayed
- **THEN** it is scored against baselines fitted from what was stored before that day began

### Requirement: Scoring Reads Only Collected Data

The scoring pass and the refresh SHALL make no request to YouTube, book no quota and need no API key, and SHALL change nothing but the baseline runs and the YouTube alert queue.

#### Scenario: No key

- **GIVEN** an environment with no YouTube API key and restored readings
- **WHEN** the refresh and the scoring pass run
- **THEN** both complete, and no request leaves the cluster

#### Scenario: Collected data is untouched

- **WHEN** the scoring pass runs
- **THEN** no video, reading, channel record or quota row is changed
