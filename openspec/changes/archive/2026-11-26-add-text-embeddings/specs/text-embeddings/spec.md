# Spec Delta

## Purpose

Semantic vectors over the texts the project already collects — Telegram post texts and YouTube titles with descriptions — so a text can be found by what it is about, and the semantic clustering axis PLAN.md names can read the same vectors.

## ADDED Requirements

### Requirement: What Is Embedded

The system SHALL embed the text of every Telegram message the raw layer holds and the title and description of every YouTube video the raw layer holds, title and description joined into one text. A message without text SHALL NOT be embedded. A video without a description SHALL still be embedded from its title alone.

#### Scenario: A post with text

- **GIVEN** a Telegram message whose stored payload carries a non-empty text
- **WHEN** the embedding job runs
- **THEN** the text is embedded

#### Scenario: A media-only message

- **GIVEN** a Telegram message whose payload carries no text
- **WHEN** the embedding job runs
- **THEN** no embedding is made for it

#### Scenario: A video without a description

- **GIVEN** a YouTube video whose payload carries a title and an empty description
- **WHEN** the embedding job runs
- **THEN** the title alone is embedded

### Requirement: Embed Once, Re-embed On Change

The system SHALL embed a Telegram text once: the raw layer keeps the first fetch of a message, so nothing changes under the embedding. A YouTube embedding SHALL carry a hash of the text it was made from, and a refresh that changes the video's title or description SHALL re-embed it; an unchanged text MUST NOT be re-embedded.

#### Scenario: An edit after the first fetch

- **GIVEN** a Telegram message that was edited after the collector stored it
- **WHEN** the embedding job runs
- **THEN** the embedding keeps the text of the first fetch, as the raw layer does

#### Scenario: A description changes on refresh

- **GIVEN** an embedded video whose refreshed payload carries a different description
- **WHEN** the embedding job runs
- **THEN** the video is re-embedded from the new text, still one embedding per video

#### Scenario: An unchanged text is left alone

- **GIVEN** an embedded video refreshed with the same title and description
- **WHEN** the embedding job runs
- **THEN** the video is not re-embedded

### Requirement: Long Texts Are Embedded From The Head

The system SHALL embed the head of a text longer than the model's input window and discard the tail, so one text yields exactly one vector.

#### Scenario: A post longer than the window

- **GIVEN** a Telegram post whose text is longer than the model's input window
- **WHEN** it is embedded
- **THEN** one vector is made, from the beginning of the text

#### Scenario: A long description

- **GIVEN** a video whose joined title and description exceed the model's input window
- **WHEN** it is embedded
- **THEN** one vector is made, from the beginning of the joined text

### Requirement: Every Vector Names Its Model

Each embedding SHALL name the model that produced it. A row whose model is not the configured one SHALL be treated as stale and re-embedded, and search SHALL rank only vectors made by the model it embeds the query with.

#### Scenario: The configured model changes

- **GIVEN** embeddings made by one model, and a configuration that names another
- **WHEN** the embedding job runs
- **THEN** the texts are re-embedded under the new model's name

#### Scenario: Search ignores a foreign model

- **GIVEN** vectors made by two different models in the tables
- **WHEN** a search runs
- **THEN** only vectors made by the query's model are ranked

### Requirement: The Embedding Job

`itgraph embed` SHALL embed texts whose embedding is missing or stale, and nothing else, in batches; an interrupted run SHALL be continued by the next, without re-embedding what is already done. The job SHALL reach nothing beyond the database: the model travels in the image, and nothing is downloaded at runtime.

#### Scenario: The nightly increment

- **GIVEN** posts and videos collected since the last run, beside rows embedded before
- **WHEN** the embedding job runs
- **THEN** the new texts are embedded and the earlier rows are not touched

#### Scenario: An interrupted backfill resumes

- **GIVEN** a backfill that stopped part way through the corpus
- **WHEN** the job runs again
- **THEN** it continues from where it stopped, embedding only what is still missing

#### Scenario: A burst is spread over runs

- **GIVEN** a day that added thousands of texts in a new seed's history walk
- **WHEN** one run's batches end
- **THEN** the run finishes and the next run's work is what remained

#### Scenario: Nothing beyond the database

- **WHEN** the job's pod is rendered
- **THEN** it holds the configuration and the database's URL and no other secret, and may reach the database and nothing else

### Requirement: Semantic Search

`itgraph search` SHALL embed the query with the configured model, rank Telegram and YouTube texts together by cosine similarity to it, and print the top results with each text's channel, date and a link to the post or video. It SHALL reach nothing beyond the database.

#### Scenario: A query over both platforms

- **WHEN** a query is searched
- **THEN** Telegram posts and YouTube videos are ranked together, each result carrying its channel, date and link

#### Scenario: Found by meaning, not by words

- **GIVEN** a post about the query's subject that shares no word with the query
- **WHEN** the query is searched
- **THEN** the post is among the ranked results

#### Scenario: Nothing to search yet

- **GIVEN** a database with no embeddings
- **WHEN** a query is searched
- **THEN** the command says there is nothing to search and exits cleanly
