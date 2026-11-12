"""The role's objects, rendered for each synthetic input set and parsed.

No network and no cluster: `render.yml` renders them as a run would, and the
assertions are about the objects that come out.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from roleharness import ANSIBLE_DIR, INPUTS, REPO_ROOT, render_set

from itgraph import config as config_module
from itgraph.metrics.serve import METRICS_PORT

ROLE = ANSIBLE_DIR / "itgraph"
SETS = ("a", "b")

type Objects = list[dict[str, Any]]


@pytest.fixture(scope="session")
def rendered(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, dict[str, Objects]]:
    root = tmp_path_factory.mktemp("rendered")
    return {name: render_set(name, root / name) for name in SETS}


def everything(streams: dict[str, Objects]) -> Objects:
    return [obj for objects in streams.values() for obj in objects]


def of(objects: Objects, kind: str) -> Objects:
    return [obj for obj in objects if obj["kind"] == kind]


def named(objects: Objects, kind: str, name: str) -> dict[str, Any]:
    matches = [o for o in of(objects, kind) if o["metadata"]["name"] == name]
    assert len(matches) == 1, f"expected exactly one {kind} named {name}"
    return matches[0]


def console_jobs(objects: Objects) -> dict[str, dict[str, Any]]:
    data = named(objects, "ConfigMap", "itgraph-console")["data"]
    return {
        key.removesuffix(".json"): json.loads(value)
        for key, value in data.items()
    }


def pods(
    objects: Objects,
) -> Iterator[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Every pod template: (name, its labels, its spec)."""
    for obj in objects:
        if obj["kind"] in ("Deployment", "StatefulSet"):
            template = obj["spec"]["template"]
        elif obj["kind"] == "CronJob":
            template = obj["spec"]["jobTemplate"]["spec"]["template"]
        else:
            continue
        yield (
            obj["metadata"]["name"],
            template["metadata"]["labels"],
            template["spec"],
        )
    for variant, job in console_jobs(objects).items():
        template = job["spec"]["template"]
        yield (
            f"console-{variant}",
            template["metadata"]["labels"],
            template["spec"],
        )


def containers(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return spec.get("initContainers", []) + spec["containers"]


def keys_read(objects: Objects, container: dict[str, Any]) -> set[str]:
    """What the kubelet would put in the container's environment."""
    keys = {entry["name"] for entry in container.get("env", [])}
    secrets = {
        o["metadata"]["name"]: o.get("stringData", {})
        for o in of(objects, "Secret")
    }
    for source in container.get("envFrom", []):
        if "secretRef" in source:
            keys |= set(secrets[source["secretRef"]["name"]])
        else:
            keys |= set(
                named(objects, "ConfigMap", source["configMapRef"]["name"])[
                    "data"
                ]
            )
    return keys


def test_each_stream_parses_and_holds_what_it_should(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    for streams in rendered.values():
        assert set(streams) == {
            "namespace",
            "foundation",
            "secrets",
            "proxy",
            "postgres",
            "bot",
            "metrics",
            "watch",
            "cronjobs",
        }


def test_one_image_everywhere(rendered: dict[str, dict[str, Objects]]) -> None:
    inputs = yaml.safe_load((INPUTS / "a.yml").read_text())
    images = [
        container["image"]
        for _, _, spec in pods(everything(rendered["a"]))
        for container in containers(spec)
        if "it-media-graph" in container["image"]
    ]
    assert set(images) == {inputs["itgraph_image"]}
    assert len(images) >= 11


def test_args_reach_the_entry_point(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    # A container that gives only `args` runs them through the image's
    # ENTRYPOINT; without one, `args: ["watch"]` asks the kubelet for a
    # binary called `watch`.
    final_stage = (
        (REPO_ROOT / "Dockerfile").read_text().rsplit("\nFROM ", 1)[1]
    )
    entrypoints = [
        line.strip()
        for line in final_stage.splitlines()
        if line.startswith("ENTRYPOINT")
    ]
    assert entrypoints == ['ENTRYPOINT ["itgraph"]']

    for name, _, spec in pods(everything(rendered["a"])):
        for container in containers(spec):
            if "it-media-graph" not in container["image"]:
                continue
            if "command" in container:
                assert name == "itgraph-detect" or name.startswith("console")
                continue
            assert container["args"], name
            assert container["args"][0] != "itgraph", name


@pytest.mark.parametrize("name", SETS)
def test_every_container_is_restricted_and_read_only(
    rendered: dict[str, dict[str, Objects]], name: str
) -> None:
    for pod, _, spec in pods(everything(rendered[name])):
        assert spec["automountServiceAccountToken"] is False, pod
        security = spec["securityContext"]
        assert security["runAsNonRoot"] is True, pod
        assert security["runAsUser"] != 0, pod
        assert security["seccompProfile"] == {"type": "RuntimeDefault"}, pod
        for container in containers(spec):
            own = container["securityContext"]
            assert own["readOnlyRootFilesystem"] is True, (pod, container)
            assert own["allowPrivilegeEscalation"] is False, pod
            assert own["capabilities"]["drop"] == ["ALL"], pod
        for volume in spec.get("volumes", []):
            assert not {"hostPath", "local"} & set(volume), pod


def test_postgres_runs_as_the_owner_of_its_volume_and_writes_below_mounts(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    postgres = named(
        everything(rendered["a"]), "StatefulSet", "itgraph-postgres"
    )
    spec = postgres["spec"]["template"]["spec"]
    assert spec["securityContext"]["runAsUser"] == 999
    assert spec["terminationGracePeriodSeconds"] == 120
    [container] = spec["containers"]
    mounts = {m["name"]: m["mountPath"] for m in container["volumeMounts"]}
    assert mounts == {
        "data": "/var/lib/postgresql/data",
        "run": "/var/run/postgresql",
        "tmp": "/tmp",
        "shm": "/dev/shm",
    }
    env = {entry["name"]: entry.get("value") for entry in container["env"]}
    assert env["PGDATA"].startswith(mounts["data"] + "/")
    # Over TCP: the temporary server of a first initialization has the socket
    # alone, and a probe of the socket would call that ready.
    probe = container["readinessProbe"]["exec"]["command"]
    assert probe[:3] == ["pg_isready", "-h", "127.0.0.1"]
    # A second `POSTGRES_INITDB_ARGS` would give a fresh volume a different
    # `pg_hba.conf` from the one on the live volume, and two paths to test.
    assert "POSTGRES_INITDB_ARGS" not in env
    volumes = {v["name"]: v for v in spec["volumes"]}
    assert volumes["shm"]["emptyDir"]["medium"] == "Memory"
    assert (
        volumes["data"]["persistentVolumeClaim"]["claimName"]
        == "itgraph-database"
    )
    # The claim is a plain volume, not a template the role could remake.
    assert "volumeClaimTemplates" not in postgres["spec"]


def test_the_names_the_previous_path_made_stay(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    objects = everything(rendered["a"])
    for name in ("itgraph-watch", "itgraph-bot", "itgraph-proxy"):
        deployment = named(objects, "Deployment", name)
        assert deployment["spec"]["selector"]["matchLabels"] == {"app": name}
    postgres = named(objects, "StatefulSet", "itgraph-postgres")
    assert postgres["spec"]["selector"]["matchLabels"] == {
        "app": "itgraph-postgres"
    }
    assert postgres["spec"]["serviceName"] == "itgraph-postgres"
    assert named(objects, "Service", "itgraph-postgres")["spec"]["type"] == (
        "ClusterIP"
    )
    assert named(objects, "Service", "itgraph-proxy")


def test_the_fixed_address(rendered: dict[str, dict[str, Objects]]) -> None:
    for name, address in (("a", "192.0.2.53"), ("b", "192.0.2.54")):
        access = named(
            everything(rendered[name]), "Service", "itgraph-postgres-access"
        )
        assert access["spec"]["clusterIP"] == address
        assert access["spec"]["type"] == "ClusterIP"
        assert access["spec"]["selector"] == {"app": "itgraph-postgres"}


def test_watch_and_bot_run_one_at_a_time(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    objects = everything(rendered["a"])
    for name in ("itgraph-watch", "itgraph-bot"):
        deployment = named(objects, "Deployment", name)
        assert deployment["spec"]["strategy"]["type"] == "Recreate"
    watch = named(objects, "Deployment", "itgraph-watch")
    assert (
        watch["spec"]["template"]["spec"]["terminationGracePeriodSeconds"]
        == 90
    )


def test_replicas_follow_whether_the_environment_collects(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    for name, replicas in (("a", 1), ("b", 0)):
        watch = named(
            everything(rendered[name]), "Deployment", "itgraph-watch"
        )
        assert watch["spec"]["replicas"] == replicas


# name -> (schedule, startingDeadline, activeDeadline). Stated in full: a
# typo'd minute belongs in a failing test, not in a cluster.
SCHEDULES = {
    "itgraph-detect": ("2-59/15 * * * *", 600, 3000),
    # Ten minutes: under Forbid a stuck pass blocks the passes after it.
    "itgraph-yt-watch": ("*/15 * * * *", 600, 600),
    "itgraph-backup": ("40 */4 * * *", 10800, 7200),
    "itgraph-baselines": ("30 4 * * *", 82800, 10800),
    "itgraph-yt-refresh": ("0 5 * * *", 82800, 10800),
    # Six minutes after each poll pass; ten to finish, as the pass it follows.
    "itgraph-yt-score": ("6-59/15 * * * *", 600, 600),
    # Half an hour after the refresh, which brings the settled readings.
    "itgraph-yt-baselines": ("30 5 * * *", 82800, 10800),
    # Monday morning; six days to catch up a missed Monday.
    "itgraph-yt-weekly": ("0 10 * * 1", 518400, 1800),
    # After the Pacific reset in either season.
    "itgraph-yt-backfill": ("30 11 * * *", 82800, 3000),
}


def test_cronjobs_moscow_time_forbid_and_no_retry(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    jobs = of(everything(rendered["a"]), "CronJob")
    assert {job["metadata"]["name"] for job in jobs} == set(SCHEDULES)
    for job in jobs:
        name = job["metadata"]["name"]
        schedule, starting, active = SCHEDULES[name]
        spec = job["spec"]
        job_spec = spec["jobTemplate"]["spec"]
        assert spec["schedule"] == schedule, name
        assert spec["timeZone"] == "Europe/Moscow", name
        assert spec["concurrencyPolicy"] == "Forbid", name
        assert spec["startingDeadlineSeconds"] == starting, name
        assert spec["successfulJobsHistoryLimit"] == 3, name
        assert spec["failedJobsHistoryLimit"] == 3, name
        assert job_spec["backoffLimit"] == 0, name
        assert job_spec["activeDeadlineSeconds"] == active, name
        assert job_spec["template"]["spec"]["restartPolicy"] == "Never", name


def test_detection_is_sibling_containers_and_derive_comes_before_alerts(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    detect = named(everything(rendered["a"]), "CronJob", "itgraph-detect")
    spec = detect["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    assert "initContainers" not in spec
    alerts, score = spec["containers"]
    assert alerts["command"] == [
        "sh",
        "-c",
        "itgraph derive && itgraph alerts",
    ]
    assert score["args"] == ["score"]


def test_every_container_of_ours_runs_in_moscow_time(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    objects = everything(rendered["a"])
    assert named(objects, "ConfigMap", "itgraph-config")["data"]["TZ"] == (
        "Europe/Moscow"
    )
    for pod, _, spec in pods(objects):
        for container in containers(spec):
            assert "TZ" in keys_read(objects, container), pod


def test_nothing_is_exposed_outside_the_cluster(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    objects = everything(rendered["a"])
    for service in of(objects, "Service"):
        assert service["spec"]["type"] == "ClusterIP"
    assert not of(objects, "Ingress")


def proxy_config(objects: Objects) -> dict[str, Any]:
    secret = named(objects, "Secret", "itgraph-proxy")
    config: dict[str, Any] = json.loads(secret["stringData"]["config.json"])
    return config


def test_the_proxy_has_exactly_one_way_out(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    config = proxy_config(everything(rendered["a"]))
    [inbound] = config["inbounds"]
    assert inbound["port"] == 1080
    assert inbound["settings"]["auth"] == "password"
    assert inbound["settings"]["udp"] is False
    assert inbound["settings"]["accounts"][0]["user"] == "itgraph"
    # One outbound, no freedom, no routing: whatever the VPN server cannot
    # carry has nowhere to go.
    assert len(config["outbounds"]) == 1
    assert config["outbounds"][0]["protocol"] != "freedom"
    assert "routing" not in config


def test_an_environment_that_does_not_collect_has_the_closing_outbound(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    config = proxy_config(everything(rendered["b"]))
    assert config["outbounds"] == [{"protocol": "blackhole"}]


def test_nothing_in_the_role_execs_into_the_proxy() -> None:
    for path in (ROLE / "tasks").glob("*.yml"):
        text = path.read_text()
        assert "exec" not in text or "itgraph-proxy" not in text, path.name


def keys_of(objects: Objects, kind: str, name: str) -> set[str]:
    obj = named(objects, kind, name)
    if kind == "CronJob":
        spec = obj["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    else:
        spec = obj["spec"]["template"]["spec"]
    return {
        key
        for container in containers(spec)
        for key in keys_read(objects, container)
    }


SECRET_KEYS = {
    "TELEGRAM_API_ID",
    "TELEGRAM_API_HASH",
    "TELEGRAM_BOT_TOKEN",
    "PROXY_PASSWORD",
    "B2_APPLICATION_KEY",
    "YOUTUBE_API_KEY",
}


@pytest.mark.parametrize(
    ("kind", "name", "has"),
    [
        ("Deployment", "itgraph-bot", {"TELEGRAM_BOT_TOKEN"}),
        ("Deployment", "itgraph-metrics", set()),
        ("CronJob", "itgraph-detect", set()),
        ("CronJob", "itgraph-baselines", set()),
        ("CronJob", "itgraph-yt-score", set()),
        ("CronJob", "itgraph-yt-baselines", set()),
        ("CronJob", "itgraph-yt-weekly", set()),
        ("CronJob", "itgraph-backup", {"B2_APPLICATION_KEY"}),
        ("CronJob", "itgraph-yt-watch", {"YOUTUBE_API_KEY"}),
        ("CronJob", "itgraph-yt-refresh", {"YOUTUBE_API_KEY"}),
        ("CronJob", "itgraph-yt-backfill", {"YOUTUBE_API_KEY"}),
        (
            "Deployment",
            "itgraph-watch",
            {"TELEGRAM_API_ID", "TELEGRAM_API_HASH", "PROXY_PASSWORD"},
        ),
    ],
)
def test_each_workload_holds_only_its_own_secrets(
    rendered: dict[str, dict[str, Objects]],
    kind: str,
    name: str,
    has: set[str],
) -> None:
    keys = keys_of(everything(rendered["a"]), kind, name)
    assert keys & SECRET_KEYS == has
    assert "DATABASE_URL" in keys


def test_the_bot_and_the_exporter_get_their_own_roles_url(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    objects = everything(rendered["a"])
    for name, role in (
        ("itgraph-bot", "itgraph_bot"),
        ("itgraph-metrics", "itgraph_metrics"),
        ("itgraph-database", "itgraph"),
    ):
        url = named(objects, "Secret", name)["stringData"]["DATABASE_URL"]
        assert url.startswith(f"postgresql+asyncpg://{role}:"), name
        assert "@itgraph-postgres:5432/" in url, name


def test_the_environment_points_where_it_must(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    objects = everything(rendered["a"])
    config = named(objects, "ConfigMap", "itgraph-config")["data"]
    # Empty, not absent: the tools run from the image, as local processes.
    assert config["POSTGRES_CONTAINER"] == ""
    assert config["BACKUP_DIR"] == "/backups"
    assert config["TELEGRAM_SESSION"].startswith("/session/")
    assert config["YOUTUBE_BACKFILL_ENABLED"] == "true"
    assert config["YOUTUBE_ALERT_SPIKE_Z"] == "3.0"
    assert "TELEGRAM_BOT_API_URL" not in config
    telegram = named(objects, "Secret", "itgraph-telegram")["stringData"]
    assert telegram["PROXY_HOST"] == "itgraph-proxy"
    assert telegram["PROXY_PORT"] == "1080"
    assert telegram["PROXY_USERNAME"] == "itgraph"
    assert telegram["PROXY_TYPE"] == "socks5"


def test_a_key_absent_means_no_youtube_job_and_no_key(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    objects = everything(rendered["b"])
    jobs = {job["metadata"]["name"] for job in of(objects, "CronJob")}
    # YouTube's scoring needs no key, and runs whatever the key.
    assert jobs == {
        "itgraph-detect",
        "itgraph-baselines",
        "itgraph-backup",
        "itgraph-yt-score",
        "itgraph-yt-baselines",
        "itgraph-yt-weekly",
    }
    assert not [
        s
        for s in of(objects, "Secret")
        if s["metadata"]["name"] == "itgraph-youtube"
    ]
    for _, _, spec in pods(objects):
        for container in containers(spec):
            assert "YOUTUBE_API_KEY" not in keys_read(objects, container)


def test_no_offsite_means_no_key_and_no_way_out(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    objects = everything(rendered["b"])
    backup = named(objects, "CronJob", "itgraph-backup")
    assert "B2_KEY_ID" not in keys_of(objects, "CronJob", "itgraph-backup")
    labels = backup["spec"]["jobTemplate"]["spec"]["template"]["metadata"][
        "labels"
    ]
    assert "itgraph/internet" not in labels


# What each pod may reach, by the labels that say so.
REACH = {
    "itgraph-watch": {"itgraph/database", "itgraph/telegram"},
    "itgraph-bot": {"itgraph/database", "itgraph/internet"},
    "itgraph-metrics": {"itgraph/database"},
    "itgraph-detect": {"itgraph/database"},
    "itgraph-baselines": {"itgraph/database"},
    "itgraph-yt-score": {"itgraph/database"},
    "itgraph-yt-baselines": {"itgraph/database"},
    "itgraph-yt-weekly": {"itgraph/database"},
    "itgraph-backup": {"itgraph/database", "itgraph/internet"},
    "itgraph-yt-watch": {"itgraph/database", "itgraph/internet"},
    "itgraph-yt-refresh": {"itgraph/database", "itgraph/internet"},
    "itgraph-yt-backfill": {"itgraph/database", "itgraph/internet"},
    "itgraph-proxy": {"itgraph/internet"},
    "itgraph-postgres": set(),
    "console-plain": {"itgraph/database", "itgraph/internet"},
    "console-session": {"itgraph/database", "itgraph/telegram"},
    "console-backups": {"itgraph/database"},
}


def test_each_workload_carries_exactly_its_labels(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    seen = set()
    for name, labels, _ in pods(everything(rendered["a"])):
        seen.add(name)
        reach = {
            key
            for key in labels
            if key
            in {"itgraph/database", "itgraph/telegram", "itgraph/internet"}
        }
        assert reach == REACH[name], name
    assert seen == set(REACH)


def test_everything_is_denied_both_ways_first(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    objects = everything(rendered["a"])
    deny = named(objects, "NetworkPolicy", "itgraph-deny-all")
    assert deny["spec"]["podSelector"] == {}
    assert sorted(deny["spec"]["policyTypes"]) == ["Egress", "Ingress"]
    assert "ingress" not in deny["spec"]
    assert "egress" not in deny["spec"]


def test_dns_is_opened_for_all_but_the_database(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    dns = named(everything(rendered["a"]), "NetworkPolicy", "itgraph-dns")
    assert dns["spec"]["podSelector"]["matchExpressions"] == [
        {"key": "app", "operator": "NotIn", "values": ["itgraph-postgres"]}
    ]


def test_the_internet_is_public_addresses_alone(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    policy = named(
        everything(rendered["a"]), "NetworkPolicy", "itgraph-reach-internet"
    )
    [rule] = policy["spec"]["egress"]
    [peer] = rule["to"]
    assert peer["ipBlock"]["cidr"] == "0.0.0.0/0"
    assert {"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"} <= set(
        peer["ipBlock"]["except"]
    )


def test_the_scraper_alone_reads_the_series_on_that_port_alone(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    objects = everything(rendered["a"])
    policy = named(objects, "NetworkPolicy", "itgraph-metrics")
    [rule] = policy["spec"]["ingress"]
    assert rule["ports"] == [{"protocol": "TCP", "port": METRICS_PORT}]
    [peer] = rule["from"]
    assert peer["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/name": "prometheus"
    }
    assert peer["namespaceSelector"]["matchLabels"] == {
        "kubernetes.io/metadata.name": "monitoring"
    }
    exporter = named(objects, "Deployment", "itgraph-metrics")
    [container] = exporter["spec"]["template"]["spec"]["containers"]
    assert container["args"] == ["metrics"]
    assert container["ports"] == [
        {"name": "metrics", "containerPort": METRICS_PORT}
    ]
    assert named(objects, "Service", "itgraph-metrics")


def test_no_job_runs_what_is_for_a_person_to_read(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    # Reports print handles read from YouTube or what the operator's own
    # subscriptions link to, and a job's output is kept in the cluster's logs;
    # search spends a bucket of its own. All of them are run by hand.
    manual = {
        "mentions",
        "tg-lookup",
        "tg-candidates",
        "mention-lookup",
        "mention-candidates",
        "featured-candidates",
        "search",
        "search-candidates",
    }
    for name, _, spec in pods(everything(rendered["a"])):
        for container in containers(spec):
            assert not manual & set(container.get("args", [])), name
    refresh = named(everything(rendered["a"]), "CronJob", "itgraph-yt-refresh")
    [container] = refresh["spec"]["jobTemplate"]["spec"]["template"]["spec"][
        "containers"
    ]
    assert container["args"] == ["yt", "refresh"]


@pytest.mark.parametrize("name", SETS)
def test_every_object_lives_in_the_namespace(
    rendered: dict[str, dict[str, Objects]], name: str
) -> None:
    objects = everything(rendered[name])
    namespace = yaml.safe_load((INPUTS / f"{name}.yml").read_text())[
        "itgraph_namespace"
    ]
    assert [o["metadata"]["name"] for o in of(objects, "Namespace")] == [
        namespace
    ]
    for obj in objects:
        if obj["kind"] != "Namespace":
            assert obj["metadata"]["namespace"] == namespace, obj["metadata"]


def test_the_namespace_manifest_holds_no_pod_security_label(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    # The label comes last in a run, by `kubectl label`. Were it here, a
    # first run on a live namespace could not go back.
    [namespace] = rendered["a"]["namespace"]
    assert "labels" not in namespace["metadata"]


def test_claims_and_sizes(rendered: dict[str, dict[str, Objects]]) -> None:
    claims = {
        claim["metadata"]["name"]: claim
        for claim in of(everything(rendered["a"]), "PersistentVolumeClaim")
    }
    sizes = {
        "itgraph-database": "20Gi",
        "itgraph-backups": "20Gi",
        "itgraph-session": "1Gi",
    }
    assert set(claims) == set(sizes)
    for name, size in sizes.items():
        spec = claims[name]["spec"]
        assert spec["storageClassName"] == "example-retaining-class"
        assert spec["resources"]["requests"]["storage"] == size


def test_no_value_of_one_set_is_in_the_others_objects(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    """What makes a test environment's objects provably its own."""

    own = (
        "itgraph_storage_class",
        "itgraph_postgres_access_ip",
        "itgraph_image",
        "itgraph_postgres_image",
        "itgraph_proxy_image",
        "itgraph_telegram_api_id",
        "itgraph_telegram_api_hash",
        "itgraph_bot_token",
        "itgraph_alert_chat_id",
        "itgraph_youtube_alert_chat_id",
        "itgraph_youtube_alert_topic_en",
        "itgraph_youtube_alert_topic_ru",
        "itgraph_postgres_password",
        "itgraph_bot_postgres_password",
        "itgraph_metrics_postgres_password",
        "itgraph_proxy_password",
    )

    def values(name: str) -> set[str]:
        inputs = yaml.safe_load((INPUTS / f"{name}.yml").read_text())
        return {str(inputs[key]) for key in own}

    a, b = values("a"), values("b")
    assert a & b == set()
    for name, other in (("a", b), ("b", a)):
        text = json.dumps(everything(rendered[name]))
        for value in other:
            assert value not in text, value


def test_no_secret_is_outside_the_secret_streams(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    inputs = yaml.safe_load((INPUTS / "a.yml").read_text())
    secrets = [
        inputs[key]
        for key in (
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
    ]
    for stream, objects in rendered["a"].items():
        kept = [o for o in objects if o["kind"] != "Secret"]
        text = json.dumps(kept)
        for secret in secrets:
            assert secret not in text, stream


def test_the_readers_password_is_in_no_object(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    """An operator's door: no workload reads it, so no Secret holds it."""
    password = yaml.safe_load((INPUTS / "a.yml").read_text())[
        "itgraph_reader_postgres_password"
    ]
    assert password

    for stream, objects in rendered["a"].items():
        assert password not in json.dumps(objects), stream


def test_a_new_bot_token_changes_the_bots_template_alone(
    rendered: dict[str, dict[str, Objects]], tmp_path: Path
) -> None:
    changed = render_set(
        "a",
        tmp_path / "new-token",
        extra={"itgraph_bot_token": "111:fakeNewBotToken"},
    )

    def annotation(streams: dict[str, Objects], kind: str, name: str) -> str:
        obj = named(everything(streams), kind, name)
        checksum: str = obj["spec"]["template"]["metadata"]["annotations"][
            "itgraph/config-checksum"
        ]
        return checksum

    assert annotation(changed, "Deployment", "itgraph-bot") != annotation(
        rendered["a"], "Deployment", "itgraph-bot"
    )
    for name in ("itgraph-watch", "itgraph-metrics", "itgraph-proxy"):
        assert annotation(changed, "Deployment", name) == annotation(
            rendered["a"], "Deployment", name
        ), name
    assert changed["postgres"] == rendered["a"]["postgres"]


YOUTUBE_ALERT_KEYS = {
    "YOUTUBE_ALERT_CHAT_ID",
    "YOUTUBE_ALERT_TOPIC_EN",
    "YOUTUBE_ALERT_TOPIC_RU",
}


def test_the_bot_holds_the_youtube_chat_and_nothing_else_does(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    objects = everything(rendered["a"])

    assert YOUTUBE_ALERT_KEYS <= keys_of(objects, "Deployment", "itgraph-bot")
    for kind, name in (
        ("Deployment", "itgraph-watch"),
        ("Deployment", "itgraph-metrics"),
        ("CronJob", "itgraph-detect"),
    ):
        keys = keys_of(objects, kind, name)
        assert not keys & (YOUTUBE_ALERT_KEYS | {"ALERT_CHAT_ID"}), name


def test_without_a_youtube_chat_the_bot_holds_no_youtube_keys(
    tmp_path: Path,
) -> None:
    streams = render_set(
        "a",
        tmp_path / "no-youtube-chat",
        extra={
            "itgraph_youtube_alert_chat_id": 0,
            "itgraph_youtube_alert_topic_en": 0,
            "itgraph_youtube_alert_topic_ru": 0,
        },
    )

    keys = keys_of(everything(streams), "Deployment", "itgraph-bot")
    assert not keys & YOUTUBE_ALERT_KEYS
    assert "ALERT_CHAT_ID" in keys


def test_a_new_youtube_topic_restarts_the_bot_alone(
    rendered: dict[str, dict[str, Objects]], tmp_path: Path
) -> None:
    changed = render_set(
        "a",
        tmp_path / "new-topic",
        extra={"itgraph_youtube_alert_topic_ru": 7109},
    )

    def annotation(streams: dict[str, Objects], name: str) -> str:
        obj = named(everything(streams), "Deployment", name)
        checksum: str = obj["spec"]["template"]["metadata"]["annotations"][
            "itgraph/config-checksum"
        ]
        return checksum

    assert annotation(changed, "itgraph-bot") != annotation(
        rendered["a"], "itgraph-bot"
    )
    for name in ("itgraph-watch", "itgraph-metrics", "itgraph-proxy"):
        assert annotation(changed, name) == annotation(rendered["a"], name)


def test_a_new_api_hash_restarts_the_collector_and_not_the_bot(
    rendered: dict[str, dict[str, Objects]], tmp_path: Path
) -> None:
    changed = render_set(
        "a",
        tmp_path / "new-hash",
        extra={"itgraph_telegram_api_hash": "fakeNewApiHash"},
    )

    def template(streams: dict[str, Objects], name: str) -> Any:
        return named(everything(streams), "Deployment", name)["spec"][
            "template"
        ]

    assert template(changed, "itgraph-watch") != template(
        rendered["a"], "itgraph-watch"
    )
    assert template(changed, "itgraph-bot") == template(
        rendered["a"], "itgraph-bot"
    )


def test_the_console_holds_no_secret_and_mounts_the_session_on_request(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    objects = everything(rendered["a"])
    jobs = console_jobs(objects)
    assert set(jobs) == {"plain", "session", "backups"}
    for job in jobs.values():
        assert job["metadata"]["generateName"] == "itgraph-console-"
        assert job["metadata"]["labels"]["itgraph/console"] == "true"
        assert job["spec"]["ttlSecondsAfterFinished"] > 0
        [container] = job["spec"]["template"]["spec"]["containers"]
        assert container["command"][0] == "sleep"
        assert "activeDeadlineSeconds" not in job["spec"]

    def mounted(variant: str) -> set[str]:
        [container] = jobs[variant]["spec"]["template"]["spec"]["containers"]
        return {m["name"] for m in container["volumeMounts"]}

    assert mounted("plain") == {"tmp"}
    assert mounted("session") == {"tmp", "session"}
    assert mounted("backups") == {"tmp", "backups"}

    [plain] = jobs["plain"]["spec"]["template"]["spec"]["containers"]
    assert "TELEGRAM_API_HASH" not in keys_read(objects, plain)
    [session] = jobs["session"]["spec"]["template"]["spec"]["containers"]
    assert "TELEGRAM_API_HASH" in keys_read(objects, session)


def test_nothing_ignored_is_in_the_image() -> None:
    """An allowlist, so `.env`, a session or a dump cannot be named by accident."""
    rules = [
        line
        for line in (REPO_ROOT / ".dockerignore").read_text().splitlines()
        if line and not line.startswith("#")
    ]
    assert rules[0] == "*"
    allowed = {rule.removeprefix("!") for rule in rules[1:]}
    assert allowed == {
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "README.md",
        "alembic.ini",
        "src",
        "src/**",
    }
    assert all(rule.startswith("!") for rule in rules[1:])


def test_neither_the_collector_nor_the_bot_listens(
    rendered: dict[str, dict[str, Objects]],
) -> None:
    objects = everything(rendered["a"])
    for name in ("itgraph-watch", "itgraph-bot"):
        spec = named(objects, "Deployment", name)["spec"]["template"]["spec"]
        for container in containers(spec):
            assert "ports" not in container, name
    sources = (REPO_ROOT / "src" / "itgraph").rglob("*.py")
    listening = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in sources
        if "start_server(" in path.read_text()
    }
    assert listening == {"src/itgraph/metrics/serve.py"}


def test_defaults_are_held_to_the_code() -> None:
    defaults = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
    fields = config_module.Settings.model_fields
    assert (
        defaults["itgraph_quiet_from_hour"]
        == fields["watch_quiet_from_hour"].default
    )
    assert (
        defaults["itgraph_quiet_to_hour"]
        == fields["watch_quiet_to_hour"].default
    )
    assert defaults["itgraph_alert_spike_z"] == fields["alert_spike_z"].default
    assert (
        defaults["itgraph_youtube_alert_spike_z"]
        == fields["youtube_alert_spike_z"].default
    )
    # The restore's trial reads one window for whether the bot may speak:
    # the bot's, for both platforms, is that same window in the same zone.
    for platform in ("alert", "youtube_alert"):
        for edge in ("from", "to"):
            assert (
                fields[f"{platform}_quiet_{edge}_hour"].default
                == defaults[f"itgraph_quiet_{edge}_hour"]
            ), (platform, edge)
    assert (
        fields["youtube_alert_timezone"].default
        == defaults["itgraph_timezone"]
    )
    for name, (schedule, _, _) in SCHEDULES.items():
        key = "itgraph_" + name.removeprefix("itgraph-").replace("-", "_")
        assert defaults[key + "_schedule"] == schedule, key


def test_no_public_artifact_names_anything_real(tmp_path: Path) -> None:
    forbidden = ("root@", "inventory.ini", ".vault_pass")
    for path in ROLE.rglob("*"):
        if path.is_file():
            text = path.read_text()
            for word in forbidden:
                assert word not in text, (path.name, word)
