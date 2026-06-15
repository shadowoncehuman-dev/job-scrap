-- ============================================================
-- Sarkari Portal — Complete Supabase Schema
-- Run in: Supabase Dashboard → SQL Editor → New query → Run
-- ============================================================

-- ── Extensions ───────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "unaccent";

-- ============================================================
-- ENUM TYPES
-- ============================================================

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
  CREATE TYPE qualification_level AS ENUM (
    'any', 'class_8', 'class_10', 'class_12', 'diploma',
    'graduate', 'post_graduate', 'doctorate'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE gender_type AS ENUM ('any', 'male', 'female', 'transgender');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE category_type AS ENUM (
    'general', 'obc', 'obc_ncl', 'sc', 'st', 'ews', 'pwd', 'ex_serviceman'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE application_status AS ENUM (
    'interested', 'applied', 'exam_scheduled', 'appeared',
    'qualified', 'selected', 'rejected', 'withdrawn'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE notification_priority AS ENUM ('low', 'medium', 'high', 'urgent');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE app_role AS ENUM ('admin', 'moderator', 'user');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- ============================================================
-- SHARED FUNCTIONS (must exist before triggers)
-- ============================================================

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
    setweight(to_tsvector('english', COALESCE(NEW.title, '')),            'A') ||
    setweight(to_tsvector('english', COALESCE(NEW.organization, '')),     'A') ||
    setweight(to_tsvector('english', COALESCE(NEW.short_description, '')), 'B') ||
    setweight(to_tsvector('english', COALESCE(NEW.description, '')),      'C') ||
    setweight(to_tsvector('english', array_to_string(COALESCE(NEW.tags, '{}'), ' ')), 'B');
  RETURN NEW;
END;
$$;


-- ============================================================
-- MAIN OPPORTUNITIES TABLE  (your existing schema)
-- ============================================================

CREATE TABLE IF NOT EXISTS public.opportunities (
  id                    UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
  slug                  TEXT          NOT NULL UNIQUE,
  title                 TEXT          NOT NULL,
  short_description     TEXT,
  description           TEXT,
  organization          TEXT          NOT NULL,
  department            TEXT,
  category              opportunity_category NOT NULL,
  status                opportunity_status   NOT NULL DEFAULT 'open',
  total_vacancies       INTEGER,
  vacancy_breakdown     JSONB         DEFAULT '{}',
  min_qualification     qualification_level  DEFAULT 'any',
  qualifications        TEXT[]        DEFAULT '{}',
  min_age               INTEGER,
  max_age               INTEGER,
  gender_eligibility    gender_type   DEFAULT 'any',
  eligible_states       TEXT[]        DEFAULT '{}',
  eligible_categories   category_type[] DEFAULT '{}',
  fee_general           NUMERIC(10,2),
  fee_obc               NUMERIC(10,2),
  fee_sc_st             NUMERIC(10,2),
  fee_female            NUMERIC(10,2),
  notification_date     DATE,
  application_start_date TIMESTAMPTZ,
  application_end_date  TIMESTAMPTZ,
  exam_date             TIMESTAMPTZ,
  admit_card_date       TIMESTAMPTZ,
  result_date           TIMESTAMPTZ,
  official_website      TEXT,
  notification_pdf_url  TEXT,
  apply_url             TEXT,
  admit_card_url        TEXT,
  answer_key_url        TEXT,
  result_url            TEXT,
  selection_process     TEXT[]        DEFAULT '{}',
  required_documents    TEXT[]        DEFAULT '{}',
  tags                  TEXT[]        DEFAULT '{}',
  is_featured           BOOLEAN       DEFAULT false,
  is_trending           BOOLEAN       DEFAULT false,
  view_count            INTEGER       DEFAULT 0,
  meta_title            TEXT,
  meta_description      TEXT,
  source_url            TEXT          UNIQUE,       -- original scrape URL
  scraped_at            TIMESTAMPTZ,
  search_vector         TSVECTOR,
  created_by            UUID          REFERENCES auth.users(id) ON DELETE SET NULL,
  created_at            TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  updated_at            TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_opp_status     ON public.opportunities (status);
CREATE INDEX IF NOT EXISTS idx_opp_category   ON public.opportunities (category);
CREATE INDEX IF NOT EXISTS idx_opp_end_date   ON public.opportunities (application_end_date);
CREATE INDEX IF NOT EXISTS idx_opp_search     ON public.opportunities USING GIN (search_vector);
CREATE INDEX IF NOT EXISTS idx_opp_tags       ON public.opportunities USING GIN (tags);
CREATE INDEX IF NOT EXISTS idx_opp_scraped    ON public.opportunities (scraped_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_opp_featured   ON public.opportunities (is_featured) WHERE is_featured = true;

DROP TRIGGER IF EXISTS trg_opp_search   ON public.opportunities;
DROP TRIGGER IF EXISTS trg_opp_updated  ON public.opportunities;

CREATE TRIGGER trg_opp_search
  BEFORE INSERT OR UPDATE ON public.opportunities
  FOR EACH ROW EXECUTE FUNCTION opportunities_search_trigger();

CREATE TRIGGER trg_opp_updated
  BEFORE UPDATE ON public.opportunities
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();


-- ============================================================
-- OPPORTUNITY LINKS  (extra — stores ALL scraped links)
-- ============================================================

DO $$ BEGIN
  CREATE TYPE link_type AS ENUM (
    'apply_online', 'notification', 'admit_card', 'result',
    'answer_key', 'syllabus', 'official_website', 'download', 'other'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS public.opportunity_links (
  id             UUID      PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id UUID      NOT NULL REFERENCES public.opportunities(id) ON DELETE CASCADE,
  label          TEXT      NOT NULL,
  url            TEXT      NOT NULL,
  type           link_type NOT NULL DEFAULT 'other',
  sort_order     INTEGER   NOT NULL DEFAULT 0,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_opp_links_opp  ON public.opportunity_links (opportunity_id);
CREATE INDEX IF NOT EXISTS idx_opp_links_type ON public.opportunity_links (type);


-- ============================================================
-- USER ROLES  (your existing schema)
-- ============================================================

CREATE TABLE IF NOT EXISTS public.user_roles (
  id         UUID      PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID      NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  role       app_role  NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (user_id, role)
);


-- ============================================================
-- BOOKMARKS  (your existing schema)
-- ============================================================

CREATE TABLE IF NOT EXISTS public.bookmarks (
  id             UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        UUID    NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  opportunity_id UUID    NOT NULL REFERENCES public.opportunities(id) ON DELETE CASCADE,
  collection     TEXT    DEFAULT 'default',
  notes          TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (user_id, opportunity_id)
);

CREATE INDEX IF NOT EXISTS idx_bookmarks_user ON public.bookmarks (user_id);
CREATE INDEX IF NOT EXISTS idx_bookmarks_opp  ON public.bookmarks (opportunity_id);


-- ============================================================
-- USER APPLICATIONS  (your existing schema)
-- ============================================================

CREATE TABLE IF NOT EXISTS public.user_applications (
  id                UUID               PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           UUID               NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  opportunity_id    UUID               NOT NULL REFERENCES public.opportunities(id) ON DELETE CASCADE,
  status            application_status NOT NULL DEFAULT 'interested',
  application_number TEXT,
  roll_number       TEXT,
  exam_city         TEXT,
  notes             TEXT,
  applied_at        TIMESTAMPTZ,
  created_at        TIMESTAMPTZ        NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ        NOT NULL DEFAULT NOW(),
  UNIQUE (user_id, opportunity_id)
);

CREATE INDEX IF NOT EXISTS idx_apps_user ON public.user_applications (user_id);
CREATE INDEX IF NOT EXISTS idx_apps_opp  ON public.user_applications (opportunity_id);

DROP TRIGGER IF EXISTS trg_apps_updated ON public.user_applications;
CREATE TRIGGER trg_apps_updated
  BEFORE UPDATE ON public.user_applications
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();


-- ============================================================
-- APPLICATION STATUS HISTORY  (your existing schema)
-- ============================================================

CREATE TABLE IF NOT EXISTS public.application_status_history (
  id             UUID               PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id UUID               NOT NULL REFERENCES public.user_applications(id) ON DELETE CASCADE,
  status         application_status NOT NULL,
  note           TEXT,
  created_at     TIMESTAMPTZ        NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_app_history ON public.application_status_history (application_id);


-- ============================================================
-- NOTIFICATIONS  (your existing schema)
-- ============================================================

CREATE TABLE IF NOT EXISTS public.notifications (
  id             UUID                  PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        UUID                  NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  opportunity_id UUID                  REFERENCES public.opportunities(id) ON DELETE CASCADE,
  title          TEXT                  NOT NULL,
  body           TEXT,
  priority       notification_priority NOT NULL DEFAULT 'medium',
  action_url     TEXT,
  is_read        BOOLEAN               DEFAULT false,
  read_at        TIMESTAMPTZ,
  created_at     TIMESTAMPTZ           NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notif_user_unread
  ON public.notifications (user_id, is_read, created_at DESC);


-- ============================================================
-- SAVED SEARCHES  (your existing schema)
-- ============================================================

CREATE TABLE IF NOT EXISTS public.saved_searches (
  id              UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID    NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  name            TEXT    NOT NULL,
  query           TEXT,
  filters         JSONB   DEFAULT '{}',
  notify_on_match BOOLEAN DEFAULT true,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_saved_searches_user ON public.saved_searches (user_id);


-- ============================================================
-- SCRAPER RUNS  (monitoring / health log)
-- ============================================================

CREATE TABLE IF NOT EXISTS public.scraper_runs (
  id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at    TIMESTAMPTZ,
  items_scraped  INTEGER     NOT NULL DEFAULT 0,
  items_new      INTEGER     NOT NULL DEFAULT 0,
  items_updated  INTEGER     NOT NULL DEFAULT 0,
  errors         JSONB       NOT NULL DEFAULT '[]',
  status         TEXT        NOT NULL DEFAULT 'running'
  -- status: 'running' | 'success' | 'partial' | 'error'
);


-- ============================================================
-- ROW LEVEL SECURITY
-- ============================================================

ALTER TABLE public.opportunities           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.opportunity_links       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_roles              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bookmarks               ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_applications       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.application_status_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notifications           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.saved_searches          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scraper_runs            ENABLE ROW LEVEL SECURITY;

-- Public read for opportunities and links
DROP POLICY IF EXISTS "public_read_opportunities" ON public.opportunities;
CREATE POLICY "public_read_opportunities"
  ON public.opportunities FOR SELECT USING (true);

DROP POLICY IF EXISTS "public_read_opp_links" ON public.opportunity_links;
CREATE POLICY "public_read_opp_links"
  ON public.opportunity_links FOR SELECT USING (true);

-- Service role can write opportunities (scraper uses service role)
DROP POLICY IF EXISTS "service_write_opportunities" ON public.opportunities;
CREATE POLICY "service_write_opportunities"
  ON public.opportunities FOR ALL
  TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "service_write_opp_links" ON public.opportunity_links;
CREATE POLICY "service_write_opp_links"
  ON public.opportunity_links FOR ALL
  TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "service_write_scraper_runs" ON public.scraper_runs;
CREATE POLICY "service_write_scraper_runs"
  ON public.scraper_runs FOR ALL
  TO service_role USING (true) WITH CHECK (true);

-- Authenticated users read scraper runs
DROP POLICY IF EXISTS "auth_read_scraper_runs" ON public.scraper_runs;
CREATE POLICY "auth_read_scraper_runs"
  ON public.scraper_runs FOR SELECT TO authenticated USING (true);

-- Users manage their own bookmarks
DROP POLICY IF EXISTS "users_own_bookmarks" ON public.bookmarks;
CREATE POLICY "users_own_bookmarks"
  ON public.bookmarks USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

-- Users manage their own applications
DROP POLICY IF EXISTS "users_own_applications" ON public.user_applications;
CREATE POLICY "users_own_applications"
  ON public.user_applications USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

-- Users see their own application history
DROP POLICY IF EXISTS "users_own_app_history" ON public.application_status_history;
CREATE POLICY "users_own_app_history"
  ON public.application_status_history FOR SELECT
  USING (EXISTS (
    SELECT 1 FROM public.user_applications a
    WHERE a.id = application_id AND a.user_id = auth.uid()
  ));

-- Users manage their own notifications
DROP POLICY IF EXISTS "users_own_notifications" ON public.notifications;
CREATE POLICY "users_own_notifications"
  ON public.notifications USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

-- Users manage their own saved searches
DROP POLICY IF EXISTS "users_own_saved_searches" ON public.saved_searches;
CREATE POLICY "users_own_saved_searches"
  ON public.saved_searches USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

-- Users see their own role
DROP POLICY IF EXISTS "users_own_roles" ON public.user_roles;
CREATE POLICY "users_own_roles"
  ON public.user_roles FOR SELECT USING (auth.uid() = user_id);


-- ============================================================
-- STORAGE BUCKET
-- ============================================================

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
  'sarkari-docs', 'sarkari-docs', true, 52428800,
  ARRAY['application/pdf', 'image/jpeg', 'image/png', 'image/webp']
)
ON CONFLICT (id) DO NOTHING;

DROP POLICY IF EXISTS "public_read_docs" ON storage.objects;
CREATE POLICY "public_read_docs"
  ON storage.objects FOR SELECT TO public
  USING (bucket_id = 'sarkari-docs');


-- ============================================================
-- HELPER VIEWS
-- ============================================================

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
  -- deadline status helper
  CASE
    WHEN o.application_end_date > NOW() THEN 'active'
    WHEN o.result_date IS NOT NULL AND o.result_date < NOW() THEN 'result_out'
    ELSE 'expired'
  END AS deadline_status,
  -- days left
  GREATEST(0, EXTRACT(DAY FROM (o.application_end_date - NOW()))::INTEGER) AS days_left
FROM public.opportunities o;

CREATE OR REPLACE VIEW public.v_deadline_soon AS
SELECT id, slug, title, organization, category, application_end_date,
       EXTRACT(DAY FROM (application_end_date - NOW()))::INTEGER AS days_left
FROM public.opportunities
WHERE application_end_date > NOW()
  AND application_end_date <= NOW() + INTERVAL '7 days'
ORDER BY application_end_date ASC;


-- ============================================================
-- VERIFY
-- ============================================================
-- SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name;
-- SELECT COUNT(*) FROM public.opportunities;
