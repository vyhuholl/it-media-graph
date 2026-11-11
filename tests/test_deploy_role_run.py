"""The role's tasks, run for real against a stub `kubectl`.

Each test states what the cluster answers and reads back what the role called.
No network and no cluster. Task files run on their own wherever one can, since a
whole run takes the longest of anything here.
"""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from roleharness import INPUTS, Run, rule, run_role

HEAD = "b7d2e4f19a30"
OLD = "a89cb273f885"
HISTORY = (
    f"<base> -> {OLD}, the past\n{OLD} -> {HEAD} (head), the metrics role\n"
)
SECRETS = (
    "itgraph_telegram_api_hash",
    "itgraph_bot_token",
    "itgraph_postgres_password",
    "itgraph_bot_postgres_password",
    "itgraph_metrics_postgres_password",
    "itgraph_reader_postgres_password",
    "itgraph_proxy_password",
    "itgraph_youtube_api_key",
    "itgraph_b2_application_key",
)


def secrets_of(name: str = "a") -> list[str]:
    inputs = yaml.safe_load((INPUTS / f"{name}.yml").read_text())
    return [str(inputs[key]) for key in SECRETS if inputs.get(key)]


def pod_answers(
    name: str, phase: str = "Succeeded", logs: str = ""
) -> list[dict[str, Any]]:
    return [
        rule(f"get pod {name} -o jsonpath", phase),
        rule(f"logs pod/{name} ", logs),
    ]


@pytest.mark.parametrize(
    ("extra", "names"),
    [
        ({"itgraph_namespace": "Not A Label"}, "itgraph_namespace"),
        (
            {"itgraph_postgres_access_ip": "nowhere"},
            "itgraph_postgres_access_ip",
        ),
        (
            {"itgraph_image": "ghcr.io/example/it-media-graph:v1"},
            "itgraph_image",
        ),
        (
            {"itgraph_proxy_image": "ghcr.io/example/x@sha256:" + "1" * 64},
            "itgraph_proxy_image",
        ),
        (
            {
                "itgraph_postgres_image": "docker.io/library/postgres:17.1@sha256:"
                + "2" * 64
            },
            "of major 16",
        ),
        ({"itgraph_postgres_password": "short"}, "itgraph_postgres_password"),
        (
            {"itgraph_reader_postgres_password": "short"},
            "itgraph_reader_postgres_password",
        ),
        (
            {
                "itgraph_reader_postgres_password": "has a @ in it and is long enough"
            },
            "itgraph_reader_postgres_password",
        ),
        (
            {"itgraph_proxy_password": "has a @ in it and is long enough"},
            "itgraph_proxy_password",
        ),
        (
            {"itgraph_proxy_outbound": {"protocol": "blackhole"}},
            "closes every connection",
        ),
        ({"itgraph_proxy_outbound": {"protocol": "freedom"}}, "`freedom`"),
        ({"itgraph_b2_application_key": ""}, "all four"),
        ({"itgraph_backup_age_recipient": ""}, "all four"),
        (
            {"itgraph_youtube_alert_topic_ru": 0},
            "itgraph_youtube_alert_topic_ru go together",
        ),
        (
            {"itgraph_youtube_alert_chat_id": 0},
            "itgraph_youtube_alert_topic_ru go together",
        ),
    ],
)
def test_a_bad_input_is_refused_before_anything_is_called(
    tmp_path: Path, extra: dict[str, Any], names: str
) -> None:
    run = run_role(tmp_path, "main", extra=extra)

    assert run.failed
    assert names in run.output
    assert run.calls == []


def test_a_real_outbound_is_refused_where_collection_is_off(
    tmp_path: Path,
) -> None:
    run = run_role(
        tmp_path,
        "main",
        inputs="b",
        extra={"itgraph_proxy_outbound": {"protocol": "vless"}},
    )

    assert run.failed
    assert "a real exit has no business here" in run.output
    assert run.calls == []


@pytest.mark.parametrize(
    "extra",
    [
        {"itgraph_youtube_api_key": "fakeYoutubeKeyGivenByMistake"},
        {
            "itgraph_backup_offsite": "b2",
            "itgraph_b2_key_id": "fake-key-id",
            "itgraph_b2_application_key": "fake-application-key",
            "itgraph_b2_bucket_name": "fake-bucket",
            "itgraph_backup_age_recipient": "fake-age-recipient",
        },
    ],
)
def test_a_key_or_a_store_is_refused_where_collection_is_off(
    tmp_path: Path, extra: dict[str, Any]
) -> None:
    run = run_role(tmp_path, "main", inputs="b", extra=extra)

    assert run.failed
    assert "itgraph_youtube_api_key must be empty" in run.output
    assert run.calls == []
    assert "fakeYoutubeKeyGivenByMistake" not in run.output
    assert "fake-application-key" not in run.output


@pytest.mark.parametrize(
    "name",
    [
        "itgraph_telegram_collection",
        # Empty is how the reader is left out, so leaving it out is refused.
        "itgraph_reader_postgres_password",
    ],
)
def test_a_missing_input_is_refused_by_name(tmp_path: Path, name: str) -> None:
    inputs = yaml.safe_load((INPUTS / "a.yml").read_text())
    del inputs[name]
    custom = tmp_path / "inputs.yml"
    custom.write_text(yaml.safe_dump(inputs))
    # A set of its own, in place of the shared one.
    run = run_role(
        tmp_path,
        "main",
        inputs=str(custom.with_suffix("")),
    )

    assert run.failed
    assert name in run.output
    assert run.calls == []


@pytest.mark.parametrize("password", ["", "fakeReaderPasswordLongEnough"])
def test_the_readers_password_may_be_empty(
    tmp_path: Path, password: str
) -> None:
    run = run_role(
        tmp_path,
        "inputs",
        extra={"itgraph_reader_postgres_password": password},
    )

    assert not run.failed, run.output


def apply_extra(
    stdin: str = "kind: Thing\n", secret: bool = False
) -> dict[str, Any]:
    return {
        "__itgraph_what": "a stream",
        "__itgraph_stream": stdin,
        "__itgraph_secret": secret,
    }


def test_an_unchanged_stream_is_not_applied(tmp_path: Path) -> None:
    run = run_role(
        tmp_path, "apply", rules=[rule("^diff ", rc=0)], extra=apply_extra()
    )

    assert not run.failed, run.output
    assert len(run.called("diff")) == 1
    assert run.called("apply") == []


def test_a_changed_stream_is_applied_with_the_same_field_manager(
    tmp_path: Path,
) -> None:
    run = run_role(
        tmp_path, "apply", rules=[rule("^diff ", rc=1)], extra=apply_extra()
    )

    assert not run.failed, run.output
    [applied] = run.called("apply")
    assert "--server-side" in applied["args"]
    assert "--field-manager=itgraph" in applied["args"]
    assert applied["stdin"].startswith("kind: Thing")


def test_a_failed_diff_fails_the_run(tmp_path: Path) -> None:
    run = run_role(
        tmp_path, "apply", rules=[rule("^diff ", rc=2)], extra=apply_extra()
    )

    assert run.failed
    assert run.called("apply") == []


def test_a_check_only_diffs(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "main",
        rules=[
            rule("^diff ", rc=1),
            rule("get namespace itgraph", "namespace/itgraph"),
        ],
        arguments=["--check", "--diff"],
    )

    assert not run.failed, run.output
    assert run.changing() == []
    assert len(run.called("diff")) == 9


def test_a_check_on_a_new_namespace_says_what_it_leaves_out(
    tmp_path: Path,
) -> None:
    run = run_role(
        tmp_path,
        "main",
        rules=[rule("^diff ", rc=1), rule("get namespace itgraph", "")],
        arguments=["--check"],
    )

    assert not run.failed, run.output
    assert "does not exist yet" in run.output
    assert run.changing() == []
    assert len(run.called("diff")) == 1


def lock_rules(
    age_seconds: int, then_free: bool = False
) -> list[dict[str, Any]]:
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    taken = now - timedelta(seconds=age_seconds)
    return [
        rule(
            "get lease",
            taken.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            times=1 if then_free else None,
        ),
        rule("get lease", ""),
    ]


FROZEN = {"__itgraph_lock_now": "2026-11-06T12:00:00.000000Z"}


def test_a_lock_an_hour_old_is_taken_over(tmp_path: Path) -> None:
    run = run_role(tmp_path, "lock", rules=lock_rules(3600), extra=FROZEN)

    assert not run.failed, run.output
    assert len(run.called("delete", "lease")) == 1
    assert len(run.called("create")) == 1


def test_a_lock_a_second_younger_is_waited_for(tmp_path: Path) -> None:
    run = run_role(
        tmp_path, "lock", rules=lock_rules(3599, then_free=True), extra=FROZEN
    )

    assert not run.failed, run.output
    # Read twice, and nothing of the other run's removed.
    assert len(run.called("get", "lease")) == 2
    assert run.called("delete", "lease") == []
    assert len(run.called("create")) == 1


def test_losing_the_race_for_the_lock_changes_nothing_else(
    tmp_path: Path,
) -> None:
    run = run_role(
        tmp_path,
        "lock",
        rules=[
            rule("get lease", ""),
            rule("^create ", rc=1, stderr="already exists"),
        ],
    )

    assert run.failed
    assert [call for call in run.changing() if "create" not in call] == []


def role_extra(key: str = "bot") -> dict[str, Any]:
    return {
        "__itgraph_role": {
            "key": key,
            "role": "itgraph_bot",
            "password": "fakeNewPasswordForThisRole",
        }
    }


def test_a_password_reaches_psql_on_stdin_alone(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "password",
        rules=[rule("get secret itgraph-postgres", "")],
        extra=role_extra(),
    )

    assert not run.failed, run.output
    [altered] = [call for call in run.calls if "psql" in call["args"]]
    assert "ALTER ROLE itgraph_bot LOGIN PASSWORD" in altered["stdin"]
    for call in run.calls:
        assert "fakeNewPasswordForThisRole" not in " ".join(call["args"])
    assert "fakeNewPasswordForThisRole" not in run.output
    [marked] = run.called("annotate")
    assert "itgraph-postgres" in marked["args"]


def test_a_password_already_set_is_not_set_again(tmp_path: Path) -> None:
    import hashlib

    hash_of = hashlib.sha256(b"fakeNewPasswordForThisRole").hexdigest()
    run = run_role(
        tmp_path,
        "password",
        rules=[
            rule(
                "get secret itgraph-postgres",
                json.dumps(
                    {
                        "metadata": {
                            "annotations": {"itgraph/password-bot": hash_of}
                        }
                    }
                ),
            )
        ],
        extra=role_extra(),
    )

    assert not run.failed, run.output
    assert [call for call in run.calls if "psql" in call["args"]] == []


def test_a_failed_password_is_not_marked_as_set(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "password",
        rules=[
            rule("get secret itgraph-postgres", ""),
            rule(" psql ", rc=1),
        ],
        extra=role_extra(),
    )

    assert run.failed
    assert run.called("annotate") == []
    assert "fakeNewPasswordForThisRole" not in run.output


def reader_extra(
    password: str = "fakeNewPasswordForTheReader",
) -> dict[str, Any]:
    return {
        "__itgraph_role": {
            "key": "reader",
            "role": "itgraph_reader",
            "password": password,
            "end_sessions": True,
        }
    }


def marks(**annotations: str) -> dict[str, Any]:
    return rule(
        "get secret itgraph-postgres",
        json.dumps({"metadata": {"annotations": annotations}}),
    )


def changes(run: Run) -> list[str]:
    """What the run gave psql to change, statement by statement."""
    return [call["stdin"] for call in run.calls if "-f" in call["args"]]


def test_a_new_reader_password_ends_its_sessions_after_the_change(
    tmp_path: Path,
) -> None:
    run = run_role(
        tmp_path,
        "password",
        rules=[rule("get secret itgraph-postgres", "")],
        extra=reader_extra(),
    )

    assert not run.failed, run.output
    [statements] = changes(run)
    altered = statements.index("ALTER ROLE itgraph_reader LOGIN PASSWORD")
    ended = statements.index("pg_terminate_backend")
    assert altered < ended
    assert "usename = 'itgraph_reader'" in statements
    [marked] = run.called("annotate")
    assert any(
        arg.startswith("itgraph/password-reader=") for arg in marked["args"]
    )
    for call in run.calls:
        assert "fakeNewPasswordForTheReader" not in " ".join(call["args"])
    assert "fakeNewPasswordForTheReader" not in run.output


def test_a_password_already_set_for_the_reader_ends_nothing(
    tmp_path: Path,
) -> None:
    import hashlib

    hash_of = hashlib.sha256(b"fakeNewPasswordForTheReader").hexdigest()
    run = run_role(
        tmp_path,
        "password",
        rules=[marks(**{"itgraph/password-reader": hash_of})],
        extra=reader_extra(),
    )

    assert not run.failed, run.output
    assert changes(run) == []


def test_a_new_bot_password_ends_no_session(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "password",
        rules=[rule("get secret itgraph-postgres", "")],
        extra=role_extra(),
    )

    assert not run.failed, run.output
    [statements] = changes(run)
    assert "pg_terminate_backend" not in statements


@pytest.mark.parametrize(
    ("can_log_in", "mark"),
    [
        ("t", None),  # a login given by hand
        ("f", "a-stale-mark"),  # a run that died before removing the mark
        ("t", "a-mark"),  # a password set by an earlier run
    ],
)
def test_an_empty_password_takes_the_login_away(
    tmp_path: Path, can_log_in: str, mark: str | None
) -> None:
    run = run_role(
        tmp_path,
        "password",
        rules=[
            marks(**({"itgraph/password-reader": mark} if mark else {})),
            rule("rolcanlogin", can_log_in),
        ],
        extra=reader_extra(""),
    )

    assert not run.failed, run.output
    [statements] = changes(run)
    taken = statements.index(
        "ALTER ROLE itgraph_reader NOLOGIN PASSWORD NULL;"
    )
    assert taken < statements.index("pg_terminate_backend")
    removed = run.called("annotate", "itgraph/password-reader-")
    assert len(removed) == (1 if mark else 0)
    assert [c for c in run.called("annotate") if c not in removed] == []


def test_an_empty_password_on_a_role_that_cannot_log_in_changes_nothing(
    tmp_path: Path,
) -> None:
    run = run_role(
        tmp_path,
        "password",
        rules=[marks(), rule("rolcanlogin", "f")],
        extra=reader_extra(""),
    )

    assert not run.failed, run.output
    assert changes(run) == []
    assert run.changing() == []


SCHEDULES = {
    "items": [
        {"metadata": {"name": "itgraph-detect"}, "spec": {}},
        {"metadata": {"name": "itgraph-backup"}, "spec": {"suspend": False}},
        {"metadata": {"name": "itgraph-baselines"}, "spec": {"suspend": True}},
    ]
}


def stopping_rules() -> list[dict[str, Any]]:
    return [
        rule("get cronjobs", json.dumps(SCHEDULES)),
        rule("get jobs", "itgraph-detect-1 Complete\n"),
        rule("get deployment itgraph-watch", "deployment.apps/itgraph-watch"),
        rule("get deployment itgraph-bot", ""),
        rule("get pods -l app=itgraph-watch", ""),
    ]


def test_writers_stop_and_a_suspended_schedule_is_left_alone(
    tmp_path: Path,
) -> None:
    run = run_role(tmp_path, "quiesce", rules=stopping_rules())

    assert not run.failed, run.output
    patched = [call["args"] for call in run.called("patch")]
    assert {args[args.index("cronjob") + 1] for args in patched} == {
        "itgraph-detect",
        "itgraph-backup",
    }
    # Marked before it is stopped, and only what exists.
    [marked] = run.called("annotate")
    assert "itgraph-watch" in marked["args"]
    assert "itgraph/stopped-by-run=true" in marked["args"]
    [scaled] = run.called("scale")
    assert "itgraph-watch" in scaled["args"]
    assert "--replicas=0" in scaled["args"]
    # The console's own Job is no writer to wait for.
    [jobs] = run.called("get", "jobs")
    assert "itgraph/console!=true" in jobs["args"]


def test_a_running_job_is_waited_for(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "quiesce",
        rules=[
            rule("get cronjobs", json.dumps({"items": []})),
            rule("get jobs", "itgraph-detect-1 \n", times=1),
            rule("get jobs", "itgraph-detect-1 Complete\n"),
        ],
    )

    assert not run.failed, run.output
    assert len(run.called("get", "jobs")) == 2


def test_the_schedules_go_back_as_they_were_found(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "resume",
        extra={
            "__itgraph_all_schedules": ["itgraph-detect", "itgraph-baselines"],
            "__itgraph_suspended_before": ["itgraph-baselines"],
        },
    )

    assert not run.failed, run.output
    [patched] = run.called("patch")
    assert "itgraph-detect" in patched["args"]
    assert '{"spec": {"suspend": false}}' in patched["args"]


def test_a_schedule_since_deleted_is_no_failure(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "resume",
        rules=[rule("patch cronjob", rc=1, stderr="NotFound")],
        extra={
            "__itgraph_all_schedules": ["itgraph-alerts"],
            "__itgraph_suspended_before": [],
        },
    )

    assert not run.failed, run.output


def state_rules(
    *,
    channels: str = "12",
    dangling: str = "0",
    version: str = OLD,
    history: str = HISTORY,
) -> list[dict[str, Any]]:
    return [
        rule(
            "get statefulset itgraph-postgres",
            "statefulset.apps/itgraph-postgres",
        ),
        rule("to_regclass\\('public.channels'\\)", "f\n"),
        rule("count\\(\\*\\) FROM channels", channels + "\n"),
        rule("pg_attribute", dangling + "\n"),
        rule("to_regclass\\('public.alembic_version'\\)", "f\n"),
        # Read once before the run changes anything: at head from then on, as
        # a migration leaves it.
        rule("version_num FROM alembic_version", version + "\n", times=1),
        rule("version_num FROM alembic_version", HEAD + "\n"),
        *pod_answers("itgraph-check-revisions", logs=history),
    ]


def test_every_pod_the_role_runs_once_waits_for_the_network_policies(
    tmp_path: Path,
) -> None:
    """A new pod is refused for a moment, and `curl` does not ask twice."""
    run = run_role(
        tmp_path,
        "exit_check",
        rules=[
            *pod_answers("itgraph-check-direct", logs=ADDRESS),
            *pod_answers("itgraph-check-proxied", logs=OTHER),
        ],
    )

    assert not run.failed, run.output
    created = [yaml.safe_load(call["stdin"]) for call in run.called("create")]
    assert len(created) == 2
    for pod in created:
        [init] = pod["spec"]["initContainers"]
        assert init["command"][0] == "sleep"
        assert init["securityContext"]["readOnlyRootFilesystem"] is True
        assert init["image"] == pod["spec"]["containers"][0]["image"]


def test_a_schema_at_head_is_not_behind(tmp_path: Path) -> None:
    run = run_role(tmp_path, "database_state", rules=state_rules(version=HEAD))

    assert not run.failed, run.output
    # The pod asking the image holds no credential and reaches nothing.
    [created] = run.called("create")
    assert "secretRef" not in created["stdin"]
    assert "itgraph/database" not in created["stdin"]


def test_a_broken_database_stops_a_run(tmp_path: Path) -> None:
    run = run_role(tmp_path, "database_state", rules=state_rules(dangling="3"))

    assert run.failed
    assert "3 column(s) reference a type" in run.output
    assert "deploy/runbook.md" in run.output
    assert run.called("create") == []


def test_a_failed_count_is_not_read_as_empty(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "database_state",
        rules=[
            rule("get statefulset", "statefulset.apps/itgraph-postgres"),
            rule("to_regclass\\('public.channels'\\)", "f\n"),
            rule("count\\(\\*\\) FROM channels", rc=2, stderr="no such thing"),
        ],
    )

    assert run.failed
    assert run.called("create") == []


def test_a_probe_that_could_not_answer_stops_a_run(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "database_state",
        rules=[
            rule("get statefulset", "statefulset.apps/itgraph-postgres"),
            rule("to_regclass\\('public.channels'\\)", "", rc=1),
        ],
    )

    assert run.failed


def test_a_schema_ahead_of_the_image_names_both(tmp_path: Path) -> None:
    run = run_role(
        tmp_path, "database_state", rules=state_rules(version="ffffffffffff")
    )

    assert run.failed
    assert "ffffffffffff" in run.output
    assert HEAD in run.output


def test_an_image_that_names_no_head_stops_a_run(tmp_path: Path) -> None:
    run = run_role(
        tmp_path, "database_state", rules=state_rules(history="traceback")
    )

    assert run.failed
    assert "did not name its revisions" in run.output


def test_no_database_yet_is_a_schema_to_create(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "database_state",
        rules=[
            rule("get statefulset", ""),
            *pod_answers("itgraph-check-revisions", logs=HISTORY),
        ],
    )

    assert not run.failed, run.output
    assert [call for call in run.calls if "psql" in call["args"]] == []


MIGRATE = {
    "__itgraph_schema_head": HEAD,
    "__itgraph_schema_current": OLD,
}


def test_at_head_nothing_is_migrated(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "migrate",
        rules=[rule("version_num FROM alembic_version", HEAD + "\n")],
        extra={**MIGRATE, "__itgraph_schema_behind": False},
    )

    assert not run.failed, run.output
    assert run.called("create") == []


def test_behind_it_migrates_from_the_image_with_its_dump(
    tmp_path: Path,
) -> None:
    run = run_role(
        tmp_path,
        "migrate",
        rules=[
            *pod_answers(
                "itgraph-migrate",
                logs="backed up to /backups/weekly/full-x.dump (1 bytes)\n",
            ),
            rule("version_num FROM alembic_version", HEAD + "\n"),
        ],
        extra={**MIGRATE, "__itgraph_schema_behind": True},
    )

    assert not run.failed, run.output
    [created] = run.called("create")
    pod = yaml.safe_load(created["stdin"])
    [container] = pod["spec"]["containers"]
    # A list of arguments: a folded block once split a command like this in two.
    assert container["command"] == ["alembic", "upgrade", "head"]
    assert {m["name"] for m in container["volumeMounts"]} == {"tmp", "backups"}
    assert pod["metadata"]["labels"]["itgraph/database"] == "true"


def test_a_failed_migration_stops_the_run(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "migrate",
        rules=[*pod_answers("itgraph-migrate", phase="Failed", logs="boom")],
        extra={**MIGRATE, "__itgraph_schema_behind": True},
    )

    assert run.failed
    assert "no workload was given the new image" in run.output


def test_a_schema_still_behind_stops_the_run(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "migrate",
        rules=[rule("version_num FROM alembic_version", OLD + "\n")],
        extra={**MIGRATE, "__itgraph_schema_behind": False},
    )

    assert run.failed
    assert "no workload is created, changed or restarted" in run.output


ADDRESS = "198.51.100.7"
OTHER = "198.51.100.8"


def exit_rules(
    direct: str, proxied: str, *, ok: bool = True
) -> list[dict[str, Any]]:
    return [
        *pod_answers("itgraph-check-direct", logs=direct),
        *pod_answers(
            "itgraph-check-proxied",
            phase="Succeeded" if ok else "Failed",
            logs=proxied,
        ),
    ]


def test_two_different_addresses_pass(tmp_path: Path) -> None:
    run = run_role(tmp_path, "exit_check", rules=exit_rules(ADDRESS, OTHER))

    assert not run.failed, run.output
    assert ADDRESS not in run.output
    assert OTHER not in run.output


def test_a_proxy_going_out_directly_fails_saying_so(tmp_path: Path) -> None:
    run = run_role(tmp_path, "exit_check", rules=exit_rules(ADDRESS, ADDRESS))

    assert run.failed
    assert "the host's own" in run.output
    assert ADDRESS not in run.output.replace("host's own", "")


def test_a_proxy_that_does_not_answer_fails(tmp_path: Path) -> None:
    run = run_role(
        tmp_path, "exit_check", rules=exit_rules(ADDRESS, "", ok=False)
    )

    assert run.failed
    assert "must both answer" in run.output


def test_the_proxy_password_is_never_an_argument(tmp_path: Path) -> None:
    run = run_role(tmp_path, "exit_check", rules=exit_rules(ADDRESS, OTHER))

    secret = yaml.safe_load((INPUTS / "a.yml").read_text())[
        "itgraph_proxy_password"
    ]
    for call in run.calls:
        assert secret not in " ".join(call["args"])
        assert secret not in call["stdin"]
    # The pod's own shell expands it from its environment.
    proxied = [c for c in run.called("create") if "proxied" in c["stdin"]]
    assert "${PROXY_PASSWORD}" in proxied[0]["stdin"]


def test_where_collection_is_off_the_proxy_must_refuse(tmp_path: Path) -> None:
    good = run_role(
        tmp_path,
        "exit_check",
        inputs="b",
        rules=[*pod_answers("itgraph-check-proxied", phase="Failed")],
    )
    assert not good.failed, good.output
    assert [c for c in good.called("create") if "direct" in c["stdin"]] == []

    bad = run_role(
        tmp_path,
        "exit_check",
        inputs="b",
        rules=[*pod_answers("itgraph-check-proxied", logs=ADDRESS)],
    )
    assert bad.failed
    assert "should close every one" in bad.output


def test_the_session_gate_passes_on_a_file(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "session_gate",
        rules=[*pod_answers("itgraph-check-session")],
    )

    assert not run.failed, run.output
    [created] = run.called("create")
    pod = yaml.safe_load(created["stdin"])
    [container] = pod["spec"]["containers"]
    # Only ever read-only, and only tests the file.
    assert container["volumeMounts"][0]["readOnly"] is True
    assert "cat" not in container["command"]
    assert "cp" not in container["command"]


def test_no_session_stops_the_run_and_says_not_to_make_one(
    tmp_path: Path,
) -> None:
    run = run_role(
        tmp_path,
        "session_gate",
        rules=[*pod_answers("itgraph-check-session", phase="Failed")],
    )

    assert run.failed
    assert "itgraph login" in run.output
    assert "deploy/runbook.md" in run.output


def test_no_task_writes_to_the_session_volume() -> None:
    from roleharness import ANSIBLE_DIR

    tasks = ANSIBLE_DIR / "itgraph" / "tasks"
    for path in tasks.glob("*.yml"):
        text = path.read_text()
        assert "> /session" not in text, path.name
        assert "itgraph.session" not in text or path.name == "defaults", (
            path.name
        )


def collector_rules(phase: str, logs: str) -> list[dict[str, Any]]:
    return [*pod_answers("itgraph-check-collector", phase=phase, logs=logs)]


ROUTE = "INFO itgraph.tg.client Telegram route: socks5 proxy at itgraph-proxy:1080\n"


def test_the_collector_spec_must_log_its_route_and_fail(
    tmp_path: Path,
) -> None:
    run = run_role(
        tmp_path,
        "collector_spec",
        inputs="b",
        rules=collector_rules("Failed", ROUTE + "ConnectionError\n"),
    )

    assert not run.failed, run.output
    [created] = run.called("create")
    pod = yaml.safe_load(created["stdin"])
    [container] = pod["spec"]["containers"]
    assert container["args"] == ["watch", "--cycles", "1"]
    assert pod["spec"]["restartPolicy"] == "Never"
    assert pod["metadata"]["labels"]["itgraph/telegram"] == "true"
    assert {"name": "session", "mountPath": "/session"} in container[
        "volumeMounts"
    ]
    # Deleted when read.
    assert run.called("delete", "itgraph-check-collector")


@pytest.mark.parametrize(
    ("phase", "logs"),
    [
        ("Succeeded", ROUTE),
        ("Failed", "no route logged, it died before\n"),
        ("Failed", "Telegram route: direct, no proxy configured\n"),
    ],
)
def test_anything_else_from_the_collector_spec_fails(
    tmp_path: Path, phase: str, logs: str
) -> None:
    run = run_role(
        tmp_path,
        "collector_spec",
        inputs="b",
        rules=collector_rules(phase, logs),
    )

    assert run.failed
    assert run.called("delete", "itgraph-check-collector")


WATCH = {"__itgraph_workload": "deployment/itgraph-watch"}


def test_a_rollout_that_finishes_is_left_alone(tmp_path: Path) -> None:
    run = run_role(tmp_path, "rollout", extra=WATCH)

    assert not run.failed, run.output
    assert run.called("undo") == []


def test_a_rollout_that_does_not_finish_is_put_back(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "rollout",
        rules=[
            rule("rollout status", rc=1),
            rule("get deployment/itgraph-watch", "2"),
            rule("logs deployment/itgraph-watch", "a stack trace\n"),
        ],
        extra={**WATCH, "__itgraph_revisions_before": {"itgraph-watch": "1"}},
    )

    assert run.failed
    assert len(run.called("undo")) == 1
    assert "put back to its previous template" in run.output
    assert "a stack trace" in run.output


def test_a_rollout_this_run_only_scaled_up_is_not_undone(
    tmp_path: Path,
) -> None:
    """An undo would go to a template older than the one it was running."""
    run = run_role(
        tmp_path,
        "rollout",
        rules=[
            rule("rollout status", rc=1),
            rule("get deployment/itgraph-watch", "2"),
        ],
        extra={**WATCH, "__itgraph_revisions_before": {"itgraph-watch": "2"}},
    )

    assert run.failed
    assert run.called("undo") == []
    assert "this run gave it no new template" in run.output


def test_a_rollout_in_a_restore_is_not_undone(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "rollout",
        rules=[
            rule("rollout status", rc=1),
            rule("get deployment/itgraph-watch", "2"),
        ],
        extra=WATCH,
    )

    assert run.failed
    assert run.called("undo") == []


def pods_json(*pods: tuple[str, int, bool]) -> str:
    return json.dumps(
        {
            "items": [
                {
                    "metadata": {"name": name},
                    "status": {
                        "containerStatuses": [
                            {
                                "restartCount": restarts,
                                "ready": ready,
                                "lastState": {
                                    "terminated": {
                                        "reason": "Error",
                                        "exitCode": 1,
                                    }
                                },
                            }
                        ]
                    },
                }
                for name, restarts, ready in pods
            ]
        }
    )


SETTLE = {
    "__itgraph_long_running": ["itgraph-watch"],
    "__itgraph_new_templates": ["itgraph-watch"],
}


def test_an_old_restart_does_not_fail_a_run(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "settle",
        rules=[
            rule("get pods -l app=itgraph-watch", pods_json(("w", 12, True)))
        ],
        extra=SETTLE,
    )

    assert not run.failed, run.output


def test_a_restart_while_settling_fails_and_puts_it_back(
    tmp_path: Path,
) -> None:
    run = run_role(
        tmp_path,
        "settle",
        rules=[
            rule("get pods", pods_json(("w", 0, True)), times=1),
            rule("get pods", pods_json(("w", 1, True))),
        ],
        extra=SETTLE,
    )

    assert run.failed
    assert "w restarted, last exit Error 1" in run.output
    assert len(run.called("undo")) == 1


def test_a_pod_not_ready_fails_and_what_this_run_did_not_change_stays(
    tmp_path: Path,
) -> None:
    run = run_role(
        tmp_path,
        "settle",
        rules=[rule("get pods", pods_json(("w", 3, False)))],
        extra={**SETTLE, "__itgraph_new_templates": []},
    )

    assert run.failed
    assert "w is not ready" in run.output
    assert run.called("undo") == []


def test_a_proxied_route_passes(tmp_path: Path) -> None:
    run = run_role(
        tmp_path, "route", rules=[rule("logs deployment/itgraph-watch", ROUTE)]
    )

    assert not run.failed, run.output


def test_a_direct_route_fails_saying_so(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "route",
        rules=[
            rule(
                "logs deployment/itgraph-watch",
                "Telegram route: direct, no proxy configured\n",
            ),
            rule(" exec ", ""),
        ],
    )

    assert run.failed
    assert "connected directly" in run.output


def test_no_route_from_a_collector_just_started_fails_saying_so(
    tmp_path: Path,
) -> None:
    run = run_role(
        tmp_path,
        "route",
        rules=[rule("logs deployment/itgraph-watch", "nothing yet\n")],
        extra={"__itgraph_route_retries": 1, "__itgraph_changed": ["watch"]},
    )

    assert run.failed
    assert "not getting as far as connecting" in run.output
    assert run.called("exec") == []


def test_a_long_running_collector_is_read_from_its_environment(
    tmp_path: Path,
) -> None:
    run = run_role(
        tmp_path,
        "route",
        rules=[
            rule("logs deployment/itgraph-watch", "rotated away\n"),
            rule("printenv", "socks5\nitgraph-proxy\n1080\n"),
        ],
        extra={"__itgraph_route_retries": 1},
    )

    assert not run.failed, run.output


def test_polls_that_advance_pass(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "polls",
        rules=[
            rule("startedAt", "2026-11-06T09:00:00Z"),
            rule("count\\(\\*\\) FROM poll_state", "3\n"),
        ],
        extra={"__itgraph_now_hour": 12},
    )

    assert not run.failed, run.output


def test_polls_that_never_come_fail(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "polls",
        rules=[
            rule("startedAt", "2026-11-06T09:00:00Z"),
            rule("count\\(\\*\\) FROM poll_state", "0\n"),
        ],
        extra={"__itgraph_now_hour": 12, "__itgraph_polls_retries": 1},
    )

    assert run.failed
    assert "concluded no poll since it started" in run.output


def test_inside_quiet_hours_polls_are_not_asked_about(tmp_path: Path) -> None:
    run = run_role(tmp_path, "polls", extra={"__itgraph_now_hour": 6})

    assert not run.failed, run.output
    assert run.calls == []


def deployments(*items: tuple[str, int, bool]) -> str:
    return json.dumps(
        {
            "items": [
                {
                    "metadata": {
                        "name": name,
                        "annotations": (
                            {"itgraph/stopped-by-run": "true"}
                            if marked
                            else {}
                        ),
                    },
                    "spec": {"replicas": replicas},
                }
                for name, replicas, marked in items
            ]
        }
    )


def running_collector() -> str:
    return pods_json(("p", 0, True))


def whole_run_rules(
    *,
    version: str = HEAD,
    deployed: str = '{"items": []}',
    channels: str = "12",
    diffed: dict[str, int] | None = None,
    marks: str = "",
) -> list[dict[str, Any]]:
    """A cluster on which everything answers as it should."""
    return [
        rule("get namespace itgraph", "namespace/itgraph"),
        rule("get lease", ""),
        rule("get deployment itgraph-watch itgraph-bot", deployed),
        rule("get storageclass", "Retain"),
        *state_rules(version=version, channels=channels),
        *pod_answers("itgraph-migrate"),
        *pod_answers("itgraph-check-direct", logs=ADDRESS),
        *pod_answers("itgraph-check-proxied", logs=OTHER),
        *pod_answers("itgraph-check-session"),
        rule("get secret itgraph-postgres", marks),
        rule("get cronjobs", json.dumps(SCHEDULES)),
        rule("get jobs", ""),
        rule("get deployment itgraph-watch ", "deployment.apps/itgraph-watch"),
        rule("get deployment itgraph-bot ", "deployment.apps/itgraph-bot"),
        rule("get pods -l app=itgraph-watch -o name", ""),
        rule("get pods -l app=itgraph-bot -o name", ""),
        rule("get pods -l app=itgraph", running_collector()),
        rule("logs deployment/itgraph-watch", ROUTE),
        rule("startedAt", "2026-11-06T09:00:00Z"),
        rule("count\\(\\*\\) FROM poll_state", "3\n"),
    ]


def marks_set() -> str:
    import hashlib

    inputs = yaml.safe_load((INPUTS / "a.yml").read_text())
    return json.dumps(
        {
            "metadata": {
                "annotations": {
                    f"itgraph/password-{key}": hashlib.sha256(
                        inputs[name].encode()
                    ).hexdigest()
                    for key, name in (
                        ("owner", "itgraph_postgres_password"),
                        ("bot", "itgraph_bot_postgres_password"),
                        ("metrics", "itgraph_metrics_postgres_password"),
                        ("reader", "itgraph_reader_postgres_password"),
                    )
                }
            }
        }
    )


def assert_no_secret_anywhere(run: Run, name: str = "a") -> None:
    for secret in secrets_of(name):
        assert secret not in run.output, secret
        for call in run.calls:
            assert secret not in " ".join(call["args"]), secret


def test_a_first_run_on_a_live_namespace_stops_writers_first(
    tmp_path: Path,
) -> None:
    run = run_role(
        tmp_path,
        "main",
        rules=[
            rule("^diff ", rc=1),
            *whole_run_rules(version=OLD),
        ],
        extra={"__itgraph_now_hour": 12},
    )

    assert not run.failed, run.output
    joined = run.joined()

    def first(word: str) -> int:
        return next(i for i, call in enumerate(joined) if word in call)

    # Stopped, then the database, then the migration, then the workloads.
    assert first("scale deployment itgraph-watch --replicas=0") < first(
        "rollout status statefulset/itgraph-postgres"
    )
    assert first("patch cronjob") < first(
        "rollout status statefulset/itgraph-postgres"
    )
    assert first("rollout status statefulset/itgraph-postgres") < first(
        "get pod itgraph-migrate"
    )
    assert first("get pod itgraph-migrate") < first(
        "rollout status deployment/itgraph-bot"
    )
    assert first("get pod itgraph-check-session") < first(
        "rollout status deployment/itgraph-watch"
    )
    # The label comes last of everything.
    assert first("label namespace") > first(
        "rollout status deployment/itgraph-watch"
    )
    # The schedules go back at the end.
    assert '{"spec": {"suspend": false}}' in " ".join(joined)

    # By name, and never a claim.
    deleted = [call for call in joined if " delete " in f" {call} "]
    assert not [
        call for call in deleted if "pvc" in call or "persistent" in call
    ]
    for gone in (
        "deployment itgraph-console",
        "cronjob itgraph-alerts",
        "cronjob itgraph-score",
        "secret itgraph-env",
    ):
        assert [call for call in deleted if gone in call], gone
    assert_no_secret_anywhere(run)
    assert "migration:" in run.output


def test_a_run_with_nothing_to_change_stops_and_restarts_nothing(
    tmp_path: Path,
) -> None:
    run = run_role(
        tmp_path,
        "main",
        rules=whole_run_rules(version=HEAD, marks=marks_set()),
        extra={"__itgraph_now_hour": 12},
    )

    assert not run.failed, run.output
    assert run.called("apply") == []
    assert run.called("scale") == []
    assert run.called("patch") == []
    # None of a password's handling touches psql's write path.
    assert [call for call in run.calls if "ALTER" in call["stdin"]] == []
    assert "migration: none needed" in run.output
    assert "restarted: none" in run.output


def test_a_new_password_for_another_role_stops_nothing(tmp_path: Path) -> None:
    marks = json.loads(marks_set())
    for key in ("bot", "metrics", "reader"):
        marks["metadata"]["annotations"][f"itgraph/password-{key}"] = "other"
    run = run_role(
        tmp_path,
        "main",
        rules=whole_run_rules(version=HEAD, marks=json.dumps(marks)),
        extra={"__itgraph_now_hour": 12},
    )

    assert not run.failed, run.output
    assert run.called("scale") == []
    assert run.called("patch") == []
    altered = [c["stdin"] for c in run.calls if "ALTER" in c["stdin"]]
    assert len(altered) == 3
    assert not [role for role in altered if "ROLE itgraph " in role]
    assert_no_secret_anywhere(run)


def test_taking_the_readers_password_away_stops_nothing(
    tmp_path: Path,
) -> None:
    run = run_role(
        tmp_path,
        "main",
        rules=[
            *whole_run_rules(version=HEAD, marks=marks_set()),
            rule("rolcanlogin", "t"),
        ],
        extra={
            "__itgraph_now_hour": 12,
            "itgraph_reader_postgres_password": "",
        },
    )

    assert not run.failed, run.output
    assert run.called("scale") == []
    assert run.called("patch") == []
    altered = [c["stdin"] for c in run.calls if "ALTER" in c["stdin"]]
    assert altered == [
        (
            "ALTER ROLE itgraph_reader NOLOGIN PASSWORD NULL;"
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE usename = 'itgraph_reader' AND pid <> pg_backend_pid();\n"
        )
    ]
    assert "restarted: none" in run.output
    assert_no_secret_anywhere(run)


def test_a_new_password_for_the_owner_stops_writers(tmp_path: Path) -> None:
    marks = json.loads(marks_set())
    marks["metadata"]["annotations"]["itgraph/password-owner"] = "other"
    run = run_role(
        tmp_path,
        "main",
        rules=whole_run_rules(version=HEAD, marks=json.dumps(marks)),
        extra={"__itgraph_now_hour": 12},
    )

    assert not run.failed, run.output
    assert run.called("scale", "--replicas=0")
    assert_no_secret_anywhere(run)


def test_only_a_workload_with_a_new_template_is_put_back(
    tmp_path: Path,
) -> None:
    def at(revision: str) -> str:
        items = json.loads(
            deployments(("itgraph-watch", 1, False), ("itgraph-bot", 1, False))
        )["items"]
        for item in items:
            item["metadata"]["annotations"] = {
                "deployment.kubernetes.io/revision": (
                    revision
                    if item["metadata"]["name"] == "itgraph-bot"
                    else "7"
                )
            }
        return json.dumps({"items": items})

    run = run_role(
        tmp_path,
        "main",
        rules=[
            rule("get deployment itgraph-watch itgraph-bot", at("1"), times=1),
            rule("get deployment itgraph-watch itgraph-bot", at("2")),
            # Both restart while settling, and only the bot has a new template.
            rule(
                "get pods -l app=itgraph-(watch|bot) -o json",
                pods_json(("p", 0, True)),
                times=2,
            ),
            rule(
                "get pods -l app=itgraph-(watch|bot) -o json",
                pods_json(("p", 1, True)),
            ),
            *whole_run_rules(version=HEAD, marks=marks_set()),
        ],
        extra={"__itgraph_now_hour": 12},
    )

    assert run.failed
    assert "p restarted" in run.output
    undone = [" ".join(c["args"]) for c in run.called("undo")]
    assert len(undone) == 1
    assert "deployment/itgraph-bot" in undone[0]


def test_a_database_that_restarts_while_settling_fails_and_stays(
    tmp_path: Path,
) -> None:
    run = run_role(
        tmp_path,
        "main",
        rules=[
            rule(
                "get pods -l app=itgraph-postgres",
                pods_json(("db", 0, True)),
                times=1,
            ),
            rule(
                "get pods -l app=itgraph-postgres", pods_json(("db", 1, True))
            ),
            *whole_run_rules(version=HEAD, marks=marks_set()),
        ],
        extra={"__itgraph_now_hour": 12},
    )

    assert run.failed
    assert "db restarted" in run.output
    assert run.called("undo") == []


def test_a_collector_stopped_by_hand_is_refused_before_anything(
    tmp_path: Path,
) -> None:
    run = run_role(
        tmp_path,
        "main",
        rules=whole_run_rules(
            deployed=deployments(
                ("itgraph-watch", 0, False), ("itgraph-bot", 1, False)
            )
        ),
        extra={"__itgraph_now_hour": 12},
    )

    assert run.failed
    assert "scaled to zero by hand" in run.output
    assert "scripts/on-host.sh --loop start" in run.output
    assert run.called("scale") == []
    assert run.called("apply") == []
    # The lock is released on the way out.
    assert run.called("delete", "lease")


def test_a_collector_a_dead_run_stopped_is_taken_up_again(
    tmp_path: Path,
) -> None:
    run = run_role(
        tmp_path,
        "main",
        rules=whole_run_rules(
            deployed=deployments(
                ("itgraph-watch", 0, True), ("itgraph-bot", 0, True)
            ),
            marks=marks_set(),
        ),
        extra={"__itgraph_now_hour": 12},
    )

    assert not run.failed, run.output
    assert "scaled to zero by hand" not in run.output


def test_a_class_that_deletes_its_volume_is_refused_before_any_claim(
    tmp_path: Path,
) -> None:
    run = run_role(
        tmp_path,
        "main",
        rules=[
            rule("get storageclass", "Delete"),
            *whole_run_rules(),
        ],
        extra={"__itgraph_now_hour": 12},
    )

    assert run.failed
    assert "reclaims with Delete" in run.output
    assert run.called("apply") == []


def test_a_warning_from_the_label_fails_the_run(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "main",
        rules=[
            rule(
                "label namespace",
                stderr="Warning: existing pods in namespace violate",
            ),
            *whole_run_rules(marks=marks_set()),
        ],
        extra={"__itgraph_now_hour": 12},
    )

    assert run.failed
    assert run.called("delete", "lease")


def test_a_failure_midway_puts_the_schedules_back(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "main",
        rules=[
            rule("^diff ", rc=1),
            rule("rollout status statefulset/itgraph-postgres", rc=1),
            *whole_run_rules(version=OLD),
        ],
        extra={"__itgraph_now_hour": 12},
    )

    assert run.failed
    assert '{"spec": {"suspend": false}}' in " ".join(run.joined())
    assert "next run of this role starts them" in run.output
    assert run.called("delete", "lease")
    # The database is for a person to read, and the message says where.
    assert "this run does not put it back" in run.output
    assert "deploy/runbook.md" in run.output
    assert run.called("undo") == []


def test_a_run_where_collection_is_off(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "main",
        inputs="b",
        rules=[
            rule("^diff ", rc=1),
            rule("get namespace itgraph-b", "namespace/itgraph-b"),
            *pod_answers("itgraph-check-proxied", phase="Failed"),
            *pod_answers(
                "itgraph-check-collector", phase="Failed", logs=ROUTE
            ),
            *whole_run_rules(version=OLD),
        ],
    )

    assert not run.failed, run.output
    joined = " | ".join(run.joined())
    # The collector is applied, at zero replicas, and never waited for.
    assert "rollout status deployment/itgraph-watch" not in joined
    assert "itgraph-check-session" not in joined
    assert "itgraph-check-direct" not in joined
    assert "get pod itgraph-check-collector" in joined
    assert "polls: not collecting here" in run.output
    assert_no_secret_anywhere(run, "b")


def test_nothing_secret_at_the_highest_verbosity(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "main",
        rules=[rule("^diff ", rc=1), *whole_run_rules(version=OLD)],
        extra={"__itgraph_now_hour": 12},
        arguments=["-vvv"],
    )

    assert not run.failed, run.output
    assert_no_secret_anywhere(run)


DUMP = "/tmp/fake-dump.dump"
RESTORE = {
    "itgraph_restore_from": DUMP,
    "itgraph_restore_allowed": True,
    "__itgraph_now_hour": 12,
    "__itgraph_trial_delay": 0,
}


def restore_rules() -> list[dict[str, Any]]:
    return [
        rule("get lease", ""),
        rule("get cronjobs", json.dumps(SCHEDULES)),
        rule("get jobs", ""),
        rule("get deployment itgraph-watch", ""),
        rule("get deployment itgraph-bot", "deployment.apps/itgraph-bot"),
        rule("get pods -l app=itgraph-bot", ""),
        *state_rules(version=OLD),
        *pod_answers("itgraph-migrate"),
        *pod_answers("itgraph-trial-baselines"),
        *pod_answers("itgraph-trial-yt-baselines"),
        *pod_answers(
            "itgraph-trial-backup",
            logs="/backups/weekly/full-20261106T090000Z.dump",
        ),
        rule("count\\(\\*\\) FROM baseline_runs", "1\n", times=1),
        rule("count\\(\\*\\) FROM baseline_runs", "2\n"),
        rule("count\\(\\*\\) FROM yt_baseline_runs", "0\n", times=1),
        rule("count\\(\\*\\) FROM yt_baseline_runs", "1\n"),
        rule("yt_alerts WHERE band = 2147483647", "2|0|en,ru\n"),
        rule("band = 2147483647", "1\n"),
        rule("pg_restore", "public.channels 12\n"),
    ]


def test_a_restore_is_refused_where_collection_is_on(tmp_path: Path) -> None:
    run = run_role(tmp_path, "restore", inputs="a", extra=RESTORE)

    assert run.failed
    assert "data that cannot be rebuilt" in run.output
    assert run.calls == []


def test_a_restore_is_refused_without_the_input(tmp_path: Path) -> None:
    run = run_role(
        tmp_path,
        "restore",
        inputs="b",
        extra={"itgraph_restore_from": DUMP},
    )

    assert run.failed
    assert "itgraph_restore_allowed is not true" in run.output
    assert run.calls == []


def test_a_file_that_is_no_dump_touches_nothing(tmp_path: Path) -> None:
    dump = tmp_path / "text.dump"
    dump.write_text("not a dump\n")
    run = run_role(
        tmp_path,
        "restore",
        inputs="b",
        rules=[rule("pg_restore --list", rc=1, stderr="not an archive")],
        extra={**RESTORE, "itgraph_restore_from": str(dump)},
    )

    assert run.failed
    assert "is not a dump pg_restore can read" in run.output
    assert run.changing() == []


def test_a_restore_in_order(tmp_path: Path) -> None:
    dump = tmp_path / "real.dump"
    dump.write_bytes(b"fake dump bytes\n")
    run = run_role(
        tmp_path,
        "restore",
        inputs="b",
        rules=restore_rules(),
        extra={**RESTORE, "itgraph_restore_from": str(dump)},
    )

    assert not run.failed, run.output
    joined = run.joined()

    def first(word: str) -> int:
        return next(i for i, call in enumerate(joined) if word in call)

    assert first("patch cronjob") < first("dropdb --force")
    assert first("scale deployment itgraph-bot --replicas=0") < first(
        "dropdb --force"
    )
    assert first("dropdb --force") < first("get pod itgraph-migrate")
    assert first("get pod itgraph-migrate") < first("VACUUM (FULL)")
    assert first("VACUUM (FULL)") < first(
        "scale deployment itgraph-bot --replicas=1"
    )
    assert first("scale deployment itgraph-bot --replicas=1") < first(
        "get pod itgraph-trial-baselines"
    )
    # YouTube's refresh is tried on the copy too, after Telegram's.
    assert first("get pod itgraph-trial-baselines") < first(
        "get pod itgraph-trial-yt-baselines"
    )
    assert "A baseline run of each platform" in run.output
    # The alert is raised on a restored post, and read back as delivered.
    assert first("get pod itgraph-trial-backup") < first("INSERT INTO alerts")
    [raised] = [call for call in joined if "INSERT INTO alerts" in call]
    assert "FROM raw_messages" in raised
    # Neutralise before read-back, each as one script on stdin.
    scripts = [call["stdin"] for call in run.calls if call["stdin"]]
    assert any("DELETE FROM channels" in script for script in scripts)
    assert any("still holds" in script for script in scripts)
    # The migration takes no dump of what is not yet neutralised.
    [migrate] = [
        c for c in run.called("create") if "itgraph-migrate" in c["stdin"]
    ]
    assert "ITGRAPH_SKIP_BACKUP" in migrate["stdin"]
    # Never the session's volume, and the replacing script stays one script.
    assert "session" not in " ".join(joined)
    [replace] = [call for call in joined if "dropdb --force itgraph" in call]
    assert "pg_restore --exit-on-error --no-owner -d itgraph" in replace
    assert '{"spec": {"suspend": false}}' in " ".join(joined)
    assert run.called("delete", "lease")
    assert "Delivered through the bot: an alert" in run.output
    # A synthetic video per language after Telegram's, delivered to both
    # topics, and removed whatever came of it.
    assert first("INSERT INTO alerts") < first("INSERT INTO yt_raw_videos")
    [youtube] = [call for call in joined if "INSERT INTO yt_alerts" in call]
    assert "lang IN ('en', 'ru')" in youtube
    assert first("INSERT INTO yt_raw_videos") < first(
        "DELETE FROM yt_raw_videos WHERE video_id LIKE 'itgTrial%'"
    )
    assert "YouTube: to the topics en,ru" in run.output
    assert_no_secret_anywhere(run, "b")


def test_a_restore_without_a_youtube_chat_says_so(tmp_path: Path) -> None:
    dump = tmp_path / "real.dump"
    dump.write_bytes(b"fake dump bytes\n")
    run = run_role(
        tmp_path,
        "restore",
        inputs="b",
        rules=restore_rules(),
        extra={
            **RESTORE,
            "itgraph_restore_from": str(dump),
            "itgraph_youtube_alert_chat_id": 0,
            "itgraph_youtube_alert_topic_en": 0,
            "itgraph_youtube_alert_topic_ru": 0,
        },
    )

    assert not run.failed, run.output
    assert "YouTube: not configured in this environment" in run.output
    assert not [c for c in run.joined() if "yt_raw_videos" in c]


def test_a_youtube_alert_not_delivered_fails_the_restore(
    tmp_path: Path,
) -> None:
    dump = tmp_path / "real.dump"
    dump.write_bytes(b"fake dump bytes\n")
    run = run_role(
        tmp_path,
        "restore",
        inputs="b",
        rules=[
            rule("yt_alerts WHERE band = 2147483647", "2|1|en\n", times=None),
            *restore_rules(),
        ],
        extra={**RESTORE, "itgraph_restore_from": str(dump)},
    )

    assert run.failed
    assert "delivered en of 2 YouTube alerts" in run.output
    # The synthetic videos are removed even so.
    assert [
        c
        for c in run.joined()
        if "DELETE FROM yt_raw_videos WHERE video_id LIKE 'itgTrial%'" in c
    ]


def test_a_read_back_that_fails_empties_the_database(tmp_path: Path) -> None:
    dump = tmp_path / "real.dump"
    dump.write_bytes(b"fake dump bytes\n")
    run = run_role(
        tmp_path,
        "restore",
        inputs="b",
        rules=[
            rule(
                "psql -X -U itgraph -d itgraph -v ON_ERROR_STOP=1 -q -f -",
                rc=3,
                stderr="a restored copy still holds recent repliers: 1",
                times=None,
            ),
            *restore_rules(),
        ],
        extra={**RESTORE, "itgraph_restore_from": str(dump)},
    )

    assert run.failed
    assert "The database was emptied" in run.output
    emptied = [
        call for call in run.joined() if "dropdb --force --if-exists" in call
    ]
    assert len(emptied) == 1
    # And the bot is not started on it.
    assert "scale deployment itgraph-bot --replicas=1" not in " ".join(
        run.joined()
    )
    assert '{"spec": {"suspend": false}}' in " ".join(run.joined())
    assert run.called("delete", "lease")
    assert "The bot is left stopped" in run.output


def test_a_youtube_refresh_that_writes_nothing_fails_the_restore(
    tmp_path: Path,
) -> None:
    dump = tmp_path / "real.dump"
    dump.write_bytes(b"fake dump bytes\n")
    run = run_role(
        tmp_path,
        "restore",
        inputs="b",
        rules=[
            rule("count\\(\\*\\) FROM yt_baseline_runs", "0\n"),
            *restore_rules(),
        ],
        extra={**RESTORE, "itgraph_restore_from": str(dump)},
    )

    assert run.failed
    assert "YouTube baseline refresh wrote no run" in run.output


def test_a_trial_that_writes_nothing_fails_the_restore(tmp_path: Path) -> None:
    dump = tmp_path / "real.dump"
    dump.write_bytes(b"fake dump bytes\n")
    run = run_role(
        tmp_path,
        "restore",
        inputs="b",
        rules=[
            rule("count\\(\\*\\) FROM baseline_runs", "1\n"),
            *restore_rules(),
        ],
        extra={**RESTORE, "itgraph_restore_from": str(dump)},
    )

    assert run.failed
    assert "wrote no run" in run.output
    # Past neutralising, so the copy is kept, with the bot running on it.
    assert [c for c in run.joined() if "dropdb --force --if-exists" in c] == []
    assert "The bot runs on the neutralised copy" in run.output
    assert "The bot is left stopped" not in run.output


def test_inside_quiet_hours_the_alert_is_reported_as_not_proven(
    tmp_path: Path,
) -> None:
    dump = tmp_path / "real.dump"
    dump.write_bytes(b"fake dump bytes\n")
    run = run_role(
        tmp_path,
        "restore",
        inputs="b",
        rules=restore_rules(),
        extra={
            **RESTORE,
            "itgraph_restore_from": str(dump),
            "__itgraph_now_hour": 3,
        },
    )

    assert not run.failed, run.output
    assert "could not be proven" in run.output
    assert [c for c in run.joined() if "INSERT INTO alerts" in c] == []
    assert "YouTube: could not be proven" in run.output
    assert [c for c in run.joined() if "INSERT INTO yt_raw_videos" in c] == []
