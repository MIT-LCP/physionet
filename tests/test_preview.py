"""Tests for previewing tables without downloading them (head, tables)."""

import gzip
import hashlib
import io
import json
import sys
from unittest.mock import patch

import pytest
import requests_mock

import physionet.preview as preview_module
from physionet.api.exceptions import ForbiddenError
from physionet.cli import main
from physionet.preview import head, tables

BASE = "https://physionet.org"
API = f"{BASE}/api/v1"
FILES = f"{BASE}/files/demo/1.0"
VERSIONS = [{"slug": "demo", "title": "Demo", "version": "1.0", "abstract": "", "citation": ""}]


def sha(body):
    return hashlib.sha256(body).hexdigest()


def big_csv(rows=20000):
    lines = ["id,name,value"] + [f"{i},row {i},{i * 3}" for i in range(rows)]
    return ("\n".join(lines) + "\n").encode()


class CountingBody(io.BytesIO):
    """A response body that records how many bytes were read from it."""

    def __init__(self, data):
        super().__init__(data)
        self.bytes_read = 0

    def read(self, size=-1):
        chunk = super().read(size)
        self.bytes_read += len(chunk)
        return chunk


def mock_project(mock, files):
    """files: {path: bytes}. Registers the versions list and checksum list."""
    mock.get(f"{API}/projects/demo/versions/", json=VERSIONS)
    mock.get(f"{FILES}/SHA256SUMS.txt", text="".join(f"{sha(body)}  {path}\n" for path, body in files.items()))


def options(tmp_path):
    return {"version": "1.0", "cache_dir": str(tmp_path / "cache"), "source": "physionet"}


def test_head_reads_only_the_start_of_a_gzipped_table(tmp_path):
    data = gzip.compress(big_csv(), mtime=0)
    body = CountingBody(data)
    with requests_mock.Mocker() as mock:
        mock_project(mock, {"hosp/big.csv.gz": data})
        mock.get(f"{FILES}/hosp/big.csv.gz", body=body)
        frame = head("demo", "hosp/big", n=5, **options(tmp_path))

    assert list(frame.columns) == ["id", "name", "value"]
    assert frame["id"].tolist() == [0, 1, 2, 3, 4]
    assert body.bytes_read < len(data)  # stopped before the end of the file
    assert not (tmp_path / "cache" / "physionet.org" / "demo" / "1.0" / "hosp").exists()  # nothing cached


def test_head_keeps_quoted_fields_with_line_breaks_across_chunks(tmp_path):
    note = "line one\nline two, with a comma\n" * 3000  # about 100 KB, longer than a read chunk
    rows = "".join(f'{i},"{note}{i}"\n' for i in range(4))
    data = f"id,text\n{rows}".encode()
    with requests_mock.Mocker() as mock:
        mock_project(mock, {"notes.csv": data})
        mock.get(f"{FILES}/notes.csv", content=data)
        frame = head("demo", "notes.csv", n=3, **options(tmp_path))

    assert frame["id"].tolist() == [0, 1, 2]
    assert frame["text"].iloc[2] == note + "2"


def test_head_handles_tsv_short_tables_and_empty_files(tmp_path):
    files = {"a.tsv": b"x\ty\n1\t2\n", "empty.csv": b"", "header_only.csv": b"p,q\n"}
    with requests_mock.Mocker() as mock:
        mock_project(mock, files)
        for path, body in files.items():
            mock.get(f"{FILES}/{path}", content=body)
        tsv = head("demo", "a", n=10, **options(tmp_path))
        empty = head("demo", "empty.csv", **options(tmp_path))
        header_only = head("demo", "header_only", **options(tmp_path))

    assert tsv.to_dict("list") == {"x": [1], "y": [2]}
    assert empty.empty and list(empty.columns) == []
    assert header_only.empty and list(header_only.columns) == ["p", "q"]


@pytest.mark.parametrize("name, stored, encoded", [
    ("t.csv.gz", gzip.compress(b"a,b\n1,2\n", mtime=0), False),
    ("t.csv.gz", gzip.compress(b"a,b\n1,2\n", mtime=0), True),   # server also says Content-Encoding: gzip
    ("t.csv", b"a,b\n1,2\n", True),                              # server compresses a plain file in transit
    ("t.csv.gz", gzip.compress(b"a,b\n", mtime=0) + gzip.compress(b"1,2\n", mtime=0), False),  # two gzip members
])
def test_head_decompresses_gzip_however_it_is_served(tmp_path, name, stored, encoded):
    sent = gzip.compress(stored, mtime=0) if (encoded and not name.endswith(".gz")) else stored
    headers = {"Content-Encoding": "gzip"} if encoded else {}
    with requests_mock.Mocker() as mock:
        mock_project(mock, {name: stored})
        mock.get(f"{FILES}/{name}", content=sent, headers=headers)
        frame = head("demo", name, **options(tmp_path))
    assert frame.to_dict("list") == {"a": [1], "b": [2]}


def test_head_reads_a_cached_file_without_a_request(tmp_path):
    data = gzip.compress(b"a,b\n5,6\n", mtime=0)
    cached = tmp_path / "cache" / "physionet.org" / "demo" / "1.0" / "t.csv.gz"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(data)
    with requests_mock.Mocker() as mock:
        mock_project(mock, {"t.csv.gz": data})
        frame = head("demo", "t", **options(tmp_path))
    assert frame.to_dict("list") == {"a": [5], "b": [6]}
    assert not any(request.url.endswith("t.csv.gz") for request in mock.request_history)


def test_head_errors(tmp_path):
    files = {"data.parquet": b"PAR1", "hosp/patients.csv.gz": b"x", "secret.csv": b"x"}
    with requests_mock.Mocker() as mock:
        mock_project(mock, files)
        mock.get(f"{FILES}/secret.csv", status_code=403)
        with pytest.raises(ValueError, match=r"physionet.load\('demo', 'data.parquet'\)"):
            head("demo", "data", **options(tmp_path))
        with pytest.raises(FileNotFoundError, match="Did you mean: hosp/patients.csv.gz"):
            head("demo", "hosp/patient.csv.gz", **options(tmp_path))
        with pytest.raises(ForbiddenError):
            head("demo", "secret.csv", **options(tmp_path))
        for bad in (0, -1, 2.5, True):
            with pytest.raises(ValueError, match="n must be"):
                head("demo", "secret.csv", n=bad, **options(tmp_path))


TABLE_FILES = {
    "hosp/patients.csv.gz": gzip.compress(b"subject_id,gender\n1,F\n", mtime=0),
    "hosp/admissions.csv": b"subject_id,hadm_id,admittime\n1,2,x\n",
    "icu/icustays.tsv": b"subject_id\tstay_id\n1\t3\n",
    "icu/features.parquet": b"PAR1",
    "README.md": b"# Demo",
    "icu/locked.csv": b"x",
}


def mock_tables(mock):
    mock_project(mock, TABLE_FILES)
    for path, body in TABLE_FILES.items():
        mock.get(f"{FILES}/{path}", content=body)
    mock.get(f"{FILES}/icu/locked.csv", status_code=403)


def test_tables_lists_columns_and_filters(tmp_path):
    with requests_mock.Mocker() as mock:
        mock_tables(mock)
        found = tables("demo", **options(tmp_path))
        by_column = tables("demo", column="SUBJECT_ID", **options(tmp_path))
        icu = tables("demo", prefix="icu/", **options(tmp_path))

    rows = {row.table: row for row in found.itertuples(index=False)}
    assert sorted(rows) == ["hosp/admissions.csv", "hosp/patients.csv.gz", "icu/features.parquet",
                            "icu/icustays.tsv", "icu/locked.csv"]  # README.md is not a table
    assert rows["hosp/patients.csv.gz"].columns == ["subject_id", "gender"]
    assert rows["icu/icustays.tsv"].columns == ["subject_id", "stay_id"]
    assert rows["icu/features.parquet"].columns is None and rows["icu/features.parquet"].error is None
    assert rows["icu/locked.csv"].columns is None and "denied" in rows["icu/locked.csv"].error
    assert list(by_column["table"]) == ["hosp/admissions.csv", "hosp/patients.csv.gz", "icu/icustays.tsv"]
    assert list(icu["table"]) == ["icu/features.parquet", "icu/icustays.tsv", "icu/locked.csv"]


def test_tables_refuses_very_many_tables_without_a_prefix(tmp_path):
    with requests_mock.Mocker() as mock, patch.object(preview_module, "MAX_TABLES", 2):
        mock_tables(mock)
        with pytest.raises(ValueError, match="Pass a prefix"):
            tables("demo", **options(tmp_path))
        assert len(tables("demo", prefix="hosp", **options(tmp_path))) == 2


def run_cli(*args):
    with patch.object(sys, "argv", ["physionet", *args]):
        return main()


def test_cli_head_prints_a_table_or_csv(tmp_path, capsys):
    with requests_mock.Mocker() as mock:
        mock_tables(mock)
        common = ["--version", "1.0", "--source", "physionet"]
        with patch.dict("os.environ", {"PHYSIONET_CACHE": str(tmp_path / "cache")}):
            assert run_cli("head", "demo", "hosp/patients", *common) == 0
            table = capsys.readouterr().out
            assert run_cli("head", "demo", "hosp/patients", "--csv", *common) == 0
            csv = capsys.readouterr().out
            assert run_cli("head", "demo", "icu/locked.csv", *common) == 1
            denied = capsys.readouterr().err
            assert run_cli("head", "demo", "nope", *common) == 1
            missing = capsys.readouterr().err
    assert "subject_id gender" in table and "F" in table
    assert csv == "subject_id,gender\n1,F\n"
    assert "Access denied" in denied and "/content/demo/" in denied
    assert missing.startswith("Not found:")


def test_cli_head_rejects_a_bad_row_count(capsys):
    with pytest.raises(SystemExit) as caught:
        run_cli("head", "demo", "t", "-n", "0")
    assert caught.value.code == 2
    assert "at least 1" in capsys.readouterr().err


def test_cli_tables_text_json_and_no_match(tmp_path, capsys):
    with requests_mock.Mocker() as mock, patch.dict("os.environ", {"PHYSIONET_CACHE": str(tmp_path / "cache")}):
        mock_tables(mock)
        common = ["--version", "1.0", "--source", "physionet"]
        assert run_cli("tables", "demo", "--column", "stay_id", *common) == 0
        text = capsys.readouterr().out
        assert run_cli("tables", "demo", "--prefix", "hosp", "--json", *common) == 0
        records = json.loads(capsys.readouterr().out)
        assert run_cli("tables", "demo", "--column", "nope", *common) == 1
        none = capsys.readouterr().err
        assert run_cli("tables", "demo", *common) == 0
        everything = capsys.readouterr().out

    assert text.split() == ["icu/icustays.tsv", "subject_id,", "stay_id"]
    assert [record["table"] for record in records] == ["hosp/admissions.csv", "hosp/patients.csv.gz"]
    assert records[1]["columns"] == ["subject_id", "gender"]
    assert "No tables in demo have a column named nope." in none
    assert "(Parquet or Excel; use physionet.load() to read it)" in everything
    assert "(could not read the header:" in everything
