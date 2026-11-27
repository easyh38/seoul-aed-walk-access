import csv

import pytest

from pipeline.plot_metrics import load_metrics


COLUMNS = [
    "scope",
    "mode",
    "straight_pct",
    "walk_pct",
    "diff_pp",
]


def valid_rows():
    return [
        {
            "scope": "서울",
            "mode": "oneway",
            "straight_pct": "78.61",
            "walk_pct": "64.52",
            "diff_pp": "14.09",
        },
        {
            "scope": "서울",
            "mode": "roundtrip",
            "straight_pct": "54.52",
            "walk_pct": "41.65",
            "diff_pp": "12.87",
        },
        {
            "scope": "종로구",
            "mode": "oneway",
            "straight_pct": "70.0",
            "walk_pct": "60.0",
            "diff_pp": "10.0",
        },
        {
            "scope": "종로구",
            "mode": "roundtrip",
            "straight_pct": "50.0",
            "walk_pct": "45.0",
            "diff_pp": "5.0",
        },
    ]


def write_csv(path, rows, columns=COLUMNS):
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def test_loads_valid_metrics(tmp_path):
    path = tmp_path / "metrics.csv"
    write_csv(path, valid_rows())

    metrics = load_metrics(path)

    assert len(metrics) == 4
    assert metrics[("서울", "oneway")]["walk_pct"] == 64.52
    assert metrics[("종로구", "roundtrip")]["diff_pp"] == 5.0


def test_accepts_negative_diff_pp(tmp_path):
    path = tmp_path / "metrics.csv"
    rows = valid_rows()
    rows[0]["straight_pct"] = "40"
    rows[0]["walk_pct"] = "50"
    rows[0]["diff_pp"] = "-10"
    write_csv(path, rows)

    metrics = load_metrics(path)

    assert metrics[("서울", "oneway")]["diff_pp"] == -10.0


def test_missing_file_raises_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_metrics(tmp_path / "missing.csv")


def test_missing_required_column_raises_error(tmp_path):
    path = tmp_path / "metrics.csv"
    columns = ["scope", "mode", "straight_pct", "walk_pct"]
    write_csv(path, valid_rows(), columns)

    with pytest.raises(ValueError, match="필수 컬럼"):
        load_metrics(path)


def test_empty_csv_raises_error(tmp_path):
    path = tmp_path / "metrics.csv"
    write_csv(path, [])

    with pytest.raises(ValueError, match="데이터가 없습니다"):
        load_metrics(path)


def test_non_numeric_metric_raises_error(tmp_path):
    path = tmp_path / "metrics.csv"
    rows = valid_rows()
    rows[0]["walk_pct"] = "invalid"
    write_csv(path, rows)

    with pytest.raises(ValueError, match="숫자가 아닙니다"):
        load_metrics(path)


def test_invalid_mode_raises_error(tmp_path):
    path = tmp_path / "metrics.csv"
    rows = valid_rows()
    rows[0]["mode"] = "unknown"
    write_csv(path, rows)

    with pytest.raises(ValueError, match="잘못된 mode"):
        load_metrics(path)


def test_duplicate_scope_mode_raises_error(tmp_path):
    path = tmp_path / "metrics.csv"
    rows = valid_rows()
    rows.append(rows[0].copy())
    write_csv(path, rows)

    with pytest.raises(ValueError, match="중복 데이터"):
        load_metrics(path)


def test_empty_scope_raises_error(tmp_path):
    path = tmp_path / "metrics.csv"
    rows = valid_rows()
    rows[0]["scope"] = ""
    write_csv(path, rows)

    with pytest.raises(ValueError, match="scope가 비어"):
        load_metrics(path)


def test_missing_seoul_data_raises_error(tmp_path):
    path = tmp_path / "metrics.csv"
    rows = [row for row in valid_rows() if row["scope"] != "서울"]
    write_csv(path, rows)

    with pytest.raises(ValueError, match="서울 전체"):
        load_metrics(path)


def test_missing_roundtrip_data_raises_error(tmp_path):
    path = tmp_path / "metrics.csv"
    rows = [
        row for row in valid_rows()
        if not (row["scope"] == "종로구" and row["mode"] == "roundtrip")
    ]
    write_csv(path, rows)

    with pytest.raises(ValueError, match="종로구의 roundtrip"):
        load_metrics(path)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("straight_pct", "-0.1"),
        ("straight_pct", "100.1"),
        ("walk_pct", "-0.1"),
        ("walk_pct", "100.1"),
        ("diff_pp", "-100.1"),
        ("diff_pp", "100.1"),
    ],
)
def test_metric_out_of_range_raises_error(tmp_path, column, value):
    path = tmp_path / "metrics.csv"
    rows = valid_rows()
    rows[0][column] = value
    write_csv(path, rows)

    with pytest.raises(ValueError, match="범위 오류"):
        load_metrics(path)
