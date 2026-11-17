# Spec Delta

## MODIFIED Requirements

### Requirement: Pending Mentions Record Their Sources

The system SHALL record which channels mention each pending username, as a set of distinct source channels rather than a count, each with the publication time of the first and the last of its messages that mentioned the name, and MUST keep that record re-derivable: a derivation pass over unchanged raw messages writes no new source and moves no time.

#### Scenario: A pending mention records the channel that made it

- **GIVEN** an in-scope channel mentioning a username not yet known to the inventory
- **WHEN** derivation runs
- **THEN** the username is queued for resolution
- **AND** that channel is recorded as a source of the mention

#### Scenario: Two channels mentioning the same username are two sources

- **GIVEN** two different in-scope channels each mentioning the same unknown username
- **WHEN** derivation runs
- **THEN** the username has two recorded sources

#### Scenario: One channel mentioning a username repeatedly is one source

- **GIVEN** an in-scope channel mentioning the same unknown username in several messages
- **WHEN** derivation runs
- **THEN** the username has one recorded source

#### Scenario: A source keeps when it mentioned the name

- **GIVEN** an in-scope channel that mentioned an unknown username in a message of 1 March and again in one of 9 March
- **WHEN** derivation runs
- **THEN** its source row holds 1 March as the first mention and 9 March as the last

#### Scenario: A later mention moves the last time only

- **GIVEN** a recorded source whose last mention was 9 March
- **WHEN** the channel mentions the name again on 20 March and derivation runs
- **THEN** the source's last mention is 20 March and its first is unchanged

#### Scenario: Re-derivation adds nothing

- **GIVEN** a derivation pass has already recorded the sources of a pending mention
- **WHEN** derivation runs again over the same raw messages
- **THEN** no source row is added
- **AND** the recorded sources are unchanged

#### Scenario: Sources do not outlive the mention they describe

- **GIVEN** a pending username with recorded sources
- **WHEN** the username resolves into a channel
- **THEN** its recorded sources are removed with it

#### Scenario: A rebuild clears the sources with the queue

- **WHEN** derivation is asked to rebuild from scratch
- **THEN** the recorded sources are emptied along with the edges and the pending mentions
- **AND** the pass that follows records them again from the raw layer

