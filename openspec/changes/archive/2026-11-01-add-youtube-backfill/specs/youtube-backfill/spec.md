## ADDED Requirements

### Requirement: Walked Channels

The system SHALL walk the uploads of every YouTube channel with status `seed` that is not marked gone, whatever its language, and no other channel. A channel whose walk ended SHALL NOT be walked again unless the operator names it, or its walk has been forgotten.

#### Scenario: Seeds of every language are walked

- **GIVEN** seed channels with languages `en`, `ru` and `other`, none walked
- **WHEN** a walk runs
- **THEN** each of them is walked

#### Scenario: Channels outside the seeds are not walked

- **GIVEN** a channel with status `candidate`, one `maybe`, one `rejected`, and a seed marked gone
- **WHEN** a walk runs
- **THEN** none of them is walked

#### Scenario: A walked channel is not walked again

- **GIVEN** a seed whose walk ended
- **WHEN** a walk runs
- **THEN** its uploads are not listed

#### Scenario: A named channel is walked again

- **GIVEN** a seed whose walk ended
- **WHEN** the operator runs a walk naming it
- **THEN** it is walked from its newest uploads

### Requirement: Walks Are Forgotten With Their Videos

A channel whose walk is forgotten SHALL be walked anew when it is next due.

#### Scenario: A channel taken out of scope

- **GIVEN** a seed whose walk ended
- **WHEN** it is marked `maybe` or rejected
- **THEN** its walk is forgotten in the same change

#### Scenario: A channel brought back into scope

- **GIVEN** a channel whose walk was forgotten when it stopped being a seed
- **WHEN** it is made a seed again and a walk runs
- **THEN** it is walked

#### Scenario: A walk nothing kept fresh

- **GIVEN** the walk disabled
- **AND** a channel whose walk ended 31 days ago
- **WHEN** any YouTube command reaches the database
- **THEN** that walk is forgotten

#### Scenario: A walk kept fresh is kept

- **GIVEN** the walk enabled
- **AND** a channel whose walk ended 90 days ago
- **WHEN** any YouTube command reaches the database
- **THEN** that walk remains

### Requirement: Depth

The system SHALL store a video a walk finds only when it was published inside the configured depth before the moment that channel's walk began, 365 days by default, and not inside the poll loop's tracking horizon. The depth MUST lie past the horizon.

#### Scenario: A video inside the depth is stored

- **GIVEN** a depth of 365 days and a horizon of 29 days
- **WHEN** a walk finds videos published 40, 200 and 400 days before it began
- **THEN** the videos 40 and 200 days old are stored
- **AND** nothing about the one 400 days old is stored

#### Scenario: A depth short of the horizon is refused

- **GIVEN** a configured depth that does not lie past the tracking horizon
- **WHEN** settings are loaded
- **THEN** loading fails, naming both settings

### Requirement: Where A Walk Stops

A walk SHALL list a channel's uploads from the newest page and SHALL stop after the page on which an item dated earlier than the depth appears, or at the end of the playlist, whichever comes first. A walk of channels the operator names whole SHALL list them to the end of the playlist, storing only what the depth admits.

#### Scenario: The walk stops on the page that crosses the depth

- **GIVEN** a channel whose third page of uploads holds the first item older than the depth
- **WHEN** it is walked
- **THEN** three pages are listed

#### Scenario: A short playlist ends the walk

- **GIVEN** a channel with thirty uploads, all inside the depth
- **WHEN** it is walked
- **THEN** one page is listed and the walk ends

#### Scenario: A whole walk goes to the end

- **GIVEN** a channel with four pages of uploads, the first item older than the depth on its second
- **WHEN** the operator walks it whole
- **THEN** four pages are listed
- **AND** only videos inside the depth are stored

### Requirement: The Listing's Order Is Checked, Not Trusted

The system SHALL count, for each walk, every listed item dated later than an item listed before it in that walk, and every item the listing gives no date. An item without a date SHALL be asked about rather than skipped, and kept or not by what YouTube then says of it.

#### Scenario: An item out of order is counted

- **GIVEN** a listing whose second page holds an item dated later than one on its first
- **WHEN** the channel is walked
- **THEN** the walk records one item out of order

#### Scenario: An undated item is asked about

- **GIVEN** a listing with an item that carries no publication time
- **WHEN** the channel is walked
- **THEN** the item is counted as undated
- **AND** the video is asked for, and stored if YouTube reports it published inside the depth

### Requirement: A Ceiling Per Channel

A walk SHALL store at most a configured number of videos for one channel, 1,000 by default, stop there, and record the channel as capped. A ceiling of zero SHALL lift the bound.

#### Scenario: The ceiling ends the walk

- **GIVEN** a ceiling of 1,000 and a channel with 1,400 uploads inside the depth
- **WHEN** it is walked
- **THEN** 1,000 of its videos are stored and the walk ends
- **AND** the channel is recorded as capped

#### Scenario: Zero lifts the ceiling

- **GIVEN** a ceiling of zero
- **WHEN** a channel with 1,400 uploads inside the depth is walked
- **THEN** all 1,400 are stored

### Requirement: What The Poll Loop Owns Is Left To It

A walk SHALL NOT ask about a video the listing dates inside the poll loop's tracking horizon, nor about a video already stored, and MUST NOT change a stored video.

#### Scenario: A recent video is left to the poll loop

- **GIVEN** a listed video published ten days ago and not stored
- **WHEN** the walk handles the listing
- **THEN** the video is neither asked about nor stored by the walk

#### Scenario: A stored video is untouched

- **GIVEN** a video the poll loop tracks, listed by a walk
- **WHEN** the walk handles the listing
- **THEN** its stored data, its schedule and the record of how it was found are unchanged

### Requirement: Broadcasts Yet To Start Are Handed To The Poll Loop

A broadcast yet to start that a walk asks about SHALL be stored and handed to the poll loop, which SHALL check it at its scheduled start and give it up as it does the broadcasts it found itself. The walk SHALL take no reading of it.

#### Scenario: A scheduled stream dated before the horizon

- **GIVEN** a listed video dated 40 days ago whose broadcast is scheduled for tomorrow
- **WHEN** the walk handles it
- **THEN** it is stored, due to be checked at its scheduled start
- **AND** no reading of it is stored

### Requirement: Found By The Walk Is Recorded

The system SHALL record, with each video a walk stores, the moment it was found and that the history walk found it.

#### Scenario: A video found by a walk

- **WHEN** a walk stores a video
- **THEN** the video is recorded as found by the history walk, with the moment

### Requirement: Progress Is Kept Per Page

The system SHALL commit what each request returned before making the next one. A channel whose walk did not end SHALL be walked again from its newest page, no page token SHALL be carried from one run to another, and a video already stored SHALL NOT be asked for again.

#### Scenario: An interrupted walk resumes

- **GIVEN** a channel whose walk stopped after its second page
- **WHEN** a walk runs again
- **THEN** the channel is listed again from its newest page
- **AND** none of the videos stored before the stop is asked for again

#### Scenario: A failure part way keeps earlier work

- **GIVEN** a walk whose fourth request fails after every attempt
- **WHEN** it stops
- **THEN** what the first three requests returned is stored
- **AND** the walk exits with a non-zero status

### Requirement: Spending Leaves The Other Commands Their Share

A walk SHALL spend from the general pool, and SHALL make no request that would leave that pool fewer units for the day than a configured floor, 2,000 by default. A run SHALL make no more requests than a limit the operator gives. The floor MUST be below the general pool's cap.

#### Scenario: The floor stops a walk

- **GIVEN** a general pool with 2,001 units left and a floor of 2,000
- **WHEN** a walk has made one request
- **THEN** it makes no further request

#### Scenario: A run bounded by requests

- **GIVEN** a limit of 50 requests
- **WHEN** a walk runs
- **THEN** it makes at most 50 requests

#### Scenario: A floor that leaves nothing is refused

- **GIVEN** a floor at or above the general pool's cap
- **WHEN** settings are loaded
- **THEN** loading fails, naming the settings involved

### Requirement: A Stop For Quota Is Not A Failure

When the floor is reached, the general pool is spent, or YouTube has declared the quota day over, a walk SHALL keep what it completed, make no further request, and exit successfully, saying when spending can resume.

#### Scenario: The floor is reached part way

- **GIVEN** a walk that reaches the floor in the middle of a channel
- **WHEN** it stops
- **THEN** what it stored is kept
- **AND** it exits with a zero status, naming the next reset

### Requirement: One Walk At A Time

The system SHALL let only one walk run at a time. A walk started while another runs SHALL make no request and exit successfully, saying so. The walk's exclusion MUST NOT block, or be blocked by, the poll loop or the Telegram session lease.

#### Scenario: A manual walk beside the scheduled one

- **GIVEN** a walk running
- **WHEN** another is started by hand
- **THEN** the second makes no request and exits with a zero status, saying a walk is already running

#### Scenario: The poll loop runs beside a walk

- **GIVEN** a walk running
- **WHEN** a pass of the poll loop starts
- **THEN** the pass runs as it would without the walk

#### Scenario: A killed walk releases the exclusion

- **GIVEN** a walk killed part way
- **WHEN** the next walk starts
- **THEN** it runs

### Requirement: One Channel's Failure Stays With That Channel

When a channel's uploads cannot be listed because the playlist is not found or not accessible, the system SHALL record the failure against that channel and continue with the others. The channel SHALL be walked again in a later run.

#### Scenario: A listing answered not found

- **GIVEN** a seed whose uploads listing is answered not found
- **WHEN** it is walked
- **THEN** the failure is recorded against that channel
- **AND** the walk continues with the other channels

### Requirement: What A Walk Records About Itself

The system SHALL record, per channel, when its walk began and ended, the moment the depth was counted back to, the pages listed and videos stored, the items out of order and undated, the listed videos YouTube did not return, whether the walk was capped, and its last failure. The record MUST NOT hold a title, a date, a count or anything else YouTube returned.

#### Scenario: A finished walk's record

- **WHEN** a channel's walk ends
- **THEN** its record holds the times, the counts of its own work and of what the API did, and no data YouTube returned

### Requirement: The Walk Derives Nothing

The walk SHALL only fetch and store. It MUST NOT compute or store an edge, a mention, a score, a classification or any other derived record, and MUST NOT change a channel's review.

#### Scenario: Only data and the walk's records are written

- **WHEN** a walk completes
- **THEN** it has written video data and its own records, and nothing else

### Requirement: Unattended Walk

The system SHALL run a walk of the due seeds once a day on the collection host, without an operator, whenever an API key is configured there and the walk is enabled, and SHALL schedule no walk otherwise. A walk started by hand SHALL run whether the walk is enabled or not.

#### Scenario: An enabled host walks daily

- **GIVEN** a collection host deployed with an API key and the walk enabled
- **THEN** a walk runs there once a day
- **AND** a run missed while the host was down runs when it is back, the same day

#### Scenario: A host with the walk disabled schedules none

- **GIVEN** a collection host deployed with an API key and the walk disabled
- **THEN** no walk is scheduled there

### Requirement: Status Report

The system SHALL provide `itgraph yt backfill-status`, reporting whether the walk is enabled; how many seeds are walked, due, interrupted, failing and capped; the totals of out-of-order, undated and not-returned items; how many stored videos walks found; and the general pool's spending against its cap and the floor. It makes no request.

#### Scenario: The order check is visible

- **GIVEN** walks that counted items out of order
- **WHEN** the command runs
- **THEN** it reports their total

#### Scenario: Reporting costs nothing

- **WHEN** the command runs
- **THEN** no request to YouTube is made

### Requirement: Logs Name Ids Only

Log records written by the walk SHALL identify channels and videos by id only, and MUST NOT carry any title, description, date or count YouTube returned.

#### Scenario: A walk at debug level

- **GIVEN** logging at debug level
- **WHEN** a walk stores videos and hands a broadcast to the poll loop
- **THEN** no log record contains a video title or description
