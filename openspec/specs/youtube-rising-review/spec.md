# youtube-rising-review Specification

## Purpose
How YouTube's rising channels reach the operator for review: the daily selection from the two rankings, the record each offered channel gains, the card the bot sends into the topic of its language, the buttons that review it as `itgraph yt mark` does, and how a decision taken there reaches the inventory.

## Requirements

### Requirement: A Daily Selection From Both Rankings

The system SHALL select, once a day, the highest-ranked rising channels that were never offered before: at most `youtube_rising_chart_offers_per_region` chart channels of each region, and at most `youtube_rising_link_offers_per_day` link channels. A channel that sits in both rankings SHALL be taken by the link one. A day's selection SHALL be computed once and stored, an empty one included. The day is the Moscow date of computing.

#### Scenario: The head of both rankings

- **GIVEN** seven never-offered chart channels of `RU` and four link channels, with sizes of three and three
- **WHEN** the day's selection is computed
- **THEN** the three chart channels ranked highest and the three link channels ranked highest are offered, in rank order, and the rest are not

#### Scenario: The link ranking wins a channel both hold

- **GIVEN** a channel ranked by both the link ranking and the chart ranking
- **WHEN** the day's selection is computed
- **THEN** it is offered as a link offer, and the chart offers go to channels the link ranking did not take

#### Scenario: The overflow waits for the next day

- **GIVEN** two link channels left out of yesterday's selection, still rising today
- **WHEN** today's selection is computed
- **THEN** they are ranked again with whatever rose since, and offered if they are among the three highest

#### Scenario: A day is computed once

- **GIVEN** today's selection already computed
- **WHEN** the selection is asked for again today
- **THEN** nothing is stored, and it says the day has its selection

#### Scenario: An empty day

- **GIVEN** no never-offered channel in either ranking
- **WHEN** the day's selection is computed
- **THEN** the day is recorded as computed, with nothing offered

### Requirement: An Offered Channel Becomes A Candidate Record

The selection SHALL create, for each channel it offers, a record with status `candidate`, unreviewed, if the inventory holds none. A chart offer's record SHALL carry the discovery source `chart`; a link offer's the source of its strongest evidence, a tie going `telegram`, then `mention`, then `featured`. Where a YouTube key is configured, the selection SHALL buy the offered channels' payloads with one `channels.list` call, stored in the same transaction as the day and before any offer can be claimed, so a card is titled the day it is sent; where no key is configured — the test environment's selection — it SHALL make no request, and the records are created without API data.

#### Scenario: A record without data

- **GIVEN** a link offer of a channel no record holds, in an environment with no key
- **WHEN** the day's selection is stored
- **THEN** a `candidate` record exists for it, with the discovery source of its strongest evidence
- **AND** no request was made

#### Scenario: The payloads are bought with the day

- **GIVEN** an environment with a key configured
- **WHEN** the day's selection is stored
- **THEN** each offered channel's record exists with its payload, written by the same transaction as the day
- **AND** the call was booked under the offer command, against the general pool

#### Scenario: A refused buy loses nothing but the titles

- **GIVEN** an environment with a key, and the buy call refused by YouTube or the ledger
- **WHEN** the day's selection is stored
- **THEN** the day and its candidate records are stored as ever, and the cards link by id until the refresh fetches the payloads

#### Scenario: A channel that gained a record another way

- **GIVEN** a ranked channel added with `itgraph yt add` after the ranking was worked out
- **WHEN** the day's selection is computed
- **THEN** it is not offered, and no second record is created

#### Scenario: The payload arrives with the refresh

- **GIVEN** an offered channel's record with no payload — no key configured, or a refused buy
- **WHEN** `itgraph yt refresh` next runs
- **THEN** its payload is fetched, and it can be exported for review

### Requirement: Each Channel Is Offered Once

The system SHALL offer a channel at most once, whatever happens to its offer. A channel nobody reviewed SHALL stay a candidate and SHALL NOT be offered again or recalled.

#### Scenario: Offered and left alone

- **GIVEN** a channel offered yesterday, with no decision, still rising today
- **WHEN** today's selection is computed
- **THEN** it is not offered again, and its status is unchanged

#### Scenario: Marked maybe

- **GIVEN** a channel marked maybe from its offer, still rising a week later
- **WHEN** that day's selection is computed
- **THEN** it is not offered again

### Requirement: Selecting Ranks Only Collected Data

Computing a day's selection SHALL rank from what is stored alone: the ranking itself SHALL make no request to YouTube and need no API key. The selection's one request SHALL be the buy of the offered channels' payloads, and only where a key is configured. It SHALL write nothing but the day's selection, the records it creates, and those payloads.

#### Scenario: Without a key

- **GIVEN** an environment with no YouTube key
- **WHEN** the day's selection is computed
- **THEN** it completes, offering what the stored rankings' data supports, and makes no request

### Requirement: An Offer Is A Card In The Topic Of Its Language

The bot SHALL send each offer as one message to the YouTube chat: a chart offer of `US` to the English topic and of `RU` to the Russian; a link offer to the topic of its payload's `defaultLanguage` when that is `en` or `ru`, and otherwise to the English topic, the card marking the language unknown or other than these two. The card SHALL link the channel — by its handle when a stored payload carries one, by its id otherwise — and show its title when a stored payload holds one; a link offer's card SHALL name the families that referred to it in the window and before it, and the first reference in the window; a chart offer's card SHALL name the region, the position, and link the charting video, named by its title when the day's reading holds one and by its id otherwise. Everything from a channel or a video SHALL be escaped, and the link preview SHALL be forced to what the card links first.

#### Scenario: A link offer of a known language

- **GIVEN** a link offer whose payload's `defaultLanguage` is `ru`
- **WHEN** the bot sends it
- **THEN** the card goes to the Russian topic, links the channel, and shows its title and evidence

#### Scenario: A link offer of an unknown language

- **GIVEN** a link offer with no stored payload
- **WHEN** the bot sends it
- **THEN** the card goes to the English topic, links the channel by its id, shows no title, and marks the language unknown

#### Scenario: A chart offer

- **GIVEN** a chart offer of `RU` at position four, whose day's reading holds the charting video's title
- **WHEN** the bot sends it
- **THEN** the card goes to the Russian topic, names the region and position, and links the charting video by its title

#### Scenario: A title that looks like markup

- **GIVEN** a channel titled with angle brackets and an ampersand
- **WHEN** its card is sent
- **THEN** the title is shown as written, and nothing in it is read as markup

### Requirement: Offers Are Delivered Outside Quiet Hours, Uncapped

The bot SHALL send offers in rank order, each to its topic, outside that topic's quiet hours, neither counted against a cap nor held for a digest. Each offer SHALL be marked delivered once sent; one that failed SHALL be tried again, and SHALL NOT be sent twice once marked. A topic that refuses SHALL fail the send, and the offer SHALL NOT go to any other topic or chat. With no YouTube chat configured, an offer SHALL wait undelivered.

#### Scenario: The day's selection

- **GIVEN** six offers computed at 11:00 and a topic that has sent its daily cap of alerts
- **WHEN** the bot next polls
- **THEN** the cards are sent, in rank order, and no alert is sent past the cap because of them

#### Scenario: Quiet hours

- **GIVEN** an offer not yet delivered at 03:00 Moscow time
- **WHEN** the bot polls
- **THEN** nothing is sent until the topic's quiet hours end

#### Scenario: A failed send

- **GIVEN** a card Telegram refused to send
- **WHEN** the bot next polls
- **THEN** it is tried again, and the cards sent before it are not sent again

#### Scenario: A deleted topic

- **GIVEN** the English topic deleted in Telegram
- **WHEN** an offer whose topic is English is due
- **THEN** the send fails and is recorded against the offer
- **AND** no message of it reaches the Russian topic, General or the Telegram chat

### Requirement: An Offer Reviewed Elsewhere Is Not Sent

An offer whose channel is no longer a candidate or a maybe when the bot comes to send it SHALL NOT be sent, and SHALL be closed as delivered.

#### Scenario: Marked from the command line first

- **GIVEN** an offer computed at 11:00, and its channel marked a seed with `itgraph yt mark` before the bot sent it
- **WHEN** the bot polls
- **THEN** no card for it is sent, and the offer is closed

### Requirement: A Card Is Reviewed As `itgraph yt mark` Reviews

A card SHALL carry three buttons: «🌱 В сиды», «🤔 Может быть», «🚫 Отклонить». Maybe SHALL be decided by one press. Seed SHALL ask for one of the five kinds, and then — only while the offer's language is unknown or other than `en` and `ru` — for one of the three languages; reject SHALL ask for one of the seven reasons; each second step SHALL carry «↩️ Назад» back. A decision SHALL be what `itgraph yt mark` would record with the same choices.

#### Scenario: A seed of a known language

- **WHEN** «🌱 В сиды» is pressed, then a kind, on an offer whose payload's `defaultLanguage` is `en`
- **THEN** the decision is a seed of that kind, in English

#### Scenario: A seed of an unknown language

- **WHEN** «🌱 В сиды» is pressed, then a kind, then «🇷🇺 RU»
- **THEN** the decision is a seed of that kind, in Russian

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

Once decided, the card SHALL show the decision and carry no button. A press on an offer already decided SHALL change nothing and SHALL be answered that it is decided. A decision SHALL be changed only with `itgraph yt mark`.

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

A decision SHALL be applied to the channel's review by the detection pass that follows it, as `itgraph yt mark` applies the same choices, if the channel is still a candidate or a maybe. Otherwise it SHALL NOT be applied, and SHALL be recorded as not applied. Either way it SHALL be applied once.

#### Scenario: A seed decided in the bot

- **GIVEN** a seed of kind personal and language `en` decided on a card
- **WHEN** the next detection pass runs
- **THEN** the channel is a seed of that kind and language, reviewed at that moment, and the backfill loop walks its history

#### Scenario: Reviewed in the meantime

- **GIVEN** a rejection decided on a card, and the channel marked a seed with `itgraph yt mark` before the next detection pass
- **WHEN** that pass runs
- **THEN** the channel stays a seed, and the decision is recorded as not applied

#### Scenario: Applied once

- **GIVEN** a decision applied by a detection pass
- **WHEN** the next pass runs
- **THEN** the channel's review is not touched again
