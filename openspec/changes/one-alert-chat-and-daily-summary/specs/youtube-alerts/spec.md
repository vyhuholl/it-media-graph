# Spec Delta

## ADDED Requirements

### Requirement: Each Topic Closes Its Day In A Summary

Each topic SHALL be sent, once per day, a summary of the alerts sent directly to it in the day that has just ended, exactly as Telegram's destination is: the period from its quiet window's end to the next start, due as the window begins, silent on a day nothing was sent directly, counting towards no cap, recorded in the database as sent. It SHALL cover only that topic's route. Each entry SHALL be one alert, ordered by the video's publication time, carrying the headline its alert used, then the video and its channel with the video's clock time, then the link to the video. The message SHALL name the day it covers, show no preview of any link in it, and go in parts rather than exceed the message limit.

#### Scenario: Only the topic's own route

- **GIVEN** a day in which English received three alerts directly and Russian one
- **WHEN** the topics' summaries are sent
- **THEN** the English topic's summary holds three entries and the Russian topic's one

#### Scenario: An entry names the video and its channel

- **GIVEN** a views alert of a video published at 14:33, scored z 4.2, delivered directly
- **WHEN** the topic's summary is sent
- **THEN** its entry is the signal with its magnitude and score, the video's title with its channel and 14:33, and the link to the video — and no counts, no age, no folding note

#### Scenario: A comment entry

- **GIVEN** a comment alert of 41 comments where the channel's videos usually have about 5, delivered directly
- **WHEN** the topic's summary is sent
- **THEN** its entry states the count and the usual, beside the score

#### Scenario: No preview

- **WHEN** a topic's daily summary is sent
- **THEN** the message shows no preview of the videos it links

## REMOVED Requirements

### Requirement: YouTube Delivery Needs Its Chat And Both Topics

**Reason**: The chat is one now, and shared with Telegram's alerts: there is no chat of YouTube's own to configure or to miss, and the topics of all three audiences are set together with it.

**Migration**: See "YouTube Delivery Needs The Chat And Its Topics" below: the one chat's id and the ids of its three topics, set together or not at all.

## ADDED Requirements

### Requirement: YouTube Delivery Needs The Chat And Its Topics

YouTube alerts SHALL be sent only when the one alert chat and its English, Russian and Telegram topics are all configured, and settings naming only some of them SHALL be refused when they load. With none configured, no YouTube alert SHALL be sent; one raised then SHALL stay undelivered and be reported as such.

#### Scenario: A topic missing beside the chat

- **GIVEN** the alert chat configured, and its Russian topic not
- **WHEN** settings load
- **THEN** loading fails naming the missing setting

#### Scenario: Nothing configured

- **GIVEN** no alert chat or topic configured, and an undelivered YouTube alert
- **WHEN** the bot looks for work
- **THEN** nothing is sent about it, and it is counted as undelivered

## MODIFIED Requirements

### Requirement: A Topic That Cannot Be Reached Fails The Send

A send that Telegram refuses for a YouTube topic SHALL count as a failed attempt on the alert and be tried again, and the alert SHALL NOT be sent to any other chat or topic.

#### Scenario: A deleted topic

- **GIVEN** the English topic deleted in Telegram
- **WHEN** an alert routed to it is sent
- **THEN** the send fails and is recorded against the alert
- **AND** no message about it reaches the Russian topic, the Telegram topic or General

#### Scenario: Reported once it keeps failing

- **GIVEN** an alert whose send has failed as often as the failure report counts
- **WHEN** the shared monitoring scrapes
- **THEN** the failing alerts of the YouTube platform are counted
