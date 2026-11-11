# Spec Delta

## MODIFIED Requirements

### Requirement: Nothing Is Silently Withheld

The system SHALL bound how many alerts are sent directly to each destination within a period. Alerts beyond that bound MUST be delivered in that destination's summary rather than discarded, and the summary MUST say how many it covers.

#### Scenario: Alerts beyond the cap are held, not dropped

- **GIVEN** the direct cap of a destination for the period has been reached
- **WHEN** a further alert for that destination is raised
- **THEN** it is not sent directly
- **AND** it remains undelivered rather than being marked delivered

#### Scenario: Held alerts are delivered as a summary

- **WHEN** a destination's summary is due
- **THEN** every alert held for it since the last one is included, however many there are
- **AND** the summary states how many alerts it covers

#### Scenario: A summary covering nothing is not sent

- **GIVEN** no alerts were held for a destination
- **WHEN** its summary would be due
- **THEN** no message is sent

#### Scenario: The summary is due at its hour in the operator's zone

- **GIVEN** the summary's hour is 09:00 and the watched zone is Moscow's
- **WHEN** it is 09:30 in Moscow, 06:30 UTC
- **THEN** the summary is due
- **AND** at 08:30 in Moscow it is not, though the hour has passed in UTC

### Requirement: Quiet Hours Hold Rather Than Drop

The system SHALL send no alert directly during its destination's configured quiet window. Alerts raised during it MUST be held for that destination's summary and delivered afterwards, rather than discarded.

#### Scenario: An alert raised at night is not sent at night

- **GIVEN** the current time falls inside the destination's quiet window
- **WHEN** an alert for it is raised
- **THEN** no message is sent

#### Scenario: The night's alerts arrive in the morning

- **WHEN** the quiet window ends
- **THEN** the alerts raised during it are delivered
- **AND** none of them was discarded

#### Scenario: Quiet hours can be switched off

- **GIVEN** a quiet window configured as empty
- **WHEN** an alert is raised at any hour
- **THEN** it is delivered directly

### Requirement: The Bot Reaches Only The Operator

The system SHALL send each alert to the destination configured for it: a chat, and a topic within it or none. It MUST NOT send to any other chat or topic, and MUST NOT act on a message from a chat it is not configured for.

#### Scenario: Alerts go to the configured recipient

- **WHEN** an alert is delivered
- **THEN** it is sent to the chat and topic configured for its route and to no other

#### Scenario: A message from a stranger is ignored

- **GIVEN** a message from a chat that is not a configured one
- **WHEN** the bot receives it
- **THEN** no command is executed
- **AND** no information about the inventory is sent in reply

#### Scenario: Any topic of a configured chat

- **GIVEN** a command sent in the General topic of the YouTube chat
- **WHEN** the bot receives it
- **THEN** it is answered, in that topic

### Requirement: The Bot Cannot Write Collection State

The system SHALL confine the bot's writes to the delivery state of the alert queues. The bot MUST NOT modify the channel inventory, the raw layer, metric snapshots, derived edges, collection progress, YouTube's stored data, or what an alert is about and the score that raised it, and this SHALL be enforced by the database's own permissions rather than by convention.

#### Scenario: The bot's credentials cannot write collection tables

- **WHEN** the bot attempts to write to the inventory, the raw layer, the snapshots, the derived edges, or YouTube's payloads and readings
- **THEN** the database refuses it

#### Scenario: The bot can record what it is for

- **WHEN** the bot marks an alert of either queue delivered, held, failed or excluded
- **THEN** the write succeeds

#### Scenario: The bot cannot raise or rewrite a YouTube alert

- **WHEN** the bot attempts to insert a YouTube alert, or to change the video, the reading, the kind or the score of one
- **THEN** the database refuses it

### Requirement: The Bot Reports Its Own State

The system SHALL let the operator ask, in each chat the bot is configured for, what the alerting of that chat's platform is doing, so that quiet and broken are distinguishable without a database client. The answer SHALL come in the topic it was asked in.

#### Scenario: Quiet is distinguishable from broken

- **WHEN** the operator asks for status in the Telegram chat
- **THEN** the reply says when the detection pass last ran, how many alerts it has raised, how many are undelivered, and of those how many are held for the summary

#### Scenario: Stale evidence is reported

- **WHEN** the operator asks for status in the Telegram chat
- **THEN** the reply says how recent the derived data the detection reads is

#### Scenario: The YouTube chat

- **WHEN** the operator asks for status in the YouTube chat
- **THEN** the reply says how many YouTube alerts were raised, folded and excluded, and, per topic, how many are undelivered, held and failing
- **AND** when YouTube's last poll pass completed
- **AND** while no acceptance of derived metrics is recorded, that YouTube is not scored

#### Scenario: Asked in a topic

- **WHEN** the operator asks for status in the Russian topic
- **THEN** the reply is in the Russian topic

## ADDED Requirements

### Requirement: Each Destination Keeps Its Own Account

The direct cap, the quiet window and the summary SHALL be kept per destination. An alert sent to one destination SHALL NOT count towards another's cap, and a destination's summary SHALL cover only the alerts held for it.

#### Scenario: A burst on Telegram

- **GIVEN** the Telegram chat's cap reached today
- **WHEN** a YouTube alert is raised outside quiet hours
- **THEN** it is sent directly

#### Scenario: One topic's cap

- **GIVEN** the English topic's cap reached today
- **WHEN** an alert for the Russian topic is raised outside quiet hours
- **THEN** it is sent directly
- **AND** a further English alert is held for the English topic's summary

#### Scenario: Two summaries

- **GIVEN** alerts held for the English topic and for the Russian topic
- **WHEN** the summaries are due
- **THEN** each topic receives a summary of its own alerts alone

### Requirement: A Held Alert Does Not Delay Others

An alert held for a summary, because it was raised in its destination's quiet window or beyond its cap, SHALL stay held until that summary, and SHALL NOT delay the direct sending of any alert raised after it.

#### Scenario: A busy night

- **GIVEN** twelve alerts held overnight for a destination whose summary is at 09:00
- **WHEN** an alert for it is raised at 08:00, under the cap and outside the quiet window
- **THEN** it is sent directly at once

#### Scenario: Held stays held

- **GIVEN** an alert held because the cap was reached
- **WHEN** the cap's period moves on and leaves room before the summary
- **THEN** the alert is not sent directly, and arrives in the summary

### Requirement: A Summary Arrives Whatever Its Length

A summary longer than one Telegram message SHALL be sent in parts, each within the message limit and each entry whole, the first stating how many alerts the whole covers. A part that fails SHALL count as a failed attempt for each alert in it and be sent again; a part already sent SHALL NOT be.

#### Scenario: Thirty held alerts

- **GIVEN** thirty alerts held for one destination
- **WHEN** its summary is sent
- **THEN** it arrives as several messages, each of at most 4,096 characters, with every alert in one of them
- **AND** the first says that thirty alerts are covered

#### Scenario: The second part fails

- **GIVEN** a summary of three parts whose second send fails
- **WHEN** the bot next looks for work
- **THEN** the alerts of the first and third parts are delivered and not sent again
- **AND** each alert of the second part has one more failed attempt and is sent again

#### Scenario: No alert outgrows a message

- **WHEN** any alert of either platform is rendered
- **THEN** its message is at most 4,096 characters

### Requirement: The Bot Starts Only With Chats It Can Reach

At start, the bot SHALL ask Telegram about every chat it is configured for, and SHALL refuse to start, naming the setting, when Telegram does not know the chat to the bot, or when a chat configured with topics is not a forum.

#### Scenario: A chat that is not a forum

- **GIVEN** the YouTube chat configured with topics, and a plain group behind its id
- **WHEN** the bot starts
- **THEN** it exits non-zero naming the YouTube chat's setting, before delivering anything

#### Scenario: A chat the bot is not in

- **GIVEN** a configured chat the bot is not a member of
- **WHEN** the bot starts
- **THEN** it exits non-zero naming that chat's setting

### Requirement: Buttons Under Old Alerts Are Retired

A press on a feedback button under a message sent before feedback was removed SHALL record nothing. The bot SHALL answer that feedback is no longer collected and remove the buttons from that message.

#### Scenario: A press on an old button

- **GIVEN** a message in the Telegram chat with the old two buttons
- **WHEN** someone presses one
- **THEN** nothing is written to the database
- **AND** the press is answered and the message's buttons are removed

#### Scenario: New alerts carry no buttons

- **WHEN** an alert of either platform is delivered, alone or in a summary
- **THEN** its message has no buttons

## REMOVED Requirements

### Requirement: Feedback Is Recorded From The First Alert

**Reason**: In three months of production one verdict was given, a test on the first day; the operator judged thresholds by reading alerts offline and asked for the buttons to be removed (decision of 2026-11-11).

**Migration**: A revision drops the verdict table and its type, after the full dump every migration takes first, which keeps the one row; its downgrade recreates both empty. Buttons under messages already sent are answered and removed when pressed ("Buttons Under Old Alerts Are Retired").
