"""분석 파이프라인을 순서대로 실행한다 (설계 3-3, 3-5).

    python pipeline/run_all.py --list                 # 단계 목록
    python pipeline/run_all.py                        # 전체 실행 (01 수집부터)
    python pipeline/run_all.py --from district        # 이미 받은 데이터로 03부터
    python pipeline/run_all.py --from grid --to metrics
    python pipeline/run_all.py --isochrone-limit 20   # 등시선을 AED 20개만 (빠른 확인용)

    # 발표용 시각별 비교 (평일 낮 vs 밤): 기본 단계 뒤에 시각마다 운영시간 분석을 추가 실행
    python pipeline/run_all.py --from metrics \\
        --availability-at 2026-11-24T14:00 --availability-at 2026-11-24T22:00

각 단계는 별도 프로세스로 실행하고, 하나라도 실패하면 거기서 멈춘다.
DB에 쓰는 단계는 --apply로 실행한다 (단계별 미리보기는 스크립트를 직접 실행).
AED는 --prune으로 이번 CSV에 없는 AED를 정리하고, 격자·판정은 --replace로 매번 다시 만들어
같은 입력이면 몇 번을 돌려도 같은 결과가 나온다.
운영시간 분석(analyze_availability → 08)은 DB를 바꾸지 않고 results/에 CSV만 남긴다.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PIPELINE_DIR.parent

# (이름, 스크립트, 기본 인자, 설명)
STEPS = [
    ("collect", "01_collect.py", [], "AED API 전체 수집 → data/raw/"),
    ("clean", "02_clean.py", [], "정제·운영시간 해석 → data/clean/aed.csv"),
    ("district", "03_load_district.py", ["--apply"], "자치구 경계 → district"),
    ("aed", "03_load_aed.py", ["--apply", "--prune"], "정제 AED → aed (CSV에 없는 AED 삭제)"),
    ("isochrone", "04_isochrone.py", ["--all"], "Valhalla 2·4분 등시선 → results/isochrones/"),
    ("load-isochrones", "04_load_isochrones.py", ["--apply"], "등시선 → isochrone"),
    ("grid", "05_generate_grid.py", ["--apply", "--replace"], "격자 다시 생성 → grid_point"),
    ("accessibility", "06_analyze_accessibility.py", ["--apply", "--replace"], "직선/실보행 판정 → grid_point"),
    ("metrics", "07_build_metrics.py", ["--apply"], "편도·왕복 지표·레이어 → metrics, layer, results/metrics.csv"),
]
STEP_NAMES = [s[0] for s in STEPS]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="분석 파이프라인 전체 실행",
        epilog="단계: " + ", ".join(STEP_NAMES),
    )
    parser.add_argument("--list", action="store_true", help="단계 목록만 출력")
    parser.add_argument("--from", dest="start", choices=STEP_NAMES, default=STEP_NAMES[0],
                        help="이 단계부터 실행 (기본: collect)")
    parser.add_argument("--to", dest="end", choices=STEP_NAMES, default=STEP_NAMES[-1],
                        help="이 단계까지 실행 (기본: metrics)")
    parser.add_argument("--isochrone-limit", type=int, default=None, metavar="N",
                        help="isochrone 단계에서 AED N개만 처리 (기본: 전체)")
    parser.add_argument("--availability-at", action="append", default=[], metavar="DATETIME",
                        help="기본 단계 뒤에 이 시각의 운영시간 반영 분석을 실행 "
                             "(예: 2026-11-24T14:00, 여러 번 지정 가능)")
    args = parser.parse_args()

    if STEP_NAMES.index(args.start) > STEP_NAMES.index(args.end):
        parser.error(f"--from {args.start}이(가) --to {args.end}보다 뒤에 있습니다.")
    if args.isochrone_limit is not None and args.isochrone_limit <= 0:
        parser.error("--isochrone-limit은 1 이상이어야 합니다.")
    return args


def print_steps() -> None:
    for i, (name, script, extra, desc) in enumerate(STEPS, start=1):
        print(f"{i:>2}. {name:<16} {script:<30} {' '.join(extra):<18} {desc}")
    print("    (--availability-at 지정 시) analyze_availability → 08_analyze_available_accessibility")


def run_step(name: str, desc: str, cmd: list[str], durations: list) -> bool:
    """한 단계를 별도 프로세스로 실행한다. 성공하면 True."""
    print(f"\n{'=' * 60}\n▶ {name}: {desc}\n  $ {' '.join(cmd[1:])}\n{'=' * 60}", flush=True)

    step_start = time.perf_counter()
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    elapsed = time.perf_counter() - step_start
    durations.append((name, elapsed))

    if result.returncode != 0:
        print(f"\n✗ {name} 실패 (종료 코드 {result.returncode}, {elapsed:.1f}초). 여기서 멈춥니다.")
        return False
    return True


def main() -> int:
    args = parse_args()
    if args.list:
        print_steps()
        return 0

    selected = STEPS[STEP_NAMES.index(args.start): STEP_NAMES.index(args.end) + 1]
    durations = []
    total_start = time.perf_counter()

    for name, script, extra, desc in selected:
        if name == "isochrone" and args.isochrone_limit is not None:
            extra = ["--limit", str(args.isochrone_limit)]

        cmd = [sys.executable, str(PIPELINE_DIR / script), *extra]
        if not run_step(name, desc, cmd, durations):
            print(f"  고친 뒤 이어서: python pipeline/run_all.py --from {name}")
            return 1

    # 운영시간 반영 분석: 시각마다 운영 여부 계산 → 접근성 계산 (DB 변경 없음, CSV만)
    for at in args.availability_at:
        # analyze_availability는 `from pipeline...`으로 import하므로 모듈로 실행한다
        steps = [
            (f"availability@{at}", "시각별 AED 운영 여부 → results/availability/",
             [sys.executable, "-m", "pipeline.analyze_availability", "--datetime", at]),
            (f"available-access@{at}", "운영 중 AED 기준 접근성 → results/availability_metrics_*.csv",
             [sys.executable, str(PIPELINE_DIR / "08_analyze_available_accessibility.py"),
              "--at", at, "--apply"]),
        ]
        for name, desc, cmd in steps:
            if not run_step(name, desc, cmd, durations):
                return 1

    total = time.perf_counter() - total_start
    print(f"\n{'=' * 60}\n✓ 완료 ({len(durations)}단계, 총 {total:.1f}초)")
    for name, elapsed in durations:
        print(f"  {name:<28} {elapsed:>8.1f}초")
    return 0


if __name__ == "__main__":
    sys.exit(main())
