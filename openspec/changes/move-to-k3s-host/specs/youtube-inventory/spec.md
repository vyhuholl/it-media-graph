# Spec Delta

## MODIFIED Requirements

### Requirement: Remote Operation

The system SHALL provide `scripts/on-host.sh`, running an `itgraph` command in the collection namespace's console on the host named in the deploy inventory, over SSH, with standard input, output and error passed through and the command's exit status returned. The same script SHALL stop and start the collection loop, so that a command needing the Telegram session can run.

#### Scenario: A review round trip from the operator's machine

- **GIVEN** the collection host in the deploy inventory
- **WHEN** the operator runs `review-export` through the script, redirecting output to a local file, edits it, and runs `review-apply -` through the script with that file on standard input
- **THEN** the export reads, and the apply writes, the database on the host

#### Scenario: Arguments arrive intact

- **WHEN** an argument containing spaces or quotes is passed through the script
- **THEN** the command on the host receives it unchanged

#### Scenario: Failure is reported as failure

- **WHEN** the command on the host exits with a non-zero status
- **THEN** the script exits with the same status

#### Scenario: Output and errors stay apart

- **WHEN** a command writes its result to standard output and a diagnostic to standard error
- **THEN** the script's standard output carries the result alone

#### Scenario: No host configured

- **GIVEN** no deploy inventory naming the collection host
- **WHEN** the script runs
- **THEN** it fails without connecting, pointing to the inventory example

#### Scenario: The console runs the deployed code

- **WHEN** a command runs through the script after a deploy
- **THEN** it runs the image that deploy deployed

#### Scenario: A session command while the loop runs

- **GIVEN** a running collection loop
- **WHEN** a command that needs the Telegram session runs through the script
- **THEN** it refuses, naming the loop as the session's holder

#### Scenario: Stopping the loop for a session command

- **WHEN** the operator stops the loop through the script
- **THEN** the script returns only once the loop has exited and released the session
- **AND** a session command run through the script then connects through the namespace's proxy

#### Scenario: Starting the loop again

- **WHEN** the operator starts the loop through the script
- **THEN** the collector runs again with the deployed image
