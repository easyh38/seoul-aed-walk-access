"""기존 metrics.csv를 이용해 접근성 분석 그래프 3개를 생성한다.

실행:
    python pipeline/plot_metrics.py

DB에 접속하거나 분석 데이터를 재계산하지 않는다.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib import font_manager


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "results" / "metrics.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "results" / "graphs"

REQUIRED_COLUMNS = {
    "scope",
    "mode",
    "straight_pct",
    "walk_pct",
    "diff_pp",
}

SEOUL_SCOPE = "서울"

COLORS = {
    "walk": "#2878B5",
    "straight": "#E69F00",
    "oneway": "#2878B5",
    "roundtrip": "#D97757",
    "connector": "#AAB4C0",
    "reference": "#64748B",
}


def load_metrics(path: Path) -> dict:
    """CSV를 읽고 그래프에 필요한 데이터 구조를 검증한다."""

    if not path.is_file():
        raise FileNotFoundError(f"분석 CSV가 없습니다: {path}")

    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV 필수 컬럼이 없습니다: {sorted(missing)}")

        rows = list(reader)

    if not rows:
        raise ValueError("분석 CSV에 데이터가 없습니다.")

    metrics = {}

    for line_number, row in enumerate(rows, start=2):
        scope = row["scope"].strip()
        mode = row["mode"].strip()

        if not scope:
            raise ValueError(f"{line_number}행: scope가 비어 있습니다.")

        if mode not in {"oneway", "roundtrip"}:
            raise ValueError(f"{line_number}행: 잘못된 mode입니다: {mode}")

        key = (scope, mode)

        if key in metrics:
            raise ValueError(f"중복 데이터입니다: {key}")

        try:
            values = {
                "straight_pct": float(row["straight_pct"]),
                "walk_pct": float(row["walk_pct"]),
                "diff_pp": float(row["diff_pp"]),
            }
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{line_number}행: 지표가 숫자가 아닙니다.") from exc

        for name, value in values.items():
            lower, upper = (-100, 100) if name == "diff_pp" else (0, 100)

            if not lower <= value <= upper:
                raise ValueError(
                    f"{line_number}행: {name} 범위 오류 "
                    f"(허용: {lower}~{upper}, 입력: {value})"
                )

        metrics[key] = {
            "scope": scope,
            "mode": mode,
            **values,
        }

    scopes = {scope for scope, _ in metrics}
    if SEOUL_SCOPE not in scopes:
        raise ValueError("서울 전체(scope=서울) 데이터가 없습니다.")

    for scope in scopes:
        for mode in ("oneway", "roundtrip"):
            if (scope, mode) not in metrics:
                raise ValueError(f"{scope}의 {mode} 데이터가 없습니다.")

    return metrics


# (일반, 굵게) 한글 글꼴 후보. 앞에서부터 처음 찾은 것을 쓴다.
# 1순위 NanumGothic(Ubuntu fonts-nanum)은 커밋된 그래프를 만든 글꼴이다.
KOREAN_FONT_CANDIDATES = (
    (
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
    ),
    (  # WSL에서 Windows 맑은 고딕
        Path("/mnt/c/Windows/Fonts/malgun.ttf"),
        Path("/mnt/c/Windows/Fonts/malgunbd.ttf"),
    ),
    (  # Windows
        Path("C:/Windows/Fonts/malgun.ttf"),
        Path("C:/Windows/Fonts/malgunbd.ttf"),
    ),
    (  # macOS
        Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
        Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
    ),
)


def find_korean_font() -> tuple[Path, Path]:
    """사용 가능한 한글 글꼴 (일반, 굵게) 파일 경로를 찾는다."""

    for regular, bold in KOREAN_FONT_CANDIDATES:
        if regular.is_file() and bold.is_file():
            return regular, bold

    tried = "\n".join(f"  - {regular}" for regular, _ in KOREAN_FONT_CANDIDATES)
    raise FileNotFoundError(
        "한글 글꼴 파일을 찾지 못했습니다. 찾아본 경로:\n"
        f"{tried}\n"
        "Ubuntu에서는 `sudo apt install fonts-nanum`으로 설치하세요."
    )


def configure_korean_font() -> None:
    """찾은 한글 글꼴 파일을 Matplotlib에 직접 등록한다."""

    font_path, bold_font_path = find_korean_font()

    for font_file in {font_path, bold_font_path}:
        font_manager.fontManager.addfont(str(font_file))

    font_name = font_manager.FontProperties(
        fname=str(font_path)
    ).get_name()

    plt.rcParams["font.family"] = font_name
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 120
    plt.rcParams["savefig.dpi"] = 180


def save_district_walk_chart(metrics: dict, output_dir: Path) -> Path:
    """편도 4분 기준 자치구별 실보행 접근률."""

    rows = [
        value
        for (scope, mode), value in metrics.items()
        if scope != SEOUL_SCOPE and mode == "oneway"
    ]
    rows.sort(key=lambda row: row["walk_pct"], reverse=True)

    seoul = metrics[(SEOUL_SCOPE, "oneway")]

    fig, ax = plt.subplots(figsize=(12, 10))

    labels = [row["scope"] for row in rows]
    values = [row["walk_pct"] for row in rows]
    positions = range(len(rows))

    bars = ax.barh(
        positions,
        values,
        color=COLORS["walk"],
        height=0.68,
    )

    ax.set_yticks(list(positions), labels)
    ax.invert_yaxis()
    ax.set_xlim(0, 105)
    ax.set_xlabel("실보행 도달 가능 비율 (%)")
    ax.set_title(
        "자치구별 실보행 도달 가능 비율",
        loc="left",
        fontsize=17,
        fontweight=600,
        pad=18,
    )

    ax.axvline(
        seoul["walk_pct"],
        color=COLORS["reference"],
        linestyle="--",
        linewidth=1.5,
        label=f"서울 전체 {seoul['walk_pct']:.2f}%",
    )

    ax.bar_label(
        bars,
        labels=[f"{value:.1f}%" for value in values],
        padding=4,
        fontsize=8,
    )

    ax.grid(axis="x", alpha=0.2)
    ax.set_axisbelow(True)
    ax.legend(loc="lower right")
    fig.tight_layout()

    output = output_dir / "01_district_walk_coverage.png"
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)

    return output


def save_straight_vs_walk_chart(metrics: dict, output_dir: Path) -> Path:
    """편도 4분 기준 직선거리와 실보행 접근률 비교."""

    rows = [
        value
        for (scope, mode), value in metrics.items()
        if scope != SEOUL_SCOPE and mode == "oneway"
    ]
    rows.sort(key=lambda row: row["walk_pct"], reverse=True)

    seoul = metrics[(SEOUL_SCOPE, "oneway")]

    fig, ax = plt.subplots(figsize=(12, 10))

    positions = list(range(len(rows)))

    for position, row in zip(positions, rows):
        straight = row["straight_pct"]
        walk = row["walk_pct"]

        ax.plot(
            [walk, straight],
            [position, position],
            color=COLORS["connector"],
            linewidth=2,
            zorder=1,
        )

    walk_points = [row["walk_pct"] for row in rows]
    straight_points = [row["straight_pct"] for row in rows]

    ax.scatter(
        walk_points,
        positions,
        color=COLORS["walk"],
        label="실보행",
        s=42,
        zorder=3,
    )
    ax.scatter(
        straight_points,
        positions,
        color=COLORS["straight"],
        label="직선거리",
        s=42,
        zorder=3,
    )

    ax.set_yticks(positions, [row["scope"] for row in rows])
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("도달 가능 비율 (%)")
    ax.set_title(
        "자치구별 직선거리와 실보행 접근성 비교",
        loc="left",
        fontsize=17,
        fontweight=600,
        pad=18,
    )

    ax.text(
        0,
        1.015,
        (
            f"서울 전체: 직선거리 {seoul['straight_pct']:.2f}%  "
            f"/ 실보행 {seoul['walk_pct']:.2f}%  "
            f"/ 차이 {seoul['diff_pp']:.2f}%p"
        ),
        transform=ax.transAxes,
        fontsize=10,
        color="#475569",
    )

    ax.grid(axis="x", alpha=0.2)
    ax.set_axisbelow(True)
    ax.legend(loc="lower right")
    fig.tight_layout()

    output = output_dir / "02_district_straight_vs_walk.png"
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)

    return output


def save_oneway_roundtrip_chart(metrics: dict, output_dir: Path) -> Path:
    """편도 4분과 왕복 기준(편도 2분) 실보행 접근률 비교."""

    districts = [
        value
        for (scope, mode), value in metrics.items()
        if scope != SEOUL_SCOPE and mode == "oneway"
    ]
    districts.sort(key=lambda row: row["walk_pct"], reverse=True)

    rows = [metrics[(SEOUL_SCOPE, "oneway")]] + districts
    roundtrip_rows = [
        metrics[(row["scope"], "roundtrip")]
        for row in rows
    ]

    fig, ax = plt.subplots(figsize=(12, 11))

    positions = list(range(len(rows)))
    bar_height = 0.34

    oneway_values = [row["walk_pct"] for row in rows]
    roundtrip_values = [row["walk_pct"] for row in roundtrip_rows]

    oneway_bars = ax.barh(
        [p - bar_height / 2 for p in positions],
        oneway_values,
        height=bar_height,
        color=COLORS["oneway"],
        label="편도 4분",
    )

    roundtrip_bars = ax.barh(
        [p + bar_height / 2 for p in positions],
        roundtrip_values,
        height=bar_height,
        color=COLORS["roundtrip"],
        label="왕복 기준 (편도 2분)",
    )

    ax.set_yticks(positions, [row["scope"] for row in rows])
    ax.invert_yaxis()
    ax.set_xlim(0, 110)
    ax.set_xlabel("실보행 도달 가능 비율 (%)")
    ax.set_title(
        "편도 4분과 왕복 기준(편도 2분) 접근성 비교",
        loc="left",
        fontsize=17,
        fontweight=600,
        pad=18,
    )

    ax.bar_label(
        oneway_bars,
        labels=[f"{value:.1f}%" for value in oneway_values],
        padding=3,
        fontsize=7,
    )
    ax.bar_label(
        roundtrip_bars,
        labels=[f"{value:.1f}%" for value in roundtrip_values],
        padding=3,
        fontsize=7,
    )

    ax.grid(axis="x", alpha=0.2)
    ax.set_axisbelow(True)
    ax.legend(loc="lower right")
    fig.tight_layout()

    output = output_dir / "03_oneway_vs_roundtrip.png"
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)

    return output


def main() -> int:
    parser = argparse.ArgumentParser(
        description="기존 metrics.csv에서 접근성 그래프 3개 생성"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="입력 metrics.csv 경로",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="그래프 저장 폴더",
    )
    args = parser.parse_args()

    metrics = load_metrics(args.input)
    configure_korean_font()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    outputs = [
        save_district_walk_chart(metrics, args.output_dir),
        save_straight_vs_walk_chart(metrics, args.output_dir),
        save_oneway_roundtrip_chart(metrics, args.output_dir),
    ]

    print("=" * 55)
    print("접근성 분석 그래프 생성 완료")
    print("=" * 55)
    print(f"입력 CSV: {args.input}")
    print(f"분석 범위: {len({scope for scope, _ in metrics})}개 (서울 포함)")
    for path in outputs:
        print(f"저장: {path} ({path.stat().st_size:,} bytes)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
