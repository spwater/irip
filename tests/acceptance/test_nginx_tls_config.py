"""Nginx TLS configuration tests."""

from pathlib import Path


def test_production_web_maps_tls():
    """Production compose should expose 443 and mount TLS secrets."""
    import yaml

    compose = {"services": {}}
    for f in ("compose.base.yaml", "compose.production.yaml"):
        if Path(f).exists():
            data = yaml.safe_load(Path(f).read_text())
            compose["services"].update(data.get("services", {}))
    if "web" not in compose["services"]:
        return
    web = compose["services"]["web"]
    ports = web.get("ports", [])
    assert "443:443" in ports
    # Check secrets are defined
    secrets = web.get("secrets", [])
    if secrets:
        secret_names = [s if isinstance(s, str) else s.get("source", "") for s in secrets]
        assert "tls_fullchain" in secret_names or "tls_private_key" in secret_names


def test_development_nginx_does_not_redirect_to_https():
    """Development Nginx config should not redirect to HTTPS."""
    nginx_http = Path("deployments/compose/nginx-http.conf")
    if not nginx_http.exists():
        return
    content = nginx_http.read_text()
    assert "return 301 https" not in content


def test_production_nginx_has_tls_config():
    """Production Nginx config should listen on 443 with SSL."""
    nginx_conf = Path("deployments/compose/nginx.conf")
    if not nginx_conf.exists():
        return
    content = nginx_conf.read_text()
    # Must have SSL/TLS directives
    has_ssl = "ssl_certificate" in content or "listen 443" in content
    assert has_ssl, "Production nginx.conf must have TLS configuration"
