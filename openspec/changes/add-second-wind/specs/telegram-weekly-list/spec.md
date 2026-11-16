# Spec Delta

## ADDED Requirements

### Requirement: A Second Wind Section Lists Older Posts That Sped Up

Each list SHALL hold a second wind section. It covers posts published before the previous week and inside the late horizon, by views alone, as the scoring pass reads posts. A post qualifies when its views a day this week are at least `weekly_second_wind_acceleration` times its pace the week before, and a week at that pace is at least `weekly_second_wind_norm_share` times its channel's eight-hour norm.

#### Scenario: A post that sped up

- **GIVEN** a post published 20 days before the week, whose channel's norm is 1,000 views
- **AND** it gained 100 views a day the week before and 400 a day this week
- **WHEN** the list is computed with an acceleration of 3 and a norm share of 0.5
- **THEN** it qualifies: four times its pace, and 2,800 views a week against a floor of 500

#### Scenario: Ordinary decay

- **GIVEN** a post whose pace fell from 100 to 50 views a day
- **WHEN** the list is computed
- **THEN** it does not qualify

#### Scenario: Speeding up by a few views

- **GIVEN** a post whose pace rose from 2 to 10 views a day, in a channel whose norm is 1,000
- **WHEN** the list is computed
- **THEN** it does not qualify: 70 views a week is under the floor

#### Scenario: A post still in its first week

- **GIVEN** a post published during the previous week
- **WHEN** the list is computed
- **THEN** it is not in the section, whatever its pace

#### Scenario: A post that did not grow the week before

- **GIVEN** a post whose views did not grow the week before, and grew this week at 600 a day
- **WHEN** the list is computed against a norm of 1,000
- **THEN** it qualifies on size alone

#### Scenario: Too few readings

- **GIVEN** a post with no reading before the week, or readings less than two days apart around a bound
- **WHEN** the list is computed
- **THEN** it is not in the section

#### Scenario: A channel without a norm

- **GIVEN** a channel with no baseline in the newest run
- **WHEN** the list is computed
- **THEN** none of its posts is in the section

### Requirement: One Second Wind Entry Per Channel, Five At Most

The section SHALL hold one entry per channel, naming its qualifying post with the largest pace's share of the norm and how many more of its posts qualified. Entries SHALL be ranked by that share, highest first, at most `weekly_second_wind_size` (five). A channel MAY be in both sections of a list, by different posts.

#### Scenario: A channel with two posts that sped up

- **GIVEN** a channel with two qualifying posts, at 2.8 and 1.1 times its norm
- **WHEN** the list is computed
- **THEN** it has one entry, naming the post at 2.8 and one more

#### Scenario: More channels than the section holds

- **GIVEN** seven channels with a qualifying post
- **WHEN** the list is computed
- **THEN** the section holds the five with the largest shares, in that order

#### Scenario: A channel in both sections

- **GIVEN** a channel whose new post is on the list and whose older post sped up
- **WHEN** the list is computed
- **THEN** the channel has an entry in each section

### Requirement: The Second Wind Stays In The Shadow Until Switched On

The section SHALL be computed and stored with every list, and printed by `--preview`, whatever its switch. A list SHALL record whether `weekly_second_wind_enabled` was on when it was computed, with the thresholds used, and the bot SHALL send the section only with a list computed while it was on. The switch SHALL be off by default.

#### Scenario: Computed in the shadow

- **GIVEN** the switch off
- **WHEN** a list is computed and sent
- **THEN** its second wind entries are stored, and the message holds no second wind section

#### Scenario: A preview

- **WHEN** the pass runs with `--preview`
- **THEN** it prints the second wind entries by ids, pace, acceleration and share, whatever the switch

#### Scenario: Switched on

- **GIVEN** a list computed while the switch was on
- **WHEN** it is sent
- **THEN** its message holds the second wind section after the list's entries

#### Scenario: Switched on after computing

- **GIVEN** a list computed while the switch was off, and the switch turned on before it is sent
- **WHEN** it is sent
- **THEN** its message holds no second wind section

### Requirement: A Second Wind Entry Shows What Sped Up

A sent second wind section SHALL be headed as such and SHALL show each entry's channel, the post's age, its link where the channel has a username, and the first line of its text. It SHALL say how many times faster than the week before the post's views went this week, and how many times its channel's norm a week at that pace is, both labelled as itgraph's. It SHALL show no count. A section with no entry SHALL be said in one line.

#### Scenario: An entry

- **WHEN** a section with entries is sent
- **THEN** every entry names its channel, the post's age, its link and first line, and the two multiples, labelled as itgraph's

#### Scenario: A post that did not grow the week before

- **GIVEN** an entry whose post did not grow the week before
- **WHEN** it is sent
- **THEN** it says the post started growing again, rather than a multiple

#### Scenario: Nothing sped up

- **GIVEN** a list computed with the switch on and no post qualifying
- **WHEN** it is sent
- **THEN** its message says in one line that nothing sped up

#### Scenario: A second wind entry gone before sending

- **GIVEN** a second wind entry whose post was removed with its channel before sending
- **WHEN** the list is sent
- **THEN** the entry is left out, and the section's ranks close up
