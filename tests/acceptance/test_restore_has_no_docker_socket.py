"""Restore must not use Docker Socket or docker compose commands."""
from pathlib import Path


def test_restore_py_has_no_docker_compose():
    content = Path("deployments/compose/restore.py").read_text()
    assert "docker compose" not in content
    assert "docker.stop" not in content
    assert "docker.start" not in content


def test_production_compose_restore_has_no_docker_socket():
    import yaml
    compose = {"services": {}}
    for f in ("compose.base.yaml", "compose.production.yaml"):
        if Path(f).exists():
            data = yaml.safe_load(Path(f).read_text())
            compose["services"].update(data.get("services", {}))
    if "restore" not in compose["services"]:
        return
    volumes = compose["services"]["restore"].get("volumes", [])
    for v in volumes:
        assert "docker.sock" not in str(v), f"Restore service mounts Docker socket: {v}"
