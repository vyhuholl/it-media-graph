# Spec Delta

## MODIFIED Requirements

### Requirement: Nothing Joined Is Stored

The system SHALL work the links, families and ranking out of `tg_raw_messages`, the stored payloads and the lookups each time it is asked, and MUST NOT store which families link a channel, a count, a rank, or anything else joined from Telegram and the API. A record created by `--add` holds only what any record holds.

#### Scenario: Reporting writes nothing

- **WHEN** the report runs without `--add`
- **THEN** no row in the database is inserted, updated or deleted

#### Scenario: A lookup no longer current changes the next report

- **GIVEN** a channel ranked through a video's lookup
- **WHEN** that lookup grows older than `youtube_answers_fresh_days` and the report runs before the next lookup run
- **THEN** that video awaits a lookup, and the channel loses that link

