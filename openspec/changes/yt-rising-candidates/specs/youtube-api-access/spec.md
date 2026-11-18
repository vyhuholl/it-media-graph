# Spec Delta

## MODIFIED Requirements

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

#### Scenario: A past upload is asked for as the poll loop asks

- **WHEN** the history walk stores a past upload, or the refresh fetches a past upload's data again
- **THEN** the request names the video's fields as the poll loop's does, counts included

#### Scenario: A late reading asks for the counts alone

- **WHEN** the refresh takes late readings of past uploads
- **THEN** the request names each video's id and its view, like and comment counts
- **AND** no title, description, channel id, publication time, duration or broadcast data

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

#### Scenario: The chart is asked for its videos' channels only

- **WHEN** the refresh reads the chart
- **THEN** the request names each video's id, its channel's id and the token for the next page
- **AND** no title, description, publication time, duration, broadcast data, counts or category

#### Scenario: Channel data stored before this rule is trimmed

- **GIVEN** channel data stored with branding, status or topic data in it
- **WHEN** the schema is migrated to the version carrying this rule
- **THEN** the stored channel data holds only the declared fields
