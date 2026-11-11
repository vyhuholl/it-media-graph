# Spec Delta

## ADDED Requirements

### Requirement: YouTube Alerts Have A Queue Of Their Own

The system SHALL keep YouTube alerts apart from Telegram's. A YouTube alert SHALL name a tracked video, the reading it was scored on, its kind — views, likes or comments — the band it crossed and the score that crossed it. At most one alert SHALL exist per kind, video and band, enforced by the schema.

#### Scenario: A video crossing a band raises one alert

- **GIVEN** a tracked video with a reading that crossed a band on views
- **WHEN** an alert is raised for it
- **THEN** one YouTube alert names that video, that reading, the kind views, the band and the score

#### Scenario: Raising again writes nothing

- **GIVEN** a YouTube alert for a video, a kind and a band
- **WHEN** the same alert is raised again
- **THEN** no second alert is recorded and the first is unchanged

#### Scenario: An alert names a reading that exists

- **WHEN** a YouTube alert is raised naming a reading that is not stored
- **THEN** the database refuses it

#### Scenario: Telegram's queue is untouched

- **WHEN** a YouTube alert is raised
- **THEN** nothing is written to Telegram's alerts

### Requirement: An Alert Goes With Its Video's Data

An alert folded into another SHALL be removed with its head. No other clock SHALL keep or remove an alert.

#### Scenario: The video is gone from YouTube

- **GIVEN** a YouTube alert about a tracked video
- **WHEN** a request for the video is answered without it
- **THEN** the video's data and the alert are removed together

#### Scenario: A head takes its folds

- **GIVEN** a YouTube alert with two alerts folded into it
- **WHEN** the head is removed with its video
- **THEN** the two folded alerts are removed as well

### Requirement: An Alert Copies Nothing YouTube Said

A YouTube alert SHALL hold no title, channel name, description or count read from YouTube.

#### Scenario: The alert's row

- **WHEN** a YouTube alert is stored
- **THEN** it holds ids, moments, its kind, band, score and delivery state, and no title, name, description or count

#### Scenario: Counts are the newest stored

- **GIVEN** a YouTube alert scored on a reading at 10:15, and a later reading at 14:05
- **WHEN** it is sent at 14:20
- **THEN** the message shows the counts of 14:05 with that time

### Requirement: Alerts Are Routed By The Channel's Language When Sent

The system SHALL send a YouTube alert to the topic of its channel's language as labelled at the moment of sending, English or Russian, and SHALL record which. An alert in a summary SHALL be routed when the summary is sent. An alert whose channel is no longer in scope SHALL still be delivered.

#### Scenario: English

- **GIVEN** an alert about a video of a channel labelled `en`
- **WHEN** it is sent
- **THEN** it goes to the YouTube chat's English topic, and the alert records `en`

#### Scenario: Russian

- **GIVEN** an alert about a video of a channel labelled `ru`
- **WHEN** it is sent
- **THEN** it goes to the YouTube chat's Russian topic, and the alert records `ru`

#### Scenario: Relabelled while waiting

- **GIVEN** an alert raised while its channel was labelled `en`, held for a summary
- **WHEN** the channel is relabelled `ru` and the Russian topic's summary is sent
- **THEN** the alert is in the Russian topic's summary and not in the English one's

#### Scenario: A channel out of scope since

- **GIVEN** an alert about a video of a channel rejected after the alert was raised
- **WHEN** the bot looks for work
- **THEN** the alert is delivered to the topic of the channel's language

### Requirement: Channels In Other Languages Get No Alert

An alert whose channel is labelled `other` at the moment of sending, or has no language to route by, SHALL NOT be sent. It SHALL be closed as excluded, and counted apart from alerts delivered and alerts undelivered.

#### Scenario: Relabelled to another language

- **GIVEN** an undelivered alert about a video of a channel relabelled `other`
- **WHEN** the bot looks for work
- **THEN** no message is sent about it
- **AND** it is recorded as excluded and is no longer outstanding

#### Scenario: A video whose channel is not in the inventory

- **GIVEN** an undelivered alert about a video YouTube now attributes to a channel with no record
- **WHEN** the bot looks for work
- **THEN** no message is sent about it, and it is recorded as excluded

#### Scenario: Excluded is reported

- **GIVEN** an alert closed as excluded
- **WHEN** the operator asks for status in the YouTube chat
- **THEN** the reply counts it as excluded, not as delivered or undelivered

### Requirement: A YouTube Alert Names Its Source And Dates What It Shows

Every YouTube alert, sent alone or as an entry of a summary, SHALL name YouTube as its source with a link to the video on YouTube, and show the video's title, its channel, its age, the counts YouTube reports with the moment they were read, and itgraph's score with the moment it was calculated and a statement that itgraph calculated it and that it is not a YouTube metric.

#### Scenario: An alert sent alone

- **WHEN** a YouTube views alert is sent
- **THEN** the message names YouTube, links to the video on YouTube, and shows the title, the channel, how long ago the video was published, the views, likes and comments with the time they were read, and itgraph's score with its time and the statement

#### Scenario: Each entry of a summary

- **GIVEN** a summary of three YouTube alerts
- **WHEN** it is sent
- **THEN** each of the three entries names YouTube and links to its own video
- **AND** none relies on an attribution given once for the whole summary

#### Scenario: A counter YouTube did not report

- **GIVEN** a video whose newest reading has no likes count
- **WHEN** its alert is sent
- **THEN** the message shows its views and comments and says nothing of likes, rather than zero

#### Scenario: A broadcast's age

- **GIVEN** a video of a broadcast that started two hours after it was published
- **WHEN** its alert is sent
- **THEN** its age is counted from the broadcast's start

#### Scenario: A comment alert states the count

- **GIVEN** a YouTube alert raised on comments
- **WHEN** it is sent
- **THEN** the message names comments, the count of the reading it was scored on, and about how many the channel's videos usually have at that age

#### Scenario: The head of folded alerts

- **GIVEN** a YouTube alert with two alerts about other videos of the same channel folded into it
- **WHEN** it is sent
- **THEN** its message states that two more videos of the channel were elevated with it

### Requirement: YouTube Delivery Needs Its Chat And Both Topics

YouTube alerts SHALL be sent only when the YouTube chat and its English and Russian topics are all configured, and settings naming only some of them SHALL be refused when they load. With none configured, no YouTube alert SHALL be sent; one raised then SHALL stay undelivered and be reported as such.

#### Scenario: A chat without its topics

- **GIVEN** the YouTube chat configured, and the Russian topic not
- **WHEN** settings load
- **THEN** loading fails naming the missing setting

#### Scenario: Nothing configured

- **GIVEN** no YouTube chat or topic configured, and an undelivered YouTube alert
- **WHEN** the bot looks for work
- **THEN** nothing is sent about it, and it is counted as undelivered

### Requirement: A Topic That Cannot Be Reached Fails The Send

A send that Telegram refuses for a YouTube topic SHALL count as a failed attempt on the alert and be tried again, and the alert SHALL NOT be sent to any other chat or topic.

#### Scenario: A deleted topic

- **GIVEN** the English topic deleted in Telegram
- **WHEN** an alert routed to it is sent
- **THEN** the send fails and is recorded against the alert
- **AND** no message about it reaches the Russian topic, General or the Telegram chat

#### Scenario: Reported once it keeps failing

- **GIVEN** an alert whose send has failed as often as the failure report counts
- **WHEN** the shared monitoring scrapes
- **THEN** the failing alerts of the YouTube platform are counted
