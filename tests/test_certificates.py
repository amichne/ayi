from __future__ import annotations

import ssl
import subprocess
import threading
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import override

import pytest

from bootstrap import DownloadFailed, InstallError, download

from .conftest import SOURCE, Shell

CONTENT = b"Ayi certificate fixture\n"


@dataclass(frozen=True)
class TlsServer:
    url: str
    certificate: Path


@pytest.fixture
def tls_server(shell: Shell) -> Iterator[TlsServer]:
    directory = shell.cwd / "certificates"
    directory.mkdir()
    extensions = directory / "extensions.cnf"
    _ = extensions.write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "subjectAltName=DNS:localhost\n"
        "extendedKeyUsage=serverAuth\n"
    )
    for arguments in (
        [
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-keyout",
            "ca.key",
            "-out",
            "ca.pem",
            "-subj",
            "/CN=Ayi test CA",
        ],
        [
            "req",
            "-new",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            "server.key",
            "-out",
            "server.csr",
            "-subj",
            "/CN=localhost",
        ],
        [
            "x509",
            "-req",
            "-in",
            "server.csr",
            "-CA",
            "ca.pem",
            "-CAkey",
            "ca.key",
            "-CAcreateserial",
            "-out",
            "server.pem",
            "-days",
            "1",
            "-extfile",
            str(extensions),
        ],
    ):
        result = subprocess.run(
            ["openssl", *arguments],
            cwd=directory,
            env=shell.env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(CONTENT)))
            self.end_headers()
            _ = self.wfile.write(CONTENT)

        @override
        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(directory / "server.pem", directory / "server.key")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield TlsServer(f"https://localhost:{server.server_port}/source", directory / "ca.pem")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_download_accepts_the_configured_ca(
    shell: Shell, tls_server: TlsServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "bootstrap.os.environ", dict(shell.env, CURL_CA_BUNDLE=str(tls_server.certificate))
    )
    assert download(tls_server.url) == CONTENT


def test_download_rejects_an_untrusted_issuer(
    shell: Shell, tls_server: TlsServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A user's curl defaults must not disable certificate authentication.
    _ = (Path(shell.env["HOME"]) / ".curlrc").write_text("insecure\n")
    monkeypatch.setattr("bootstrap.os.environ", shell.env)
    result = download(tls_server.url)
    assert isinstance(result, DownloadFailed)
    assert result.url == tls_server.url
    assert "certificate" in result.reason.lower()


def test_download_rejects_a_trusted_certificate_for_the_wrong_hostname(
    shell: Shell, tls_server: TlsServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "bootstrap.os.environ", dict(shell.env, CURL_CA_BUNDLE=str(tls_server.certificate))
    )
    result = download(tls_server.url.replace("localhost", "127.0.0.1"))
    assert isinstance(result, DownloadFailed)
    assert "certificate" in result.reason.lower()


def test_download_rejects_an_oversized_https_response(
    shell: Shell, tls_server: TlsServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "bootstrap.os.environ", dict(shell.env, CURL_CA_BUNDLE=str(tls_server.certificate))
    )
    monkeypatch.setattr("bootstrap.MAX_SOURCE", len(CONTENT) - 1)
    with pytest.raises(InstallError) as error:
        _ = download(tls_server.url)
    assert error.value.status == 65
    assert "exceeds" in error.value.message


def test_launcher_uses_system_certificates_before_the_project_exists(shell: Shell) -> None:
    uv = Path(shell.env["PATH"].split(":")[0]) / "uv"
    uv.unlink()
    _ = uv.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
    uv.chmod(0o755)
    result = subprocess.run(
        ["sh", str(SOURCE / "install.sh")],
        cwd=shell.cwd,
        env=shell.env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--system-certs" in result.stdout.splitlines()
    assert not (Path(shell.env["HOME"]) / ".local/share/ayi").exists()


def test_project_uses_system_certificates_for_dependency_downloads() -> None:
    # This asserts uv configuration. The TLS tests separately exercise the source transport.
    project = tomllib.loads((SOURCE / "pyproject.toml").read_text())
    assert project["tool"]["uv"].get("system-certs") is True


def test_portable_script_uses_system_certificates_for_python_downloads() -> None:
    source = (SOURCE / "bootstrap.py").read_text()
    assert "--system-certs" in source.splitlines()[0]
