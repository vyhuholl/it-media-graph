# personal-data Specification

## Purpose
What names a person in the payloads Telegram returns, and that the system keeps none of it: not when collecting, not in what it stored before, not in a restored copy. Channels are what the project studies, and they stay wherever they appear.

## Requirements

### Requirement: What Names A Person

A value names a person when it is a user's account, a mention of a user by id, an entry that identifies a user through its peer, a name signed under a post or carried by a forward, or a list of user ids. Removing it SHALL make it null where it is a field and drop it where it is an item of a list. A channel, a bot, a post's text and counters, a linked page's content and a contact the channel posted SHALL stay as received.

#### Scenario: A post's latest commenters

- **GIVEN** a post whose latest commenters are two users and a channel
- **WHEN** what names a person is removed
- **THEN** the list holds the channel alone
- **AND** the number of comments is unchanged

#### Scenario: Its paid reactors

- **GIVEN** a post whose top paid reactors are a user, a channel and an anonymous reactor
- **WHEN** what names a person is removed
- **THEN** the channel and the anonymous reactor remain, with their counts
- **AND** the reaction counts are unchanged

#### Scenario: Who wrote it

- **GIVEN** a post signed with a name, whose author is a user, which forwards a user's message under a hidden sender's name and a signature
- **WHEN** what names a person is removed
- **THEN** the author, the forward's origin and the three names are null
- **AND** the forward header remains, so the post still reads as a forward

#### Scenario: A forward from a channel

- **GIVEN** a post forwarded from a channel
- **WHEN** what names a person is removed
- **THEN** the forward's origin is the channel, as received

#### Scenario: Mentions

- **GIVEN** a post whose entities are a mention of a user by id, an input mention of a user and a link
- **WHEN** what names a person is removed
- **THEN** the entities hold the link alone, and the text is unchanged

#### Scenario: Polls and giveaways

- **GIVEN** a poll whose results list recent voters, and giveaway results listing winners
- **WHEN** what names a person is removed
- **THEN** both lists are empty, and the totals remain

#### Scenario: A channel's payload

- **GIVEN** a channel's extended information listing a bot, a user who is not a bot, and pending join requesters
- **WHEN** what names a person is removed
- **THEN** the bot remains, the user is gone and the requesters are an empty list

#### Scenario: A page linked from a post

- **GIVEN** a post linking a web page whose preview names its author
- **WHEN** what names a person is removed
- **THEN** the preview is unchanged

#### Scenario: A contact the channel posted

- **GIVEN** a post whose media is a contact card, with a user id, a phone number and names
- **WHEN** what names a person is removed
- **THEN** the card is unchanged: it is what the channel chose to publish

#### Scenario: Removing twice

- **WHEN** what names a person is removed from a payload that holds none
- **THEN** the payload is unchanged

### Requirement: The Collector Stores No Person

Every payload the system stores, of a post or of a channel, SHALL have had what names a person removed before it is written, by every command that stores one. A person's value SHALL NOT appear in a log line or an error either.

#### Scenario: A polled post

- **GIVEN** a channel whose newest post has commenters who are users
- **WHEN** the collection loop stores it
- **THEN** the stored payload names no user

#### Scenario: A history walk

- **WHEN** a history walk stores posts with signatures and paid reactors
- **THEN** no stored payload names a person

#### Scenario: A channel's extended information

- **WHEN** the metadata pass stores a channel's payload listing a user who is not a bot
- **THEN** the stored payload lists the channel's bots alone

### Requirement: What Was Stored Before Is Cleared Once

The schema SHALL have a revision that removes what names a person from every payload stored before it, of posts and of channels, and then rewrites both tables on disk, so their files no longer hold the old values. It SHALL change no other value, and running it on rows that name nobody SHALL change nothing. Its downgrade SHALL change nothing, since what it removed cannot come back.

#### Scenario: A database collected under the previous release

- **GIVEN** a database at the previous revision whose posts and channel payloads name people
- **WHEN** the schema is brought to head
- **THEN** no stored payload names a person
- **AND** every other value of every payload is unchanged

#### Scenario: Rows that name nobody

- **GIVEN** a database at the previous revision whose payloads name nobody
- **WHEN** the schema is brought to head
- **THEN** no payload has changed

#### Scenario: The dump taken first

- **GIVEN** a working database whose schema is brought to head
- **WHEN** the dump taken before the migration is listed
- **THEN** it holds the people as they were, and is kept and pruned like any other full dump, by age

#### Scenario: Going back

- **GIVEN** a scratch database at head
- **WHEN** the schema is taken down one revision
- **THEN** no table or value changes

### Requirement: Nothing Derived Depends On A Person

Everything derived from the raw layer SHALL be the same whether or not what names a person has been removed from it: edges, references, metric readings, links to YouTube and alerts.

#### Scenario: Deriving over cleared payloads

- **GIVEN** the same posts, once as received and once with what names a person removed
- **WHEN** each is derived from
- **THEN** the edges, references, readings and links are identical
