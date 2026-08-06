## MODIFIED Requirements

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
