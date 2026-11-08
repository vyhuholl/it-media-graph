# Spec Delta

## MODIFIED Requirements

### Requirement: An Alert Records What It Took To Decide

The system SHALL record, for each alert, the kind, the post, the band crossed, the measured value at the moment it crossed, and when it was raised. A comment alert SHALL also record the comment count and the expectation it was scored against, because the next baseline run would give another expectation. It MUST NOT store a rendered message, and it MUST NOT store a copy of evidence that can be read from the derived tables.

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

#### Scenario: A comment alert keeps its count and expectation

- **WHEN** a comment alert is raised
- **THEN** the count that crossed and the expectation it was scored against are stored on it
- **AND** neither changes when baselines are refreshed

### Requirement: An Alert Says What Happened And When

The system SHALL identify, in each delivered alert, the post it is about, the channel that published it, how old the post is, and what crossed the threshold — worded according to the kind of alert it is.

The delivery path itself stays kind-blind: claiming, the daily cap, quiet hours, the digest and the retry never inspect the kind. Only the wording does, because a kind is precisely a claim about what the message should say, and a single wording would deliver a view spike as several sources reposting.

#### Scenario: The alert links to the post

- **WHEN** an alert is delivered for a post whose channel has a username
- **THEN** the message carries a link to that post

#### Scenario: The alert states the post's age

- **WHEN** an alert is delivered
- **THEN** the message says how long ago the post was published

#### Scenario: One alert per post, not one per reposter

- **GIVEN** a post carried by several channels
- **WHEN** the cascade alert is delivered
- **THEN** one message describes the post and the channels that carried it

#### Scenario: A spike says which metric it is about

- **GIVEN** an alert raised because a post's views, reactions or forwards passed the threshold
- **WHEN** it is delivered
- **THEN** the message names that metric and how far past normal the post went
- **AND** it is not worded as though the post had been reposted

#### Scenario: A comment alert states the count

- **GIVEN** an alert raised because a post's comments passed the threshold
- **WHEN** it is delivered
- **THEN** the message names comments, the count, and about how many the channel's posts usually have at that age
