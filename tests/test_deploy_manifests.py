"""The Kubernetes manifests, rendered with example variables and
parsed. No network and no cluster: the templates are plain Jinja, so
they render here exactly as the playbook renders them, and the
assertions are about the objects that come out.
"""

import json
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

TEMPLATES = Path(__file__).parents[1] / "deploy" / "templates" / "k8s"

NAMESPACE = "itgraph"

# Deliberately not a plausible digest: nothing here checks the format,
# and a value that looks real costs a reader the ability to tell.
IMAGE = "ghcr.io/vyhuholl/it-media-graph@sha256:test-image-not-real"

EXAMPLE: dict[str, Any] = {
    "itgraph_image": IMAGE,
    "itgraph_commit": "test-commit-not-a-real-sha",
    "itgraph_env_checksum": "test-checksum-not-real",
    "itgraph_storage_class": "test-storage-class",
    "db_user": "itgraph",
    "db_name": "itgraph",
    "db_password": "test-db-password",
    "bot_db_password": "test-bot-db-password",
    "telegram_api_id": 0,
    "telegram_api_hash": "test-api-hash",
    "telegram_bot_token": "test-bot-token",
    "alert_chat_id": 0,
    "proxy_socks_password": "test-proxy-password",
    "proxy_outbound": {
        "protocol": "vless",
        "settings": {
            "vnext": [{"address": "test-vpn-server.invalid", "port": 443}]
        },
    },
    "youtube_api_key": "test-youtube-key",
    "watch_timezone": "Europe/Moscow",
    "alert_spike_z": 3.7,
    "itgraph_alerts_schedule": "2-59/15 * * * *",
    "itgraph_score_schedule": "8-59/15 * * * *",
    "itgraph_yt_watch_schedule": "*/15 * * * *",
    "itgraph_backup_schedule": "40 */4 * * *",
    "itgraph_baselines_schedule": "30 4 * * *",
    "itgraph_yt_refresh_schedule": "0 5 * * *",
    "b2_key_id": "test-b2-key-id",
    "b2_application_key": "test-b2-app-key",
    "b2_bucket_name": "test-b2-bucket",
    "backup_age_recipient": "test-age-recipient",
}


def render(
    template: str, variables: dict[str, Any] | None = None
) -> list[Any]:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES),
        undefined=StrictUndefined,
    )
    text = environment.get_template(template).render(
        **(variables if variables is not None else EXAMPLE)
    )
    return [document for document in yaml.safe_load_all(text) if document]


def render_all(variables: dict[str, Any] | None = None) -> list[Any]:
    objects: list[Any] = []
    for template in sorted(TEMPLATES.glob("*.j2")):
        objects.extend(render(template.name, variables))
    return objects


def of(objects: list[Any], kind: str) -> list[Any]:
    return [obj for obj in objects if obj["kind"] == kind]


def named(objects: list[Any], kind: str, name: str) -> Any:
    matches = [o for o in of(objects, kind) if o["metadata"]["name"] == name]
    assert len(matches) == 1, f"expected exactly one {kind} named {name}"
    return matches[0]


def pod_specs(objects: list[Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every pod template, as (object name, pod spec)."""
    specs: list[tuple[str, dict[str, Any]]] = []
    for obj in objects:
        kind = obj["kind"]
        if kind in ("Deployment", "StatefulSet"):
            specs.append(
                (obj["metadata"]["name"], obj["spec"]["template"]["spec"])
            )
        elif kind == "CronJob":
            job = obj["spec"]["jobTemplate"]["spec"]
            specs.append((obj["metadata"]["name"], job["template"]["spec"]))
    return specs


def containers(pod_spec: dict[str, Any]) -> list[dict[str, Any]]:
    return pod_spec.get("initContainers", []) + pod_spec["containers"]


def secret_data(objects: list[Any]) -> dict[str, dict[str, str]]:
    data: dict[str, dict[str, str]] = {}
    for obj in of(objects, "Secret"):
        data[obj["metadata"]["name"]] = obj.get("stringData", {})
    return data


def environment_of(
    container: dict[str, Any], secrets: dict[str, Any]
) -> dict[str, Any]:
    """The container's environment, with envFrom resolved through the
    rendered Secrets — the same resolution the kubelet does."""
    env = {
        entry["name"]: entry.get("value", "")
        for entry in container.get("env", [])
    }
    for source in container.get("envFrom", []):
        name = source.get("secretRef", {}).get("name")
        if name:
            env.update(secrets.get(name, {}))
    return env


def test_every_template_renders_and_parses() -> None:
    templates = sorted(TEMPLATES.glob("*.j2"))
    assert len(templates) >= 10
    for template in templates:
        assert render(template.name), f"{template.name} rendered nothing"


def test_one_image_everywhere() -> None:
    images = [
        container["image"]
        for _, pod_spec in pod_specs(render_all())
        for container in containers(pod_spec)
        if container["image"].startswith("ghcr.io/vyhuholl/it-media-graph")
    ]
    assert set(images) == {IMAGE}
    # Every itgraph workload: three deployments, six jobs, the alerts
    # pass's init container.
    assert len(images) >= 10


def test_watch_and_bot_run_one_at_a_time() -> None:
    objects = render_all()
    for name in ("itgraph-watch", "itgraph-bot"):
        deployment = named(objects, "Deployment", name)
        assert deployment["spec"]["strategy"]["type"] == "Recreate"
    watch = named(objects, "Deployment", "itgraph-watch")
    assert (
        watch["spec"]["template"]["spec"]["terminationGracePeriodSeconds"]
        == 90
    )


# name -> (schedule, startingDeadline, activeDeadline), design
# decision 14. Stated in full: a typo'd minute belongs in a failing
# test, not in a cluster.
SCHEDULES = {
    "itgraph-alerts": ("2-59/15 * * * *", 600, 3000),
    "itgraph-score": ("8-59/15 * * * *", 600, 3000),
    "itgraph-yt-watch": ("*/15 * * * *", 600, 3000),
    "itgraph-backup": ("40 */4 * * *", 10800, 7200),
    "itgraph-baselines": ("30 4 * * *", 82800, 10800),
    "itgraph-yt-refresh": ("0 5 * * *", 82800, 10800),
}


def test_cronjobs_moscow_time_forbid_and_no_retry() -> None:
    jobs = of(render_all(), "CronJob")
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
        pod = job_spec["template"]["spec"]
        assert pod["restartPolicy"] == "Never", name


def test_main_yml_carries_the_design_schedules() -> None:
    # The schedules are group_vars, so a hand edit can drift them away
    # from the design's staggered minutes; this holds the file to the
    # table the decision fixed.
    path = TEMPLATES.parents[1] / "group_vars" / "all" / "main.yml"
    settings = yaml.safe_load(path.read_text())
    for name, (schedule, _, _) in SCHEDULES.items():
        var = "itgraph_" + name.removeprefix("itgraph-").replace("-", "_")
        var += "_schedule"
        assert settings[var] == schedule, var


def test_alerts_derives_before_it_alerts() -> None:
    alerts = named(render_all(), "CronJob", "itgraph-alerts")
    pod = alerts["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    init = pod["initContainers"][0]
    assert init["name"] == "derive"
    assert init["args"] == ["derive"]
    assert pod["containers"][0]["args"] == ["alerts"]


def test_every_container_runs_in_moscow_time() -> None:
    objects = render_all()
    secrets = secret_data(objects)
    for _, pod_spec in pod_specs(objects):
        for container in containers(pod_spec):
            env = environment_of(container, secrets)
            assert env.get("TZ") == "Europe/Moscow", container["name"]


def test_nothing_is_exposed_outside_the_cluster() -> None:
    objects = render_all()
    for service in of(objects, "Service"):
        assert service["spec"]["type"] == "ClusterIP"
    assert not of(objects, "Ingress")
    assert not of(objects, "Node")
    assert not of(objects, "IngressClass")


def test_the_proxy_has_exactly_one_way_out() -> None:
    config = json.loads(
        secret_data(render_all())["itgraph-proxy"]["config.json"]
    )
    [inbound] = config["inbounds"]
    assert inbound["port"] == 1080
    assert inbound["settings"]["auth"] == "password"
    assert inbound["settings"]["udp"] is False
    assert inbound["settings"]["accounts"][0]["user"] == "itgraph"
    # One outbound, no freedom, no routing: whatever the VPN server
    # cannot carry has nowhere to go.
    assert len(config["outbounds"]) == 1
    assert config["outbounds"][0]["protocol"] != "freedom"
    assert "routing" not in config


def test_itgraph_pods_are_contained() -> None:
    for name, pod_spec in pod_specs(render_all()):
        assert pod_spec["automountServiceAccountToken"] is False, name
        if name == "itgraph-postgres":
            # The official image manages its own user, root to postgres,
            # and writes its socket directory — no read-only root
            # filesystem for it.
            continue
        for container in containers(pod_spec):
            security = container["securityContext"]
            assert security["readOnlyRootFilesystem"] is True, name
            assert security["allowPrivilegeEscalation"] is False, name
            assert security["capabilities"]["drop"] == ["ALL"], name
        assert pod_spec["securityContext"]["runAsNonRoot"] is True, name


def test_youtube_cronjobs_follow_the_key() -> None:
    without_key = dict(EXAMPLE)
    del without_key["youtube_api_key"]
    jobs = {
        job["metadata"]["name"]
        for job in of(render_all(without_key), "CronJob")
    }
    assert "itgraph-yt-watch" not in jobs
    assert "itgraph-yt-refresh" not in jobs
    assert "itgraph-backup" in jobs

    with_key = {job["metadata"]["name"] for job in of(render_all(), "CronJob")}
    assert {"itgraph-yt-watch", "itgraph-yt-refresh"} <= with_key

    # And the key itself travels only in the Secret.
    without_key_env = secret_data(render_all(without_key))["itgraph-env"]
    assert "YOUTUBE_API_KEY" not in without_key_env
    assert "YOUTUBE_API_KEY" in secret_data(render_all())["itgraph-env"]


def test_every_object_lives_in_the_namespace() -> None:
    objects = render_all()
    namespaces = of(objects, "Namespace")
    assert [ns["metadata"]["name"] for ns in namespaces] == [NAMESPACE]
    for obj in objects:
        if obj["kind"] == "Namespace":
            continue
        assert obj["metadata"]["namespace"] == NAMESPACE, obj["metadata"][
            "name"
        ]


def test_claims_and_sizes() -> None:
    claims = {
        claim["metadata"]["name"]: claim
        for claim in of(render_all(), "PersistentVolumeClaim")
    }
    sizes = {
        "itgraph-database": "20Gi",
        "itgraph-backups": "20Gi",
        "itgraph-session": "1Gi",
    }
    assert set(claims) == set(sizes)
    for name, size in sizes.items():
        assert claims[name]["spec"]["storageClassName"] == "test-storage-class"
        assert claims[name]["spec"]["resources"]["requests"]["storage"] == size


def test_the_environment_points_where_it_must() -> None:
    env = secret_data(render_all())["itgraph-env"]
    # At the Service, not at localhost or a published port.
    assert "@itgraph-postgres:5432/" in env["DATABASE_URL"]
    assert "@itgraph-postgres:5432/" in env["BOT_DATABASE_URL"]
    assert env["BOT_DATABASE_URL"].startswith(
        "postgresql+asyncpg://itgraph_bot:"
    )
    # Empty, not absent: the tools run from the image, as local
    # processes.
    assert env["POSTGRES_CONTAINER"] == ""
    assert env["BACKUP_DIR"] == "/backups"
    assert env["TELEGRAM_SESSION"].startswith("/session/")
    assert env["PROXY_HOST"] == "itgraph-proxy"
    assert env["PROXY_PORT"] == "1080"
    assert env["PROXY_USERNAME"] == "itgraph"
    assert env["PROXY_TYPE"] == "socks5"
