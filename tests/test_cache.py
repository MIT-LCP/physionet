"""Tests for listing and cleaning the cache."""

import json
import os
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

import physionet
from physionet.cache import clean_cache, list_cache
from physionet.cli import main

DAY = 24 * 3600


def add_version(cache, slug, version, files, origin="physionet.org", access="open", age_days=0):
    """Create a cached project version as fetch() would; return its folder."""
    manifest = cache / origin / ".manifests" / slug / version
    manifest.mkdir(parents=True)
    (manifest / "SHA256SUMS.txt").write_text("".join(f"{'0' * 64}  {name}\n" for name in files))
    if access:
        (manifest / "ACCESS").write_text(f"{access}\n")
    folder = cache / origin / slug / version
    for name, content in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        set_age(path, age_days)
    return folder


def set_age(path, days):
    when = time.time() - days * DAY
    os.utime(path, (when, when), follow_symlinks=False)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv("PHYSIONET_CACHE", str(cache))
    return cache


def run_cli(*args):
    with patch("sys.argv", ["physionet", *args]):
        return main()


def test_list_cache_describes_each_version(cache):
    add_version(cache, "demo", "1.0", {"a.csv": b"12345", "sub/b.csv": b"123"}, age_days=3)
    add_version(cache, "mimiciv", "3.1", {"x.csv": b"1"}, access="restricted")
    add_version(cache, "demo", "1.0", {"c.txt": b"12"}, origin="http_localhost_8000")

    entries = list_cache()

    assert [(e.origin, e.slug, e.version, e.files, e.size) for e in entries] == [
        ("http_localhost_8000", "demo", "1.0", 1, 2),
        ("physionet.org", "demo", "1.0", 2, 8),
        ("physionet.org", "mimiciv", "3.1", 1, 1),
    ]
    assert [e.restricted for e in entries] == [False, False, True]
    assert entries[1].path == cache / "physionet.org" / "demo" / "1.0"
    assert time.time() - entries[1].last_downloaded.timestamp() == pytest.approx(3 * DAY, abs=60)
    assert [e.slug for e in list_cache("mimiciv")] == ["mimiciv"]
    assert list_cache is physionet.list_cache and clean_cache is physionet.clean_cache


def test_list_cache_of_missing_or_empty_folder(tmp_path):
    assert list_cache(cache_dir=str(tmp_path / "missing")) == []
    assert list_cache(cache_dir=str(tmp_path)) == []


def test_folders_without_a_checksum_list_are_not_part_of_the_cache(tmp_path):
    # As if the cache folder were set to a folder with other files in it
    other = tmp_path / "Documents" / "papers" / "2024"
    other.mkdir(parents=True)
    (other / "draft.txt").write_text("keep me")
    add_version(tmp_path, "demo", "1.0", {"a.csv": b"1"})

    assert [e.slug for e in list_cache(cache_dir=str(tmp_path))] == ["demo"]
    clean_cache(cache_dir=str(tmp_path))

    assert (other / "draft.txt").read_text() == "keep me"
    assert not (tmp_path / "physionet.org" / "demo").exists()


def test_clean_everything(cache):
    add_version(cache, "demo", "1.0", {"a.csv": b"12345", "sub/b.csv": b"123"})
    add_version(cache, "mimiciv", "3.1", {"x.csv": b"1"}, access="restricted")

    result = clean_cache()

    assert (result.files, result.size, result.errors) == (3, 9, [])
    assert [(e.slug, e.version) for e in result.versions] == [("demo", "1.0"), ("mimiciv", "3.1")]
    assert list_cache() == []
    assert [p.name for p in (cache / "physionet.org").iterdir()] == [".manifests"]
    assert list((cache / "physionet.org" / ".manifests").iterdir()) == []


def test_clean_one_project_or_version(cache):
    add_version(cache, "demo", "1.0", {"a.csv": b"1"})
    add_version(cache, "demo", "2.0", {"a.csv": b"2"})
    add_version(cache, "other", "1.0", {"a.csv": b"3"})

    clean_cache("demo", "1.0")
    assert [(e.slug, e.version) for e in list_cache()] == [("demo", "2.0"), ("other", "1.0")]

    clean_cache("demo")
    assert [(e.slug, e.version) for e in list_cache()] == [("other", "1.0")]


def test_version_needs_slug(cache):
    with pytest.raises(ValueError, match="needs slug"):
        clean_cache(version="1.0")


def test_clean_older_than_keeps_recent_files(cache):
    folder = add_version(cache, "demo", "1.0", {"old.csv": b"123", "sub/old.csv": b"4"}, age_days=100)
    (folder / "new.csv").write_bytes(b"56")
    add_version(cache, "stale", "1.0", {"a.csv": b"7"}, age_days=100)

    result = clean_cache(older_than=timedelta(days=90))

    assert (result.files, result.size) == (3, 5)
    assert [(e.slug, e.version) for e in result.versions] == [("stale", "1.0")]
    remaining = list_cache()
    assert [(e.slug, e.files) for e in remaining] == [("demo", 1)]
    assert sorted(p.name for p in folder.iterdir()) == ["new.csv"]
    # A number is a number of days
    assert clean_cache(older_than=0).files == 1


@pytest.mark.parametrize("older_than", [-1, timedelta(days=-1), "90d", True])
def test_older_than_must_be_an_age(cache, older_than):
    with pytest.raises(ValueError):
        clean_cache(older_than=older_than)


def test_dry_run_deletes_nothing(cache):
    folder = add_version(cache, "demo", "1.0", {"a.csv": b"12345"})

    result = clean_cache(dry_run=True)

    assert (result.files, result.size) == (1, 5)
    assert [e.slug for e in result.versions] == ["demo"]
    assert (folder / "a.csv").exists()
    assert len(list_cache()) == 1


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need extra rights on Windows")
def test_links_are_removed_not_followed(cache, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("keep me")
    folder = add_version(cache, "demo", "1.0", {"a.csv": b"1"})
    (folder / "linked-folder").symlink_to(outside, target_is_directory=True)
    (folder / "linked-file").symlink_to(outside / "precious.txt")

    assert list_cache()[0].files == 1
    result = clean_cache()

    assert result.files == 1
    assert (outside / "precious.txt").read_text() == "keep me"
    assert not folder.exists()


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need extra rights on Windows")
def test_a_linked_version_folder_is_not_entered(cache, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("keep me")
    add_version(cache, "demo", "1.0", {})
    (cache / "physionet.org" / "demo").mkdir()
    (cache / "physionet.org" / "demo" / "1.0").symlink_to(outside, target_is_directory=True)

    assert list_cache()[0].files == 0
    clean_cache()

    assert (outside / "precious.txt").read_text() == "keep me"


def test_unfinished_downloads_are_kept_for_a_day(cache):
    folder = add_version(cache, "demo", "1.0", {"a.csv": b"1"}, age_days=10)
    running = folder / ".download-new"
    running.mkdir()
    (running / "b.csv").write_bytes(b"partial")
    stale = folder / "sub" / ".download-old"
    stale.mkdir(parents=True)
    (stale / "c.csv").write_bytes(b"12")
    set_age(stale / "c.csv", 2)
    set_age(stale, 2)

    entry = list_cache()[0]
    assert (entry.files, entry.size, entry.unfinished) == (1, 1, 2)

    result = clean_cache()

    # Unfinished downloads count towards the space freed, not the files
    assert (result.files, result.size) == (1, 3)
    assert result.versions == []
    assert (running / "b.csv").exists()
    assert not stale.exists()
    assert not (folder / "a.csv").exists()
    assert len(list_cache()) == 1


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions, not as root")
def test_files_that_cannot_be_deleted_are_reported(cache):
    folder = add_version(cache, "demo", "1.0", {"sub/a.csv": b"1"})
    (folder / "sub").chmod(0o500)
    try:
        result = clean_cache()
    finally:
        (folder / "sub").chmod(0o700)

    assert result.files == 0
    assert [path for path, _ in result.errors] == [folder / "sub" / "a.csv"]
    assert result.versions == []
    assert list_cache()[0].files == 1


# --- Command line ---


def test_cli_list(cache, capsys):
    add_version(cache, "demo", "1.0", {"a.csv": b"x" * 2048})
    add_version(cache, "mimiciv", "3.1", {"x.csv": b"1"}, access="restricted")

    assert run_cli("cache") == 0
    out = capsys.readouterr().out
    assert f"Cache: {cache} (2 dataset version(s), 2 file(s), 2.00 KB)" in out
    lines = out.splitlines()
    assert lines[2].split() == ["DATASET", "VERSION", "FILES", "SIZE", "DOWNLOADED"]
    assert lines[3].split()[:5] == ["demo", "1.0", "1", "2.00", "KB"]
    # FILES and SIZE are right-aligned, the other columns left-aligned
    assert lines[2].index("VERSION") == lines[3].index("1.0")
    assert lines[2].index("SIZE") + len("SIZE") == lines[3].index("KB") + len("KB")
    assert lines[4].endswith("restricted")
    assert "SERVER" not in out

    assert run_cli("cache", "list", "mimiciv", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["cache"] == str(cache)
    assert [(v["slug"], v["restricted"], v["files"]) for v in data["versions"]] == [("mimiciv", True, 1)]


def test_cli_list_shows_the_server_when_not_physionet_org(cache, capsys):
    add_version(cache, "demo", "1.0", {"a.csv": b"1"}, origin="http_localhost_8000")

    assert run_cli("cache", "list") == 0
    out = capsys.readouterr().out
    assert "SERVER" in out and "http_localhost_8000" in out


def test_cli_list_empty_and_dir(cache, tmp_path, capsys):
    assert run_cli("cache", "list") == 0
    assert capsys.readouterr().out == f"Cache: {cache} (empty)\n"
    assert run_cli("cache", "list", "demo") == 0
    assert capsys.readouterr().out == f"demo is not in the cache at {cache}.\n"
    assert run_cli("cache", "dir") == 0
    assert capsys.readouterr().out == f"{cache}\n"
    assert run_cli("cache", "dir", "--cache-dir", str(tmp_path / "other")) == 0
    assert capsys.readouterr().out == f"{tmp_path / 'other'}\n"
    assert run_cli("cache", "--cache-dir", str(tmp_path / "other")) == 0
    assert capsys.readouterr().out == f"Cache: {tmp_path / 'other'} (empty)\n"


def test_cli_clean_needs_yes_without_a_terminal(cache, capsys):
    folder = add_version(cache, "demo", "1.0", {"a.csv": b"12345"})

    with patch("sys.stdin.isatty", return_value=False):
        assert run_cli("cache", "clean") == 1
    assert "Pass --yes" in capsys.readouterr().err
    assert (folder / "a.csv").exists()

    assert run_cli("cache", "clean", "--yes") == 0
    assert capsys.readouterr().out == "Deleted 1 file(s), 5.00 B.\n  Removed demo 1.0\n"
    assert not folder.exists()


def test_cli_clean_asks_first(cache, capsys):
    folder = add_version(cache, "demo", "1.0", {"a.csv": b"12345"})

    with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="n") as ask:
        assert run_cli("cache", "clean", "demo") == 0
    assert ask.call_args.args[0] == f"Delete 1 file(s), 5.00 B from demo in {cache}? [y/N] "
    assert capsys.readouterr().out == "Nothing was deleted.\n"
    assert folder.exists()

    with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="y"):
        assert run_cli("cache", "clean", "demo", "--version", "1.0") == 0
    assert not folder.exists()


def test_cli_clean_dry_run_and_older_than(cache, capsys):
    add_version(cache, "demo", "1.0", {"a.csv": b"1"}, age_days=20)
    add_version(cache, "new", "1.0", {"a.csv": b"1"})

    assert run_cli("cache", "clean", "--older-than", "2w", "--dry-run") == 0
    assert capsys.readouterr().out == (
        f"Would delete 1 file(s), 1.00 B from the cache downloaded more than 2 week(s) ago in {cache}.\n"
        "  Would remove demo 1.0\n"
    )
    assert len(list_cache()) == 2

    assert run_cli("cache", "clean", "--older-than", "30", "--yes") == 0
    assert capsys.readouterr().out == "Nothing to delete from the cache downloaded more than 30 day(s) ago.\n"

    assert run_cli("cache", "clean", "--older-than", "12h", "-y") == 0
    assert [e.slug for e in list_cache()] == ["new"]


def test_cli_clean_version_needs_a_dataset(cache, capsys):
    assert run_cli("cache", "clean", "--version", "1.0", "--yes") == 1
    assert "--version needs a dataset" in capsys.readouterr().err


@pytest.mark.parametrize("age", ["soon", "-3d", "inf", "nan", "d", "3m"])
def test_cli_rejects_bad_ages(cache, age, capsys):
    with pytest.raises(SystemExit):
        run_cli("cache", "clean", f"--older-than={age}")
    assert "must be an age" in capsys.readouterr().err


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions, not as root")
def test_cli_clean_reports_errors(cache, capsys):
    folder = add_version(cache, "demo", "1.0", {"sub/a.csv": b"1"})
    (folder / "sub").chmod(0o500)
    try:
        assert run_cli("cache", "clean", "--yes") == 1
    finally:
        (folder / "sub").chmod(0o700)
    err = capsys.readouterr().err
    assert "1 path(s) could not be deleted:" in err
    assert str(Path(folder / "sub" / "a.csv")) in err
