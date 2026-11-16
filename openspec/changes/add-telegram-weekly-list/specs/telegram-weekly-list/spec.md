# Spec Delta

## Purpose

The weekly list of what took off on Telegram: which week it covers, how a post and a channel are ranked, what a list holds and how long, what its message says, and how it is delivered.

## ADDED Requirements

### Requirement: A List For Each Week That Has Ended

The system SHALL provide `itgraph weekly`, which computes one list for one week and stores it. A week SHALL run from Monday 00:00 to the next Monday 00:00 in Moscow time, and a post SHALL belong to the week of its publication. Without `--week`, the week SHALL be the last one that has ended before the run.

#### Scenario: A post of Sunday evening

- **GIVEN** a post published on Sunday at 23:00 Moscow time
- **WHEN** the list is computed on the Monday after
- **THEN** the post is scored for that week's list

#### Scenario: A post of Monday just after midnight

- **GIVEN** a post published on Monday at 00:30 Moscow time
- **WHEN** the list is computed at 10:00 that Monday
- **THEN** the post is in neither that list nor any earlier one

#### Scenario: A run late by days

- **GIVEN** no list computed on Monday
- **WHEN** the pass runs on Wednesday
- **THEN** it computes the list of the week that ended on that Monday

### Requirement: A Week's List Is Computed Once

`--week` SHALL name the Monday of a week that has ended, and any other date SHALL be refused. Computing a week that already has its list SHALL write nothing.

#### Scenario: A week that has not ended

- **WHEN** `--week` names the Monday of the current week, or a date that is not a Monday
- **THEN** the command refuses before reading anything

#### Scenario: Computing a week again

- **GIVEN** the list of a week already stored
- **WHEN** the pass runs for that week
- **THEN** nothing is written, and the pass says the week has its list

### Requirement: A Post Is Scored As The Live Pass Scored It

Each reading of a week's post SHALL be scored against the baseline run that was the newest completed one when the reading was taken, on the alerts' signals, with the alerts' comment minimum, at the reading's age, and as the scoring pass reads posts: an album by its first part, a burst by its first message, seeds that are not chats. A post's score SHALL be its best across its readings. A reading with no run before it SHALL NOT be scored, and SHALL be counted.

#### Scenario: A run completed between two readings

- **GIVEN** a post read at 04:00 and at 06:00, and a baseline run completed at 04:30
- **WHEN** the list is computed
- **THEN** the 04:00 reading is scored against the run before 04:30, and the 06:00 reading against the run of 04:30

#### Scenario: Readings before the first run

- **GIVEN** a week whose first readings precede every completed run
- **WHEN** the list is computed
- **THEN** those readings are not scored, and the summary counts them

#### Scenario: The best reading

- **GIVEN** a post scoring z 3.9 on views at two hours and z 4.4 on reactions at eight
- **WHEN** the list is computed
- **THEN** its score is 4.4 on reactions

#### Scenario: Too few comments

- **GIVEN** a post whose comments score high on a count under the comment minimum
- **WHEN** the list is computed
- **THEN** that comment score is not the post's score

#### Scenario: An album

- **GIVEN** a post published as an album of four messages
- **WHEN** the list is computed
- **THEN** it is scored once, as its first message, and an entry for it names that message

#### Scenario: A late reading

- **GIVEN** a post of the week read again at four days of age by the poll loop's late readings
- **WHEN** the list is computed
- **THEN** that reading scores nothing, as no band of the scoring reaches it

### Requirement: One Entry Per Channel, At Or Above The Alerts' Threshold

A list SHALL hold the channels whose best post of the week scored at least the alerts' threshold, `alert_spike_z`. Each channel gets one entry, naming its best post and how many of its other posts of the week scored that much. Entries SHALL be ranked by the best post's score, highest first, with at most `weekly_list_size` entries. Fewer channels at the threshold SHALL make a shorter list. Every kind of channel SHALL be ranked alike.

#### Scenario: A channel with three posts at the threshold

- **GIVEN** a channel whose three posts of the week scored 4.5, 4.0 and 3.8, against a threshold of 3.7
- **WHEN** the list is computed
- **THEN** it has one entry, naming the post of 4.5 and two more at the threshold

#### Scenario: More channels than the list holds

- **GIVEN** twelve channels at the threshold, and a size of ten
- **WHEN** the list is computed
- **THEN** it holds the ten with the highest scores, in that order

#### Scenario: Fewer channels than the list holds

- **GIVEN** four channels at the threshold
- **WHEN** the list is computed
- **THEN** it holds four entries, and no channel below the threshold

#### Scenario: The threshold moves with the alerts'

- **GIVEN** the alerts' threshold set to 4.0, and a channel whose best post of the week scored 3.8
- **WHEN** the list is computed
- **THEN** the channel is not on it

### Requirement: An Empty Week Is Said

A list with no channel at the threshold SHALL be stored and sent, saying that nothing reached the threshold and how many posts were scored.

#### Scenario: Nothing took off

- **GIVEN** a week in which no channel's post reached the threshold, and 3,900 posts were scored
- **WHEN** the list is sent
- **THEN** its message names the week, says nothing reached the threshold, and says 3,900 posts were scored

#### Scenario: Nothing was scored

- **GIVEN** a restored copy with no reading in the last week
- **WHEN** the pass runs and the list is sent
- **THEN** the message says nothing reached the threshold and that no post was scored

### Requirement: A List Holds Itgraph's Numbers

A list SHALL hold its week, its moments, its parameters, its counts and its delivery state. An entry SHALL hold its rank, its channel, its post, the reading its score came from, its signal, its score, the count scored and the expectation it was scored against, the count of its channel's other posts, and its delivery state. Neither SHALL hold a post's text or a channel's name. An entry SHALL be removed with the reading it names.

#### Scenario: What is stored

- **WHEN** a list is computed
- **THEN** its rows hold ids, moments, itgraph's numbers, the count each score came from and delivery state, and no text or channel name

#### Scenario: A post gone before the list is sent

- **GIVEN** a computed list whose second entry's post is removed with its channel before sending
- **WHEN** the list is sent
- **THEN** the entry is gone, and the list is sent with the others, ranked without a gap

### Requirement: Each Entry Shows What An Alert Shows

Every entry SHALL show its channel's title and username, the post's age, a link to the post where the channel has a username, the first line of its text cut at 120 characters, its signal with how many times the channel's usual it reached and its z, and how many other posts of the channel reached the threshold. It SHALL show no count of views, reactions or forwards.

#### Scenario: An entry

- **WHEN** a list with entries is sent
- **THEN** every entry names its channel, the post's age and a link to the post, the first line of its text, its signal with the multiple and z, and how many more posts of the channel reached the threshold

#### Scenario: A channel without a username

- **GIVEN** an entry whose channel has no username
- **WHEN** the list is sent
- **THEN** the entry has no link, and everything else

#### Scenario: A long first line

- **GIVEN** an entry whose post begins with a paragraph of 400 characters
- **WHEN** the list is sent
- **THEN** the entry shows its first 120 characters, marked as cut

#### Scenario: A comment entry

- **GIVEN** an entry whose best signal is comments
- **WHEN** the list is sent
- **THEN** it states the count of the reading scored and about how many the channel's posts usually have at that age

### Requirement: A List States Its Week And Whose Score It Shows

A list's message SHALL state that it is Telegram's list, its week, how many posts were scored, the moment the score was calculated, and that the score is calculated by itgraph. Every moment in it SHALL be Moscow time.

#### Scenario: The head of a list

- **WHEN** the list of 16–22 November is sent
- **THEN** its first message names Telegram's list, the week of 16–22 November, the posts scored, the moment of the score, and that the score is itgraph's

### Requirement: A List Is Delivered Once, Whatever Its Length

The bot SHALL send each undelivered list to Telegram's alert chat, in as many messages as fit, each entry whole in one message, without a link preview. A message that fails SHALL count an attempt on the list and be sent again on a later pass; messages already sent SHALL NOT be sent again. A list SHALL NOT go to any other chat. With no alert chat configured, a list SHALL wait undelivered.

#### Scenario: A list longer than a message

- **GIVEN** a list whose entries do not fit in 4,096 characters
- **WHEN** it is sent
- **THEN** it goes as two messages, each within the limit, every entry whole

#### Scenario: The second message fails

- **GIVEN** a list sent as two messages, the second refused
- **WHEN** the next pass comes
- **THEN** only the second is sent again, and the list counts one failed attempt

#### Scenario: Not to YouTube's chat

- **GIVEN** a YouTube chat configured beside Telegram's
- **WHEN** Telegram's list is sent
- **THEN** no message of it reaches the YouTube chat or its topics

#### Scenario: No alert chat

- **GIVEN** no alert chat configured
- **WHEN** the bot looks for work
- **THEN** no list is sent, and each stays undelivered

### Requirement: A List Is Neither Capped Nor Sent In Quiet Hours

A list SHALL NOT be counted against the chat's daily cap nor held for its digest, and SHALL NOT be sent inside the chat's quiet hours; one computed inside them SHALL be sent once they end.

#### Scenario: The cap already reached

- **GIVEN** a chat that has sent its daily cap of alerts
- **WHEN** a list is undelivered
- **THEN** the list is sent, and no alert is sent past the cap because of it

#### Scenario: Computed inside quiet hours

- **GIVEN** a list computed at 03:00 by a run catching up, inside the chat's quiet hours
- **WHEN** the bot passes before the window ends
- **THEN** nothing is sent, and the list goes once the window has ended

### Requirement: Computing A List Reads Only Collected Data

`itgraph weekly` SHALL make no request to Telegram and take no session lease. It SHALL write nothing but the list tables. With `--preview` it SHALL print the list by ids and scores and write nothing. Its logs SHALL name channel and message ids only.

#### Scenario: No request

- **WHEN** the pass runs
- **THEN** no request leaves the process, no session is opened, and only the list tables change

#### Scenario: A preview

- **WHEN** the pass runs with `--preview`
- **THEN** it prints the list's ranks, scores, signals, channel and message ids and others, and the database is unchanged

#### Scenario: Beside a running collector

- **GIVEN** the poll loop holding the session lease
- **WHEN** the pass runs
- **THEN** it computes the list as anywhere else
