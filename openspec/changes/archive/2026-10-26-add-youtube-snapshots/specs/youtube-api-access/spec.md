# Spec Delta

## MODIFIED Requirements

### Requirement: Reserved Share

The system SHALL hold a configurable share of the daily quota for the YouTube poll loop. No command other than the poll loop MAY spend from it, including when the poll loop is not running. The poll loop SHALL book every request against that share alone and MUST NOT spend from the general pool, including when its share is spent.

#### Scenario: Other commands cannot reach the reserve

- **GIVEN** a reserved share for the poll loop
- **AND** a general pool that is spent for the day
- **WHEN** an import or a refresh attempts a request
- **THEN** no request is sent, although the reserve is untouched

#### Scenario: The poll loop spends its own share

- **WHEN** the poll loop makes a request
- **THEN** its units are booked against the poll loop's share
- **AND** the general pool's spending for the day is unchanged

#### Scenario: The poll loop cannot reach the general pool

- **GIVEN** a poll loop share that is spent for the day
- **AND** a general pool with units remaining
- **WHEN** the poll loop attempts a request
- **THEN** no request is sent

#### Scenario: Both pools are reported

- **WHEN** the quota report runs
- **THEN** it shows the general pool and the poll loop's share, each against its own cap

## ADDED Requirements

### Requirement: Requests Read Only Declared Fields

Every request SHALL name the resource parts and the fields within them that it reads, and those SHALL be no more than the privacy policy published with the project lists as read from YouTube. Data a request did not ask for MUST NOT be stored.

#### Scenario: A channel request asks for the declared fields only

- **WHEN** a YouTube command asks for channels
- **THEN** the request names the channel id, title, handle, description, declared country and language, the public subscriber, video and view counts, and the uploads playlist id
- **AND** no branding, status or topic data

#### Scenario: A video request asks for the declared fields only

- **WHEN** the poll loop asks for videos
- **THEN** the request names the video id, its channel id, title, description, publication time, duration, broadcast state and times, and the view, like and comment counts
- **AND** no tags, thumbnails, category or localizations

#### Scenario: An uploads listing asks for ids and dates only

- **WHEN** the poll loop lists a channel's uploads
- **THEN** the request names each item's video id and publication time and nothing else

#### Scenario: Channel data stored before this rule is trimmed

- **GIVEN** channel data stored with branding, status or topic data in it
- **WHEN** the schema is migrated to the version carrying this rule
- **THEN** the stored channel data holds only the declared fields
