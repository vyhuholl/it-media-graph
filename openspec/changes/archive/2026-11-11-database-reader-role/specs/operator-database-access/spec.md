# Spec Delta

## Purpose

What an operator's tunnel may do in the database under the reader role, `itgraph_reader`: read every table, change nothing, start each session guarded against accidents, and log in only when the deploy's input gives it a password.

## ADDED Requirements

### Requirement: The Reader Reads Every Table

The role `itgraph_reader` SHALL be able to read every table and view in the schema `public`, including any created by a later migration, without a migration of its own. Its sessions SHALL read nothing outside what `SELECT` on them gives.

#### Scenario: Every table at head

- **GIVEN** a database at head
- **WHEN** the reader selects from each table and view in `public`
- **THEN** every select succeeds

#### Scenario: A table a later migration creates

- **GIVEN** a database at head
- **WHEN** the role that runs migrations creates a new table in `public`, with no grant of its own
- **THEN** the reader selects from it

### Requirement: The Reader Changes Nothing

The reader SHALL hold no privilege to change data: no `INSERT`, `UPDATE`, `DELETE` or `TRUNCATE` on any table, no privilege on any sequence, and no `CREATE` on the schema. A write SHALL be refused for want of privilege even in a session that has lifted read-only.

#### Scenario: A write in a session as it starts

- **GIVEN** a session of the reader, as it starts
- **WHEN** it inserts, updates, deletes or truncates in any table
- **THEN** it is refused, and nothing changes

#### Scenario: A write after lifting read-only

- **GIVEN** a session of the reader that has set its transactions read-write
- **WHEN** it inserts, updates, deletes or truncates in any table
- **THEN** it is refused with permission denied, and nothing changes

#### Scenario: A sequence

- **WHEN** the reader asks a sequence for its next value
- **THEN** it is refused with permission denied

#### Scenario: A new table in the schema

- **WHEN** the reader creates a table in `public`
- **THEN** it is refused

### Requirement: The Reader's Sessions Start Guarded

Each session of the reader SHALL start read-only, with `work_mem` of 16 MB, a `statement_timeout` of 10 minutes and an `idle_in_transaction_session_timeout` of 5 minutes, and the role SHALL take at most 4 connections at once. The settings guard against accidents, not against the reader: a session may change any of them, and the grants stay the boundary.

#### Scenario: A session as it starts

- **WHEN** the reader logs in
- **THEN** `transaction_read_only` is on, `work_mem` is 16 MB, `statement_timeout` is 10 minutes and `idle_in_transaction_session_timeout` is 5 minutes

#### Scenario: A transaction left open

- **GIVEN** a session of the reader that opened a transaction
- **WHEN** it stays idle in it for more than 5 minutes
- **THEN** the server ends the session, and its locks go with it

#### Scenario: A fifth connection

- **GIVEN** four sessions of the reader open
- **WHEN** a fifth logs in
- **THEN** it is refused

### Requirement: The Reader Logs In Only With Its Input

The role SHALL be created unable to log in. A deploy given a non-empty `itgraph_reader_postgres_password` SHALL let it log in with that password; a deploy given an empty one SHALL leave it unable to log in and with no password, including when an earlier deploy had set one.

#### Scenario: No password given

- **GIVEN** an empty `itgraph_reader_postgres_password`
- **WHEN** the role runs, and something then connects as the reader with any password
- **THEN** the database refuses it, and the role is one that may not log in and has no password

#### Scenario: A password given

- **GIVEN** a non-empty `itgraph_reader_postgres_password`
- **WHEN** the role runs, and the reader connects with that password
- **THEN** the connection is accepted

#### Scenario: A password taken away

- **GIVEN** a deploy that set the reader's password
- **WHEN** the role runs again with an empty `itgraph_reader_postgres_password`
- **THEN** the reader can no longer log in, with the old password or any other

#### Scenario: A new password

- **GIVEN** a deploy that set the reader's password
- **WHEN** the role runs with a different one
- **THEN** the reader logs in with the new password and not with the old

### Requirement: A Revoked Login Ends The Reader's Sessions

A deploy that changes the reader's password or takes it away SHALL end every session of the reader open at that moment, after the change, so that what the change revokes is revoked at once. A run that leaves the reader's login as it was SHALL end no session.

#### Scenario: A session open when the password is taken away

- **GIVEN** a session of the reader, open
- **WHEN** the role runs with an empty `itgraph_reader_postgres_password`
- **THEN** that session is ended by the server, and logging in again is refused

#### Scenario: A session open when the password changes

- **GIVEN** a session of the reader, open
- **WHEN** the role runs with a different password
- **THEN** that session is ended, and logging in again succeeds with the new password alone

#### Scenario: A run that changes nothing

- **GIVEN** a session of the reader, open
- **WHEN** the role runs with the same password as before
- **THEN** that session stays open

### Requirement: The Reader Goes With Its Revision

Downgrading past the revision that creates the reader SHALL remove every privilege it granted in that database, its default privileges included, and SHALL drop the role unless another database of the cluster still grants it something, in which case the role SHALL be kept and the downgrade SHALL succeed.

#### Scenario: Nothing of the reader is left in the database

- **GIVEN** a scratch database at head
- **WHEN** it is downgraded past the reader's revision
- **THEN** nothing in that database refers to the reader: no privilege on a table, the schema or the database, and no default privilege

#### Scenario: Another database still names it

- **GIVEN** two databases of one cluster at head
- **WHEN** one is downgraded past the reader's revision
- **THEN** the downgrade succeeds, the reader holds nothing in that database, and still reads the other
