## ADDED Requirements

### Requirement: Search Pool

The system SHALL book every `search.list` request against a pool of its own, and no other request against it. A search MUST NOT spend from the general pool or the poll loop's share, and no other command MAY spend from the search pool, including when its own pool is spent.

#### Scenario: A search spends its own pool

- **WHEN** a search makes a request
- **THEN** its unit is booked against the search pool
- **AND** the general pool's and the poll loop's spending for the day are unchanged

#### Scenario: A spent search pool does not reach the general pool

- **GIVEN** a search pool spent for the day and a general pool with units left
- **WHEN** a search attempts a request
- **THEN** no request is sent

## MODIFIED Requirements

### Requirement: Spending Caps

The system SHALL cap each pool's daily spending. The general pool's cap SHALL be the daily quota less a safety margin and less every share reserved for another pool. The search pool's cap SHALL be the search bucket's daily calls less the same margin. The defaults SHALL be a daily quota of 10,000 units, a search bucket of 100 calls and a margin of 10%.

#### Scenario: The general cap leaves the margin and the reserve unspent

- **GIVEN** a daily quota of 10,000 units, a 10% margin and 3,000 units reserved for the poll loop
- **WHEN** the commands of this capability spend for a day
- **THEN** together they spend at most 6,000 units that day

#### Scenario: The search cap leaves the margin unspent

- **GIVEN** a search bucket of 100 calls and a 10% margin
- **WHEN** searches spend for a day
- **THEN** they spend at most 90 calls that day
- **AND** the general cap is unchanged by them

#### Scenario: Caps that leave nothing to spend are refused

- **GIVEN** a margin and reserves that together reach the daily quota
- **WHEN** settings are loaded
- **THEN** loading fails with an error naming the settings involved

### Requirement: Quota Exhaustion

The system SHALL treat a `quotaExceeded` response as the end of the quota day for the bucket the request was charged to: the search bucket for `search.list`, the main bucket for every other method. It MUST NOT retry the request, and every command spending from that bucket SHALL refuse to start until the next quota day begins.

#### Scenario: Exhaustion stops the running command

- **WHEN** a request is answered with `quotaExceeded`
- **THEN** the command stops without retrying
- **AND** what it completed before the answer is kept and reported
- **AND** the exhaustion is recorded against the quota day and the bucket

#### Scenario: Exhaustion stops every later command until the reset

- **GIVEN** exhaustion of the main bucket recorded for the current quota day
- **WHEN** any YouTube command spending from the main bucket starts
- **THEN** it exits without making a request
- **AND** it reports the moment of the next reset in UTC and in local time

#### Scenario: The next quota day starts clean

- **GIVEN** exhaustion recorded for the previous quota day
- **WHEN** a YouTube command starts after the reset
- **THEN** it proceeds

#### Scenario: Exhaustion is not mistaken for a pool's own cap

- **GIVEN** a general pool with units remaining by its own count
- **WHEN** YouTube answers a request of the main bucket with `quotaExceeded`
- **THEN** the exhaustion stops the general pool and the poll loop's share alike

#### Scenario: A spent search bucket leaves the poll loop running

- **GIVEN** a search answered with `quotaExceeded`
- **WHEN** the poll loop, a refresh or a lookup starts the same quota day
- **THEN** it proceeds
- **AND** the next search that day exits without making a request

#### Scenario: A spent main bucket leaves search running

- **GIVEN** exhaustion of the main bucket recorded for the current quota day
- **WHEN** a search starts
- **THEN** it proceeds within the search pool

### Requirement: Quota Report

The system SHALL provide `itgraph yt quota`, reporting the current quota day's spending per pool against its cap, whether each bucket is exhausted for the day, the moment of the next reset in UTC and in local time, and the day's spending by command and method. It makes no request.

#### Scenario: Today's position is reported

- **WHEN** the command runs
- **THEN** it reports units spent and remaining per pool, the search pool included
- **AND** the next reset in UTC and in local time
- **AND** units spent per command and method for the day

#### Scenario: Exhaustion is reported per bucket

- **GIVEN** the search bucket exhausted today and the main bucket not
- **WHEN** the command runs
- **THEN** it says the search bucket is exhausted and the main bucket is not

#### Scenario: Reporting costs nothing

- **WHEN** the command runs
- **THEN** no request to YouTube is made

### Requirement: Requests Read Only Declared Fields

Every request SHALL name the resource parts and the fields within them that it reads, and those SHALL be no more than the privacy policy published with the project lists as read from YouTube, together with the token a listing needs to reach its next page. Data a request did not ask for MUST NOT be stored.

#### Scenario: A channel request asks for the declared fields only

- **WHEN** a YouTube command asks for channels' data — an import, a refresh or the poll loop
- **THEN** the request names the channel id, title, handle, description, declared country and language, the public subscriber, video and view counts, and the uploads playlist id
- **AND** no branding, status or topic data

#### Scenario: A video request asks for the declared fields only

- **WHEN** the poll loop asks for videos
- **THEN** the request names the video id, its channel id, title, description, publication time, duration, broadcast state and times, and the view, like and comment counts
- **AND** no tags, thumbnails, category or localizations

#### Scenario: An uploads listing asks for ids and dates only

- **WHEN** the poll loop lists a channel's uploads
- **THEN** the request names each item's video id and publication time and nothing else

#### Scenario: A walk's listing pages through the uploads

- **WHEN** the history walk lists a channel's uploads
- **THEN** the request names each item's video id and publication time and the token for the next page
- **AND** no total count of the playlist's items

#### Scenario: A linked video is asked for its channel only

- **WHEN** a lookup asks for linked videos
- **THEN** the request names each video's id and its channel's id
- **AND** no title, description, publication time, duration, broadcast data or counts

#### Scenario: A linked handle or legacy name is asked for the channel id only

- **WHEN** a lookup asks for the channel behind a handle or a legacy name
- **THEN** the request names the channel's id and nothing else

#### Scenario: Channel sections are asked for their channel ids only

- **WHEN** the refresh asks for a seed's channel sections
- **THEN** the request names the channel ids each section lists and nothing else
- **AND** no section type, title, playlist or position

#### Scenario: A search reads each result's channel id only

- **WHEN** a search is made
- **THEN** the request names each result's channel id and the token for the next page
- **AND** no title, description, thumbnail, channel title, publication time or video id

#### Scenario: Channel data stored before this rule is trimmed

- **GIVEN** channel data stored with branding, status or topic data in it
- **WHEN** the schema is migrated to the version carrying this rule
- **THEN** the stored channel data holds only the declared fields
