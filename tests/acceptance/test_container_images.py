"""Runtime images must not contain ops tools."""
from pathlib import Path


def _stages(content: str) -> list[tuple[str, list[str]]]:
    """Split a Dockerfile into (stage_name, lines) pairs by FROM directives."""
    stages: list[tuple[str, list[str]]] = []
    current_name = "default"
    current_lines: list[str] = []
    for line in content.splitlines():
        if line.startswith("FROM"):
            if current_lines:
                stages.append((current_name, current_lines))
            # FROM <image> AS <name>
            parts = line.split()
            current_name = parts[-1] if "AS" in [p.upper() for p in parts] else parts[1]
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        stages.append((current_name, current_lines))
    return stages


def test_api_dockerfile_uses_multi_stage():
    content = Path("deployments/compose/api.Dockerfile").read_text()
    assert "FROM" in content
    # Must have at least 2 stages (builder + runtime)
    from_lines = [line for line in content.split("\n") if line.startswith("FROM")]
    assert len(from_lines) >= 2


def test_ops_dockerfile_exists():
    assert Path("deployments/compose/ops.Dockerfile").exists()
    content = Path("deployments/compose/ops.Dockerfile").read_text()
    assert "FROM" in content


def test_api_dockerfile_does_not_install_pg_tools():
    content = Path("deployments/compose/api.Dockerfile").read_text()
    # Runtime stage should not install postgresql-client or mc
    stages = _stages(content)
    assert stages, "api.Dockerfile must define at least one stage"
    final_name, final_lines = stages[-1]
    forbidden = ("postgresql-client", "minio-client", "mc ", "docker.io", "docker-ce")
    joined = "\n".join(final_lines)
    for token in forbidden:
        assert token not in joined, (
            f"runtime stage '{final_name}' must not install '{token}'"
        )


def test_ops_dockerfile_installs_pg_client_and_mc():
    content = Path("deployments/compose/ops.Dockerfile").read_text()
    assert "postgresql-client-16" in content
    assert "mc" in content
    assert "age" in content


def test_ops_dockerfile_does_not_install_docker_cli():
    content = Path("deployments/compose/ops.Dockerfile").read_text()
    assert "docker.io" not in content
    assert "docker-ce" not in content
