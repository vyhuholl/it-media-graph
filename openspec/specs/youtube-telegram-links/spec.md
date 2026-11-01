# youtube-telegram-links Specification

## Purpose
Which YouTube channels the Telegram seeds link to, read from the posts already collected, and the queue of channels to review it orders. The links are read on demand and joined with the lookups, and nothing joined from the two is stored, so a channel enters the inventory only as a reviewable candidate.

## Requirements

### Requirement: Where A Post's Links Are Read

The system SHALL read a post's YouTube links from three places only: the links Telegram marked in its text, the links behind its text, and its link preview's URL. Plain text Telegram did not mark as a link, and a preview's title, description, author and site name, SHALL NOT be read.

#### Scenario: A link after an emoji

- **GIVEN** a post whose text holds an emoji and then a link marked at its offset in UTF-16 units
- **WHEN** its links are read
- **THEN** the whole link is read, its first character included

#### Scenario: A link behind text

- **GIVEN** a post whose words `my talk` carry the link `https://youtu.be/fakevideo01`
- **WHEN** its links are read
- **THEN** it links the video `fakevideo01`

#### Scenario: A link only in the preview

- **GIVEN** a post whose link preview's URL is `https://www.youtube.com/watch?v=fakevideo02` and whose text marks no link
- **WHEN** its links are read
- **THEN** it links the video `fakevideo02`

#### Scenario: The preview's metadata is not read

- **GIVEN** a post whose link preview names a channel in its title and author
- **WHEN** its links are read
- **THEN** that channel is not linked by them

### Requirement: Link Forms

The system SHALL read each link by the forms and the handle grammar of `youtube-mention-graph`: a channel id, handle or legacy-name link is a link to that channel, and a video link a link to that video. A link to any other host SHALL link nothing.

#### Scenario: Every video form

- **GIVEN** posts linking one video as `watch?v=`, `youtu.be/` with a share token, `/shorts/`, `/live/` and `music.youtube.com/watch?v=`
- **WHEN** their links are read
- **THEN** each links that video

#### Scenario: A percent-encoded handle in another script

- **GIVEN** a link behind text to `youtube.com/@` followed by a percent-encoded handle in Cyrillic letters
- **WHEN** its links are read
- **THEN** it links the decoded, lowercased handle

#### Scenario: A host that only ends like YouTube

- **GIVEN** a post linking `notyoutube.com/@fake-seed` and `fake-youtube.example/watch?v=fakevideo01`
- **WHEN** its links are read
- **THEN** it links nothing

#### Scenario: A separator ending a handle

- **GIVEN** a post linking `youtube.com/@fake-seed-`
- **WHEN** its links are read
- **THEN** it links the handle `fake-seed`

### Requirement: Telegram Mentions Are Not Handles

The system SHALL NOT read an `@name` in a post — marked as a mention by Telegram or not — as a YouTube handle.

#### Scenario: A Telegram mention

- **GIVEN** a post mentioning `@fake_tg_channel`
- **AND** a YouTube channel whose handle is `@fake_tg_channel`
- **WHEN** its links are read
- **THEN** it links nothing

### Requirement: Forms Counted, Never Followed

The system SHALL count, and never turn into a link to a channel, a custom URL (`youtube.com/c/<name>` or `youtube.com/<name>`), a playlist page and a link through a link shortener. A custom URL SHALL be kept by its name and the families linking it, for a person to look up by hand.

#### Scenario: A custom URL

- **GIVEN** a post linking `youtube.com/c/FakeCustom`
- **WHEN** its links are read
- **THEN** it links no channel
- **AND** the custom URL `fakecustom` is counted with that post's family

#### Scenario: A playlist and a shortened link

- **GIVEN** a post linking `youtube.com/playlist?list=PLfakefake` and `bit.ly/fake-short`
- **WHEN** its links are read
- **THEN** it links no channel
- **AND** one playlist page and one shortened link are counted

### Requirement: Sources Are Seeds' Own Posts

The system SHALL read links only from posts of channels with status `seed` that are not discussion chats, over all their collected history, and only from posts the channel wrote itself. A forwarded post SHALL NOT be read, whoever it was forwarded from.

#### Scenario: Channels out of scope are not sources

- **GIVEN** posts of a `candidate`, a `maybe` and a `rejected` channel and of a seed's discussion chat, each linking a YouTube channel
- **WHEN** links are read
- **THEN** none of them is read

#### Scenario: A forwarded post is not read

- **GIVEN** a seed's post forwarded from another channel, linking a YouTube channel
- **WHEN** links are read
- **THEN** that link is not read

#### Scenario: Old posts count

- **GIVEN** a seed's post from years ago linking a YouTube channel
- **WHEN** links are read
- **THEN** that link is read

### Requirement: Families Are The Unit Of Evidence

The system SHALL count, for each YouTube channel, the distinct families of the seeds linking it, a family being a set of confirmed affiliated channels and a channel outside every family a family of its own. Posts, links and channels within one family SHALL count once.

#### Scenario: Two affiliated seeds are one family

- **GIVEN** two seeds confirmed as one author's, each linking the same YouTube channel
- **WHEN** links are counted
- **THEN** that channel is linked by one family

#### Scenario: A family linking repeatedly is one family

- **GIVEN** one seed linking the same YouTube channel in five posts, once by handle and four times by its videos
- **WHEN** links are counted
- **THEN** that channel is linked by one family

#### Scenario: Unaffiliated seeds are separate families

- **GIVEN** two seeds with no confirmed affiliation, each linking the same YouTube channel
- **WHEN** links are counted
- **THEN** that channel is linked by two families

### Requirement: A Link Names A Channel Through What Is Stored

The system SHALL take a channel link by id as that channel, and a video, handle or legacy-name link as the channel a stored video payload, a stored channel payload or a lookup in date names. A link nothing stored answers SHALL await a lookup, and one looked up as not found SHALL name no channel.

#### Scenario: A video link names its channel

- **GIVEN** a post linking a video whose lookup names `UCfakeChannel0000000000b`
- **WHEN** links are counted
- **THEN** the post's family links `UCfakeChannel0000000000b`

#### Scenario: Two forms, one channel

- **GIVEN** one family linking `youtube.com/@fake-unknown` and another linking a video, both looked up as `UCfakeChannel0000000000b`
- **WHEN** links are counted
- **THEN** `UCfakeChannel0000000000b` is linked by two families

#### Scenario: A link not yet looked up

- **GIVEN** a post linking a video with no stored payload and no lookup
- **WHEN** links are counted
- **THEN** the video awaits a lookup and names no channel

#### Scenario: A link looked up as not found

- **GIVEN** a post linking a handle looked up as not found
- **WHEN** links are counted
- **THEN** it names no channel, and is counted as not found

### Requirement: Ranking

The system SHALL rank the YouTube channels absent from the inventory — whatever a record's status, gone included — by the number of distinct families linking them, most first, then by the newest seed post linking them, newest first, then by channel id. A channel linked by fewer families than the minimum, by default two, SHALL NOT be ranked.

#### Scenario: More families rank first

- **GIVEN** a channel linked by three families and another by two
- **WHEN** the ranking runs
- **THEN** the channel linked by three families is first

#### Scenario: A tie goes to the newer link

- **GIVEN** two channels linked by two families each, one most recently in a post from last week and the other in a post from last year
- **WHEN** the ranking runs
- **THEN** the channel linked last week is first

#### Scenario: Known channels are never ranked

- **GIVEN** channels linked by many families, one of them a `seed`, one `maybe`, one `rejected`, one `candidate` and one marked gone
- **WHEN** the ranking runs
- **THEN** none of them is ranked

#### Scenario: One family is not enough by default

- **GIVEN** a channel linked by one family
- **WHEN** the ranking runs with the default minimum
- **THEN** it is not ranked

#### Scenario: Ties are ordered the same way every time

- **GIVEN** two channels with equal families and equal newest links
- **WHEN** the ranking runs twice over unchanged data
- **THEN** both runs order them the same way

### Requirement: Candidates Report

The system SHALL provide `itgraph yt tg-candidates`, reporting what was read, the top of the ranking, the rest of it in one line, and what awaits a lookup, was not found, or is counted and not followed. Each ranked channel SHALL show its id, the handle or legacy name Telegram wrote for it if any, its family count and its newest link's date. The report SHALL show no data read from the API.

#### Scenario: A ranked row

- **GIVEN** a channel linked by two families as `youtube.com/@fake-unknown` and by its videos
- **WHEN** the report runs
- **THEN** its row shows its channel id, `@fake-unknown`, two families and the date of the newest post linking it
- **AND** no title, subscriber count or other data read from the API

#### Scenario: The top and the rest

- **GIVEN** 150 ranked channels
- **WHEN** the report runs with a top of 100
- **THEN** 100 rows are shown, and one line says how many more are ranked, by family count

#### Scenario: What could not be counted

- **WHEN** the report runs
- **THEN** it states how many links await a lookup and how many were not found
- **AND** lists each custom URL with the number of families linking it
- **AND** states how many playlist pages and shortened links were counted

#### Scenario: Without a key

- **GIVEN** no API key configured
- **WHEN** the report runs
- **THEN** it completes and makes no request to YouTube or Telegram

### Requirement: Candidates Are Added On Request

With `--add`, `itgraph yt tg-candidates` SHALL create a record for each of the ranked channels shown, 100 by default, with status `candidate` and discovery source `telegram`, unreviewed and without API data. It SHALL refuse, writing nothing, while any link awaits a lookup.

#### Scenario: The top hundred are added

- **GIVEN** 150 ranked channels and nothing awaiting a lookup
- **WHEN** the command runs with `--add`
- **THEN** the first 100 become `candidate` records with discovery source `telegram`
- **AND** no review is applied and no request is made

#### Scenario: An incomplete lookup refuses the cut

- **GIVEN** links awaiting a lookup
- **WHEN** the command runs with `--add`
- **THEN** it fails, saying how many await one and to run `itgraph yt tg-lookup`
- **AND** no record is created

#### Scenario: The next run brings the next hundred

- **GIVEN** a previous `--add` whose candidates have since been reviewed or left unreviewed
- **WHEN** the command runs with `--add` again
- **THEN** none of them is added again, and the next ranked channels are

#### Scenario: A candidate's data arrives with the refresh

- **GIVEN** a candidate added from Telegram, with no API data
- **WHEN** `itgraph yt refresh` runs
- **THEN** its payload is fetched, and it can be exported for review

### Requirement: Nothing Joined Is Stored

The system SHALL work the links, families and ranking out of `raw_messages`, the stored payloads and the lookups each time it is asked, and MUST NOT store which families link a channel, a count, a rank, or anything else joined from Telegram and the API. A record created by `--add` holds only what any record holds.

#### Scenario: Reporting writes nothing

- **WHEN** the report runs without `--add`
- **THEN** no row in the database is inserted, updated or deleted, apart from what expiry removes before any command

#### Scenario: An expired lookup changes the next report

- **GIVEN** a channel ranked through a video's lookup
- **WHEN** that lookup expires and the report runs before the next lookup run
- **THEN** that video awaits a lookup, and the channel loses that link

### Requirement: Not Scheduled

No scheduled job SHALL run `itgraph yt tg-lookup` or `itgraph yt tg-candidates`: the report names channels read out of the operator's own subscriptions, and a job's output is kept in the cluster's logs.

#### Scenario: The deployed jobs

- **WHEN** the deploy's manifests are rendered with an API key
- **THEN** no scheduled job runs either command

### Requirement: Telegram Links Logs Name Ids Only

Log records written while reading links and ranking SHALL identify Telegram channels, YouTube channels and videos by id only, and MUST NOT carry a username, a handle, a legacy name, a custom URL or a link.

#### Scenario: Ranking at debug level

- **GIVEN** logging at debug level
- **WHEN** the report runs over posts linking handles and custom URLs
- **THEN** no log record contains a username, a handle, a custom URL or a link
