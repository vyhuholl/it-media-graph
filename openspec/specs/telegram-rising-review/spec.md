# telegram-rising-review Specification

## Purpose
How Telegram's rising channels reach the operator for review: the daily selection from the ranking, the card the bot sends for each channel, the buttons that review it as `itgraph mark` does, and how a decision taken there reaches the inventory.

## Requirements

### Requirement: A Daily Selection Of Rising Channels

The system SHALL select, once a day, the highest-ranked rising channels that have a public username and were never offered before, at most `rising_offers_per_day`. A day's selection SHALL be computed once and stored, an empty one included. The day is the Moscow date of computing.

#### Scenario: The head of the ranking

- **GIVEN** seven rising channels with a username, none offered before, and five a day
- **WHEN** the day's selection is computed
- **THEN** the five ranked highest are offered, in rank order, and the other two are not

#### Scenario: The overflow waits for the next day

- **GIVEN** two rising channels left out of yesterday's selection, still rising today
- **WHEN** today's selection is computed
- **THEN** they are ranked again with whatever rose since, and offered if they are among the five highest

#### Scenario: A day is computed once

- **GIVEN** today's selection already computed
- **WHEN** the selection is asked for again today
- **THEN** nothing is stored, and it says the day has its selection

#### Scenario: An empty day

- **GIVEN** no rising channel with a username that was never offered
- **WHEN** the day's selection is computed
- **THEN** the day is recorded as computed, with nothing offered

### Requirement: Each Channel Is Offered Once

The system SHALL offer a channel at most once, whatever happens to its offer. A channel nobody reviewed SHALL stay as it was and SHALL NOT be offered again or recalled.

#### Scenario: Offered and left alone

- **GIVEN** a channel offered yesterday, with no decision, still rising today
- **WHEN** today's selection is computed
- **THEN** it is not offered again, and its status is unchanged

#### Scenario: Marked maybe

- **GIVEN** a channel marked maybe from its offer, still rising a week later
- **WHEN** that day's selection is computed
- **THEN** it is not offered again

### Requirement: Selecting Reads Only Collected Data

Computing a day's selection SHALL read only what is stored: it SHALL make no request to Telegram, take no session lease, and write nothing but the day's selection.

#### Scenario: Beside the poll loop

- **GIVEN** the poll loop holding the session lease
- **WHEN** the day's selection is computed
- **THEN** it completes, makes no request and takes no lease

### Requirement: An Offer Is A Card

The bot SHALL send each offer as one message to the alert chat's Telegram topic: the channel's `@username` linked to it, its title, the families that referred to it in the window and before it, the first reference in the window, and links to up to three seed posts that referred to it in the window, one per family. Everything from a channel or a post SHALL be escaped.

#### Scenario: A card

- **GIVEN** an offer of a channel referred to by three seed families in the window and by one before it
- **WHEN** the bot sends it
- **THEN** the message names the channel by its username with a link, its title, three families in the window and one before, the date of the first reference in the window, and three links to seed posts, each of another family

#### Scenario: A title that looks like markup

- **GIVEN** a channel titled with angle brackets and an ampersand
- **WHEN** its card is sent
- **THEN** the title is shown as written, and nothing in it is read as markup

### Requirement: Offers Are Delivered Outside Quiet Hours, Uncapped

The bot SHALL send offers in rank order, to the alert chat's Telegram topic, outside its quiet hours, neither counted against the alert cap nor held for the digest. Each offer SHALL be marked delivered once sent; one that failed SHALL be tried again, and SHALL NOT be sent twice once marked.

#### Scenario: The day's selection

- **GIVEN** five offers computed at 11:00 and an alert cap already reached
- **WHEN** the bot next polls
- **THEN** the five cards are sent, in rank order, and the cap is unchanged

#### Scenario: Quiet hours

- **GIVEN** an offer not yet delivered at 03:00 Moscow time
- **WHEN** the bot polls
- **THEN** nothing is sent until the quiet hours end

#### Scenario: A failed send

- **GIVEN** a card Telegram refused to send
- **WHEN** the bot next polls
- **THEN** it is tried again, and the cards sent before it are not sent again

### Requirement: An Offer Reviewed Elsewhere Is Not Sent

An offer whose channel is no longer a candidate or a maybe, or no longer has a public username, when the bot comes to send it SHALL NOT be sent, and SHALL be closed as delivered.

#### Scenario: Marked from the command line first

- **GIVEN** an offer computed at 11:00, and its channel marked a seed with `itgraph mark` before the bot sent it
- **WHEN** the bot polls
- **THEN** no card for it is sent, and the offer is closed

#### Scenario: A channel that lost its username

- **GIVEN** an offer computed at 11:00, and its channel resolved again without a public username before the bot sent it
- **WHEN** the bot polls
- **THEN** no card for it is sent, and the offer is closed

### Requirement: A Card Is Reviewed As `itgraph mark` Reviews

A card SHALL carry three buttons: «🌱 В сиды», «🤔 Может быть», «🚫 Отклонить». Maybe SHALL be decided by one press. Seed SHALL ask for one of the seven kinds, and reject for one of the seven reasons, each with «↩️ Назад» back to the three. A decision SHALL be what `itgraph mark` would record with the same choices.

#### Scenario: A seed

- **WHEN** «🌱 В сиды» is pressed, then a kind
- **THEN** the decision is a seed of that kind

#### Scenario: A rejection

- **WHEN** «🚫 Отклонить» is pressed, then a reason
- **THEN** the decision is a rejection for that reason

#### Scenario: Back

- **GIVEN** the kinds shown after «🌱 В сиды»
- **WHEN** «↩️ Назад» is pressed
- **THEN** the three buttons are shown again, and nothing is decided

#### Scenario: Maybe

- **WHEN** «🤔 Может быть» is pressed
- **THEN** the decision is maybe

### Requirement: A Decision Is Final In The Bot

Once decided, the card SHALL show the decision and carry no button. A press on an offer already decided SHALL change nothing and SHALL be answered that it is decided. A decision SHALL be changed only with `itgraph mark`.

#### Scenario: The card after a decision

- **WHEN** a seed of kind media is decided on a card
- **THEN** the card says so, and has no buttons left

#### Scenario: Two presses at once

- **GIVEN** a card shown to two people
- **WHEN** one decides a seed and the other a rejection at the same moment
- **THEN** one decision is recorded, and the other press is answered that the card is decided

### Requirement: Nobody Who Pressed Is Recorded

A decision SHALL record what was decided and when, and SHALL NOT record, store or log who pressed the button.

#### Scenario: A decision's record

- **WHEN** a decision is recorded
- **THEN** nothing stored or logged names or identifies the person who pressed

### Requirement: A Decision Reaches The Inventory Within Fifteen Minutes

A decision SHALL be applied to the channel's review by the detection pass that follows it, as `itgraph mark` applies the same choices, if the channel is still a candidate or a maybe. Otherwise it SHALL NOT be applied, and SHALL be recorded as not applied. Either way it SHALL be applied once.

#### Scenario: A seed decided in the bot

- **GIVEN** a seed of kind personal decided on a card
- **WHEN** the next detection pass runs
- **THEN** the channel is a seed of kind personal, reviewed at that moment, and the poll loop walks its history

#### Scenario: Reviewed in the meantime

- **GIVEN** a rejection decided on a card, and the channel marked a seed with `itgraph mark` before the next detection pass
- **WHEN** that pass runs
- **THEN** the channel stays a seed, and the decision is recorded as not applied

#### Scenario: Applied once

- **GIVEN** a decision applied by a detection pass
- **WHEN** the next pass runs
- **THEN** the channel's review is not touched again
