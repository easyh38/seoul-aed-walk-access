CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE district ( -- 자치구 경계 25개
    name TEXT PRIMARY KEY,
    geom geometry(MultiPolygon, 4326) -- SGIS SHP(EPSG:5179)는 to_crs(4326) / ST_Transform(geom, 4326) 후 적재
);

CREATE TABLE aed ( -- 정제된 AED
    id SERIAL PRIMARY KEY,
    source_id TEXT UNIQUE, -- API 원본의 고유번호
    place TEXT, -- 설치 장소
    address TEXT,
    gu TEXT REFERENCES district(name),
    hours JSONB, -- 요일별 운영시간 {"mon":["0900","1800"], ..., "sun":[...], "hol":[...]} (hol: 공휴일, S-06용)
    geom geometry(Point, 4326)
);
CREATE INDEX aed_geom_idx ON aed USING GIST (geom);

CREATE TABLE isochrone ( -- AED별 도달 구역
    aed_id INT REFERENCES aed(id) ON DELETE CASCADE,
    minutes INT CHECK (minutes IN (2, 4)), -- 4 또는 2
    geom geometry(MultiPolygon, 4326), -- Valhalla Polygon은 ST_Multi(...)로 감싸서 저장
    PRIMARY KEY (aed_id, minutes)
);
CREATE INDEX iso_geom_idx ON isochrone USING GIST (geom);

CREATE TABLE grid_point ( -- 100m 격자 평가 지점
    id BIGSERIAL PRIMARY KEY,
    gu TEXT REFERENCES district(name),
    geom geometry(Point, 4326),
    straight_4 BOOLEAN,
    walk_4 BOOLEAN,
    straight_2 BOOLEAN,
    walk_2 BOOLEAN
);
CREATE INDEX grid_geom_idx ON grid_point USING GIST (geom);

CREATE TABLE pipeline_run ( -- 실행 기록
    id SERIAL PRIMARY KEY,
    run_at TIMESTAMPTZ DEFAULT now(),
    config JSONB, -- config.yaml 내용 그대로
    osm_date DATE,
    aed_date DATE
);

CREATE TABLE metrics ( -- 지표 (서울 전체 + 자치구별)
    run_id INT REFERENCES pipeline_run(id),
    scope TEXT, -- '서울' 또는 자치구 이름
    mode TEXT, -- 'oneway' / 'roundtrip'
    straight_pct NUMERIC, walk_pct NUMERIC, diff_pp NUMERIC,
    deadzone_km2 NUMERIC, aed_count INT,
    PRIMARY KEY (run_id, scope, mode) -- 재실행 시 중복 방지
);

CREATE TABLE layer ( -- 화면용으로 미리 합친 다각형
    mode TEXT, kind TEXT, -- kind: 'coverage' / 'deadzone'
    geom geometry(MultiPolygon, 4326),
    PRIMARY KEY (mode, kind)
);

CREATE TABLE coverage_cache ( -- 실시간 반영 30분 캐시
    weekday INT, slot INT, mode TEXT, is_holiday BOOLEAN,
    coverage geometry(MultiPolygon, 4326),
    walk_pct NUMERIC,
    PRIMARY KEY (weekday, slot, mode, is_holiday)
);

CREATE TABLE report ( -- 오류 제보 (개인정보 칼럼 없음)
    id SERIAL PRIMARY KEY,
    aed_id INT REFERENCES aed(id),
    type TEXT CHECK (type IN ('wrong_location','missing','unusable')),
    description TEXT CHECK (char_length(description) <= 500),
    created_at TIMESTAMPTZ DEFAULT now(),
    status TEXT DEFAULT '접수'
);

-- EPSG:5179 직선거리 검색용 공간 인덱스
CREATE INDEX aed_geom_5179_idx
    ON aed USING GIST (ST_Transform(geom, 5179));
