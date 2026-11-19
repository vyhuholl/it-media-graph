# Spec Delta

## MODIFIED Requirements

### Requirement: A List Is Delivered Once, Whatever Its Length

The bot SHALL send each undelivered list to the alert chat's Telegram topic, in as many messages as fit, each entry whole in one message, without a link preview. A message that fails SHALL count an attempt on the list and be sent again on a later pass; messages already sent SHALL NOT be sent again. A list SHALL NOT go to any other chat or topic. With no alert chat configured, a list SHALL wait undelivered.

#### Scenario: A list longer than a message

- **GIVEN** a list whose entries do not fit in 4,096 characters
- **WHEN** it is sent
- **THEN** it goes as two messages, each within the limit, every entry whole

#### Scenario: The second message fails

- **GIVEN** a list sent as two messages, the second refused
- **WHEN** the next pass comes
- **THEN** only the second is sent again, and the list counts one failed attempt

#### Scenario: Not to YouTube's chat

- **GIVEN** a list undelivered, and the English and Russian topics beside the Telegram one
- **WHEN** Telegram's list is sent
- **THEN** no message of it reaches the English or the Russian topic, or any other chat

#### Scenario: No alert chat

- **GIVEN** no alert chat configured
- **WHEN** the bot looks for work
- **THEN** no list is sent, and each stays undelivered
