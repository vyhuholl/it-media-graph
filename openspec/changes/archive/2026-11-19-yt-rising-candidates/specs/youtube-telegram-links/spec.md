# Spec Delta

## MODIFIED Requirements

### Requirement: Not Scheduled

No scheduled job SHALL run `itgraph yt tg-candidates`: the report names channels read out of the operator's own subscriptions, and a job's output is kept in the cluster's logs. The lookup itself SHALL run unattended, as `youtube-lookups` provides, and its scheduled report SHALL be counts only.

#### Scenario: The deployed jobs

- **WHEN** the deploy's manifests are rendered with an API key
- **THEN** no scheduled job runs `yt tg-candidates`
- **AND** the daily lookup job runs `yt rising-lookups`, which prints counts and no channel
