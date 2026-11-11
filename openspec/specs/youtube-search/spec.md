# youtube-search Specification

## Purpose
Searching YouTube with the operator's queries to find English-language IT channels to review: the request and the one field it reads, the search quota bucket, not asking the same query twice inside its window, the results kept, and the review queue built from them in YouTube's own order.

## Requirements

### Requirement: Search Command

The system SHALL provide `itgraph yt search`, taking queries as arguments, from a file or from standard input, one per line. Blank lines and lines beginning with `#` SHALL be ignored, and a query given twice SHALL be asked once.

#### Scenario: Queries from standard input

- **GIVEN** standard input holding three queries, a blank line and a comment
- **WHEN** the command runs with `--from-file -`
- **THEN** the three queries are asked

#### Scenario: A repeated line

- **GIVEN** the same query on two lines
- **WHEN** the command runs
- **THEN** it is asked once

### Requirement: What A Search Asks

A search SHALL ask for 50 results a page, in English by relevance, of the type the operator gives: recent videos by default, or channels. It SHALL read only the id of the channel each result belongs to, and the token for the next page.

#### Scenario: Searching videos

- **WHEN** a query is searched for videos
- **THEN** the request names the query, the video type, 50 results, English as the relevance language and a publication bound
- **AND** reads each result's channel id and nothing else of it

#### Scenario: Searching channels

- **WHEN** a query is searched for channels
- **THEN** the request names the query, the channel type, 50 results and English as the relevance language
- **AND** reads each result's channel id and nothing else of it

#### Scenario: More pages on request

- **WHEN** the command runs with `--pages 3`
- **THEN** each query is asked for at most three pages, following the next-page token

### Requirement: The Publication Window

A video search SHALL ask for videos published in the last `youtube_answers_fresh_days` (30 by default). A query searched again with `--force` while an earlier run of it is in date SHALL ask only for videos published since that run.

#### Scenario: A first video search

- **WHEN** a query is first searched for videos
- **THEN** the request is bounded to videos published in the last 30 days, by default

#### Scenario: A forced repeat

- **GIVEN** a query searched for videos 10 days ago
- **WHEN** it is searched again with `--force`
- **THEN** the request is bounded to videos published since that run

### Requirement: A Query Is Not Asked Twice In Its Window

The system SHALL NOT search a query of one type again while a run of it is in date — younger than `youtube_answers_fresh_days` — unless `--force` is given. A skipped query SHALL be reported as skipped and cost nothing.

#### Scenario: A repeat within the window

- **GIVEN** a query searched for channels 5 days ago
- **WHEN** the command runs with it again
- **THEN** no request is made for it, and it is reported as skipped

#### Scenario: Another type is another query

- **GIVEN** a query searched for videos 5 days ago
- **WHEN** it is searched for channels
- **THEN** it is asked

#### Scenario: After the window

- **GIVEN** a query whose last run is older than `youtube_answers_fresh_days`
- **WHEN** the command runs with it
- **THEN** it is asked

### Requirement: Search Spending

A search SHALL book every request against the search pool alone, make no more requests than `--limit` gives, and commit each page's results before the next request. A spent pool, or `quotaExceeded` from the search bucket, SHALL stop the run, keeping what it stored.

#### Scenario: The search pool is spent

- **GIVEN** a search pool with no units left today
- **WHEN** the command runs
- **THEN** no request is made, and it reports when the next quota day begins
- **AND** the general pool and the poll loop's share are untouched

#### Scenario: A stopped run keeps its pages

- **WHEN** a run stops at its limit after two pages
- **THEN** both pages' results are stored

### Requirement: Search Results Are Kept

The system SHALL store each run — the query, its type, the moment and the publication bound — and each result's channel id with its position in the run. Only runs younger than `youtube_answers_fresh_days` feed the review queue; older ones are kept. A run holds no data read from the API.

#### Scenario: A run's results

- **GIVEN** a video search returning 50 results from 31 distinct channels
- **WHEN** the run is stored
- **THEN** 50 results are stored, each with its position and its channel id
- **AND** no title, description or video id

#### Scenario: A run past its window

- **GIVEN** a run made 31 days ago
- **WHEN** the review queue is built
- **THEN** the run is not read, and it and its results are still stored

### Requirement: The Operator's Queries Are Kept

The system SHALL keep each query the operator has searched, with its type and when it was first and last asked, for as long as the database lives. This record holds no data read from the API.

#### Scenario: A query asked twice over two months

- **GIVEN** a query asked in September and again in November
- **WHEN** the queries are listed
- **THEN** it appears once, first asked in September, last asked in November

### Requirement: Search Ranking

The system SHALL take the channels in results in date that are absent from the inventory, whatever a record's status, round-robin across runs in the order they were made: each run's first position, then each run's second, and so on. A channel already taken SHALL be skipped. No count SHALL order or cut it.

#### Scenario: Two queries interleaved

- **GIVEN** a run whose results name channels A, B, C and a later run naming D, A, E
- **WHEN** the ranking runs
- **THEN** the order is A, D, B, C, E

#### Scenario: Known channels are skipped

- **GIVEN** results naming a `seed`, a `rejected` channel and an unknown one
- **WHEN** the ranking runs
- **THEN** only the unknown one is ranked

### Requirement: Search Candidates Report

The system SHALL provide `itgraph yt search-candidates`, reporting each run in date with its query, type, moment, number of results and number of channels outside the inventory, then the top of the ranking with each channel's id and the query that found it first, then the rest in one line. It SHALL show no title or handle, and make no request.

#### Scenario: A ranked row

- **WHEN** the report runs
- **THEN** each row shows a channel id and a query, and no title or handle

#### Scenario: Without a key

- **GIVEN** no API key configured
- **WHEN** the report runs
- **THEN** it completes and makes no request

### Requirement: Search Candidates Are Added On Request

With `--add`, `itgraph yt search-candidates` SHALL create a record for each ranked channel shown, 100 by default, with status `candidate` and discovery source `search`, unreviewed and without API data.

#### Scenario: The top hundred are added

- **GIVEN** 150 ranked channels
- **WHEN** the command runs with `--add`
- **THEN** the first 100 become `candidate` records with discovery source `search`
- **AND** no request is made

### Requirement: Search Is Not Scheduled

No scheduled job SHALL run `itgraph yt search` or `itgraph yt search-candidates`.

#### Scenario: The deployed jobs

- **WHEN** the deploy's manifests are rendered with an API key
- **THEN** no scheduled job runs either command

### Requirement: Search Logs Name Ids Only

Log records written while searching or ranking SHALL identify runs and channels by id only, and MUST NOT carry a query, a title or a request URL.

#### Scenario: Searching at debug level

- **GIVEN** logging at debug level
- **WHEN** a search runs
- **THEN** no log record contains the query or a request URL
