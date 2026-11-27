-- coverage_cache에 운영 중 AED 집합 해시 칼럼 추가 (#23 open_now)
-- init.sql로 새로 만든 DB에는 이미 있다. 기존 DB에만 한 번 실행한다 (여러 번 실행해도 안전).
--   docker exec -i aed-db psql -U aed -d aed < db/migrations/001_coverage_cache_aed_set_hash.sql
ALTER TABLE coverage_cache ADD COLUMN IF NOT EXISTS aed_set_hash TEXT;

-- 해시 없이 저장된 예전 캐시는 쓰지 않도록 비운다
DELETE FROM coverage_cache WHERE aed_set_hash IS NULL;
