# AED 접근성 분석 시스템 개발 설계 가이드

Nov 3, 2026 · @Lee ji hwan

처음 쓰는 기술이 많으니, 한 번에 다 만들지 말고 **작은 것을 하나 띄워서 확인한 뒤 다음 것을 붙이는 순서**로 진행한다. 각 단계는 "완료 기준"을 통과해야 다음 단계로 넘어간다. 프론트엔드는 바이브코딩으로 만들고, 이 문서는 나머지 기술 스택(Valhalla, PostGIS, Python 파이프라인, FastAPI, Docker Compose, 서버 배포)을 자세히 다룬다.

## 전체 순서 한눈에 보기

핵심 원칙은 세 가지다. ① 남이 만든 것(Valhalla, PostGIS)을 **먼저 그대로 띄워 보고**, ② 그 위에 우리 코드를 한 단계씩 붙이고, ③ 모든 단계를 끝낼 때마다 GitHub에 기록한다. 주차는 예시이고, 밀리면 6단계(서버 배포)의 HTTPS 같은 선택 기능부터 덜어낸다.

| 단계 | 할 일 | 주차(예시) | 담당 | 완료 기준 |
| --- | --- | --- | --- | --- |
| 0 | 개발 환경·저장소 준비 | 10주차 초 | 둘 다 | 두 사람 모두 VM에서 `docker run hello-world` 성공, 저장소에 첫 커밋 |
| 1 | Valhalla 재현 | 10주차 | A | 서울 지도로 도보 경로와 4분 등시선 응답이 오고, geojson.io에서 모양 확인 |
| 2 | PostgreSQL + PostGIS | 10주차 | B | DB 컨테이너에 접속해서 테이블 9개가 만들어짐 |
| 3 | 분석 파이프라인 | 11주차 | A | `run_all.py` 한 번으로 서울 전체 지표와 그래프가 나옴 |
| 4 | 백엔드(FastAPI) | 11\~12주차 | B | `/docs` 화면에서 API 7개가 모두 응답함 |
| 5 | 프론트엔드 | 12주차 | B (바이브코딩) | 지도 클릭 → 가장 가까운 AED 경로가 표시됨 |
| 6 | Docker Compose 통합 | 13주차 | 둘 다 | 새 폴더에서 `docker compose up` 한 번으로 전체가 뜨고, **기능 추가 중단** |
| 7 | Linux 서버 배포 | 13주차 | B | 다른 사람 PC 브라우저에서 서버 IP로 접속됨 |
| 8 | 검증·문서화 | 14주차 | 둘 다 | 지표 표·그래프, README, 오류 기록(Issue) 완성 |

- **A = 데이터·분석 담당, B = 서비스·배포 담당.** 1단계와 2단계는 서로 의존하지 않아서 동시에 진행할 수 있다.
- 3단계와 4단계도 동시에 가능하다. B는 3단계가 끝나기 전에 **가짜 AED 5개를 DB에 직접 넣고** API를 먼저 만들면 된다.
- 이미 해 본 것: VM에 Docker 설치, Valhalla 안도라 연습. 1단계는 서울 지도 재현부터 이어가면 된다.

## 0단계. 개발 환경과 저장소 준비

두 사람의 환경을 똑같이 맞추는 것이 목표다. 환경이 다르면 "내 컴퓨터에서는 되는데"가 계속 나온다.

### 0-1. 설치할 것 (VM의 Ubuntu 안에서)

| 도구 | 용도 | 확인 명령 |
| --- | --- | --- |
| Docker + Docker Compose | Valhalla, DB, 전체 서비스 실행 | `docker compose version` |
| Git | 코드 관리 | `git --version` |
| Python 3.12 + venv | 분석 파이프라인, FastAPI 개발 | `python3 --version` |
| osmium-tool | 한국 지도에서 서울만 자르기 | `osmium --version` |
| VS Code + Remote-SSH 확장 (선택) | 윈도우 VS Code로 VM 안 파일 편집 | VS Code에서 VM 접속 |

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip osmium-tool
```

- VM 메모리는 8GB, 디스크는 30GB 이상을 권장한다. 서울 지도 가공과 등시선 계산이 가장 무겁다.
- Python 라이브러리는 컴퓨터 전체가 아니라 \*\*프로젝트 전용 가상환경(venv)\*\*에 설치한다. 두 사람의 버전이 달라지는 것을 막는다.

### 0-2. GitHub 저장소 만들기

한 사람이 저장소를 만들고 다른 사람을 Collaborator로 초대한다. 처음부터 아래 폴더를 만들어 두면, 각자 어디에 무엇을 넣을지 헷갈리지 않는다.

```
aed-access/
├── README.md             # 목적, 설치, 실행 방법
├── LICENSE               # 사용·수정·배포 권한 (GitHub 라이선스 템플릿으로 생성)
├── CONTRIBUTING.md       # 기여 절차: Issue → 브랜치 → 커밋 → PR → 리뷰 → 병합
├── .gitignore            # .env, data/, valhalla/custom_files/, *.log, .venv/ 제외
├── .gitmessage           # 커밋 메시지 템플릿
├── .env.example          # API 키 자리만 있는 예시 (실제 키는 .env에)
├── .github/
│   ├── ISSUE_TEMPLATE/
│   │   └── task.md       # Issue 양식 (0-3)
│   └── pull_request_template.md  # PR 양식 (0-3)
├── docker-compose.yml    # 6단계에서 완성
├── config.yaml           # 보행 속도, 시간, 격자 간격 등 분석 조건
├── db/
│   └── init.sql          # 테이블 만드는 SQL (2단계)
├── pipeline/             # 분석 스크립트 01~06 (3단계)
├── api/                  # FastAPI (4단계)
├── web/                  # 프론트엔드 (5단계)
├── nginx/                # Nginx 설정 (6단계)
├── valhalla/
│   └── custom_files/     # 서울 지도, 가공된 타일 (GitHub에 올리지 않음)
├── data/raw/             # API 원본 응답 (GitHub에 올리지 않음)
├── results/              # 지표 CSV, 그래프 PNG (올림)
├── tests/                # pytest — 재현 가능한 검증
└── docs/
    ├── WORKFLOW.md       # 팀 협업 규칙 5가지 (0-3)
    └── ...               # 설치·오류 기록, 아키텍처 그림, 배포 절차
```

- 수업에서 제시한 "처음 보는 사람도 실행할 수 있는 저장소"의 다섯 가지(README.md, LICENSE, .gitignore, CONTRIBUTING.md, docs/·tests/)를 모두 처음부터 만든다.
- `.gitignore`는 **이미 커밋된 파일을 지우지 않는다.** 그래서 `.env`를 만들기 전에 첫 커밋에 포함시킨다.

### 0-3. 협업 흐름 (수업 Git 3주차 방식)

모든 작업은 수업에서 배운 **Issue → Branch → Commit → PR/Review → Merge → Tag** 흐름을 따른다. 두 사람이 번갈아 기여자(변경을 제안)와 관리자(검토 후 병합) 역할을 맡는다. 내가 올린 PR은 팀원이 리뷰하고 병합한다.

| 단계 | 하는 일 | 결과물 |
| --- | --- | --- |
| 1. Issue | 무엇을 왜 해결하는지, 현재/기대 동작, 완료 기준을 작성 | GitHub Issue (작업 계약서) |
| 2. Branch | main에서 작업 브랜치 분리: `git switch -c feature/aed-collect` | `feature/<주제>` 브랜치 |
| 3. Commit | 한 가지 의도만 담아 커밋 (아래 "커밋 메시지 규칙") | 의미 있는 커밋 기록 |
| 4. PR 전 점검 | `git log --oneline main..feature/aed-collect`, `git diff main...feature/aed-collect`로 내가 제안하는 변경만 들어갔는지 확인하고 실행·테스트 | 변경 범위와 실행 결과 |
| 5. PR / Review | PR 양식에 맞춰 제출, 팀원이 리뷰 체크리스트로 검토·댓글 | 리뷰 기록, 승인 |
| 6. Merge | 승인 후 **병합 커밋을 남기는 방식**(GitHub "Create a merge commit" = `git merge --no-ff`)으로 main에 통합, 연결된 Issue 자동 종료 | main 갱신 |
| 7. Tag | 단계가 끝날 때마다 검증된 main에 annotated tag (8-3) | `v0.1.0` 등 |

- 병합 후 각자 로컬 main을 맞춘다: `git switch main && git pull --ff-only origin main`
- 작업 상태는 GitHub **Project** 보드(할 일 / 진행 중 / 완료)로 관리한다. Issue는 "무엇을 왜", Project는 "언제 어떤 순서로", PR은 "어떤 변경을 main에"를 담당한다.
- `main`에는 직접 push하지 않는다. GitHub 설정의 Branch protection으로 "PR과 리뷰 1명 승인 후 병합"을 강제해 두면 실수가 없다.

**Issue 양식** (`.github/ISSUE_TEMPLATE/task.md`) — 수업의 "좋은 Issue = 제목 + 배경 + 기대 동작 + 완료 기준"

```markdown
## 배경
왜 필요한가 (관련 PRD 기능 ID: 예 P-02)

## 현재 동작
예: 운영시간 '2400'을 넣으면 ValueError가 난다

## 기대 동작
예: '2400'은 자정(1440분)으로 해석된다

## 완료 기준
- [ ] pytest tests/test_hours.py 통과
- [ ] cleaning_report.csv에 해석 실패 0건
```

**PR 양식** (`.github/pull_request_template.md`) — 수업의 "PR은 ‘바꿨습니다’가 아니라 ‘이렇게 검토해 주세요’"

```markdown
## 연결된 Issue
Closes #번호

## 변경 요약
- 

## 재현·실행 방법
(리뷰어가 그대로 따라 할 명령)

## 검증 결과
(테스트 출력, 응답 예시, 스크린샷)

## 리뷰 체크리스트
- [ ] 요구 충족: Issue의 완료 기준을 만족하는가
- [ ] 변경 범위: 관계없는 파일 수정·불필요한 파일 추가가 없는가
- [ ] 재현성: README 절차로 설치·실행·테스트할 수 있는가
- [ ] 안전성: 비밀번호, API 키, 개인정보가 없는가
- [ ] 이력 품질: 커밋 메시지가 의미를 설명하는가
```

**팀 규칙 5가지** (`docs/WORKFLOW.md`, 수업 실습 C-2와 같은 내용) — 자세한 절차는 `CONTRIBUTING.md`에 이 섹션 내용을 옮겨 적는다.

1. 모든 변경은 Issue로 먼저 설명한다.
2. `feature/<주제>` 브랜치에서 작업하고 main에서 직접 개발하지 않는다.
3. PR에 실행 결과를 포함하고, 병합 전에 diff와 테스트를 검토한다.
4. 최소 1명이 리뷰한 뒤 병합하고, main은 항상 실행 가능한 상태로 유지한다.
5. 릴리스마다 tag를 만든다.

**보안 규칙**

- API 키·토큰·비밀번호는 절대 커밋하지 않는다. 발표 자료·스크린샷에도 넣지 않는다.
- 실수로 올렸다면 파일을 지우는 것으로 끝내지 않는다. 커밋 기록에 남아 있으므로 **즉시 공공데이터포털에서 키를 폐기·재발급**한다.
- 다른 코드를 가져올 때는 그 라이선스를 확인한다. 공개 저장소라고 자유롭게 쓸 수 있는 것은 아니다.

### 0-4. 완료 기준

- [ ] 두 사람 VM 모두에서 `docker run hello-world` 성공
- [ ] 저장소에 폴더 구조, `.gitignore`, `README.md` 초안이 첫 커밋으로 올라감
- [ ] 두 사람 모두 브랜치 → PR → 합치기를 한 번씩 연습해 봄 (README에 자기 이름 추가하기 정도로)

## 1단계. Valhalla 재현 (담당 A)

Valhalla가 서울 지도로 "도보 경로"와 "4분 도달 구역"을 돌려주는 것을 확인하는 단계다. 이 단계가 과목 평가의 "원본 재현"에 해당한다. 안도라 연습은 끝났으니 서울부터 하면 된다.

### 1-1. 서울 지도 준비

```bash
cd aed-access/valhalla
wget https://download.geofabrik.de/asia/south-korea-latest.osm.pbf
# 서울 범위로 자르기 (서쪽경도,남쪽위도,동쪽경도,북쪽위도)
osmium extract -b 126.76,37.41,127.19,37.72 south-korea-latest.osm.pbf -o custom_files/seoul.osm.pbf
ls -lh custom_files    # seoul.osm.pbf 하나만 있어야 함
```

- `custom_files`에는 **서울 파일 하나만** 둔다. 예전 안도라 파일이나 `valhalla_tiles.tar`가 남아 있으면 그 지도를 계속 쓴다(지난번 `error_code 171`의 원인).
- 받은 날짜를 기록해 둔다. Geofabrik 파일은 매일 갱신되므로, README에 "어느 날짜 지도로 계산했는지"를 적어야 재현할 수 있다.

### 1-2. 실행

```bash
docker run -dt --name valhalla -p 8002:8002 \
  -v $PWD/custom_files:/custom_files \
  -e server_threads=2 \
  ghcr.io/valhalla/valhalla-scripted:latest
docker logs -f valhalla    # 가공이 끝날 때까지 지켜본다 (Ctrl+C로 나오기)
```

- 가공에 걸린 시간을 재 둔다. 보고서의 "실행 환경" 항목에 쓴다.
- 가공 중 컨테이너가 꺼지면 메모리 부족이다. `server_threads=1`로 낮추거나 VM 메모리를 늘린다.
- 테스트에 쓸 이미지 태그(예: `latest`가 가리키는 실제 버전)를 기록해 두고, 6단계에서는 `latest` 대신 그 버전으로 고정한다.

### 1-3. 확인할 요청 3가지

이 세 가지가 이 프로젝트가 Valhalla에게 시킬 일의 전부다. 한 번씩 직접 보내서 응답 모양을 익혀 두면 3·4단계가 쉬워진다.

| 요청 | 주소 | 쓰는 곳 | 응답에서 볼 것 |
| --- | --- | --- | --- |
| 등시선 | `/isochrone` | 3단계 P-03: AED별 4분·2분 도달 구역 | `features[0].geometry` (다각형) |
| 다대다 시간 | `/sources_to_targets` | 4단계 S-04: 클릭 지점 → 후보 AED 5개의 도보 시간 | 후보별 `time`(초), `distance`(km) |
| 경로 | `/route` | 4단계 S-04: 가장 빠른 AED까지 선 | `trip.summary`, `trip.legs[0].shape`(압축된 경로선) |

```bash
# 등시선: 서울시청에서 걸어서 4분
 curl -s http://localhost:8002/isochrone --data '{"locations":[{"lat":37.5663,"lon":126.9779}],"costing":"pedestrian","contours":[{"time":4}],"polygons":true}' > iso.geojson

# 다대다: 시청 → 광화문, 시청역
 curl -s http://localhost:8002/sources_to_targets --data '{"sources":[{"lat":37.5663,"lon":126.9779}],"targets":[{"lat":37.5759,"lon":126.9768},{"lat":37.5657,"lon":126.9769}],"costing":"pedestrian"}'

# 경로: 시청 → 광화문
 curl -s http://localhost:8002/route --data '{"locations":[{"lat":37.5663,"lon":126.9779},{"lat":37.5759,"lon":126.9768}],"costing":"pedestrian","units":"kilometers"}'
```

- `iso.geojson`을 [geojson.io](https://geojson.io)에 끌어다 놓아 모양을 확인하고 캡처한다. 보고서의 "재현 결과" 증거가 된다.
- 보행 속도를 바꾸는 법도 이때 확인해 둔다: `"costing_options":{"pedestrian":{"walking_speed":3.6}}`를 추가하면 도달 구역이 작아져야 한다.
- 요청은 터미널 대신 Python `requests`로도 보내 본다. 3단계에서 이 코드를 그대로 재사용한다.

```python
import requests
body = {"locations": [{"lat": 37.5663, "lon": 126.9779}], "costing": "pedestrian",
        "contours": [{"time": 4}], "polygons": True}
res = requests.post("http://localhost:8002/isochrone", json=body, timeout=30)
print(res.status_code, res.json()["features"][0]["geometry"]["type"])
```

### 1-4. 완료 기준

- [ ] 서울 지도로 위 요청 3개가 모두 정상 응답
- [ ] geojson.io 캡처를 `docs/`에 저장
- [ ] Issue에 기록: 이미지 버전, OSM 날짜, 자른 범위, 가공 시간, VM 사양, 난 오류와 해결 (안도라 타일이 남아 생긴 171 오류 포함)

## 2단계. PostgreSQL + PostGIS (담당 B)

DB를 Docker로 띄우고, PRD 7-5의 테이블을 SQL 파일 하나(`db/init.sql`)로 만드는 단계다. 테이블 구조를 먼저 정해 두면 A(파이프라인)와 B(API)가 같은 약속을 보고 따로 작업할 수 있다.

### 2-1. 먼저 알아야 할 개념 3가지

- **geometry 칼럼**: PostGIS가 추가하는 "도형" 칸. `Point`(AED 위치), `MultiPolygon`(도달 구역, 자치구 경계)을 저장한다. 뒤의 숫자 `4326`은 일반 위도·경도 좌표계라는 뜻이다.
- **미터 단위 계산**: 위도·경도는 "도" 단위라 거리 계산에 바로 쓸 수 없다. 거리는 `::geography`로 바꿔서(미터), 격자·면적은 `ST_Transform(geom, 5179)`(한국 통합 좌표계, 미터)로 바꿔서 계산한다.
- **공간 인덱스(GIST)**: "근처에 있는 점 찾기"를 빠르게 해 주는 색인. 도형 칼럼에는 거의 항상 단다.

### 2-2. 테이블 설계 (`db/init.sql`)

PRD의 8개 테이블에, 화면에 보여줄 "미리 합친 도달 구역·사각지대"를 담는 `layer` 테이블을 하나 더해 9개로 설계한다.

```sql
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE district (            -- 자치구 경계 25개
  name  TEXT PRIMARY KEY,
  geom  geometry(MultiPolygon, 4326)
);

CREATE TABLE aed (                 -- 정제된 AED
  id         SERIAL PRIMARY KEY,
  source_id  TEXT UNIQUE,           -- API 원본의 고유번호
  place      TEXT,                  -- 설치 장소
  address    TEXT,
  gu         TEXT REFERENCES district(name),
  hours      JSONB,                 -- 요일별 운영시간 {"mon":["0900","1800"], ...}
  geom       geometry(Point, 4326)
);
CREATE INDEX aed_geom_idx ON aed USING GIST (geom);

CREATE TABLE isochrone (           -- AED별 도달 구역
  aed_id   INT REFERENCES aed(id) ON DELETE CASCADE,
  minutes  INT,                     -- 4 또는 2
  geom     geometry(MultiPolygon, 4326),
  PRIMARY KEY (aed_id, minutes)
);
CREATE INDEX iso_geom_idx ON isochrone USING GIST (geom);

CREATE TABLE grid_point (          -- 100m 격자 평가 지점
  id          BIGSERIAL PRIMARY KEY,
  gu          TEXT,
  geom        geometry(Point, 4326),
  straight_4  BOOLEAN, walk_4 BOOLEAN,
  straight_2  BOOLEAN, walk_2 BOOLEAN
);
CREATE INDEX grid_geom_idx ON grid_point USING GIST (geom);

CREATE TABLE pipeline_run (        -- 실행 기록
  id        SERIAL PRIMARY KEY,
  run_at    TIMESTAMPTZ DEFAULT now(),
  config    JSONB,                  -- config.yaml 내용 그대로
  osm_date  DATE,
  aed_date  DATE
);

CREATE TABLE metrics (             -- 지표 (서울 전체 + 자치구별)
  run_id        INT REFERENCES pipeline_run(id),
  scope         TEXT,               -- '서울' 또는 자치구 이름
  mode          TEXT,               -- 'oneway' / 'roundtrip'
  straight_pct  NUMERIC, walk_pct NUMERIC, diff_pp NUMERIC,
  deadzone_km2  NUMERIC, aed_count INT
);

CREATE TABLE layer (               -- 화면용으로 미리 합친 다각형
  mode  TEXT, kind TEXT,            -- kind: 'coverage' / 'deadzone'
  geom  geometry(MultiPolygon, 4326),
  PRIMARY KEY (mode, kind)
);

CREATE TABLE coverage_cache (      -- 실시간 반영 30분 캐시
  weekday  INT, slot INT, mode TEXT, is_holiday BOOLEAN,
  coverage geometry(MultiPolygon, 4326),
  walk_pct NUMERIC,
  PRIMARY KEY (weekday, slot, mode, is_holiday)
);

CREATE TABLE report (              -- 오류 제보 (개인정보 칼럼 없음)
  id           SERIAL PRIMARY KEY,
  aed_id       INT REFERENCES aed(id),
  type         TEXT CHECK (type IN ('wrong_location','missing','unusable')),
  description  TEXT CHECK (char_length(description) <= 500),
  created_at   TIMESTAMPTZ DEFAULT now(),
  status       TEXT DEFAULT '접수'
);
```

- `hours` 칼럼의 실제 형식은 AED API 응답을 본 뒤 확정한다. 요일별 시작·종료 시각(HHMM)을 위 예시처럼 JSON으로 정리해 넣는 것을 권장한다.
- 테이블을 바꾸고 싶으면 혼자 고치지 말고 PR로 올려 팀원과 합의한다. A와 B가 모두 이 구조에 맞춰 코드를 쓴다.

### 2-3. DB 띄우기

```bash
cd aed-access
docker run -d --name aed-db -p 5432:5432 \
  -e POSTGRES_USER=aed -e POSTGRES_PASSWORD=aedpass -e POSTGRES_DB=aed \
  -v aed-db-data:/var/lib/postgresql/data \
  -v $PWD/db/init.sql:/docker-entrypoint-initdb.d/init.sql \
  postgis/postgis:16-3.4

docker exec -it aed-db psql -U aed -d aed     # DB에 접속
```

- `docker-entrypoint-initdb.d`에 넣은 SQL은 **DB가 처음 만들어질 때 한 번만** 실행된다. `init.sql`을 고친 뒤 다시 적용하려면 `docker rm -f aed-db && docker volume rm aed-db-data` 후 다시 띄운다(데이터는 사라진다).
- 비밀번호는 연습용이다. 6단계에서 `.env`로 옮긴다.
- **추천 도구: QGIS**(무료 오픈소스 지도 프로그램). PostGIS에 접속하면 테이블의 점·다각형을 지도 위에 바로 그려 준다. 3단계 결과가 맞는지 눈으로 확인할 때 가장 유용하다.

### 2-4. 연습 쿼리 (psql에서 직접 입력)

이 네 개를 직접 돌려 보면 3·4단계에서 쓸 공간 함수를 모두 한 번씩 다루게 된다.

```sql
\dt                                         -- 테이블 9개가 보이는지
SELECT postgis_version();                   -- PostGIS가 켜져 있는지

-- ① 가짜 AED 넣기 (서울시청 근처)
INSERT INTO aed (source_id, place, geom)
VALUES ('TEST1', '테스트 AED', ST_SetSRID(ST_MakePoint(126.9779, 37.5663), 4326));

-- ② 특정 지점에서 직선 340m 안의 AED (직선거리 판정)
SELECT place FROM aed
WHERE ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(126.9768, 37.5680), 4326)::geography, 340);

-- ③ 가장 가까운 AED 5개 (S-04 후보 고르기)
SELECT place FROM aed
ORDER BY geom <-> ST_SetSRID(ST_MakePoint(126.9768, 37.5680), 4326) LIMIT 5;

-- ④ Valhalla 등시선(GeoJSON)을 그대로 저장하는 방법
-- INSERT INTO isochrone VALUES (1, 4, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON('<geometry 부분 JSON>'), 4326)));
```

### 2-5. 자치구 경계 데이터 구하기

`district` 테이블에는 서울 25개 구의 경계 다각형이 필요하다. 격자 생성과 자치구별 집계에 쓴다.

- 후보: 통계청 SGIS(통계지리정보서비스)의 행정구역 경계 파일, 또는 OSM의 행정경계(admin\_level 6). 어느 쪽을 쓸지 정하고 출처·기준일을 README에 적는다(PRD 미결 사항 2번).
- Shapefile이나 GeoJSON을 받으면 GeoPandas의 `read_file()` → `to_postgis()`로 넣는다(3단계 03\_load에서 같이 처리). 좌표계가 4326이 아니면 `to_crs(4326)`으로 바꾼 뒤 넣는다.

### 2-6. 완료 기준

- [ ] `\dt`로 테이블 9개 확인
- [ ] 연습 쿼리 ①\~③가 예상대로 동작
- [ ] 자치구 경계 25개가 `district`에 들어가고, QGIS에서 서울 모양으로 보임

## 3단계. 분석 파이프라인 (담당 A)

이 프로젝트의 핵심 결과(직선거리 대비 실보행 도달 가능 비율)를 만드는 단계다. 스크립트 6개를 **하나씩 만들고, 만들 때마다 결과를 눈으로 확인한 뒤** 다음 스크립트로 넘어간다. 처음에는 서울 전체가 아니라 **구 하나(예: 종로구)만**으로 끝까지 돌려 보고, 되면 서울 전체로 넓힌다.

### 3-1. Python 환경

```bash
cd aed-access
python3 -m venv .venv && source .venv/bin/activate
pip install requests pandas geopandas shapely sqlalchemy geoalchemy2 "psycopg[binary]" \
            pyyaml python-dotenv matplotlib holidays pytest
pip freeze > pipeline/requirements.txt     # 팀원도 같은 버전을 설치하도록
```

- `.venv/`는 `.gitignore`에 넣는다. 팀원은 `pip install -r pipeline/requirements.txt`로 같은 환경을 만든다.
- `.env`에 `AED_API_KEY`, `DATABASE_URL=postgresql+psycopg://aed:aedpass@localhost:5432/aed`, `VALHALLA_URL=http://localhost:8002`를 넣고, 코드는 `python-dotenv`로 읽는다. 코드에 키나 비밀번호를 직접 적지 않는다.

### 3-2. 설정 파일 (`config.yaml`)

분석 조건은 모두 여기에 모은다. 조건을 바꾸어 비교할 때 코드를 고치지 않고 이 파일만 바꾼다.

```yaml
region: seoul
test_gu: null              # 예: "종로구" 으로 두면 한 구만 분석
walking_speed_kmh: 5.1     # Valhalla 보행 기본 속도
modes:
  oneway:    { minutes: 4, radius_m: 340 }
  roundtrip: { minutes: 2, radius_m: 170 }
grid_m: 100
hours_missing_policy: open # 운영시간 없는 AED: open(운영 중으로 간주) / exclude
```

### 3-3. 스크립트 한눈에 보기

처음 계획한 6개(01~06)를 구현하면서 적재·판정·지표 단계를 나눠 아래 9개가 되었다 (#26).

| 스크립트 | 입력 | 하는 일 | 출력 | 확인 방법 |
| --- | --- | --- | --- | --- |
| `01_collect.py` | API 키 | AED API 전체 페이지 호출 | `data/raw/aed_seoul_*.json` | 파일 건수 = API가 알려주는 전체 건수 |
| `02_clean.py` | 원본 JSON | 좌표·중복·서울 밖 제거, 운영시간 해석 | `data/clean/aed.csv`, `results/cleaning_report.csv` | pytest 통과, 제거 건수 표 |
| `03_load_district.py` | 구 경계 SHP | `district` 테이블에 넣기 | DB | 25개 구, 유효한 도형 |
| `03_load_aed.py` | 정제 CSV | `aed` 테이블에 넣기, AED에 구 이름 지정 | DB | QGIS에서 점이 서울 안에 찍힘 |
| `04_isochrone.py` | 정제 CSV, Valhalla | AED마다 4분·2분 등시선 요청 | `results/isochrones/*.jsonl`, 실패 목록 CSV | 실패율 |
| `04_load_isochrones.py` | 등시선 JSONL | `isochrone` 테이블에 넣기 | DB | QGIS에서 다각형 확인 |
| `05_generate_grid.py` | `district` | 100m 격자 생성 | `grid_point` 테이블 | 격자 수, 구 경계 중복 0 |
| `06_analyze_accessibility.py` | `grid_point`, `aed`, `isochrone` | 직선/실보행 판정 | `grid_point`의 판정 칼럼 | 무작위 20개 지점 수동 확인 |
| `07_build_metrics.py` | `grid_point`, `isochrone` | 서울·자치구별 지표, 화면용 다각형 합치기 | `metrics`, `layer` 테이블 (`results/metrics.csv`, 그래프는 추가 예정) | 두 번 돌려도 같은 숫자 |

`run_all.py`는 이 9개를 순서대로 실행하고 하나라도 실패하면 멈춘다 (`--list`로 단계 확인, `--from`/`--to`로 일부만 실행). DB 연결·설정 읽기 같은 공통 코드는 `pipeline/common.py`에 모아 둔다. 운영시간 반영 분석(`08_analyze_available_accessibility.py`)은 분석 시각을 인자로 받게 고친 뒤 `run_all.py`에 추가한다.

### 3-4. 단계별 구현 포인트

**01\_collect — 수집**

1. 공공데이터포털 "국립중앙의료원\_전국 자동심장충격기(AED) 정보 조회 서비스" 페이지에서 **요청 주소, 필수 파라미터, 응답 예시**를 먼저 읽는다. 필드 이름(위도·경도·운영시간)은 추측하지 말고 실제 응답에서 확인한다.
2. 브라우저나 `curl`로 1페이지(10건)만 먼저 받아 본다. 서울만 필터하는 파라미터(시·도)가 있는지도 확인한다.
3. 그다음 전체 건수를 보고 `numOfRows`를 크게 잡아 페이지를 돌며 모두 받는다. 실패하면 3번까지 다시 시도하고, 요청 사이에 잠깐 쉬어 호출 한도를 지킨다.
4. 원본은 가공하지 않고 그대로 날짜별 파일로 저장한다. 이후 단계는 이 파일만 읽는다(API를 반복 호출하지 않기 위해).

- 자주 나는 문제: 공공데이터포털 키에는 Encoding과 Decoding 두 가지가 있다. `requests`의 `params`에 넣을 때는 **Decoding 키**를 쓴다. Encoding 키를 넣으면 한 번 더 인코딩되어 인증 오류가 난다.

**02\_clean — 정제와 운영시간 해석**

제거 순서는 ① 좌표 누락 → ② 서울 범위 밖 → ③ 같은 위치·같은 이름 중복. 단계마다 남은 건수를 `cleaning_report.csv`에 기록한다. 운영시간 해석은 틀리기 쉬운 부분이라 함수로 따로 만들고 테스트를 먼저 쓴다.

```python
# pipeline/hours.py
def parse_hhmm(value):
    """'0930' -> 570(분). 빈 값은 None, '2400'은 1440."""
    if value is None or str(value).strip() == "":
        return None
    s = str(value).strip().zfill(4)
    h, m = int(s[:2]), int(s[2:])
    if h == 24 and m == 0:
        return 1440
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"잘못된 시각: {value}")
    return h * 60 + m

def is_open(start, end, now):
    """start, end, now는 분 단위. 정보 없으면 None."""
    if start is None or end is None:
        return None
    if start == end:                 # 0000~0000, 0000~2400 등은 하루 종일로 본다 (팀 결정 사항)
        return True
    if start < end:                  # 일반: 0900~1800
        return start <= now < end
    return now >= start or now < end # 자정 넘김: 2200~0600
```

```python
# tests/test_hours.py
from pipeline.hours import parse_hhmm, is_open

def test_parse():
    assert parse_hhmm("0930") == 570
    assert parse_hhmm("2400") == 1440
    assert parse_hhmm("") is None

def test_open():
    assert is_open(540, 1080, 600) is True      # 09~18시, 10시
    assert is_open(540, 1080, 1200) is False    # 20시
    assert is_open(1320, 360, 60) is True       # 22~06시, 새벽 1시
    assert is_open(None, 1080, 600) is None
```

- 실행: `pytest tests/` → 모두 통과해야 02\_clean에서 이 함수를 쓴다.
- 실제 데이터에서 예상과 다른 값(예: `2500`, 문자)을 발견하면 테스트 케이스를 추가하고 처리 규칙을 정한다. 이 과정도 Issue에 기록한다.

**03\_load — DB에 넣기**

```python
import geopandas as gpd, pandas as pd
from sqlalchemy import create_engine

engine = create_engine(DATABASE_URL)
df = pd.read_csv("data/clean/aed.csv")
gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.lon, df.lat), crs=4326)
# 자치구 경계도 같은 방식: gpd.read_file("경계파일").to_crs(4326)
```

- DB에 넣는 방법은 두 가지다. `gdf.to_postgis(...)`는 간편하지만 테이블 구조를 바꿀 수 있으니, `init.sql`로 만든 테이블에 넣을 때는 `if_exists="append"`를 꼭 쓴다.
- AED의 자치구는 SQL 한 줄로 지정한다: `UPDATE aed a SET gu = d.name FROM district d WHERE ST_Contains(d.geom, a.geom);`
- 다시 실행해도 중복이 쌓이지 않게, 넣기 전에 테이블을 비우거나(`TRUNCATE aed CASCADE`) `source_id` 기준으로 건너뛴다.

**04\_isochrone — 등시선 계산 (가장 오래 걸림)**

1. `isochrone`에 아직 없는 AED만 골라서 요청한다. 중간에 멈춰도 다시 실행하면 이어서 계산된다.
2. 1단계의 Python 요청 코드를 그대로 쓴다. `contours`에 `[{"time":4},{"time":2}]`를 함께 넣으면 한 번 요청으로 두 개를 받는다. 보행 속도는 `config.yaml` 값을 `costing_options`로 넣는다.
3. 응답의 `geometry`를 `ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(...), 4326))`로 저장한다.
4. 실패(예: 길에서 너무 먼 AED, 오류 171)는 `results/isochrone_failed.csv`에 기록하고 다음 AED로 넘어간다.
5. 전체 시간과 실패율을 출력한다. 보고서에 쓴다.

**05\_evaluate — 격자 판정 (SQL로 처리)**

Python에서 점 6만 개를 하나씩 비교하면 느리다. PostGIS에게 SQL로 한꺼번에 시킨다. Python은 아래 SQL을 순서대로 실행만 한다.

```sql
-- ① 100m 격자 중심점 만들기 (미터 좌표계 5179에서 만든 뒤 4326으로)
TRUNCATE grid_point;
INSERT INTO grid_point (gu, geom)
SELECT d.name, ST_Transform(ST_Centroid(g.geom), 4326)
FROM district d,
     ST_SquareGrid(100, ST_Transform(d.geom, 5179)) AS g
WHERE ST_Contains(ST_Transform(d.geom, 5179), ST_Centroid(g.geom));

-- ② 직선거리 판정: 340m 안에 AED가 하나라도 있나
UPDATE grid_point p SET straight_4 = EXISTS (
  SELECT 1 FROM aed a WHERE ST_DWithin(a.geom::geography, p.geom::geography, 340));

-- ③ 실보행 판정: AED의 4분 도달 구역 안에 들어가나
UPDATE grid_point p SET walk_4 = EXISTS (
  SELECT 1 FROM isochrone i WHERE i.minutes = 4 AND ST_Intersects(i.geom, p.geom));

-- ④ 왕복 기준(2분, 170m)도 같은 방식으로 straight_2, walk_2

-- ⑤ 화면용 다각형: 도달 구역 합치기, 사각지대 = 서울 - 도달 구역
INSERT INTO layer (mode, kind, geom)
SELECT 'oneway', 'coverage',
       ST_Multi(ST_SimplifyPreserveTopology(ST_Union(geom), 0.00005))
FROM isochrone WHERE minutes = 4;
```

- 여기서 쓴 격자 간격(100), 반경(340), 시간(4)은 Python에서 `config.yaml` 값으로 채워 넣는다.
- 사각지대는 `ST_Difference(서울 경계 전체, 도달 구역 합친 것)`으로 만든다.
- 단순화 값(0.00005도, 약 5m)은 화면 속도를 위한 것이다. 지표는 단순화하지 않은 `grid_point`로 계산한다.
- **수동 검증**: QGIS에서 `grid_point`와 `isochrone`을 같이 열고, 무작위 20개 점의 판정이 눈으로 본 것과 맞는지 확인해 표로 남긴다.

**06\_metrics — 지표 계산**

격자 점 하나가 100m × 100m = 0.01km²를 대표하므로, 사각지대 면적은 "도달 못 하는 점 수 × 0.01"로 구한다.

```sql
SELECT gu,
       ROUND(AVG(straight_4::int) * 100, 1)                       AS straight_pct,
       ROUND(AVG(walk_4::int) * 100, 1)                           AS walk_pct,
       ROUND((AVG(straight_4::int) - AVG(walk_4::int)) * 100, 1)  AS diff_pp,
       ROUND(SUM((NOT walk_4)::int) * 0.01, 2)                    AS deadzone_km2
FROM grid_point GROUP BY gu ORDER BY walk_pct;
```

- 서울 전체는 `GROUP BY` 없이 같은 쿼리를 돌린다. 결과는 `metrics` 테이블과 `results/metrics.csv`에 모두 저장한다.
- 그래프 3개(matplotlib): 자치구별 실보행 도달 가능 비율 막대, 자치구별 직선 vs 실보행 비교, 편도 vs 왕복. 한글 글꼴이 깨지면 `sudo apt install fonts-nanum` 후 matplotlib에 나눔글꼴을 지정한다.

### 3-5. 완료 기준

- [ ] `pytest tests/` 전부 통과
- [ ] 구 하나로 끝까지 성공 → 서울 전체로 성공
- [ ] `python pipeline/run_all.py` 한 번으로 `results/metrics.csv`와 그래프가 생김
- [ ] 같은 설정으로 두 번 돌려 같은 숫자가 나옴
- [ ] 수동 검증 20개 표, 등시선 실패율, 총 계산 시간을 `docs/`에 기록

## 4단계. 백엔드 — FastAPI (담당 B)

백엔드는 화면의 요청을 받아 DB에서 분석 결과를 꺼내 주거나, Valhalla에 경로 계산을 시키는 중간 다리다. API 7개를 **쉬운 것부터 하나씩** 만들고, 만들 때마다 `/docs` 화면에서 직접 눌러 본다. 3단계가 끝나기 전에는 2단계에서 넣은 가짜 AED로 개발한다.

### 4-1. 폴더 구조

```
api/
├── main.py          # 앱 생성, 라우터 연결
├── db.py            # DB 연결 (DATABASE_URL을 .env에서 읽음)
├── valhalla.py      # Valhalla 호출 함수 (VALHALLA_URL)
├── hours.py         # 3단계 hours.py와 같은 운영시간 함수
├── routers/
│   ├── meta.py      # /api/meta
│   ├── aeds.py      # /api/aeds
│   ├── stats.py     # /api/stats
│   ├── layers.py    # /api/layers/coverage, /api/layers/deadzone
│   ├── nearest.py   # /api/nearest
│   └── reports.py   # /api/reports
└── requirements.txt # fastapi, uvicorn, sqlalchemy, psycopg[binary], requests, holidays, python-dotenv
```

- 파일을 기능별로 나누면 PR이 작아지고, 두 사람이 같은 파일을 동시에 고칠 일이 줄어든다.
- 실행: `uvicorn api.main:app --reload --port 8000` → 브라우저에서 `http://localhost:8000/docs` (VM 브라우저 또는 포트 포워딩)

### 4-2. 구현 순서

| 순서 | API | 배우는 것 | 완료 기준 |
| --- | --- | --- | --- |
| 1 | `GET /api/meta` | FastAPI 기본, DB 연결 | 데이터 기준일·설정값이 `pipeline_run`에서 나옴 |
| 2 | `GET /api/aeds` | 공간 쿼리 결과를 GeoJSON으로 돌려주기 | 화면 범위(bbox) 안 AED만 나옴 |
| 3 | `GET /api/stats` | 표 데이터 돌려주기 | `metrics.csv`와 같은 숫자 |
| 4 | `GET /api/layers/...` | 큰 다각형 전달 | 2초 안에 응답 |
| 5 | `GET /api/nearest` | Valhalla 호출, 여러 단계 조합 | 2초 안에 가장 가까운 AED와 경로 |
| 6 | `POST /api/reports` | 입력값 검사(Pydantic) | 잘못된 입력은 422 오류로 거부 |
| 7 | `open_now` 파라미터 | 시간·공휴일 처리, 캐시 | 같은 30분 구간 두 번째 요청 1초 이내 |

### 4-3. 기본 뼈대와 첫 API

```python
# api/db.py
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
load_dotenv()
engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)

def query(sql, **params):
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql), params)]
```

```python
# api/routers/aeds.py
import json
from fastapi import APIRouter
from api.db import query

router = APIRouter(prefix="/api")

@router.get("/aeds")
def aeds(west: float, south: float, east: float, north: float):
    rows = query("""
        SELECT id, place, address, hours, ST_AsGeoJSON(geom) AS g
        FROM aed WHERE geom && ST_MakeEnvelope(:w, :s, :e, :n, 4326)
    """, w=west, s=south, e=east, n=north)
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": json.loads(r["g"]),
         "properties": {k: r[k] for k in ("id", "place", "address", "hours")}}
        for r in rows]}
```

```python
# api/main.py
from fastapi import FastAPI
from api.routers import aeds  # meta, stats, layers, nearest, reports
app = FastAPI(title="AED 접근성 분석 API")
app.include_router(aeds.router)
```

- 지도 데이터는 모두 **GeoJSON**(`FeatureCollection`)으로 돌려준다. Leaflet이 그대로 읽는 형식이라 프론트엔드가 쉬워진다.
- SQL에 값을 넣을 때는 위처럼 `:w` 같은 자리표시를 쓴다. 문자열을 직접 이어 붙이면 보안 문제(SQL 인젝션)가 생긴다.

### 4-4. 가장 복잡한 API: `/api/nearest`

클릭한 지점 하나에 대해 아래 다섯 단계를 순서대로 처리한다. 단계마다 함수를 하나씩 만들고 따로 테스트한 뒤 합친다.

1. **서울 안인지 확인**: `SELECT EXISTS (SELECT 1 FROM district WHERE ST_Contains(geom, 점))` → 아니면 400 오류와 안내 문구
2. **후보 5개**: `ORDER BY geom <-> 점 LIMIT 5` (직선 1km 안). `open_now=true`면 운영 중인 AED만 남긴다.
3. **실제 도보 시간**: Valhalla `/sources_to_targets`에 출발점 1개 + 후보 5개를 한 번에 보낸다. 응답의 `time`(초)으로 정렬한다.
4. **경로**: 1등 AED까지만 `/route`를 호출해 경로선을 받는다. 응답의 `shape`는 압축된 문자열(정밀도 6자리 polyline)이라, 백엔드에서 좌표 목록으로 풀어서 GeoJSON LineString으로 돌려주면 프론트가 편하다.
5. **응답**: 1등 AED(이름, 시간, 거리, 운영 상태, 경로) + 후보 2개 + `straight_first_differs`(직선 1등과 실보행 1등이 다른지). 마지막 값이 화면의 "직선거리 1위는 실제로는 …" 안내에 쓰인다.

- Valhalla 호출에는 `timeout`을 꼭 건다(예: 5초). 실패하면 500이 아니라 "도보 경로를 찾을 수 없습니다" 안내를 돌려준다.
- 클릭한 좌표는 계산에만 쓰고 저장하지 않는다(PRD 8-6).

### 4-5. 오류 제보와 입력값 검사

```python
# api/routers/reports.py
from typing import Literal, Optional
from pydantic import BaseModel, Field

class ReportIn(BaseModel):
    aed_id: int
    type: Literal["wrong_location", "missing", "unusable"]
    description: Optional[str] = Field(default=None, max_length=500)
```

- 이렇게 모델을 선언하면 유형이 틀리거나 500자를 넘는 요청은 FastAPI가 자동으로 422 오류를 돌려준다. DB의 `CHECK` 제약과 이중으로 막는 셈이다.

### 4-6. 실시간 반영(`open_now`)

1. 현재 시각을 **서울 시간**으로 구한다: `datetime.now(ZoneInfo("Asia/Seoul"))`. 서버 시계가 UTC여도 틀리지 않는다.
2. 오늘이 공휴일인지: `date.today() in holidays.KR()`. 공휴일이면 공휴일 운영시간을 쓴다.
3. AED별로 `is_open()`을 계산해 운영 중인 id 목록을 만든다. 정보 없음은 `config.yaml`의 정책을 따른다.
4. 캐시 키 = (요일, 현재 분 // 30, mode, 공휴일 여부). `coverage_cache`에 있으면 그대로 돌려주고, 없으면 운영 중 AED의 `isochrone`만 `ST_Union` → 격자 점 비율 계산 → 저장 후 돌려준다.
5. 발표용 비교 수치(평일 낮 14시 vs 평일 밤 22시)는 같은 함수에 시각만 넣어 미리 뽑아 둔다. 그래서 함수는 "현재 시각"을 인자로 받게 만든다.

### 4-7. 완료 기준

- [ ] `/docs`에서 7개 API가 모두 정상 응답
- [ ] 서울 밖 클릭, 후보 없음, 잘못된 제보 입력에서 안내 문구가 나옴 (500 오류 없음)
- [ ] `/api/nearest` 응답 시간을 10번 재서 평균 2초 이내임을 기록
- [ ] (선택) FastAPI `TestClient`로 주요 API 테스트 3개 이상

## 5단계. 프론트엔드 (바이브코딩)

프론트는 AI에게 맡기되, **API 약속과 디자인 가이드를 같이 주는 것**만 지키면 수정이 적다.

- **AI에게 줄 자료**: PRD 5장(화면 구성), 6장(디자인 가이드), 7-4(API 목록), 그리고 4단계에서 실제로 받은 **API 응답 예시 JSON**. 예시 응답이 있어야 AI가 필드 이름을 추측하지 않는다.
- **요청 예시**: "순수 HTML/CSS/JS와 Leaflet으로, 첨부한 화면 구성과 디자인 토큰을 따르는 한 페이지 지도 화면을 만들어 줘. API는 같은 주소의 `/api/...`를 fetch로 호출하고, 응답 형식은 첨부한 JSON과 같다. 빌드 도구는 쓰지 마."
- **API 주소는 상대 경로(`/api/aeds`)로 쓴다.** 나중에 Nginx가 화면과 API를 같은 주소에서 제공하므로, `http://localhost:8000` 같은 주소를 코드에 박으면 서버에서 안 된다.
- **개발 중 테스트**: 화면 파일을 FastAPI가 같이 제공하게 해 두면(`app.mount("/", StaticFiles(directory="web", html=True))`, API 라우터 등록 뒤에) 주소가 하나라 편하다. 6단계에서는 Nginx가 이 역할을 대신한다.
- **수정할 때 확인할 것**: 출처 표기(© OpenStreetMap contributors, AED 데이터 기준일), 상단 119 안내, 이모지 없음, HTTPS가 아닐 때 내 위치 버튼의 안내 문구.
- **완료 기준**: 지도 클릭 → 가장 가까운 AED와 경로 표시, 레이어 켜기/끄기, 통계 표, 실시간 반영 토글, 제보 제출이 모두 동작.

## 6단계. Docker Compose 통합 (둘 다)

지금까지 따로 띄운 것들(Valhalla, DB, FastAPI, 화면)을 `docker-compose.yml` 하나로 묶는다. 이 단계가 끝나면 어느 컴퓨터에서든 `docker compose up -d` 한 줄로 전체가 뜬다. **이 단계부터는 기능을 더 추가하지 않는다.**

### 6-1. 추가할 파일

```
aed-access/
├── docker-compose.yml
├── .env                  # 실제 비밀번호·API 키 (GitHub에 올리지 않음)
├── .env.example          # 같은 항목, 값만 비운 예시 (올림)
├── api/Dockerfile
├── pipeline/Dockerfile
└── nginx/default.conf    # web/ 안에 두면 화면 파일처럼 외부에 노출되므로 따로 둔다
```

```
# .env.example
POSTGRES_USER=aed
POSTGRES_PASSWORD=
POSTGRES_DB=aed
AED_API_KEY=
```

### 6-2. `docker-compose.yml`

```yaml
services:
  db:
    image: postgis/postgis:16-3.4
    env_file: .env
    volumes:
      - db-data:/var/lib/postgresql/data
      - ./db/init.sql:/docker-entrypoint-initdb.d/init.sql:ro
    ports:
      - "127.0.0.1:5432:5432"     # 내 컴퓨터에서만 접속 (QGIS용), 외부 비공개
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER}"]
      interval: 5s
      retries: 10

  valhalla:
    image: ghcr.io/valhalla/valhalla-scripted:<1단계에서 기록한 버전>
    environment:
      - server_threads=2
    volumes:
      - ./valhalla/custom_files:/custom_files

  api:
    build: ./api
    env_file: .env
    environment:
      - DATABASE_URL=postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}
      - VALHALLA_URL=http://valhalla:8002
    depends_on:
      db: { condition: service_healthy }
      valhalla: { condition: service_started }

  web:
    image: nginx:1.27-alpine
    ports:
      - "80:80"                   # 외부에 열리는 문은 이것 하나
    volumes:
      - ./web:/usr/share/nginx/html:ro
      - ./nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
    depends_on: [api]

  pipeline:                     # 평소에는 안 켜지고, 필요할 때만 실행
    build: ./pipeline
    profiles: ["pipeline"]
    env_file: .env
    environment:
      - DATABASE_URL=postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}
      - VALHALLA_URL=http://valhalla:8002
    volumes:
      - ./data:/app/data
      - ./results:/app/results
      - ./config.yaml:/app/config.yaml:ro
    depends_on:
      db: { condition: service_healthy }

volumes:
  db-data:
```

**알아야 할 개념**

- **서비스 이름 = 주소**: Compose 안에서는 컨테이너끼리 서비스 이름으로 부른다. 그래서 API 코드의 DB 주소는 `localhost`가 아니라 `db`, Valhalla는 `http://valhalla:8002`다. 코드는 그대로이고 환경변수만 다르게 주는 것이 포인트다.
- **포트**: 외부에 열리는 것은 `web`의 80번뿐이다. Valhalla와 API는 `ports`가 없어서 밖에서 직접 접근할 수 없다(PRD 8-6).
- **볼륨**: `db-data`는 컨테이너를 지워도 DB 내용이 남는 저장 공간이다. `./valhalla/custom_files` 같은 폴더 연결은 내 파일을 컨테이너와 공유한다.
- **버전 고정**: `latest` 대신 정확한 태그를 쓴다. 나중에 이미지가 업데이트돼도 같은 결과가 나온다.

### 6-3. Dockerfile과 Nginx 설정

```dockerfile
# api/Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./api
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```nginx
# nginx/default.conf
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;

    location /api/ {
        proxy_pass http://api:8000;        # /api/... 요청을 FastAPI로 전달
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    location / {
        try_files $uri $uri/ /index.html; # 나머지는 화면 파일
    }
}
```

- `pipeline/Dockerfile`도 같은 방식으로 만든다. 마지막 줄만 `CMD ["python", "pipeline/run_all.py"]`.
- FastAPI에 넣었던 `StaticFiles`(5단계 개발용)는 빼도 된다. 이제 Nginx가 화면을 주고 `/api/`만 FastAPI로 넘긴다.

### 6-4. 실행 순서

```bash
docker rm -f valhalla aed-db              # 1·2단계에서 따로 띄운 컨테이너 정리 (포트 충돌 방지)
cp .env.example .env && nano .env        # 비밀번호·API 키 입력
docker compose up -d --build             # 전체 실행
docker compose ps                        # 모두 running/healthy인지
docker compose logs -f api               # 오류 확인
docker compose run --rm pipeline         # 분석 파이프라인 실행 (처음 한 번 + 데이터 갱신 때)
```

- Compose가 만드는 DB 볼륨은 2단계의 `aed-db-data`와 다른 새 볼륨이다. 그래서 처음에는 파이프라인을 한 번 다시 돌려야 데이터가 찬다.
- 브라우저에서 `http://localhost`(포트 없이)로 화면이 뜨면 성공이다.

### 6-5. 완료 기준

- [ ] 팀원의 VM에서 `git clone` → `.env` 작성 → `docker compose up -d --build` → 파이프라인 실행만으로 화면이 뜨고 기능이 동작
- [ ] 이 명령 순서를 README의 "실행 방법"에 그대로 작성
- [ ] 모든 이미지 버전이 고정되어 있음 (`latest` 없음)

## 7단계. Linux 서버 배포 (담당 B)

6단계의 Compose 설정을 그대로 서버에 가져가고, 노트북에서 계산한 **DB 내용과 가공된 Valhalla 지도만** 옮겨서 켜는 단계다. 무거운 계산(지도 가공, 등시선 수천 개)은 서버에서 하지 않는다.

### 7-1. 서버 준비

- **서버**: 학교 실습 서버 또는 클라우드(AWS, Oracle Cloud, 네이버 클라우드 등)의 Ubuntu 서버. 권장 사양은 메모리 4GB, 디스크 20GB 이상이다. 무료 조건과 CPU 종류(ARM이면 이미지 지원 여부)는 가입 전에 확인한다.
- **접속**: `ssh 사용자@서버IP`. 클라우드는 보통 키 파일로 접속한다(`ssh -i 키파일.pem ...`).
- **설치**: 0단계와 같은 방법으로 Docker와 Git만 설치한다. Python, osmium은 서버에 필요 없다.
- **방화벽**: SSH(22)와 웹(80)만 연다. 클라우드는 콘솔의 보안 그룹(인바운드 규칙)에서도 80을 열어야 한다.

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw enable
```

- 참고: Docker로 공개한 포트는 ufw 규칙을 건너뛸 수 있다. 그래서 6단계에서 DB는 `127.0.0.1:5432`에만 묶고, Valhalla·API는 포트를 아예 공개하지 않았다. 이 설정을 바꾸지 않는다.
- `docker-compose.yml`의 각 서비스에 `restart: unless-stopped`를 추가해 두면 서버가 재부팅돼도 자동으로 다시 켜진다.

### 7-2. 코드와 데이터 옮기기

```bash
# [서버] 코드 받기, .env 작성 (비밀번호는 연습용과 다르게)
git clone https://github.com/<팀>/aed-access.git && cd aed-access
cp .env.example .env && nano .env

# [노트북] ① 가공된 Valhalla 지도 폴더 통째로 복사
scp -r valhalla/custom_files 사용자@서버IP:~/aed-access/valhalla/

# [노트북] ② DB 내용을 파일 하나로 뽑아서 복사
docker compose exec -T db pg_dump -U aed -d aed -Fc > aed.dump
scp aed.dump 사용자@서버IP:~/aed-access/

# [서버] ③ DB만 먼저 켜고 내용 복원 → 전체 실행
docker compose up -d db
docker compose exec -T db pg_restore -U aed -d aed --clean --if-exists < aed.dump
docker compose up -d --build
```

- ① Valhalla는 폴더에 가공된 파일(`valhalla_tiles.tar`)이 있으면 다시 가공하지 않고 그것을 쓴다. 그래서 메모리가 작은 서버에서도 돌아간다.
- ② `pg_dump -Fc`는 DB 전체(테이블 구조 + 데이터)를 압축 파일 하나로 만든다. `-T`는 파일을 주고받을 때 필요한 옵션이다.
- ③ `--clean --if-exists`는 서버 DB에 이미 있는 테이블을 지우고 노트북 내용으로 바꾼다.
- `aed.dump`와 `custom_files`는 GitHub에 올리지 않는다(용량, 재생성 가능).

### 7-3. 확인과 업데이트

- 다른 사람 컴퓨터나 휴대폰 브라우저에서 `http://서버IP`로 접속해 지도 클릭까지 되는지 확인한다.
- **코드를 고쳤을 때**: 노트북에서 PR → main에 합침 → 서버에서 `git pull && docker compose up -d --build`
- **데이터를 갱신했을 때**: 노트북에서 파이프라인 실행 → 7-2의 ②·③만 다시 한다.

### 7-4. (선택) 도메인 + HTTPS

시간이 남을 때만 한다. 하면 "내 위치" 버튼이 서버에서도 동작한다.

1. 도메인: 구매하거나, 무료로 `서버IP를-대시로.sslip.io`(예: `1-2-3-4.sslip.io`)를 쓴다.
2. 인증서: `web`을 잠시 끄고 서버에서 `sudo certbot certonly --standalone -d 도메인`으로 Let's Encrypt 인증서를 받는다.
3. Nginx: `/etc/letsencrypt`를 `web` 컨테이너에 연결하고, `default.conf`에 443 서버 블록(`ssl_certificate` 경로 지정)과 80→443 이동을 추가한다. 방화벽에서 443도 연다.
4. 인증서는 90일마다 갱신해야 한다. 학기 안에는 갱신 없이도 충분하다.

### 7-5. 완료 기준

- [ ] 외부에서 `http://서버IP`로 접속해 모든 기능 동작
- [ ] 서버에서 DB(5432)와 Valhalla(8002)가 외부에서 접속되지 않음을 확인
- [ ] 서버 재부팅 후 자동으로 다시 켜짐
- [ ] 배포 절차를 README의 "배포 방법"에 기록

## 8단계. 검증과 문서화 (둘 다)

과목 평가는 "돌아간다"보다 \*\*"숫자로 확인했고, 다른 사람도 실행할 수 있다"\*\*를 본다. 그래서 마지막 주는 새 기능 없이 측정과 기록만 한다.

### 8-1. 측정할 지표

| 지표 | 어디서 나오나 | 보고서에 넣을 형태 |
| --- | --- | --- |
| 직선거리 vs 실보행 도달 가능 비율 차이(%p), 서울·자치구별 | 06\_metrics | 자치구별 막대그래프 + 서울 전체 수치 |
| 편도 4분 vs 왕복 기준 도달 가능 비율 | 06\_metrics (mode별) | 비교 그래프 |
| 평일 낮 vs 밤 도달 가능 비율 | 4-6의 시각 지정 함수 | 두 시각 지도 캡처 + 수치 |
| 자동 판정 정확성 | QGIS 수동 확인 20개 | 일치 개수/20 |
| 데이터 품질 | cleaning\_report, isochrone\_failed | 제거·실패 건수 표 |
| 응답 시간 | `/api/nearest` 10회 측정 | 평균·최대(초) |
| 실행 환경·소요 시간 | 1·3단계 기록 | VM 사양, 지도 가공·등시선 계산 시간 |

### 8-2. README 체크리스트

- [ ] 프로젝트 한 줄 소개, 아키텍처 그림, 화면 캡처
- [ ] 사용한 오픈소스와 **라이선스 표** (PRD 8-4 그대로)
- [ ] 데이터 출처와 기준일: OSM(© OpenStreetMap contributors, ODbL), AED API(국립중앙의료원), 자치구 경계
- [ ] 실행 방법: 준비물 → 서울 지도 자르기 → `.env` → `docker compose up` → 파이프라인 실행
- [ ] 배포 방법 (7단계)
- [ ] 분석 조건(`config.yaml`)과 결과 요약(`results/`)
- [ ] 한계: OSM 보행로 완성도, 건물 내부 이동 미반영, AED 데이터 오류 가능성, 119를 대신하지 않음
- [ ] 팀원별 역할

### 8-3. GitHub 기록 점검

- [ ] 두 사람 모두 커밋·PR·리뷰가 남아 있음 (Insights → Contributors에서 확인)
- [ ] 모든 PR이 Issue와 연결되어 있고, 리뷰 댓글과 실행 결과가 남아 있음
- [ ] 주요 오류 Issue가 "증상 → 원인 → 해결" 형식으로 3개 이상 (예: 안도라 타일 잔존 171 오류, 메모리 부족, 인증키 인코딩)
- [ ] 단계별 annotated tag가 있음: `v0.1.0`(재현 + DB) → `v0.2.0`(분석 파이프라인) → `v0.3.0`(API + 화면) → `v1.0.0`(Compose·배포·문서, 최종 제출)

```bash
git switch main && git pull --ff-only origin main
git tag -a v1.0.0 -m "Final submission: analysis pipeline, API, web, deployment"
git show v1.0.0 --stat      # 태그가 가리키는 커밋 확인
git push origin v1.0.0
```

- tag는 새 커밋을 만들지 않고 특정 커밋에 버전 이름을 붙인다. GitHub의 Releases에서 이 tag로 릴리스를 만들고 변경 요약을 적어 두면 발표에서 "어느 버전을 제출했는가"를 바로 보여줄 수 있다.

### 8-4. 발표 시연 준비

- 시연 순서: 레이어 탭(직선 vs 실보행) → 통계 탭(자치구 순위) → 실시간 반영 ON → 지도 클릭(가장 가까운 AED)
- 서버가 안 될 때를 대비해 노트북 VM에서도 같은 시연을 할 수 있게 해 두고, 3\~5분 시연 영상도 미리 녹화해 둔다.

## 막혔을 때

### 자주 나는 문제

| 증상 | 원인 | 해결 |
| --- | --- | --- |
| Valhalla `error_code 171` (No suitable edges) | 다른 지도(예: 안도라) 타일이 남아 있거나, 좌표가 길에서 너무 멀거나, lat/lon이 뒤바뀜 | `custom_files`에 서울 파일만 두고 새로 가공. 좌표는 lat=37, lon=126 |
| Valhalla 가공 중 컨테이너 종료 | 메모리 부족 | `server_threads=1`, VM 메모리 증가 |
| `port is already allocated` | 같은 포트를 다른 컨테이너가 사용 중 | `docker ps`로 찾아 `docker rm -f` |
| `permission denied ... docker.sock` | docker 그룹 추가 후 재로그인 안 함 | 로그아웃 후 다시 로그인 |
| `init.sql`을 고쳤는데 테이블이 안 바뀜 | 초기화 SQL은 빈 DB에서만 실행 | DB 볼륨 삭제 후 다시 실행 (데이터 삭제됨) |
| 공공데이터 API 인증 오류 | Encoding 키를 `params`에 넣어 이중 인코딩 | Decoding 키 사용 |
| API 컨테이너가 DB에 연결 못 함 | 주소가 `localhost`로 되어 있음 | Compose 안에서는 `db`, `valhalla` 서비스 이름 사용 |
| 화면은 뜨는데 지도 데이터가 안 나옴 | 프론트가 `http://localhost:8000`을 호출 | API 주소를 `/api/...` 상대 경로로 |
| 그래프 한글이 □로 나옴 | 한글 글꼴 없음 | `fonts-nanum` 설치 후 matplotlib 글꼴 지정 |
| 등시선 계산이 너무 오래 걸림 | AED 수천 개를 한 번에 | `test_gu`로 한 구만 먼저, 이어서 계산 기능 활용 |

**Git에서 막혔을 때** (수업 자료의 문제 해결 순서)

- 진단 순서: `pwd` → `git status` → `git branch -avv` → `git log --oneline --graph --all`. 상태를 확인하기 전에 `git reset --hard`를 쓰지 않는다.
- `non-fast-forward`로 push가 거절됨 → 원격에 팀원의 새 커밋이 있다. `git fetch` 후 `git log`로 확인하고 합친다.
- `merge conflict` → 같은 줄을 두 사람이 고쳤다. `git status`로 충돌 파일을 찾고 `<<<<<<<` 표시 부분을 팀원과 상의해 고친 뒤 커밋한다.
- `nothing to commit` → 변경이 없거나 `git add`를 빠뜨렸다. `git status`, `git diff`로 확인한다.

### AI에게 질문하는 법

AI 도움을 받을 때 아래 네 가지를 함께 주면 헛도는 답이 크게 줄어든다.

1. **지금 단계와 목표**: "3단계 04\_isochrone.py를 만드는 중이고, `aed` 테이블의 AED마다 Valhalla 등시선을 받아 `isochrone` 테이블에 저장하려 한다."
2. **약속된 구조**: 해당 테이블의 `CREATE TABLE` 문, `config.yaml`, 실제 API 응답 예시 일부
3. **현재 코드와 전체 오류 메시지** (오류는 마지막 몇 줄만 자르지 말고 전부)
4. **제약**: "이 문서의 폴더 구조와 테이블은 바꾸지 말고, 새 라이브러리를 추가하기 전에 먼저 물어봐줘."

- AI가 준 코드는 **한 줄씩 설명해 달라고 해서 이해한 뒤** 커밋한다. 발표에서 "이 부분은 어떻게 동작하나요?"라는 질문에 답할 수 있어야 한다.
- 한 번에 파일 여러 개를 통째로 만들게 하지 말고, 이 문서의 단계 단위로 요청한다. 단계마다 완료 기준을 확인하고 다음으로 넘어간다.

## README 작성 방법

README에는 모두가 따르는 단 하나의 공식 규격은 없다. 대신 널리 쓰이는 기준 세 가지가 있고, 내용이 거의 같다. 이 세 기준을 합치고 과목 요구사항(라이선스, 실행 방법, 재현성)을 더해 우리 프로젝트용 틀을 만들었다.

### 범용 기준 3가지

| 기준 | 핵심 내용 |
| --- | --- |
| [GitHub Docs — About READMEs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes) | README가 답해야 할 것: 무엇을 하는지, 왜 유용한지, 어떻게 시작하는지, 어디서 도움을 받는지, 누가 관리하는지 |
| [Standard Readme 명세](https://github.com/RichardLitt/standard-readme/blob/main/spec.md) | 섹션 순서를 정해 둔 명세. **필수**: 제목, 짧은 설명, 목차(100줄 미만이면 생략 가능), 설치, 사용법, 기여 방법, 라이선스(맨 마지막). 나머지(배지, 배경, API, 관리자 등)는 선택 |
| [Make a README](https://www.makeareadme.com/) | 섹션별 작성 요령: 설명에 다른 프로젝트와의 차이, 스크린샷·GIF, 초보자도 따라 할 수 있는 단계별 설치와 요구사항, 기대 출력이 있는 사용 예시, 프로젝트 상태 |

### 레퍼런스 저장소

[awesome-readme](https://github.com/matiassingers/awesome-readme)(별 약 2.1만 개)는 잘 쓴 README를 모아 둔 목록이다. 이 중 우리 프로젝트에 참고할 만한 것:

| 저장소 | 참고할 점 |
| --- | --- |
| [dbt-labs/dbt-core](https://github.com/dbt-labs/dbt-core) | 처음 보는 사람도 이해하는 설명 + 화면 캡처 |
| [gofiber/fiber](https://github.com/gofiber/fiber) | 측정 결과를 그래프로 제시, 잘 정리된 코드 예시 |
| [ma-shamshiri/Human-Activity-Recognition](https://github.com/ma-shamshiri/Human-Activity-Recognition) | 목차, 배너, 폴더 구조 설명. 학생 프로젝트 규모에 가까움 |
| [dutrevis/spark-resources-metrics-plugin](https://github.com/dutrevis/spark-resources-metrics-plugin) | Mermaid로 그린 아키텍처 다이어그램, 접히는 설치·사용법 섹션 |

### 우리 프로젝트용 README 틀

아래를 `README.md`에 복사해서 채운다. 상단 세 줄(무엇을·왜·어떻게)만 읽어도 프로젝트를 알 수 있게 쓰는 것이 핵심이다.

````markdown
# 실보행 경로 기반 AED 접근성 분석 시스템

서울시 AED까지 실제로 걸어서 4분 안에 닿을 수 있는지를 실제 보행 경로와 운영시간으로 분석해,
AED 사각지대와 가장 빨리 닿는 AED까지의 경로를 지도로 보여주는 웹서비스입니다.

![화면 캡처](docs/screenshot.png)

> 이 서비스는 119 신고를 대신하지 않는 분석·참고용 도구입니다.

## 목차
- [배경](#배경) · [주요 기능](#주요-기능) · [시스템 구조](#시스템-구조) · [설치](#설치)
- [실행](#실행) · [분석 결과](#분석-결과) · [한계](#한계) · [팀](#팀) · [라이선스](#라이선스)

## 배경
직선거리 기반 분석의 한계와 이 프로젝트가 다른 점 (2~3문장)

## 주요 기능
- 가장 가까운 AED 도보 경로 안내
- 자치구별 도달 가능 비율과 사각지대 지도
- 직선거리 vs 실보행 비교
- 운영시간 기반 실시간 반영, AED 위치 오류 제보

## 시스템 구조
![아키텍처](docs/architecture.png)

| 구분 | 사용 오픈소스 | 역할 |
| --- | --- | --- |
| 경로 탐색 | Valhalla | 등시선·도보 경로 계산 |
| ... | ... | ... |

### 폴더 구조
(0-2의 폴더 트리)

## 설치
### 요구사항
- Ubuntu 24.04, Docker + Docker Compose, osmium-tool
- 메모리 8GB 이상 권장

### 준비
1. 저장소 받기: `git clone ...`
2. 서울 지도 준비: (1-1의 명령)
3. 환경변수: `cp .env.example .env` 후 API 키 입력

## 실행
```bash
docker compose up -d --build
docker compose run --rm pipeline
```
브라우저에서 http://localhost 접속. 배포 방법은 [docs/deploy.md](docs/deploy.md)

## 분석 결과
분석 조건(config.yaml)과 핵심 수치, 그래프 (results/)

## 한계
OSM 보행로 완성도, 건물 내부 이동 미반영, AED 데이터 오류 가능성

## 팀
| 이름 | 역할 |
| --- | --- |
| ○○○ | 데이터·분석 |
| ○○○ | 서비스·배포 |

## 기여 방법
브랜치·커밋·PR 규칙 (CONTRIBUTING.md 또는 docs/commit-convention.md 참고)

## 데이터 출처
- © OpenStreetMap contributors (ODbL 1.0), 기준일 YYYY-MM-DD
- 국립중앙의료원 전국 AED 정보 조회 서비스 (공공데이터포털), 수집일 YYYY-MM-DD

## 라이선스
이 저장소는 MIT 라이선스를 따릅니다. 사용한 오픈소스의 라이선스는 아래와 같습니다.
(PRD 8-4 라이선스 표)
````

### 작성 팁

- **처음부터 완성하려 하지 않는다.** 0단계에서는 제목과 한 줄 설명만 쓰고, 단계를 끝낼 때마다 그 단계의 내용(설치 명령, 실행 방법)을 채운다.
- **명령어는 직접 복사해서 실행해 본 것만** 적는다. 팀원이 README만 보고 따라 해 보면 빠진 단계가 드러난다(6단계 완료 기준).
- 라이선스는 Standard Readme처럼 **맨 마지막 섹션**에 둔다. 우리 저장소의 라이선스와 사용한 오픈소스의 라이선스를 함께 적는다.
- 긴 내용(배포 절차, 오류 해결 기록)은 `docs/`에 따로 두고 README에서는 링크만 건다.

## 커밋 메시지 규칙

수업 자료(Git 3주차 "브랜치와 커밋 이름에 작업의 의도를 담자")의 형식을 그대로 따르고, 그 형식의 바탕인 [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/)과 [좋은 커밋 메시지 7가지 규칙](https://cbea.ms/git-commit/)으로 빈 부분을 채웠다. 두 사람이 같은 틀로 쓰면 코드를 보지 않아도 커밋 목록만으로 작업 맥락이 보인다.

### 형식

```
<type>(<scope>): <summary>

<body: what and why (optional)>

<footer: Closes #12 / Refs: #12 (optional)>
```

**제목 줄 규칙**

1. **영어, 동사·행동 중심**으로 쓴다(수업 원칙). 동사 원형으로 시작하는 명령문: `add`, `fix`, `support`, `handle`, `remove`, `update`.
2. type과 요약은 모두 소문자로 시작하고, 끝에 마침표를 찍지 않는다. 콜론 뒤에 한 칸 띄운다.
3. 제목은 50자 안팎으로 짧게 쓴다.
4. 커밋 하나는 한 가지 의도만 담는다. "운영시간 파서 추가"와 "README 오타 수정"은 따로 커밋한다.
5. `update`, `changes`, `final`, `test123`처럼 의도가 없는 메시지는 쓰지 않는다(수업의 "피할 예").
6. 본문은 필요할 때만 쓰고, 제목과 한 줄 띄운 뒤 **무엇을·왜** 바꿨는지를 적는다. 본문은 한국어로 써도 된다.

### 타입

| 타입 | 언제 | 예시 |
| --- | --- | --- |
| `feat` | 새 기능 | `feat(api): add nearest AED route endpoint` |
| `fix` | 버그 수정 | `fix(pipeline): handle 2400 as midnight` |
| `docs` | 문서만 변경 (README, docs/) | `docs: add Valhalla setup guide` |
| `test` | 테스트 추가·수정 | `test(pipeline): add overnight hours cases` |
| `refactor` | 동작은 그대로, 코드 구조 개선 | `refactor(api): move db access to db.py` |
| `build` | Docker, 의존성(requirements) | `build(docker): pin valhalla image version` |
| `style` | 동작과 무관한 형식(들여쓰기, 공백) | `style(web): format css` |
| `chore` | 그 밖의 설정·잡일 | `chore: ignore data directory` |

- 수업 자료에 나온 예(`chore: initial app`, `docs: define greeting issue`, `feat: support greeting name`, `docs: add team workflow`)와 같은 형식이다.
- `feat`, `fix`는 Conventional Commits의 필수 타입이고, 나머지는 명세가 권장하는 Angular 규칙 기반 목록에서 골랐다. 목록 밖 타입은 만들지 않는다.
- 기존 동작을 깨뜨리는 변경(예: API 응답 형식 변경)은 타입 뒤에 `!`를 붙이고 본문에 이유를 쓴다: `feat(api)!: return route as GeoJSON`

### 범위 (선택, 폴더와 맞춤)

| 범위 | 대상 |
| --- | --- |
| `pipeline` | 분석 스크립트 (`pipeline/`, `config.yaml`) |
| `db` | 테이블 설계 (`db/init.sql`) |
| `api` | FastAPI (`api/`) |
| `web` | 화면 (`web/`) |
| `valhalla` | 지도 준비·Valhalla 설정 |
| `docker` | `docker-compose.yml`, Dockerfile, Nginx 설정 |
| `deploy` | 서버 배포 |

범위는 수업 형식(`feat: ...`)에 더해 어느 부분을 고쳤는지 보여 주는 용도다. 저장소 전체에 해당하면 생략한다(`docs: add install steps to README`).

### 예시 (본문 + 꼬리말)

```
fix(valhalla): use Seoul-only tile folder

연습용 custom_files에 안도라 valhalla_tiles.tar가 남아 있어
use_tiles_ignore_pbf 기본값(True)에 따라 서울 pbf가 무시됨.
서울 전용 폴더를 만들고 README에 주의사항을 추가함.

Closes #4
```

- `Closes #4`는 이 커밋(또는 PR)이 main에 합쳐질 때 Issue #4를 자동으로 닫는다. 연결만 할 때는 `Refs: #4`.
- 두 사람이 같이 작업한 커밋은 마지막 줄에 `Co-authored-by: 이름 <GitHub 이메일>`을 넣으면 두 사람 모두 기여자로 표시된다.

### 브랜치·PR·병합도 같은 틀로

| 항목 | 규칙 | 예 | 피할 예 (수업) |
| --- | --- | --- | --- |
| 기능 브랜치 | `feature/<주제>` | `feature/aed-collect`, `feature/nearest-api` | `new`, `test123` |
| 수정 브랜치 | `fix/<주제>` | `fix/hours-2400` | `final` |
| 문서 브랜치 | `docs/<주제>` | `docs/readme-install` | `changes` |
| PR 제목 | 커밋 제목과 같은 형식 | `feat(pipeline): add AED collector` | `작업함` |
| 병합 | 리뷰 승인 후 "Create a merge commit" (= `git merge --no-ff`) | 그래프에 기능 통합 기록이 남음 | 리뷰 없이 자기 PR 병합 |

- 병합 커밋을 남기는 방식은 수업 실습 B-4(`--no-ff`)와 같다. `git log --oneline --graph --all`에서 기능 브랜치가 언제 통합됐는지 보여서 Git 협업 기록을 설명하기 좋다.

### 규칙을 잊지 않게: 커밋 템플릿

저장소의 `.gitmessage`를 각자 한 번만 설정하면, 메시지 없이 `git commit` 할 때마다 틀이 편집기에 뜬다. `#`으로 시작하는 줄은 커밋에 포함되지 않는다.

```
# <type>(<scope>): <summary>   영어 동사 원형, 소문자, 50자 안팎, 마침표 없음
# type : feat fix docs test refactor build style chore
# scope: pipeline db api web valhalla docker deploy (전체면 생략)

# body : 무엇을, 왜 바꿨는지 (한국어 가능)

# footer: Closes #번호 / Refs: #번호
```

```bash
git config commit.template .gitmessage.txt   # 각자 저장소 폴더에서 한 번
```

- 이 섹션과 0-3을 `CONTRIBUTING.md`에 옮겨 두면 README의 "기여 방법"에서 링크할 수 있고, 발표 때 "협업 규칙을 문서화했다"는 근거가 된다.

## 참고 자료

- [Valhalla Docker README](https://github.com/valhalla/valhalla/blob/master/docker/README.md) — 이미지 종류, 환경변수(`server_threads`, `use_tiles_ignore_pbf`, `force_rebuild`), `/custom_files` 구조
- [Geofabrik South Korea](https://download.geofabrik.de/asia/south-korea.html) — 한국 OSM 파일, ODbL 1.0
- [국립중앙의료원\_전국 자동심장충격기(AED) 정보 조회 서비스](https://www.data.go.kr/data/15000652/openapi.do) — 요청 주소·파라미터·응답 필드는 이 페이지에서 확인
- 프로젝트 PRD — 기능 ID(P-01, S-04 등), 테이블(7-5), API 목록(7-4), 디자인 가이드(6장)
- [GitHub Docs — About READMEs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes)
- [Standard Readme 명세](https://github.com/RichardLitt/standard-readme/blob/main/spec.md)
- [Make a README](https://www.makeareadme.com/)
- [awesome-readme](https://github.com/matiassingers/awesome-readme)
- [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/)
- [How to Write a Git Commit Message (Chris Beams)](https://cbea.ms/git-commit/)
- 수업 자료: 2026 오픈소스기초설계 Git 3주차 "GitHub 오픈소스 협업과 릴리스" — Issue·브랜치·커밋 이름 규칙, PR·리뷰 체크리스트, `--no-ff` 병합, annotated tag, 팀 규칙 5가지
