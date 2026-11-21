# Spec Delta

## ADDED Requirements

### Requirement: A Daily Summary Closes The Day

Each destination SHALL be sent, once per day, a summary of the alerts it was sent directly in the day that has just ended: the period from its quiet window's end to the next start, in the destination's own zone. The summary SHALL be due as the quiet window begins, and a day with no directly delivered alert SHALL close in silence. An alert held for the digest SHALL NOT be repeated in a daily summary, and neither shall a rising offer or a weekly list, which are not alerts. The daily summary SHALL count towards no destination's cap, and which day a destination has already been sent SHALL be kept in the database, so that a restart does not send a day's summary twice.

#### Scenario: The day is the quiet window's complement

- **GIVEN** a destination whose quiet hours run from 02:00 to 07:00
- **WHEN** its summary for the 18th is sent at 02:00 on the 19th
- **THEN** it covers the alerts sent directly between 07:00 on the 18th and 02:00 on the 19th, and no alert sent before 07:00 on the 18th

#### Scenario: Held alerts are not repeated

- **GIVEN** an alert raised at 03:00, inside the quiet window, and delivered in the morning digest
- **WHEN** the day's summary is sent
- **THEN** the alert is not in it: the digest remains its summary

#### Scenario: A quiet day closes in silence

- **GIVEN** a day in which the destination was sent no alert directly
- **WHEN** the quiet window begins
- **THEN** no summary is sent

#### Scenario: The bot was down at the window's start

- **GIVEN** a day whose summary is due, and a bot that starts again at 09:00
- **WHEN** it looks for work
- **THEN** the day's summary is sent then

#### Scenario: A restart does not repeat it

- **GIVEN** a day's summary already sent, and the bot restarted at 14:00
- **WHEN** it looks for work
- **THEN** no second summary of that day is sent

#### Scenario: The summary counts nowhere

- **GIVEN** a destination that has reached its daily cap of alerts
- **WHEN** its daily summary is sent
- **THEN** the cap's count is unchanged, and no alert is held because of the summary

#### Scenario: Quiet hours switched off

- **GIVEN** a destination whose quiet window is empty: its start and end are the same hour
- **WHEN** a day ends
- **THEN** no daily summary is sent, since no day is defined

### Requirement: A Daily Summary Reads As The Day's Record

Each entry of a daily summary SHALL be one directly delivered alert, ordered by the publication time of the post it is about. An entry SHALL carry the headline its alert used — the metric and how far past normal the post went, a comment alert its count and what the channel's posts usually have, a cascade its families — then the channel and the post's clock time in the destination's zone, then the link to the post, where the channel has one, and nothing else. The message SHALL name the day it covers, show no link preview, and not exceed the message limit; a longer one SHALL go in parts, each entry whole in one part, as the digest does.

#### Scenario: A views entry

- **GIVEN** a views alert delivered directly, of a post published at 08:56, that scored z 3.7
- **WHEN** the day's summary is sent
- **THEN** its entry is the metric with its magnitude and score, the channel and 08:56, and the link to the post — and no excerpt of the post

#### Scenario: A comment entry

- **GIVEN** a comment alert delivered directly, of 68 comments where the channel's posts usually have about 2, that scored z 5.6
- **WHEN** the day's summary is sent
- **THEN** its entry states the count and the usual, beside the score

#### Scenario: A cascade entry

- **GIVEN** a cascade alert of two families delivered directly
- **WHEN** the day's summary is sent
- **THEN** its entry states the families, as the alert's headline does, and does not list the carriers

#### Scenario: One post, two entries

- **GIVEN** a post with a views alert and a cascade alert, both delivered directly
- **WHEN** the day's summary is sent
- **THEN** both entries are in it, beside one another, ordered by the post's publication time rather than by when the alerts were sent

#### Scenario: Ordered by the post, not by the alert

- **GIVEN** a post published at 09:00 whose alert was sent at 15:00, and a post published at 12:00 whose alert was sent at 12:30
- **WHEN** the day's summary is sent
- **THEN** the 09:00 post's entry comes first

#### Scenario: No preview

- **WHEN** a daily summary is sent
- **THEN** the message shows no preview of any link in it

#### Scenario: A day longer than a message

- **GIVEN** a day whose entries do not fit in 4,096 characters
- **WHEN** its summary is sent
- **THEN** it goes as several messages, each within the limit, every entry whole in one of them

## MODIFIED Requirements

### Requirement: The Bot Reaches Only The Operator

The system SHALL send each alert to the destination configured for it: a topic of the one alert chat, Telegram's or one of YouTube's two. It MUST NOT send to any other chat or topic, and MUST NOT act on a message from a chat it is not configured for.

#### Scenario: Alerts go to the configured recipient

- **WHEN** an alert is delivered
- **THEN** it is sent to the topic configured for its destination and to no other, and to no chat but the one alert chat

#### Scenario: A message from a stranger is ignored

- **GIVEN** a message from a chat that is not the configured one
- **WHEN** the bot receives it
- **THEN** no command is executed
- **AND** no information about the inventory is sent in reply

#### Scenario: Any topic of a configured chat

- **GIVEN** a command sent in the General topic of the alert chat
- **WHEN** the bot receives it
- **THEN** it is answered, in that topic

### Requirement: The Bot Cannot Write Collection State

The system SHALL confine the bot's writes to the delivery state of the alert queues, of both platforms' weekly lists and their second wind entries, of both platforms' rising offers, of the daily summaries' markers, and to an offer's decision. The bot MUST NOT modify the channel inventory of either platform, the raw layer, metric snapshots, derived edges, collection progress, YouTube's stored data, what an alert is about and the score that raised it, what a weekly list holds, or which channel an offer is and what it was offered with, and this SHALL be enforced by the database's own permissions rather than by convention.

#### Scenario: The bot's credentials cannot write collection tables

- **WHEN** the bot attempts to write to the inventory of either platform, the raw layer, the snapshots, the derived edges, or YouTube's payloads and readings
- **THEN** the database refuses it

#### Scenario: The bot can record what it is for

- **WHEN** the bot marks an alert of either queue delivered, held, failed or excluded
- **THEN** the write succeeds

#### Scenario: The bot records a day as summarized

- **WHEN** the bot records that a destination's daily summary of a day has been sent
- **THEN** the write succeeds, and the day and the destination alone are written

#### Scenario: The bot cannot raise or rewrite a YouTube alert

- **WHEN** the bot attempts to insert a YouTube alert, or to change the video, the reading, the kind or the score of one
- **THEN** the database refuses it

#### Scenario: The bot records a list's delivery

- **WHEN** the bot marks a weekly list of either platform, its entries or its second wind entries delivered, or counts a failed attempt on a list
- **THEN** the write succeeds

#### Scenario: The bot cannot write a list

- **WHEN** the bot attempts to insert a weekly list or an entry of either platform, or to change an entry's rank, channel, post or video, reading, signal, count or score
- **THEN** the database refuses it

#### Scenario: The bot cannot write a second wind entry

- **WHEN** the bot attempts to insert a second wind entry of either platform, or to change its rank, channel, post or video, readings, acceleration or share
- **THEN** the database refuses it

#### Scenario: The bot records an offer's delivery and its decision

- **WHEN** the bot marks a rising offer of either platform delivered, counts a failed attempt on it, or records its decision, kind, language or reason and when it was decided
- **THEN** the write succeeds

#### Scenario: The bot cannot write an offer or apply a decision

- **WHEN** the bot attempts to insert a rising offer of either platform or a day's selection, to change an offer's channel, rank or numbers, to mark a decision applied, or to change a channel's status, kind, language or reason in either inventory
- **THEN** the database refuses it

### Requirement: The Bot Reports Its Own State

The system SHALL let the operator ask, in the alert chat, what the alerting of both platforms is doing, so that quiet and broken are distinguishable without a database client. One answer SHALL cover both platforms, whatever topic it was asked in, and SHALL come in the topic it was asked in.

#### Scenario: Quiet is distinguishable from broken

- **WHEN** the operator asks for status
- **THEN** the reply says when Telegram's detection pass last ran, how many alerts it has raised, how many are undelivered, and of those how many are held for the summary
- **AND** when YouTube's last poll pass completed

#### Scenario: Stale evidence is reported

- **WHEN** the operator asks for status
- **THEN** the reply says how recent the derived data the detection reads is

#### Scenario: The YouTube chat

- **WHEN** the operator asks for status
- **THEN** the reply says how many YouTube alerts were raised, folded and excluded, and, per topic, how many are undelivered, held and failing

#### Scenario: Asked in a topic

- **WHEN** the operator asks for status in the Russian topic
- **THEN** the reply is in the Russian topic

### Requirement: The Bot Starts Only With Chats It Can Reach

At start, the bot SHALL ask Telegram about the alert chat, and SHALL refuse to start, naming the setting, when Telegram does not know the chat to the bot, or when the chat, configured with topics, is not a forum.

#### Scenario: A chat that is not a forum

- **GIVEN** the alert chat configured with topics, and a plain group behind its id
- **WHEN** the bot starts
- **THEN** it exits non-zero naming the chat's setting, before delivering anything

#### Scenario: A chat the bot is not in

- **GIVEN** the alert chat, which the bot is not a member of
- **WHEN** the bot starts
- **THEN** it exits non-zero naming that chat's setting
