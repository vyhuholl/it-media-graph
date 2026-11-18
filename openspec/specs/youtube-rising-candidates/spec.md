# youtube-rising-candidates Specification

## Purpose
Which YouTube channels are rising: the link ranking — channels newly cited, referenced or featured by seed families on both platforms within a window — and the chart ranking — the channels of the day's regional mostPopular reading. How each is worked out and ordered, what is left out and counted, the report that shows them, and the bounded unattended lookups that keep the link ranking's evidence complete.

## Requirements

### Requirement: What Counts As Evidence

The system SHALL take the evidence for a channel from three sources, each naming a channel by its own rules: a Telegram seed's post linking it, as `youtube-telegram-links` reads links; a YouTube seed's video referencing it in title or description, as `youtube-mention-candidates` reads references, a featured list naming it alone not counting here; and a YouTube seed newly featuring it in its channel sections. A reference that awaits a lookup SHALL be counted and name no channel; one looked up as not found SHALL be counted as not found.

#### Scenario: All three sources name one channel

- **GIVEN** a Telegram seed's post, a YouTube seed's video description and a YouTube seed's newest featured reading all naming `UCfakeChannel0000000000b`
- **WHEN** the link ranking is worked out
- **THEN** all three are evidence for that channel

#### Scenario: A reference awaiting a lookup

- **GIVEN** a seed's video linking a handle that nothing stored answers
- **WHEN** the link ranking is worked out
- **THEN** the handle is counted as awaiting a lookup and names no channel

#### Scenario: A reference looked up as not found

- **GIVEN** a seed's post linking a video recorded as not found
- **WHEN** the link ranking is worked out
- **THEN** it is counted as not found and is evidence for no channel

#### Scenario: A standing feature is not this queue's evidence

- **GIVEN** a channel outside the inventory listed in a seed's featured reading, and listed equally in the reading before it
- **WHEN** the link ranking is worked out
- **THEN** that listing is no evidence in this ranking

### Requirement: An Appearance Is New In A Seed's List

The system SHALL take a seed's featuring a channel as evidence only from the moment the channel appeared in the seed's featured readings: a channel in the newest in-date reading that the reading before it, of any age, lacked. A seed's first reading SHALL establish its list, and nothing in it SHALL be an appearance. An appearance is dated by the reading that first lists it.

#### Scenario: A channel added to a list

- **GIVEN** a seed whose previous reading lists two channels and whose newest reading lists those two and `UCfakeChannel0000000000b`
- **WHEN** appearances are read
- **THEN** `UCfakeChannel0000000000b` appeared, dated by the newest reading's fetch

#### Scenario: The first reading establishes the list

- **GIVEN** a seed with exactly one reading, listing five channels
- **WHEN** appearances are read
- **THEN** none of the five is an appearance

#### Scenario: A channel taken off and put back

- **GIVEN** a seed whose readings list a channel, then drop it, then list it again
- **WHEN** appearances are read
- **THEN** the re-listing is an appearance, dated by the reading that lists it again

#### Scenario: A list out of date contributes nothing

- **GIVEN** a seed whose newest reading is older than `youtube_answers_fresh_days`
- **WHEN** the link ranking is worked out
- **THEN** the seed contributes no evidence, and is counted among the seeds whose list is out of date

### Requirement: Families Across Both Platforms

The system SHALL count evidence by distinct families: a Telegram seed's family — the family `tg_channel_families` gives it, or the seed's own channel when it is in none — for evidence from Telegram posts; a YouTube seed's family — the family `yt_channel_families` gives it, or the seed's own channel — for evidence from videos, references and featured readings. A family of one platform and a family of the other are two families, and a family SHALL count once whatever its members did.

#### Scenario: One family, many links

- **GIVEN** two YouTube seeds confirmed as one family, one linking a channel in a video's description and the other featuring it
- **WHEN** the link ranking is worked out
- **THEN** the channel is referred to by one family

#### Scenario: A family across platforms is two families

- **GIVEN** a Telegram seed and a YouTube seed with no confirmed affiliation, each linking the same channel
- **WHEN** the link ranking is worked out
- **THEN** the channel is referred to by two families

#### Scenario: A family's repeated references count once

- **GIVEN** one Telegram family linking the same channel in five posts, by handle and by video
- **WHEN** the link ranking is worked out
- **THEN** that family counts once

### Requirement: What Is Rising

The system SHALL count as rising a channel absent from the inventory — whatever a record's status would be, gone included, there being no record — that at least `rising_min_families` distinct families referred to within the trailing `rising_window_days`, the same settings Telegram's rising ranking reads. Evidence SHALL be dated by its content: a post's or a video's publication time, an appearance by its reading. A channel the inventory holds SHALL NOT be ranked, whatever its status.

#### Scenario: Two families in the window

- **GIVEN** a channel linked by a Telegram family in a post of last week and featured by a YouTube family's newest reading
- **WHEN** the link ranking is worked out
- **THEN** the channel is rising, with two families in the window

#### Scenario: One family is not enough

- **GIVEN** a channel referenced within the window by three seeds of one family
- **WHEN** the link ranking is worked out
- **THEN** the channel is not rising

#### Scenario: Old evidence is not in the window

- **GIVEN** a channel referred to by three families, every reference older than the window
- **WHEN** the link ranking is worked out
- **THEN** the channel is not rising

#### Scenario: A known channel is never ranked

- **GIVEN** a channel referred to by many families, one held as `candidate`, one as `maybe`, one rejected and one marked gone
- **WHEN** the link ranking is worked out
- **THEN** none of them is ranked, nor counted among those left out

### Requirement: How Rising Link Candidates Rank

The system SHALL rank rising channels by the families that referred to them within the window, most first. A tie SHALL go to the one fewer families referred to before the window, as the newer, and then to a fixed order by channel id, so that the same data always gives the same ranking.

#### Scenario: More families first

- **GIVEN** a rising channel with three families in the window and one with two
- **WHEN** the link ranking is worked out
- **THEN** the one with three ranks first

#### Scenario: The newer comes first

- **GIVEN** two rising channels with two families each, one of them referred to by four families before the window and the other by none
- **WHEN** the link ranking is worked out
- **THEN** the one with none before the window ranks first

### Requirement: The Daily Chart Reading

The refresh SHALL read, once a day, the `chart=mostPopular` listing of every region `youtube_chart_regions` names, at the category `youtube_chart_category` gives, asking for each video's id, its channel's id and the token for the next page, and nothing else. Each answer SHALL be stored as a raw reading for its region and moment, appended and never overwritten. An answer that fails or is refused SHALL fail nothing else in the refresh.

#### Scenario: The defaults

- **GIVEN** no regions and no category set
- **WHEN** the chart is read
- **THEN** the regions are `US` and `RU`, and the category 28

#### Scenario: Two regions, two readings

- **GIVEN** regions `US` and `RU` configured
- **WHEN** the refresh runs
- **THEN** the chart of each region is asked for, and each answer is stored as its own reading

#### Scenario: An empty chart

- **GIVEN** a region whose chart the API answers with no video
- **WHEN** the refresh runs
- **THEN** an empty reading is stored for that region, and the next day's reading replaces nothing of it

#### Scenario: A refused chart fails nothing else

- **GIVEN** a region whose chart the API refuses with a client error
- **WHEN** the refresh runs
- **THEN** the rest of the refresh completes, and no reading is stored for that region that day

#### Scenario: A reading is never rewritten

- **GIVEN** yesterday's chart reading of `RU`
- **WHEN** today's refresh runs
- **THEN** yesterday's reading is unchanged, and today's is stored beside it

### Requirement: The Chart Ranking

The system SHALL rank, for each configured region, the channels of that region's newest chart reading fetched on the Moscow day of computing that are absent from the inventory, in the reading's order. Every such channel SHALL rank: no threshold, no window, no filter of subscribers or age. A region with no reading that day SHALL contribute nothing, and SHALL be said.

#### Scenario: The day's reading in order

- **GIVEN** today's `US` reading whose first, third and seventh videos belong to channels absent from the inventory, the rest to channels it holds
- **WHEN** the chart ranking is worked out
- **THEN** those three channels rank, in the reading's order

#### Scenario: A channel already held is not ranked

- **GIVEN** a chart video of a channel held as `rejected`
- **WHEN** the chart ranking is worked out
- **THEN** that channel is not ranked

#### Scenario: No reading today

- **GIVEN** a region whose newest chart reading was fetched yesterday
- **WHEN** the chart ranking is worked out today
- **THEN** the region contributes nothing, and the ranking says so

#### Scenario: Nothing is filtered as noise

- **GIVEN** a chart reading holding a gadget-review channel with a million subscribers and a two-year-old channel with fifty videos
- **WHEN** the chart ranking is worked out
- **THEN** both rank, in the reading's order

### Requirement: The Rising Report

The system SHALL provide `itgraph yt rising`, printing both rankings: each link-ranked channel's id, its families in the window and before it, and its first reference in the window; each chart-ranked channel's id, its region and its position; then how many references await a lookup, how many were not found, and how many seeds' featured lists are out of date. It SHALL show no data read from the API beyond channel ids, make no request, write nothing, and need no API key.

#### Scenario: A ranked row of each ranking

- **GIVEN** a rising channel with two families in the window, and a chart channel at position four of `RU`
- **WHEN** the report runs
- **THEN** the link row shows the channel's id, two families and the first reference in the window
- **AND** the chart row shows the channel's id, the region `RU` and position four
- **AND** no title, handle or other data read from the API appears

#### Scenario: What could not be counted

- **WHEN** the report runs
- **THEN** it states how many references await a lookup, pointing to the lookups, and how many were not found
- **AND** how many seeds' featured lists are out of date

#### Scenario: Without a key

- **GIVEN** no API key configured
- **WHEN** the report runs
- **THEN** it completes and makes no request

#### Scenario: Reporting writes nothing

- **WHEN** the report runs
- **THEN** no row in the database is inserted, updated or deleted

### Requirement: Nothing Joined Is Stored

The system SHALL work both rankings out of the stored payloads, featured readings, chart readings and lookups each time they are asked, and MUST NOT store which families referred to a channel, a count, a rank, or anything else joined from the sources. The raw readings themselves are what was read, and are kept as read.

#### Scenario: Ranking writes nothing

- **WHEN** either ranking is worked out
- **THEN** no row in the database is inserted, updated or deleted

#### Scenario: A lookup no longer current changes the next ranking

- **GIVEN** a channel ranked through a video's lookup
- **WHEN** that lookup grows older than `youtube_answers_fresh_days` and the ranking is worked out before the next lookup run
- **THEN** that video awaits a lookup, and the channel loses that reference

### Requirement: Rising Logs Name Ids Only

Log records written while the rankings are worked out or the chart is read SHALL identify channels and videos by id only, and MUST NOT carry a handle, a legacy name, a custom URL, a link or a title.

#### Scenario: Ranking at debug level

- **GIVEN** logging at debug level
- **WHEN** the rankings are worked out over posts linking handles and videos referencing channels
- **THEN** no log record contains a handle, a legacy name, a custom URL or a link
