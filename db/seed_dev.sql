-- 개발용 가짜 데이터 (3단계 파이프라인 결과가 나오기 전 API 개발용)
-- 실행: docker exec -i aed-db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < db/seed_dev.sql
-- 여러 번 실행해도 중복이 쌓이지 않는다 (source_id 기준 덮어쓰기, pipeline_run은 비어 있을 때만 추가)
-- 진짜 데이터는 3단계 03_load가 넣는다. 가짜 AED는 source_id가 'TEST'로 시작한다.

BEGIN;

-- 1. 서울시청 근처 AED 5개
--    운영시간은 open_now(4단계 7번) 테스트를 위해 일부러 다양하게 넣는다
--    (24시간 / 평일 낮만 / 주말 다름 / 정보 없음)
INSERT INTO aed (source_id, place, address, hours, geom) VALUES
('TEST1', '테스트 AED (서울시청 1층)', '서울특별시 중구 세종대로 110',
 '{"mon":["0000","2400"],"tue":["0000","2400"],"wed":["0000","2400"],"thu":["0000","2400"],"fri":["0000","2400"],"sat":["0000","2400"],"sun":["0000","2400"],"hol":["0000","2400"]}',
 ST_SetSRID(ST_MakePoint(126.9779, 37.5663), 4326)),

('TEST2', '테스트 AED (시청역 2호선 대합실)', '서울특별시 중구 세종대로 지하 101',
 '{"mon":["0530","2400"],"tue":["0530","2400"],"wed":["0530","2400"],"thu":["0530","2400"],"fri":["0530","2400"],"sat":["0530","2400"],"sun":["0530","2300"],"hol":["0530","2300"]}',
 ST_SetSRID(ST_MakePoint(126.9770, 37.5657), 4326)),

('TEST3', '테스트 AED (덕수궁 매표소)', '서울특별시 중구 세종대로 99',
 '{"mon":null,"tue":["0900","2100"],"wed":["0900","2100"],"thu":["0900","2100"],"fri":["0900","2100"],"sat":["0900","2100"],"sun":["0900","2100"],"hol":["0900","2100"]}',
 ST_SetSRID(ST_MakePoint(126.9752, 37.5658), 4326)),

('TEST4', '테스트 AED (광화문역 5호선)', '서울특별시 종로구 세종대로 지하 172',
 '{"mon":["0900","1800"],"tue":["0900","1800"],"wed":["0900","1800"],"thu":["0900","1800"],"fri":["0900","1800"],"sat":null,"sun":null,"hol":null}',
 ST_SetSRID(ST_MakePoint(126.9768, 37.5710), 4326)),

('TEST5', '테스트 AED (을지로입구역)', '서울특별시 중구 을지로 지하 42',
 NULL,  -- 운영시간 정보 없음 (config의 정보 없음 정책 테스트용)
 ST_SetSRID(ST_MakePoint(126.9826, 37.5660), 4326))
ON CONFLICT (source_id) DO UPDATE
SET place = EXCLUDED.place,
    address = EXCLUDED.address,
    hours = EXCLUDED.hours,
    geom = EXCLUDED.geom;

-- 2. 자치구 지정 (03_load와 같은 방식)
UPDATE aed a SET gu = d.name
FROM district d
WHERE a.source_id LIKE 'TEST%' AND ST_Contains(d.geom, a.geom);

-- 3. 파이프라인 실행 기록 1줄 (/api/meta가 읽음) — 비어 있을 때만
INSERT INTO pipeline_run (config, osm_date, aed_date)
SELECT
  '{"note": "개발용 가짜 실행 기록",
    "valhalla": {"costing": "pedestrian", "walking_speed_kmh": 5.1},
    "analysis": {"contours_minutes": [2, 4]}}',
  DATE '2026-11-01',
  DATE '2026-11-10'
WHERE NOT EXISTS (SELECT 1 FROM pipeline_run);

COMMIT;

-- 확인
SELECT id, source_id, place, gu, hours IS NOT NULL AS has_hours FROM aed ORDER BY id;
SELECT id, run_at, osm_date, aed_date FROM pipeline_run;
