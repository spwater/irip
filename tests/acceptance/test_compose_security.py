"""Production Compose must not expose internal services."""

from pathlib import Path

import yaml


def _load_compose(*files):
    config = {"services": {}, "networks": {}, "volumes": {}}
    for f in files:
        if Path(f).exists():
            data = yaml.safe_load(Path(f).read_text())
            config["services"].update(data.get("services", {}))
            config["networks"].update(data.get("networks", {}))
            config["volumes"].update(data.get("volumes", {}))
    return config


def test_only_web_exposes_production_ports():
    compose = _load_compose("compose.base.yaml", "compose.production.yaml")
    if not compose["services"]:
        return
    services = compose["services"]
    exposed = {name for name, config in services.items() if config.get("ports")}
    assert exposed == {"web"}, f"Only web should expose ports, got: {exposed}"
    assert set(services["web"]["ports"]) == {"80:80", "443:443"}
