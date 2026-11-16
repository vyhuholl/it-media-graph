# Spec Delta

## ADDED Requirements

### Requirement: A Second Wind Section Lists Older Videos That Sped Up

Each language's list SHALL hold a second wind section of its channels' long-form videos published before the previous week and inside the walk's depth, by views alone. A video qualifies when its views a day this week are at least `youtube_weekly_second_wind_acceleration` times its pace the week before, and a week at that pace is at least `youtube_weekly_second_wind_norm_share` times its channel's long-form norm at a day old.

#### Scenario: A talk that sped up

- **GIVEN** a long-form video published 60 days before the week, whose channel's norm is 2,000 views
- **AND** it gained 50 views a day the week before and 400 a day this week
- **WHEN** the list is computed with an acceleration of 3 and a norm share of 0.5
- **THEN** it qualifies: eight times its pace, and 2,800 views a week against a floor of 1,000

#### Scenario: A slow-burn channel

- **GIVEN** a channel whose old videos gain from search about as fast each week as the week before
- **WHEN** the list is computed
- **THEN** none of them qualifies, however large its gain against the channel's norm

#### Scenario: A Short that sped up

- **GIVEN** a 40-second video whose pace rose tenfold
- **WHEN** the list is computed
- **THEN** it is not in the section

#### Scenario: Views taken away

- **GIVEN** a video whose views at the end of the week are fewer than at its start
- **WHEN** the list is computed
- **THEN** it does not qualify

#### Scenario: A video read weekly

- **GIVEN** a video 200 days old, read once a week
- **WHEN** the list is computed
- **THEN** its paces are measured between its weekly readings around the week's bounds

### Requirement: One Second Wind Entry Per Channel, Five At Most

The section SHALL hold one entry per channel, naming its qualifying video with the largest pace's share of the norm and how many more of its videos qualified. Entries SHALL be ranked by that share, highest first, at most `youtube_weekly_second_wind_size` (five). A channel MAY be in both sections of a list, by different videos. The language is the channel's label when the list is computed.

#### Scenario: More channels than the section holds

- **GIVEN** seven English channels with a qualifying video
- **WHEN** the English list is computed
- **THEN** its section holds the five with the largest shares, in that order

#### Scenario: A channel in both sections

- **GIVEN** a channel whose new video is on the list and whose older talk sped up
- **WHEN** the list is computed
- **THEN** the channel has an entry in each section

### Requirement: The Second Wind Stays In The Shadow Until Switched On

The section SHALL be computed and stored with every list, and printed by `--preview`, whatever its switch. A list SHALL record whether `youtube_weekly_second_wind_enabled` was on when it was computed, with the thresholds used, and the bot SHALL send the section only with a list computed while it was on. The switch SHALL be off by default.

#### Scenario: Computed in the shadow

- **GIVEN** the switch off
- **WHEN** the lists are computed and sent
- **THEN** their second wind entries are stored, and no message holds a second wind section

#### Scenario: Switched on

- **GIVEN** a list computed while the switch was on
- **WHEN** it is sent
- **THEN** its message holds the second wind section after the list's entries

### Requirement: A Second Wind Entry Copies Nothing YouTube Said

A second wind entry SHALL hold its rank, channel, video, the readings its paces were measured between, its acceleration, its share of the norm, the count of its channel's other qualifying videos, and its delivery state. It SHALL hold no title, name or count read from YouTube. It SHALL be removed with the reading that ends its week.

#### Scenario: What is stored

- **WHEN** a list's section is computed
- **THEN** its rows hold ids, moments and itgraph's multiples, and no title, name or count

#### Scenario: A video gone before sending

- **GIVEN** a second wind entry whose video YouTube stops returning before the list is sent
- **WHEN** the list is sent
- **THEN** the entry is gone, and the section's ranks close up

### Requirement: A Second Wind Entry Names Its Source And Dates What It Shows

A sent second wind section SHALL be headed as such. Each entry SHALL name YouTube with a link to its video, and show its title, channel and age. It SHALL show the views YouTube reported at the two readings around the week, each with the moment it was taken, and the gain between them. It SHALL say how many times faster than the week before and how many times its channel's norm, labelled as itgraph's. A section with no entry SHALL be said in one line.

#### Scenario: An entry

- **WHEN** a section with entries is sent
- **THEN** every entry links to its own video on YouTube and shows its title, channel and age, the views at the start and end of the week with their moments, the gain, and the two multiples labelled as itgraph's

#### Scenario: Nothing sped up

- **GIVEN** a list computed with the switch on and no video qualifying
- **WHEN** it is sent
- **THEN** its message says in one line that nothing sped up
