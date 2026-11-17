# Spec Delta

## REMOVED Requirements

### Requirement: Polling Spends No Quota-Bearing Request

**Reason**: The poll loop now resolves the head of Telegram's rising candidates under a cap, so "a running loop resolves nothing" no longer holds of the loop, while it still holds of every poll. Replaced by "Polls Spend No Quota-Bearing Request", which keeps the rule for polls and says what the loop may resolve.

**Migration**: Nothing for an operator to do. A `ResolveUsernameRequest` recorded under `watch` is now expected within the cap; past it, it is a regression, as before (telegram-rising-candidates).

## ADDED Requirements

### Requirement: Polls Spend No Quota-Bearing Request

The system SHALL poll a channel using its peer from the session's entity cache alone. A poll MUST NOT resolve a username and MUST NOT request extended channel information. The loop SHALL request no extended information at all, and resolve nothing but the head of the rising ranking under its cap (telegram-rising-candidates).

#### Scenario: The peer comes from the session's own cache

- **GIVEN** an in-scope channel whose peer the session can supply
- **WHEN** it is polled
- **THEN** the peer is obtained from the session's entity cache
- **AND** no username is resolved

#### Scenario: A channel with no cached peer is skipped rather than resolved

- **GIVEN** an in-scope channel whose peer the session cannot supply
- **WHEN** the loop reaches it
- **THEN** no username resolution is requested
- **AND** the channel is skipped and counted as such
- **AND** the loop continues with the remaining channels

#### Scenario: A loop with nothing rising resolves nothing, however long it runs

- **GIVEN** no rising candidate awaiting an identity
- **WHEN** the loop has polled every in-scope channel
- **THEN** it has issued no username resolution and no extended-information request

#### Scenario: A loop with rising candidates resolves within the cap

- **GIVEN** rising candidates awaiting an identity
- **WHEN** the loop runs for a day
- **THEN** its resolutions are of rising candidates only, and fewer than the cap leaves
- **AND** it has issued no extended-information request
