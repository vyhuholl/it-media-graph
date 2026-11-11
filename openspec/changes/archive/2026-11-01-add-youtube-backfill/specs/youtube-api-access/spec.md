## MODIFIED Requirements

### Requirement: Requests Read Only Declared Fields

Every request SHALL name the resource parts and the fields within them that it reads, and those SHALL be no more than the privacy policy published with the project lists as read from YouTube, together with the token a listing needs to reach its next page. Data a request did not ask for MUST NOT be stored.

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

#### Scenario: A walk's listing pages through the uploads

- **WHEN** the history walk lists a channel's uploads
- **THEN** the request names each item's video id and publication time and the token for the next page
- **AND** no total count of the playlist's items

#### Scenario: Channel data stored before this rule is trimmed

- **GIVEN** channel data stored with branding, status or topic data in it
- **WHEN** the schema is migrated to the version carrying this rule
- **THEN** the stored channel data holds only the declared fields
