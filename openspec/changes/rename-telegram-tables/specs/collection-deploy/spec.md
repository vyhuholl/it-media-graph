# Spec Delta

## MODIFIED Requirements

### Requirement: The Deploy Verifies The Exit

Before reporting success, a deploy in an environment that collects SHALL check that the collector logged a proxied route since its last start, or, for a collector this run did not change and whose log no longer holds the line, that its environment names the proxy; and that the address the internet sees through the proxy differs from the one it sees directly. In an environment that does not collect, it SHALL check that a connection through the proxy fails. Any check failing SHALL fail the deploy.

#### Scenario: The collector connected directly

- **GIVEN** a collector whose configuration lost the proxy settings
- **WHEN** the deploy verifies
- **THEN** it fails, saying the collector connected directly

#### Scenario: A proxy that goes out directly

- **GIVEN** a proxy that accepts connections but sends them out from the host's own address
- **WHEN** the deploy verifies
- **THEN** it fails, saying the address seen through the proxy is the host's own

#### Scenario: The collector has not connected

- **GIVEN** a collector that logged no route since its last start
- **WHEN** the deploy verifies and the wait for that line runs out
- **THEN** it fails, saying the collector is not getting as far as connecting

#### Scenario: A collector whose log has rotated

- **GIVEN** a collector this run did not change, whose log no longer holds the route line
- **WHEN** the deploy verifies
- **THEN** it reads the route from the collector's environment, and passes only if that names the proxy

#### Scenario: Polls advance

- **GIVEN** an environment that collects, outside quiet hours, from which they do not begin before the wait for a poll would end
- **WHEN** the deploy verifies
- **THEN** it waits a bounded time for a poll newer than the collector's start, and fails if none comes

#### Scenario: Quiet hours that would begin during the wait for a poll

- **GIVEN** an environment that collects, outside quiet hours, from which they would begin before the wait for a poll ends
- **WHEN** the deploy verifies
- **THEN** it does not wait for a poll, and does not fail for the lack of one

#### Scenario: A closed exit in a test environment

- **GIVEN** an environment that does not collect
- **WHEN** a connection through the proxy is tried
- **THEN** it fails, and the deploy passes this check

### Requirement: The Run Reports What It Did

At the end of a deploy, the report SHALL state the release and image digests deployed, whether a migration ran and between which revisions, where the pre-migration dump was written, how many channels are there, which workloads were restarted, and, for each check, whether it passed or could not be made.

#### Scenario: A re-run with nothing to migrate

- **WHEN** a deploy on a live host at head finishes
- **THEN** the report names the release and digests, the channel count, that no migration ran, and which workloads were restarted, if any

#### Scenario: Inside quiet hours

- **GIVEN** a deploy that ends inside the collector's quiet hours
- **WHEN** it reports
- **THEN** it says that polls advancing could not be checked, and until when

#### Scenario: Quiet hours that would begin during the wait

- **GIVEN** a deploy whose wait for a poll would reach the collector's quiet hours
- **WHEN** it reports
- **THEN** it says that polls advancing could not be checked, and from when quiet hours hold

