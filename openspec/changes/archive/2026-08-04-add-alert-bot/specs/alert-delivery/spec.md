## ADDED Requirements

### Requirement: Alerts Are Raised Once Per Post Per Band

The system SHALL raise at most one alert for a given kind, post and threshold band. Re-running a detection pass over unchanged data MUST write nothing, and this SHALL be enforced by the schema rather than by a pass remembering what it has already raised.

#### Scenario: A post crossing a threshold raises one alert

- **GIVEN** a post that has crossed a configured band
- **WHEN** the detection pass runs
- **THEN** one alert is recorded for that kind, post and band

#### Scenario: Re-running raises nothing further

- **GIVEN** an alert already recorded for a post and band
- **WHEN** the pass runs again over unchanged data
- **THEN** no second alert is recorded
- **AND** the existing alert's delivery state is unchanged

#### Scenario: Crossing a higher band is a second alert

- **GIVEN** a post that has already raised an alert at the lower band
- **WHEN** it later crosses a higher configured band
- **THEN** a second alert is recorded for that higher band
- **AND** the first alert is left as it was

#### Scenario: Staying at the same band raises nothing

- **GIVEN** a post that crossed a band and has gained no further reposts
- **WHEN** the pass runs repeatedly
- **THEN** no further alert is recorded for it

#### Scenario: The number of alerts about one post is bounded

- **WHEN** a post crosses every configured band
- **THEN** it has raised exactly as many alerts as there are bands

### Requirement: An Alert Records What It Took To Decide

The system SHALL record, for each alert, the kind, the post, the band crossed, the measured value at the moment it crossed, and when it was raised. It MUST NOT store a rendered message, and it MUST NOT store a copy of evidence that can be read from the derived tables.

#### Scenario: The deciding measure is preserved

- **WHEN** an alert is raised
- **THEN** the value that crossed the threshold is stored on it
- **AND** that value does not change when the underlying data does

#### Scenario: Evidence is read at rendering time

- **WHEN** an alert is rendered for delivery
- **THEN** the channels and posts it names are read from the derived tables
- **AND** no copy of them was stored on the alert

#### Scenario: An alert names a post that exists

- **WHEN** an alert is raised
- **THEN** the post it refers to is present in the raw layer

### Requirement: Delivery Is Claimed, Confirmed, And Retried

The system SHALL mark an alert delivered only once it has been sent. An alert whose send fails MUST remain undelivered and be attempted again, and no alert may be delivered twice.

#### Scenario: A delivered alert is not sent again

- **GIVEN** an alert that has been sent
- **WHEN** the bot next looks for work
- **THEN** that alert is not among the alerts to send

#### Scenario: A failed send is retried

- **GIVEN** an alert whose send failed
- **WHEN** the bot next looks for work
- **THEN** the alert is attempted again
- **AND** the failure is recorded against it

#### Scenario: Two senders do not send one alert twice

- **GIVEN** two processes reading the alert queue at once
- **WHEN** both look for work
- **THEN** no alert is claimed by both

#### Scenario: A repeatedly failing alert is reported rather than hidden

- **GIVEN** an alert that has failed to send several times
- **WHEN** the bot's state is asked for
- **THEN** the failing alert is reported

### Requirement: Delivery Does Not Depend On A Notification

The system SHALL deliver every undelivered alert whether or not a notification about it was received. A notification MAY reduce delivery latency and MUST NOT be the only path by which an alert is delivered.

#### Scenario: An alert raised while the bot was down is still delivered

- **GIVEN** an alert raised while the bot was not running
- **WHEN** the bot starts
- **THEN** the alert is delivered

#### Scenario: The outstanding set is the same for both paths

- **WHEN** the bot is woken by a notification, or wakes on its own interval
- **THEN** it acts on the same set of undelivered alerts

### Requirement: Nothing Is Silently Withheld

The system SHALL bound how many alerts are sent directly within a period. Alerts beyond that bound MUST be delivered in a summary rather than discarded, and the summary MUST say how many it covers.

#### Scenario: Alerts beyond the cap are held, not dropped

- **GIVEN** the direct cap for the period has been reached
- **WHEN** a further alert is raised
- **THEN** it is not sent directly
- **AND** it remains undelivered rather than being marked delivered

#### Scenario: Held alerts are delivered as a summary

- **WHEN** the summary is due
- **THEN** every alert held since the last one is included
- **AND** the summary states how many alerts it covers

#### Scenario: A summary covering nothing is not sent

- **GIVEN** no alerts were held
- **WHEN** the summary would be due
- **THEN** no message is sent

### Requirement: Quiet Hours Hold Rather Than Drop

The system SHALL send no alert directly during a configured quiet window. Alerts raised during it MUST be delivered afterwards rather than discarded.

#### Scenario: An alert raised at night is not sent at night

- **GIVEN** the current time falls inside the quiet window
- **WHEN** an alert is raised
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

The system SHALL send alerts to one configured recipient. It MUST NOT send to any other chat, and MUST NOT act on a message from anyone else.

#### Scenario: Alerts go to the configured recipient

- **WHEN** an alert is delivered
- **THEN** it is sent to the configured chat and to no other

#### Scenario: A message from a stranger is ignored

- **GIVEN** a message from a chat that is not the configured one
- **WHEN** the bot receives it
- **THEN** no command is executed
- **AND** no information about the inventory is sent in reply

### Requirement: The Bot Cannot Write Collection State

The system SHALL confine the bot's writes to the alert tables. The bot MUST NOT modify the channel inventory, the raw layer, metric snapshots, derived edges or collection progress, and this SHALL be enforced by the database's own permissions rather than by convention.

#### Scenario: The bot's credentials cannot write collection tables

- **WHEN** the bot attempts to write to the inventory, the raw layer, the snapshots or the derived edges
- **THEN** the database refuses it

#### Scenario: The bot can record what it is for

- **WHEN** the bot marks an alert delivered or records feedback
- **THEN** the write succeeds

### Requirement: The Bot Holds No Telegram Session

The system SHALL run the bot without a Telethon session. It MUST NOT acquire the session lease, so that it can run while collection is running.

#### Scenario: The bot runs alongside the collector

- **GIVEN** a collection command holding the session lease
- **WHEN** the bot is started
- **THEN** it runs

#### Scenario: The bot does not block collection

- **GIVEN** a running bot
- **WHEN** a collection command is started
- **THEN** it acquires the session lease and proceeds

### Requirement: Feedback Is Recorded From The First Alert

The system SHALL offer the operator a verdict on every delivered alert and record it against that alert. A verdict MUST be recorded whether or not anything reads it yet.

#### Scenario: A verdict is recorded

- **WHEN** the operator answers an alert
- **THEN** the verdict is stored against that alert, with the moment it was given

#### Scenario: A changed verdict replaces the earlier one

- **GIVEN** an alert the operator has already answered
- **WHEN** they answer it differently
- **THEN** the stored verdict is the later one

#### Scenario: An unanswered alert is not a verdict

- **GIVEN** a delivered alert the operator has not answered
- **WHEN** the feedback is read
- **THEN** it carries no verdict for that alert, rather than a neutral one

### Requirement: An Alert Says What Happened And When

The system SHALL identify, in each delivered alert, the post it is about, the channel that published it, how old the post is, and what crossed the threshold.

#### Scenario: The alert links to the post

- **WHEN** an alert is delivered for a post whose channel has a username
- **THEN** the message carries a link to that post

#### Scenario: The alert states the post's age

- **WHEN** an alert is delivered
- **THEN** the message says how long ago the post was published

#### Scenario: One alert per post, not one per reposter

- **GIVEN** a post carried by several channels
- **WHEN** the alert is delivered
- **THEN** one message describes the post and the channels that carried it

### Requirement: The Bot Reports Its Own State

The system SHALL let the operator ask what the alerting is doing, so that quiet and broken are distinguishable without a database client.

#### Scenario: Quiet is distinguishable from broken

- **WHEN** the operator asks for status
- **THEN** the reply says when the detection pass last ran, how many alerts it has raised, and how many are undelivered

#### Scenario: Stale evidence is reported

- **WHEN** the operator asks for status
- **THEN** the reply says how recent the derived data the detection reads is
