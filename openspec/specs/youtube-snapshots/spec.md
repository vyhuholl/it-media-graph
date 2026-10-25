# youtube-snapshots Specification

## Purpose
The YouTube poll loop: finding the new uploads of the channels in scope, and reading each video's public counters at increasing ages, so that what a channel's videos normally reach at a given age can later be measured. It collects and stores; it judges nothing.

## Requirements

### Requirement: Watched Channels

The system SHALL watch every YouTube channel with status `seed`, whatever its language, and no other. A channel that stops being a seed SHALL stop being watched, and its videos SHALL stop being read.

#### Scenario: Seeds of every language are watched

- **GIVEN** seed channels with languages `en`, `ru` and `other`
- **WHEN** a pass runs
- **THEN** each of them is watched

#### Scenario: Undecided and rejected channels are not watched

- **GIVEN** a channel with status `maybe`, one `candidate` and one `rejected`
- **WHEN** a pass runs
- **THEN** none of them is asked about by the poll loop

#### Scenario: A channel taken out of scope

- **GIVEN** a seed channel with videos being read
- **WHEN** it is marked `maybe` or rejected
- **THEN** the next pass reads none of its videos and does not list its uploads

### Requirement: New Uploads Are Noticed By The Video Count

Each pass SHALL ask for every watched channel by id, fifty channels to a request, and SHALL list a channel's uploads only when its public video count differs from the count stored before the pass, in either direction, or when no count was stored. A listing SHALL read the newest page of the channel's uploads.

#### Scenario: Channels share requests

- **GIVEN** a hundred watched channels
- **WHEN** a pass runs
- **THEN** two requests ask for the channels

#### Scenario: An unchanged count costs no listing

- **GIVEN** a channel whose video count is the one stored before the pass
- **AND** whose daily check is not due
- **WHEN** a pass runs
- **THEN** its uploads are not listed

#### Scenario: A changed count is listed

- **GIVEN** a channel whose video count has risen, or fallen, since the stored one
- **WHEN** a pass runs
- **THEN** its uploads are listed in that pass

#### Scenario: The channel data is refreshed in passing

- **WHEN** a pass asks for a watched channel
- **THEN** the channel's stored data is replaced by the answer, as a refresh would replace it

### Requirement: Every Channel Is Checked Daily

The system SHALL list the uploads of every watched channel at least once per configurable interval, 24 hours by default, whatever its video count says. A listing made for any reason SHALL count as that check.

#### Scenario: A channel whose count did not move is still listed

- **GIVEN** a watched channel whose uploads were last listed more than 24 hours ago
- **AND** whose video count has not changed
- **WHEN** a pass runs
- **THEN** its uploads are listed

#### Scenario: A listing caused by the count postpones the check

- **GIVEN** a channel listed two hours ago because its count changed
- **WHEN** a pass runs with its count unchanged
- **THEN** its uploads are not listed

### Requirement: What Counts As A New Video

The system SHALL start tracking a listed video when no video with its id is tracked, the listing dates it inside the tracking horizon or gives no date, and YouTube then reports it published inside the horizon or as a broadcast yet to start. A channel's first listing SHALL therefore start tracking every video it published inside the horizon. A video dated older SHALL NOT be tracked, and nothing about it SHALL be stored.

#### Scenario: A fresh upload is tracked

- **GIVEN** a listing that contains a video published an hour ago and not tracked
- **WHEN** the pass handles it
- **THEN** the video is tracked from this pass

#### Scenario: A channel's first listing brings its recent videos

- **GIVEN** a channel watched for the first time, with videos published 2, 10 and 40 days ago
- **AND** a horizon of 29 days
- **WHEN** its uploads are first listed
- **THEN** the videos 2 and 10 days old are tracked
- **AND** the video 40 days old is not, and nothing about it is stored

#### Scenario: A broadcast the listing dates before the horizon

- **GIVEN** a listed video the listing dates 40 days ago, whose broadcast has yet to start
- **WHEN** the pass handles the listing
- **THEN** the video is not asked about and not tracked
- **AND** nothing about it is stored

#### Scenario: A video whose data expired is not taken for new

- **GIVEN** a video tracked earlier, past the horizon, whose stored data has since expired
- **WHEN** a listing contains it again
- **THEN** it is not tracked again

### Requirement: Discovery Is Recorded

The system SHALL record, with each tracked video, when it was found and by which path: a changed video count, the daily check, or the channel's first listing.

#### Scenario: A video found by the daily check

- **GIVEN** a video the video count did not reveal
- **WHEN** the daily check lists it
- **THEN** the video is recorded as found by the daily check, with the moment

### Requirement: One Request Serves Payload And Reading

The system SHALL read a video's data and its counters from the same request. The request that starts tracking a video SHALL take its first reading, and every reading SHALL replace the video's stored data.

#### Scenario: Discovery takes the first reading

- **WHEN** a video starts being tracked
- **THEN** a reading of it is stored in the same pass, with no separate request for its counters

#### Scenario: A reading refreshes the stored video

- **WHEN** a tracked video is read
- **THEN** its stored data is replaced by the answer and its fetch time is the moment of the reading

#### Scenario: Videos of different channels share requests

- **GIVEN** sixty videos of several channels due in one pass
- **WHEN** they are read
- **THEN** two requests are made

### Requirement: Readings Are Observations

The system SHALL record each reading as one row per video per moment, holding the views, likes and comments as YouTube reported them, and SHALL never update a reading. A counter YouTube did not report SHALL be recorded as absent, not as zero. All videos in one response SHALL carry that response's moment.

#### Scenario: A second reading is a second row

- **GIVEN** a video with one reading
- **WHEN** it is read again
- **THEN** a second reading is stored and the first is unchanged

#### Scenario: A hidden counter is absent, not zero

- **GIVEN** a video whose answer carries a view count and no like count
- **WHEN** it is read
- **THEN** the reading holds the views and records the likes as absent

#### Scenario: Counts beyond two billion

- **GIVEN** a video with more than 2,147,483,647 views
- **WHEN** it is read
- **THEN** the reading holds the exact count

### Requirement: Reading Schedule

The system SHALL read a tracked video at configured ages, by default 1, 2, 4, 8, 12, 18, 24 and 36 hours and 2, 3, 4, 5, 7, 10, 14, 21 and 28 days, and not past a configured horizon, 29 days by default, which MUST lie past the last age. A reading SHALL be taken at the first configured age after the video's current age. A reading whose moment passed unread SHALL NOT be taken.

#### Scenario: A video is read at the next age it has not reached

- **GIVEN** a video 3 hours old, last read at 2 hours
- **WHEN** its next reading is scheduled
- **THEN** it is due at 4 hours

#### Scenario: A long gap produces no backlog

- **GIVEN** passes that did not run for 10 hours
- **AND** a video whose readings at 2, 4 and 8 hours fell inside that gap
- **WHEN** passes resume
- **THEN** the video is read once, and next at 12 hours
- **AND** no reading is taken for 2, 4 or 8 hours

#### Scenario: Reading ends at the horizon

- **GIVEN** a video older than the horizon
- **WHEN** a pass runs
- **THEN** it is not read

#### Scenario: A horizon short of the last age is refused

- **GIVEN** a configured horizon that does not lie past the last configured age
- **WHEN** settings are loaded
- **THEN** loading fails, naming both settings

### Requirement: The Age Of A Reading

The system SHALL measure a video's age from the actual start of its broadcast when it has one, and from its publication time otherwise. Every reading SHALL carry the moment it was taken, so that its age is obtainable from the reading and the video's stored data alone, never from which age in the schedule it was meant for.

#### Scenario: A finished stream is aged from its start

- **GIVEN** a video whose broadcast actually started two hours after its publication time
- **WHEN** its readings are scheduled
- **THEN** the ages are counted from the broadcast's start

### Requirement: Upcoming Broadcasts Are Not Read

The system SHALL take no reading of a video whose broadcast has not started. It SHALL check such a video at its scheduled start and then hourly until it starts, and SHALL stop checking it 7 days after its scheduled start if it has not started. While it waits, its stored data SHALL be fetched again before the refresh age.

#### Scenario: A scheduled stream is tracked but not read

- **GIVEN** a listed video whose broadcast is scheduled for tomorrow
- **WHEN** the pass handles it
- **THEN** it is tracked and no reading of it is stored

#### Scenario: The stream starts

- **GIVEN** a tracked upcoming video
- **WHEN** a check finds its broadcast live
- **THEN** a reading is stored and its schedule runs from the actual start

#### Scenario: A stream that never starts is given up

- **GIVEN** an upcoming video whose scheduled start was 7 days ago
- **WHEN** a pass runs
- **THEN** it is not checked again

#### Scenario: A stream scheduled far ahead keeps its data current

- **GIVEN** an upcoming video scheduled to start in 40 days
- **WHEN** its stored data reaches the refresh age
- **THEN** it is fetched again, with no reading stored

### Requirement: Every Format Is Collected And None Is Classified

The system SHALL track long-form videos, Shorts, streams and premieres alike, and MUST NOT store any classification of a video's format. It SHALL NOT use undocumented playlists or any request outside the YouTube Data API to tell formats apart.

#### Scenario: A short video is tracked like any other

- **GIVEN** a listing that contains a 40-second video
- **WHEN** the pass handles it
- **THEN** it is tracked and read on the same schedule as any other

#### Scenario: No format is recorded

- **WHEN** a tracked video is read
- **THEN** nothing stored says whether it is a Short, a stream or a premiere beyond what YouTube's own answer holds

### Requirement: A Video That Leaves The Channel

The system SHALL attribute a video to the channel YouTube currently reports for it, and SHALL stop reading a video whose channel is not watched.

#### Scenario: A video moved to an unwatched channel

- **GIVEN** a tracked video whose answer now names a channel that is not watched
- **WHEN** it is read
- **THEN** that reading is stored and no further reading is scheduled

### Requirement: Spending Is Paced Across The Quota Day

The system SHALL compare the poll loop's spending in the current quota day with the share of its cap the elapsed part of that day allows, plus a configured margin. When spending is ahead of it, a pass SHALL defer the daily check and the readings of videos older than 7 days, and SHALL proceed with everything else.

#### Scenario: Ahead of pace, the oldest work waits

- **GIVEN** a quota day a quarter elapsed and a poll pool already half spent
- **WHEN** a pass runs
- **THEN** it lists no channel for its daily check and reads no video older than 7 days
- **AND** it still notices new uploads and reads the younger videos due

#### Scenario: Deferred work is counted

- **WHEN** a pass defers work
- **THEN** the number of deferred checks and readings is reported

### Requirement: A Spent Pool Ends A Pass Quietly

When the poll loop's pool is spent, or YouTube has declared the quota day over, a pass SHALL make no further request, keep what it completed, and exit successfully, saying when spending can resume. Expiry SHALL still run.

#### Scenario: The pool runs out part way

- **GIVEN** a pass whose pool is spent after its first requests
- **WHEN** the next request is refused by the ledger
- **THEN** the pass stops, keeping what it stored
- **AND** exits with a zero status, naming the next reset

#### Scenario: A pass after exhaustion

- **GIVEN** a quota day YouTube declared over
- **WHEN** a pass starts
- **THEN** expired data is removed, no request is made, and the pass exits with a zero status

### Requirement: One Pass At A Time

The system SHALL let only one pass run at a time. A pass started while another runs SHALL make no request and exit successfully, saying so. The poll loop's exclusion MUST NOT block, or be blocked by, the Telegram session lease.

#### Scenario: A manual pass beside the scheduled one

- **GIVEN** a pass running
- **WHEN** another pass is started by hand
- **THEN** the second makes no request and exits with a zero status, saying a pass is already running

#### Scenario: Telegram commands are unaffected

- **GIVEN** a running pass
- **WHEN** a Telegram command that takes the session lease starts
- **THEN** it is not refused on account of the pass

#### Scenario: A killed pass releases the exclusion

- **GIVEN** a pass killed part way
- **WHEN** the next pass starts
- **THEN** it runs

### Requirement: Progress Is Kept Per Request

The system SHALL commit what each request returned before making the next one, so that a pass that stops for any reason keeps everything before its last request, and the next pass continues from the stored state.

#### Scenario: A failure part way keeps earlier work

- **GIVEN** a pass whose third request fails after every attempt
- **WHEN** it stops
- **THEN** what the first two requests returned is stored
- **AND** the pass exits with a non-zero status

### Requirement: One Channel's Failure Stays With That Channel

The system SHALL continue a pass when one channel's uploads cannot be listed, recording the failure against that channel and listing it again in a later pass.

#### Scenario: A listing answered not found

- **GIVEN** a watched channel whose uploads listing is answered not found
- **WHEN** the pass lists it
- **THEN** the failure is recorded against that channel
- **AND** the pass continues with the other channels

### Requirement: The Loop Derives Nothing

The system SHALL confine the poll loop to fetching and storing. It MUST NOT compute or store a score, a classification, an alert or any other derived record, and MUST NOT change a channel's review.

#### Scenario: Only data and readings are written

- **WHEN** a pass completes
- **THEN** it has written channel data, video data, readings and the loop's own schedule, and nothing else

### Requirement: Unattended Polling

The system SHALL run a pass on the collection host every fifteen minutes without an operator whenever an API key is configured there, and SHALL schedule nothing on a host without one.

#### Scenario: A host with a key polls

- **GIVEN** a collection host deployed with an API key
- **THEN** a pass runs there every fifteen minutes

#### Scenario: A host without a key schedules nothing

- **GIVEN** a collection host deployed without an API key
- **THEN** no pass is scheduled there

### Requirement: Status Report

The system SHALL provide `itgraph yt watch-status`, reporting the number of tracked videos, how many readings are due and the oldest overdue one, the last pass and what it deferred or could not do, the poll pool's spending against its cap and its pace, and, over the last 7 days, how many videos each discovery path found. It makes no request.

#### Scenario: What the count missed is visible

- **GIVEN** videos found over the last week by a changed count and by the daily check
- **WHEN** the command runs
- **THEN** it reports how many each path found

#### Scenario: Reporting costs nothing

- **WHEN** the command runs
- **THEN** no request to YouTube is made

### Requirement: Logs Name Ids Only

Log records written by the poll loop SHALL identify channels and videos by id only, and MUST NOT carry any title, description or count YouTube returned.

#### Scenario: A pass at debug level

- **GIVEN** logging at debug level
- **WHEN** a pass tracks a new video and reads others
- **THEN** no log record contains a video title or description
