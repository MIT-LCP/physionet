"""Tests for reading WFDB records and annotations through the cache."""

import hashlib
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
import requests_mock as rm

wfdb = pytest.importorskip("wfdb")

import physionet  # noqa: E402
from physionet.waveform import _to_seconds, load_annotation, load_record  # noqa: E402

BASE = "https://physionet.org"
FS = 100
SAMPLES = 1000  # 10 seconds per record or segment


def signal(offset=0):
    t = np.arange(SAMPLES) + offset
    return np.column_stack([np.sin(t / 10), np.cos(t / 10), t / SAMPLES])


def write_project(folder: Path):
    """Write a small WFDB project: a single-segment record with annotations, and a multi-segment one."""
    folder.mkdir(parents=True)
    names = ["I", "II", "RESP"]
    wfdb.wrsamp("rec", fs=FS, units=["mV", "mV", "V"], sig_name=names, p_signal=signal(),
                fmt=["16"] * 3, adc_gain=[1000] * 3, baseline=[0] * 3, write_dir=str(folder))
    wfdb.wrann("rec", "atr", sample=np.array([50, 250, 450, 650, 850]), symbol=["N", "N", "V", "N", "N"],
               fs=FS, write_dir=str(folder))
    wfdb.wrann("rec", "qrs", sample=np.array([60, 260]), symbol=["N", "N"], write_dir=str(folder))
    deep = folder / "p00" / "p001"
    deep.mkdir(parents=True)
    for i in range(3):
        wfdb.wrsamp(f"p001_{i}", fs=FS, units=["mV", "mV", "V"], sig_name=names, p_signal=signal(i * SAMPLES),
                    fmt=["16"] * 3, adc_gain=[1000] * 3, baseline=[0] * 3, write_dir=str(deep))
    (deep / "p001.hea").write_text(
        f"p001/3 3 {FS} {3 * SAMPLES}\n" + "".join(f"p001_{i} {SAMPLES}\n" for i in range(3))
    )
    return folder


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Serve the project from a mock PhysioNet; yield (mocker, requested file paths)."""
    monkeypatch.setenv("PHYSIONET_CACHE", str(tmp_path / "cache"))
    folder = write_project(tmp_path / "project")
    files = {path.relative_to(folder).as_posix(): path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    manifest = "".join(f"{hashlib.sha256(content).hexdigest()}  {name}\n" for name, content in sorted(files.items()))
    requested = []
    with rm.Mocker() as mocker:
        mocker.get(f"{BASE}/files/demo/1.0/SHA256SUMS.txt", text=manifest)
        for name, content in files.items():
            def respond(request, context, name=name, content=content):
                requested.append(name)
                return content
            mocker.get(f"{BASE}/files/demo/1.0/{name}", content=respond)
        mocker.get(f"{BASE}/api/v1/projects/demo/versions/", json=[
            {"slug": "demo", "title": "Demo", "version": "1.0", "abstract": "", "citation": ""},
        ])
        yield requested, folder


def read(name, **kwargs):
    return load_record("demo", name, "1.0", source="physionet", show_progress=False, **kwargs)


def test_load_record_reads_the_whole_record(project):
    requested, folder = project
    record = read("rec")

    expected = wfdb.rdrecord(str(folder / "rec"))
    assert isinstance(record, wfdb.Record)
    assert record.sig_name == ["I", "II", "RESP"]
    assert record.fs == FS
    np.testing.assert_array_equal(record.p_signal, expected.p_signal)
    assert sorted(requested) == ["rec.dat", "rec.hea"]
    assert load_record is physionet.load_record and load_annotation is physionet.load_annotation


def test_load_record_reads_a_time_range_and_channels(project):
    _, folder = project
    record = read("rec.hea", start="2s", end=timedelta(seconds=3.5), channels=["RESP", "I"])

    expected = wfdb.rdrecord(str(folder / "rec"), sampfrom=200, sampto=350, channels=[2, 0])
    assert record.sig_name == ["RESP", "I"]
    np.testing.assert_array_equal(record.p_signal, expected.p_signal)

    by_index = read("rec", start=2, end=3.5, channels=[2, 0])
    np.testing.assert_array_equal(by_index.p_signal, expected.p_signal)

    digital = read("rec", end="00:00:01", physical=False)
    assert digital.p_signal is None and digital.d_signal.shape == (100, 3)


def test_end_past_the_record_is_clamped(project):
    assert read("rec", start=9, end="1h").p_signal.shape == (100, 3)
    with pytest.raises(ValueError, match="at or after the end of rec, which is 0:00:10 long"):
        read("rec", start=10)
    with pytest.raises(ValueError, match="start must be before end"):
        read("rec", start=5, end=5)


def test_multi_segment_record_fetches_only_the_segments_it_needs(project):
    requested, folder = project
    record = read("p00/p001/p001", start=12, end=15)

    assert isinstance(record, wfdb.Record)
    np.testing.assert_allclose(record.p_signal[:, 2], (np.arange(1200, 1500)) / SAMPLES, atol=1e-3)
    assert sorted(requested) == ["p00/p001/p001.hea", "p00/p001/p001_1.dat", "p00/p001/p001_1.hea"]

    # Spanning two segments fetches the next one too; cached files are not fetched again
    read("p00/p001/p001", start=15, end=25)
    assert sorted(requested) == sorted([
        "p00/p001/p001.hea", "p00/p001/p001_1.dat", "p00/p001/p001_1.hea",
        "p00/p001/p001_2.dat", "p00/p001/p001_2.hea",
    ])


def test_cached_record_is_read_without_requests(project):
    requested, _ = project
    read("rec", end=1)
    count = len(requested)
    read("rec", start=5)
    assert len(requested) == count


def test_latest_version_is_used_when_none_is_given(project):
    record = load_record("demo", "rec", source="physionet", show_progress=False, end=1)
    assert record.p_signal.shape == (100, 3)


def test_missing_record_or_file(project):
    _, folder = project
    with pytest.raises(FileNotFoundError, match=r"nope.hea is not a file in demo 1.0. Did you mean: rec.hea\?"):
        read("nope")

    # A header that names a signal file the project does not have
    broken = (folder / "rec.hea").read_text().replace("rec.dat", "gone.dat")
    with rm.Mocker(real_http=False) as mocker:
        manifest = f"{hashlib.sha256(broken.encode()).hexdigest()}  broken.hea\n"
        mocker.get(f"{BASE}/files/other/1.0/SHA256SUMS.txt", text=manifest)
        mocker.get(f"{BASE}/files/other/1.0/broken.hea", text=broken)
        with pytest.raises(FileNotFoundError, match="needs gone.dat, which is not a file in other 1.0"):
            load_record("other", "broken", "1.0", source="physionet", show_progress=False)


@pytest.mark.parametrize("record", ["../rec", "a/../../rec", ""])
def test_record_names_must_stay_in_the_project(project, record):
    with pytest.raises(ValueError):
        read(record)


@pytest.mark.parametrize("channels", [[0, "I"], [-1], [True], [1.5]])
def test_channels_must_be_names_or_numbers(project, channels):
    with pytest.raises(ValueError, match="channels must be"):
        read("rec", channels=channels)


def test_load_annotation(project):
    requested, folder = project
    annotation = load_annotation("demo", "rec", version="1.0", source="physionet", show_progress=False)

    assert list(annotation.sample) == [50, 250, 450, 650, 850]
    assert annotation.symbol == ["N", "N", "V", "N", "N"]
    assert requested == ["rec.atr"]

    window = load_annotation("demo", "rec", version="1.0", source="physionet", show_progress=False,
                             start="2s", end="7s")
    assert list(window.sample) == [250, 450, 650]


def test_annotation_without_fs_uses_the_header(project):
    requested, _ = project
    window = load_annotation("demo", "rec", "qrs", "1.0", source="physionet", show_progress=False, start=2)

    assert list(window.sample) == [260]
    assert "rec.hea" in requested


def test_annotation_extension_is_checked(project):
    with pytest.raises(ValueError, match="not a valid annotation extension"):
        load_annotation("demo", "rec", "../x", "1.0")


@pytest.mark.parametrize(
    "value, seconds",
    [
        (300, 300), (1.5, 1.5), ("90", 90), ("90s", 90), ("5min", 300), ("5 min", 300), ("1.5h", 5400),
        ("250ms", 0.25), ("2:30", 150), ("01:02:03.5", 3723.5), (timedelta(minutes=2), 120), (0, 0),
    ],
)
def test_times(value, seconds):
    assert _to_seconds(value, "start") == pytest.approx(seconds)


@pytest.mark.parametrize("value", [-1, "soon", "5 days", "1:2:3:4", True, float("inf"), float("nan"), [1]])
def test_bad_times(value):
    with pytest.raises(ValueError, match="start must be a time"):
        _to_seconds(value, "start")


def test_without_wfdb(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_wfdb(name, *args, **kwargs):
        if name == "wfdb":
            raise ImportError("No module named 'wfdb'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_wfdb)
    with pytest.raises(ImportError, match=r'pip install "physionet\[wfdb\]"'):
        load_record("demo", "rec", "1.0")
