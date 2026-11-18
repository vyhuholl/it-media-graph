# Spec Delta

## ADDED Requirements

### Requirement: Bulk Rejection Of Shown Candidates

The system SHALL provide a command that rejects, in one statement, every candidate pair the review list shows — every pending pair in which at least one channel has been accepted into scope. It MUST ask for a typed confirmation before writing anything, and MUST leave every pair it does not name exactly as it was.

#### Scenario: Rejecting the shown list

- **GIVEN** pending pairs, some shown because a channel on at least one side is in scope, and some hidden because neither is
- **WHEN** the operator rejects every shown candidate and confirms
- **THEN** every shown pending pair is recorded as rejected, with when it was decided
- **AND** every hidden pair remains pending
- **AND** every already-decided pair is left exactly as it was

#### Scenario: The confirmation is typed

- **WHEN** the bulk rejection asks to proceed
- **THEN** it states how many pairs it will reject, and that undoing a rejection is one pair at a time
- **AND** only the word `yes` proceeds; any other answer writes nothing

#### Scenario: Nothing to reject

- **GIVEN** no pending pair with a channel in scope
- **WHEN** the command runs
- **THEN** it writes nothing and asks nothing

#### Scenario: A note names the bulk decision

- **WHEN** the bulk rejection is recorded
- **THEN** each rejected pair carries a note naming the bulk rejection, unless the operator supplied a note of their own

#### Scenario: A bulk rejection does not suppress future proposals

- **GIVEN** pairs rejected in bulk
- **WHEN** detection runs again and proposes a pair it had not proposed before
- **THEN** the new pair is shown among the candidates awaiting review
- **AND** none of the bulk-rejected pairs is proposed again
