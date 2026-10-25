## ADDED Requirements

### Requirement: A Folded Alert Is Told Through Its Head

The system SHALL NOT deliver an alert folded into another as a message of its own. The head's message SHALL state how many other posts of the channel were folded into it at the moment it is rendered, so a reader learns that the whole channel was elevated rather than this one post.

A folded alert SHALL be marked delivered, as folded, once its head has been delivered, and MUST NOT be counted as outstanding while it waits for its head. A fold recorded after its head was already delivered belongs to an event the reader has been told about, and is marked delivered without a further message.

Folding is not withholding: every folded alert is recorded, stays attributable to the head it was told through, and is counted in the bot's own report, so "folded into a message you received" and "never sent" remain different facts.

#### Scenario: A folded alert is not sent on its own

- **GIVEN** an alert folded into a head
- **WHEN** the bot looks for work
- **THEN** the folded alert is not among the alerts to send

#### Scenario: The head says how many posts were folded into it

- **GIVEN** a head alert with three alerts about other posts of the same channel folded into it
- **WHEN** the head is delivered
- **THEN** its message states that three more posts of the channel were elevated with it

#### Scenario: A head with nothing folded says nothing about folding

- **GIVEN** a head alert with no alert folded into it
- **WHEN** it is delivered
- **THEN** its message makes no claim about other posts of the channel

#### Scenario: A fold is settled once its head is delivered

- **GIVEN** an alert folded into a head that has been delivered
- **WHEN** the bot next looks for work
- **THEN** the folded alert is marked delivered as folded
- **AND** no message is sent for it

#### Scenario: A fold waiting for its head is not outstanding

- **GIVEN** an alert folded into a head held by quiet hours
- **WHEN** the operator asks for status
- **THEN** the folded alert is not counted among the undelivered alerts

#### Scenario: Folded alerts do not count towards the daily cap

- **GIVEN** a head delivered directly with several alerts folded into it
- **WHEN** the daily cap is next checked
- **THEN** only the head counts as sent directly

#### Scenario: Status distinguishes folded alerts

- **WHEN** the operator asks for status
- **THEN** the reply says how many of the alerts raised were folded into another
