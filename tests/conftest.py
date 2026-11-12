import pytest


@pytest.fixture(autouse=True)
def isolated_credentials(tmp_path, monkeypatch):
    """Keep tests away from the real saved sign-in and credentials."""
    monkeypatch.setenv("PHYSIONET_CONFIG_DIR", str(tmp_path / "physionet-config"))
    for name in ("PHYSIONET_TOKEN", "PHYSIONET_USERNAME", "PHYSIONET_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
