-- ============================================================
-- PATCH SQL — Run this if your opportunities table already exists
-- Safely adds only the columns/tables that are missing
-- ============================================================

-- ── 1. Add missing columns to existing opportunities table ───
ALTER TABLE public.opportunities
  ADD COLUMN IF NOT EXISTS source_url   TEXT UNIQUE,
  ADD COLUMN IF NOT EXISTS scraped_at   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS short_description TEXT,
  ADD COLUMN IF NOT EXISTS department   TEXT,
  ADD COLUMN IF NOT EXISTS min_qualification TEXT DEFAULT 'any',
  ADD COLUMN IF NOT EXISTS qualifications   TEXT[] DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS gender_eligibility TEXT DEFAULT 'any',
  ADD COLUMN IF NOT EXISTS eligible_states   TEXT[] DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS eligible_categories TEXT[] DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS required_documents  TEXT[] DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS meta_title        TEXT,
  ADD COLUMN IF NOT EXISTS meta_description  TEXT,
  ADD COLUMN IF NOT EXISTS admit_card_date   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS notification_date DATE;

-- ── 2. Add missing indexes ────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_opp_scraped
  ON public.opportunities (scraped_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_opp_featured
  ON public.opportunities (is_featured) WHERE is_featured = true;

-- ── 3. Create ENUMs that may be missing ──────────────────────
DO $$ BEGIN
  CREATE TYPE opportunity_category AS ENUM (
    'latest_job', 'result', 'admit_card', 'answer_key', 'admission', 'syllabus'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE opportunity_status AS ENUM (
    'upcoming', 'open', 'closed', 'result_declared', 'cancelled'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE link_type AS ENUM (
    'apply_online', 'notification', 'admit_card', 'result',
    'answer_key', 'syllabus', 'official_website', 'download', 'other'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── 4. Create functions ───────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := NOW();
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION opportunities_search_trigger()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.search_vector :=
    setweight(to_tsvector('english', COALESCE(NEW.title, '')), 'A') ||
    setweight(to_tsvector('english', COALESCE(NEW.organization, '')), 'A') ||
    setweight(to_tsvector('english', COALESCE(NEW.short_description, '')), 'B') ||
    setweight(to_tsvector('english', COALESCE(NEW.description, '')), 'C') ||
    setweight(to_tsvector('english', array_to_string(COALESCE(NEW.tags, '{}'), ' ')), 'B');
  RETURN NEW;
END;
$$;

-- ── 5. Create triggers ────────────────────────────────────────
DROP TRIGGER IF EXISTS trg_opp_search  ON public.opportunities;
DROP TRIGGER IF EXISTS trg_opp_updated ON public.opportunities;

CREATE TRIGGER trg_opp_search
  BEFORE INSERT OR UPDATE ON public.opportunities
  FOR EACH ROW EXECUTE FUNCTION opportunities_search_trigger();

CREATE TRIGGER trg_opp_updated
  BEFORE UPDATE ON public.opportunities
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ── 6. Create opportunity_links table ────────────────────────
CREATE TABLE IF NOT EXISTS public.opportunity_links (
  id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id UUID        NOT NULL REFERENCES public.opportunities(id) ON DELETE CASCADE,
  label          TEXT        NOT NULL,
  url            TEXT        NOT NULL,
  type           link_type   NOT NULL DEFAULT 'other',
  sort_order     INTEGER     NOT NULL DEFAULT 0,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_opp_links_opp  ON public.opportunity_links (opportunity_id);
CREATE INDEX IF NOT EXISTS idx_opp_links_type ON public.opportunity_links (type);

-- ── 7. Create scraper_runs table ─────────────────────────────
CREATE TABLE IF NOT EXISTS public.scraper_runs (
  id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at   TIMESTAMPTZ,
  items_scraped INTEGER     NOT NULL DEFAULT 0,
  items_new     INTEGER     NOT NULL DEFAULT 0,
  items_updated INTEGER     NOT NULL DEFAULT 0,
  errors        JSONB       NOT NULL DEFAULT '[]',
  status        TEXT        NOT NULL DEFAULT 'running'
);

-- ── 8. RLS for new tables ─────────────────────────────────────
ALTER TABLE public.opportunity_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scraper_runs      ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "public_read_opp_links"     ON public.opportunity_links;
DROP POLICY IF EXISTS "service_write_opp_links"   ON public.opportunity_links;
DROP POLICY IF EXISTS "service_write_scraper_runs" ON public.scraper_runs;
DROP POLICY IF EXISTS "auth_read_scraper_runs"     ON public.scraper_runs;

CREATE POLICY "public_read_opp_links"
  ON public.opportunity_links FOR SELECT USING (true);
CREATE POLICY "service_write_opp_links"
  ON public.opportunity_links FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "service_write_scraper_runs"
  ON public.scraper_runs FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "auth_read_scraper_runs"
  ON public.scraper_runs FOR SELECT TO authenticated USING (true);

-- ── 9. Views ──────────────────────────────────────────────────
CREATE OR REPLACE VIEW public.v_opportunities_summary AS
SELECT
  o.id, o.slug, o.title, o.short_description,
  o.organization, o.category, o.status,
  o.total_vacancies, o.tags,
  o.fee_general, o.fee_obc, o.fee_sc_st,
  o.application_start_date, o.application_end_date,
  o.exam_date, o.result_date,
  o.apply_url, o.result_url, o.admit_card_url,
  o.is_featured, o.is_trending, o.view_count,
  o.scraped_at, o.created_at, o.updated_at,
  CASE
    WHEN o.application_end_date > NOW() THEN 'active'
    WHEN o.result_date IS NOT NULL AND o.result_date < NOW() THEN 'result_out'
    ELSE 'expired'
  END AS deadline_status,
  GREATEST(0, EXTRACT(DAY FROM (o.application_end_date - NOW()))::INTEGER) AS days_left
FROM public.opportunities o;

CREATE OR REPLACE VIEW public.v_deadline_soon AS
SELECT id, slug, title, organization, category, application_end_date,
       EXTRACT(DAY FROM (application_end_date - NOW()))::INTEGER AS days_left
FROM public.opportunities
WHERE application_end_date > NOW()
  AND application_end_date <= NOW() + INTERVAL '7 days'
ORDER BY application_end_date ASC;

-- ── 10. Storage bucket ────────────────────────────────────────
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('sarkari-docs', 'sarkari-docs', true, 52428800,
        ARRAY['application/pdf','image/jpeg','image/png','image/webp'])
ON CONFLICT (id) DO NOTHING;

-- ── Verify ────────────────────────────────────────────────────
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public' ORDER BY table_name;
