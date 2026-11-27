-- aed에 좌표 출처·위치 의심 표시 칼럼 추가 (02_refine_location)
-- init.sql로 새로 만든 DB에는 이미 있다. 기존 DB에만 한 번 실행한다 (여러 번 실행해도 안전).
--   docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < db/migrations/002_aed_location_quality.sql
-- 실행 뒤 파이프라인을 refine 단계부터 다시 돌린다: python pipeline/run_all.py --from refine
ALTER TABLE aed ADD COLUMN IF NOT EXISTS location_source TEXT NOT NULL DEFAULT 'api';
ALTER TABLE aed ADD COLUMN IF NOT EXISTS location_flags TEXT[] NOT NULL DEFAULT '{}';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'aed_location_source_check'
    ) THEN
        ALTER TABLE aed ADD CONSTRAINT aed_location_source_check
            CHECK (location_source IN ('api', 'osm_building'));
    END IF;
END $$;
