## Purpose

The weekly list of what took off on YouTube, per language: which week it covers, how a video and a channel are ranked, what a list holds and how long, what its message says, and how it is delivered.

## ADDED Requirements

### Requirement: A List Per Language For Each Week That Has Ended

The system SHALL provide `itgraph yt weekly`, which computes, for one week, one list per language — English and Russian — and stores them. A week SHALL run from Monday 00:00 to the next Monday 00:00 in Moscow time, and a video SHALL belong to the week of its publication. Without `--week`, the week SHALL be the last one that has ended before the run.

#### Scenario: A video of Sunday evening

- **GIVEN** a video published on Sunday at 23:00 Moscow time
- **WHEN** the list is computed on the Monday after
- **THEN** the video is scored for that week's list

#### Scenario: A video of Monday just after midnight

- **GIVEN** a video published on Monday at 00:30 Moscow time
- **WHEN** the list is computed at 10:00 that Monday
- **THEN** the video is in neither that list nor any earlier one

#### Scenario: A run late by days

- **GIVEN** no list computed on Monday
- **WHEN** the pass runs on Wednesday
- **THEN** it computes the lists of the week that ended on that Monday

### Requirement: A Week's Lists Are Computed Once

`--week` SHALL name the Monday of a week that has ended, and any other date SHALL be refused. Computing a week that already has its lists SHALL write nothing.

#### Scenario: A week that has not ended

- **WHEN** `--week` names the Monday of the current week, or a date that is not a Monday
- **THEN** the command refuses before reading anything

#### Scenario: Computing a week again

- **GIVEN** the lists of a week already stored
- **WHEN** the pass runs for that week
- **THEN** nothing is written, and the pass says the week has its lists

### Requirement: A Video Is Scored As The Live Pass Scored It

Each reading of a week's video SHALL be scored against the baseline run that was the newest completed one at the moment of the reading, on the alerts' signals, with the alerts' comment minimum, at the reading's age. A video's score SHALL be its best across its readings up to the moment the list is computed. A reading with no completed run before it SHALL NOT be scored, and SHALL be counted. Broadcasts and videos whose length cannot be read SHALL NOT be scored, as by the live pass.

#### Scenario: A run completed between two readings

- **GIVEN** a video read at 04:00 and at 08:00, and a baseline run completed at 05:30
- **WHEN** the list is computed
- **THEN** the 04:00 reading is scored against the run before 05:30, and the 08:00 reading against the run of 05:30

#### Scenario: Readings before the first run

- **GIVEN** a week whose first readings precede every completed run
- **WHEN** the list is computed
- **THEN** those readings are not scored, and the summary counts them

#### Scenario: The best reading

- **GIVEN** a video scoring z 2.4 on views at 24 hours and z 3.1 on likes at four days
- **WHEN** the list is computed
- **THEN** its score is 3.1 on likes

#### Scenario: Too few comments

- **GIVEN** a video whose comments score high on a count under the comment minimum
- **WHEN** the list is computed
- **THEN** that comment score is not the video's score

#### Scenario: A broadcast

- **GIVEN** a stream published in the week
- **WHEN** the list is computed
- **THEN** it is in no entry, and the summary counts it as not scored

### Requirement: One Entry Per Channel, The Best Above A Floor

A list SHALL hold the channels in scope with that language when the list is computed whose best video of the week scored at least `youtube_weekly_list_z`, one entry per channel naming its best video and how many of its other videos of the week scored that much, ranked by the best video's score, highest first, at most `youtube_weekly_list_size` entries. Fewer channels above the floor SHALL make a shorter list. Short-form and long-form videos SHALL be ranked together.

#### Scenario: A channel with three videos above the floor

- **GIVEN** a channel whose three videos of the week scored 4.0, 2.6 and 2.1, against a floor of 2.0
- **WHEN** its language's list is computed
- **THEN** it has one entry, naming the video of 4.0 and two more above the floor

#### Scenario: More channels than the list holds

- **GIVEN** twelve channels of one language above the floor, and a size of ten
- **WHEN** the list is computed
- **THEN** it holds the ten with the highest scores, in that order

#### Scenario: Fewer channels than the list holds

- **GIVEN** four channels of one language above the floor
- **WHEN** the list is computed
- **THEN** it holds four entries, and no channel below the floor

#### Scenario: A Short ahead of a talk

- **GIVEN** a Short scoring 3.5 and a long-form video of another channel scoring 3.0
- **WHEN** the list is computed
- **THEN** the Short's channel is ranked first

#### Scenario: A channel relabelled before the list

- **GIVEN** a channel labelled `ru` while its video took off, relabelled `other` before the list is computed
- **WHEN** the lists are computed
- **THEN** it is on neither list

#### Scenario: The list does not move once computed

- **GIVEN** a Russian list computed with a channel on it
- **WHEN** the channel is relabelled `en` before the list is sent
- **THEN** the entry is sent in the Russian list

### Requirement: An Empty Week Is Said

A list with no channel above the floor SHALL be stored and sent, saying that nothing was above the floor and how many videos of that language were scored.

#### Scenario: Nothing took off

- **GIVEN** a week in which no Russian channel's video scored at least the floor, and 180 Russian videos were scored
- **WHEN** the Russian list is sent
- **THEN** its message names the week, says nothing was above the floor, and says 180 videos were scored

#### Scenario: Nothing was scored

- **GIVEN** a restored copy with no reading in the last week
- **WHEN** the pass runs and the list is sent
- **THEN** the message says nothing was above the floor and that no video was scored

### Requirement: A List Copies Nothing YouTube Said

A list SHALL hold its week, its language, its moments, its parameters, its counts and its delivery state; an entry its rank, its channel, its video, the reading its score came from, its signal, its score, the expectation it was scored against, the count of its channel's other videos, and its delivery state. Neither SHALL hold a title, channel name, description, count or format read from YouTube. An entry SHALL be removed with the reading it names.

#### Scenario: What is stored

- **WHEN** a list is computed
- **THEN** its rows hold ids, moments, itgraph's numbers and delivery state, and no title, name, description, count or format

#### Scenario: A video gone before the list is sent

- **GIVEN** a computed list whose second entry's video YouTube stops returning
- **WHEN** the video's data is removed and the list is sent
- **THEN** the entry is gone, and the list is sent with the others, ranked without a gap

### Requirement: Each Entry Names Its Source And Dates What It Shows

Every entry of a list SHALL name YouTube as its source with a link to its video on YouTube, and show the video's title, its channel, a mark when it is short-form, its age, the counts of its newest stored reading with the moment it was taken, and itgraph's score of the entry's signal labelled as itgraph's, with how many other videos of the channel were above the floor. A counter YouTube did not report SHALL be left out, never shown as zero.

#### Scenario: An entry

- **WHEN** a list with entries is sent
- **THEN** every entry links to its own video on YouTube and shows the title, the channel, the age, the views, likes and comments with the time they were read, and itgraph's score labelled as itgraph's
- **AND** none relies on an attribution given once for the whole list

#### Scenario: A Short

- **GIVEN** an entry whose video is short-form
- **WHEN** the list is sent
- **THEN** the entry marks it as a Short

#### Scenario: Counts newer than the score

- **GIVEN** an entry scored on a reading of Saturday and a later reading of Monday 09:47
- **WHEN** the list is sent on Monday at 10:00
- **THEN** the entry shows the counts of 09:47 with that time

#### Scenario: Likes hidden

- **GIVEN** an entry whose newest reading has no likes count
- **WHEN** the list is sent
- **THEN** it shows views and comments and says nothing of likes

#### Scenario: A comment entry

- **GIVEN** an entry whose best signal is comments
- **WHEN** the list is sent
- **THEN** it states the count of the reading scored and about how many the channel's videos usually have at that age

### Requirement: A List States Its Week And Whose Score It Shows

A list's message SHALL state its language, its week, how many videos of that language were scored, the moment the score was calculated, and that the score is calculated by itgraph and is not a YouTube metric. Every moment in it SHALL be Moscow time.

#### Scenario: The head of a list

- **WHEN** the Russian list of 16–22 November is sent
- **THEN** its first message names YouTube's Russian list, the week of 16–22 November, the videos scored, the moment of the score, and that the score is itgraph's and not a YouTube metric

### Requirement: A List Is Delivered Once, Whatever Its Length

The bot SHALL send each undelivered list to the YouTube topic of its language, in as many messages as fit, each entry whole in one message, without a link preview. A message that fails SHALL count an attempt on the list and be sent again on a later pass; messages already sent SHALL NOT be sent again. A topic that refuses SHALL fail the send, and the list SHALL NOT go to any other chat or topic. With no YouTube chat configured, a list SHALL wait undelivered.

#### Scenario: A list longer than a message

- **GIVEN** a list whose entries do not fit in 4,096 characters
- **WHEN** it is sent
- **THEN** it goes as two messages, each within the limit, every entry whole

#### Scenario: The second message fails

- **GIVEN** a list sent as two messages, the second refused
- **WHEN** the next pass comes
- **THEN** only the second is sent again, and the list counts one failed attempt

#### Scenario: A deleted topic

- **GIVEN** the English topic deleted in Telegram
- **WHEN** the English list is sent
- **THEN** the send fails and is recorded against the list
- **AND** no message of it reaches the Russian topic, General or the Telegram chat

#### Scenario: No YouTube chat

- **GIVEN** no YouTube chat configured
- **WHEN** the bot looks for work
- **THEN** no list is sent, and each stays undelivered

### Requirement: A List Is Neither Capped Nor Sent In Quiet Hours

A list SHALL NOT be counted against the topic's daily cap nor held for its digest, and SHALL NOT be sent inside the topic's quiet hours; one computed inside them SHALL be sent once they end.

#### Scenario: The cap already reached

- **GIVEN** a topic that has sent its daily cap of alerts
- **WHEN** a list for it is undelivered
- **THEN** the list is sent, and no alert is sent past the cap because of it

#### Scenario: Computed inside quiet hours

- **GIVEN** a list computed at 03:00 by a run catching up, inside the topic's quiet hours
- **WHEN** the bot passes before the window ends
- **THEN** nothing is sent, and the list goes once the window has ended

### Requirement: Computing A List Reads Only Collected Data

`itgraph yt weekly` SHALL make no request to YouTube, book no quota and need no YouTube key, and SHALL write nothing but the list tables. With `--preview` it SHALL print the lists by ids and scores and write nothing. Its logs SHALL name channel and video ids only.

#### Scenario: No request

- **WHEN** the pass runs
- **THEN** no request leaves the process, the quota ledger is unchanged, and only the list tables change

#### Scenario: A preview

- **WHEN** the pass runs with `--preview`
- **THEN** it prints each list's ranks, scores, signals, formats, video and channel ids and others, and the database is unchanged

#### Scenario: Without a key

- **GIVEN** an environment with no YouTube key
- **WHEN** the pass runs
- **THEN** it computes the lists as anywhere else
