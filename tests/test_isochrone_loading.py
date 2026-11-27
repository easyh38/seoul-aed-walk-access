import csv
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = ROOT / "pipeline"

if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

MODULE_PATH = PIPELINE_DIR / "04_load_isochrones.py"
SPEC = importlib.util.spec_from_file_location(
    "pipeline_04_load_isochrones_test",
    MODULE_PATH,
)

if SPEC is None or SPEC.loader is None:
    raise ImportError(f"모듈을 불러올 수 없습니다: {MODULE_PATH}")

isochrone_loader = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = isochrone_loader
SPEC.loader.exec_module(isochrone_loader)


class TestLoadIsochrones(unittest.TestCase):

    def test_empty_success_file_is_allowed(self):
        """모든 AED의 생성 실패 상황에서 빈 JSONL을 허용한다."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "empty.jsonl"
            input_path.write_text("", encoding="utf-8")

            with patch.object(
                isochrone_loader,
                "INPUT_PATH",
                input_path,
            ):
                rows, source_ids = isochrone_loader.load_isochrones()

        self.assertEqual(rows, [])
        self.assertEqual(source_ids, set())


class TestLoadFailures(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.csv_path = Path(self.temp_dir.name) / "failures.csv"

    def write_csv(self, rows, fieldnames=None):
        if fieldnames is None:
            fieldnames = ["serialSeq", "attempts", "error"]

        with self.csv_path.open(
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        return self.csv_path

    def test_valid_failure_csv(self):
        self.write_csv([
            {
                "serialSeq": "AED-001",
                "attempts": "3",
                "error": "Valhalla timeout",
            },
            {
                "serialSeq": "AED-002",
                "attempts": "2",
                "error": "No pedestrian route",
            },
        ])

        failures = isochrone_loader.load_isochrone_failures(self.csv_path)

        self.assertEqual(set(failures), {"AED-001", "AED-002"})
        self.assertEqual(failures["AED-001"]["attempts"], "3")

    def test_header_only_csv_is_valid(self):
        self.write_csv([])

        failures = isochrone_loader.load_isochrone_failures(self.csv_path)

        self.assertEqual(failures, {})

    def test_missing_file_raises_error(self):
        missing = Path(self.temp_dir.name) / "missing.csv"

        with self.assertRaises(FileNotFoundError):
            isochrone_loader.load_isochrone_failures(missing)

    def test_missing_required_column_raises_error(self):
        self.write_csv(
            [{"serialSeq": "AED-001", "attempts": "2"}],
            fieldnames=["serialSeq", "attempts"],
        )

        with self.assertRaises(ValueError):
            isochrone_loader.load_isochrone_failures(self.csv_path)

    def test_empty_serial_raises_error(self):
        self.write_csv([
            {
                "serialSeq": "",
                "attempts": "2",
                "error": "timeout",
            }
        ])

        with self.assertRaises(ValueError):
            isochrone_loader.load_isochrone_failures(self.csv_path)

    def test_duplicate_serial_raises_error(self):
        row = {
            "serialSeq": "AED-001",
            "attempts": "2",
            "error": "timeout",
        }
        self.write_csv([row, row])

        with self.assertRaises(ValueError):
            isochrone_loader.load_isochrone_failures(self.csv_path)

    def test_non_integer_attempts_raises_error(self):
        self.write_csv([
            {
                "serialSeq": "AED-001",
                "attempts": "abc",
                "error": "timeout",
            }
        ])

        with self.assertRaises(ValueError):
            isochrone_loader.load_isochrone_failures(self.csv_path)

    def test_zero_attempts_raises_error(self):
        self.write_csv([
            {
                "serialSeq": "AED-001",
                "attempts": "0",
                "error": "timeout",
            }
        ])

        with self.assertRaises(ValueError):
            isochrone_loader.load_isochrone_failures(self.csv_path)

    def test_empty_error_raises_error(self):
        self.write_csv([
            {
                "serialSeq": "AED-001",
                "attempts": "2",
                "error": " ",
            }
        ])

        with self.assertRaises(ValueError):
            isochrone_loader.load_isochrone_failures(self.csv_path)


class TestValidateAedStatus(unittest.TestCase):

    def test_exact_success_failure_partition(self):
        isochrone_loader.validate_aed_status(
            db_ids={"A", "B", "C"},
            success_ids={"A", "B"},
            failed_ids={"C"},
        )

    def test_all_aeds_failed_is_valid(self):
        """성공 AED가 0개여도 전체 실패 목록이 일치하면 유효하다."""
        isochrone_loader.validate_aed_status(
            db_ids={"A", "B"},
            success_ids=set(),
            failed_ids={"A", "B"},
        )

    def test_success_failure_overlap_raises_error(self):
        with self.assertRaises(RuntimeError):
            isochrone_loader.validate_aed_status(
                db_ids={"A"},
                success_ids={"A"},
                failed_ids={"A"},
            )

    def test_unclassified_aed_raises_error(self):
        with self.assertRaises(RuntimeError):
            isochrone_loader.validate_aed_status(
                db_ids={"A", "B"},
                success_ids={"A"},
                failed_ids=set(),
            )

    def test_unknown_aed_raises_error(self):
        with self.assertRaises(RuntimeError):
            isochrone_loader.validate_aed_status(
                db_ids={"A"},
                success_ids={"A", "UNKNOWN"},
                failed_ids=set(),
            )


if __name__ == "__main__":
    unittest.main()
