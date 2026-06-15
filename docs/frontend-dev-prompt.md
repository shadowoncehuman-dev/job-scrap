# Frontend Developer Brief — Sarkari Portal

## What This App Is

A government jobs portal where users can browse vacancies, results, admit cards,
answer keys, admissions, and syllabus updates scraped from sarkariresult.com.cm.
Users can bookmark items, track their applications, save searches, and get alerts.

---

## Stack

| Layer | Technology |
|---|---|
| Database + Auth + Storage | **Supabase** (PostgreSQL) |
| Scraper (backend) | Python worker on Render — runs every 5 min |
| Frontend | Your choice — Next.js / React recommended |
| Supabase client | `@supabase/supabase-js` |

---

## Core Table: `opportunities`

Every job post, result, admit card, answer key, admission, or syllabus is **one row**.

```typescript
interface Opportunity {
  id: string               // UUID primary key
  slug: string             // URL slug e.g. "rrb-alp-2026" (unique)
  title: string            // "Railway RRB ALP Online Form 2026 (11,127 Posts)"
  short_description: string  // First 300 chars — for cards
  description: string      // Full text
  organization: string     // "Railway Recruitment Board (RRB)"
  department: string | null
  category: OpportunityCategory
  status: OpportunityStatus

  // Vacancy
  total_vacancies: number | null
  vacancy_breakdown: Record<string, string>[]  // [{"Category Name":"UR","No. Of Post":"4860"}]

  // Eligibility
  min_qualification: QualificationLevel
  qualifications: string[]
  min_age: number | null
  max_age: number | null
  gender_eligibility: GenderType
  eligible_states: string[]
  eligible_categories: CategoryType[]

  // Fees (INR)
  fee_general: number | null   // e.g. 500.00
  fee_obc: number | null
  fee_sc_st: number | null
  fee_female: number | null

  // Key dates
  notification_date: string | null        // "YYYY-MM-DD"
  application_start_date: string | null
  application_end_date: string | null     // ← MOST IMPORTANT for "last date" badges
  exam_date: string | null
  admit_card_date: string | null
  result_date: string | null

  // Direct links (primary link per type)
  official_website: string | null
  notification_pdf_url: string | null
  apply_url: string | null
  admit_card_url: string | null
  answer_key_url: string | null
  result_url: string | null

  // Arrays
  selection_process: string[]    // ["CBT 1", "CBT 2", "CBAT", "Document Verification"]
  required_documents: string[]
  tags: string[]                 // ["Railway", "Latest Job", "Engineering"]

  // Flags
  is_featured: boolean
  is_trending: boolean
  view_count: number

  // Meta
  source_url: string
  scraped_at: string
  created_at: string
  updated_at: string
}
```

### Enums

```typescript
type OpportunityCategory = 'latest_job' | 'result' | 'admit_card' | 'answer_key' | 'admission' | 'syllabus'
type OpportunityStatus   = 'upcoming' | 'open' | 'closed' | 'result_declared' | 'cancelled'
type QualificationLevel  = 'any' | 'class_8' | 'class_10' | 'class_12' | 'diploma' | 'graduate' | 'post_graduate' | 'doctorate'
type GenderType          = 'any' | 'male' | 'female' | 'transgender'
type CategoryType        = 'general' | 'obc' | 'obc_ncl' | 'sc' | 'st' | 'ews' | 'pwd' | 'ex_serviceman'
```

---

## `opportunity_links` — All Links for an Item

Extra table. Each opportunity can have many links (all PDFs, zone-wise result links, etc.).

```typescript
interface OpportunityLink {
  id: string
  opportunity_id: string   // → opportunities.id
  label: string            // "Apply Online", "Download Result PDF", etc.
  url: string
  type: 'apply_online' | 'notification' | 'admit_card' | 'result' |
        'answer_key' | 'syllabus' | 'official_website' | 'download' | 'other'
  sort_order: number
}
```

**Query pattern:** Fetch links grouped by type for the "Important Links" section.

---

## User Tables (all require Supabase Auth)

### `bookmarks`
```typescript
{ user_id, opportunity_id, collection, notes, created_at }
```

### `user_applications` — Track application progress
```typescript
{
  user_id, opportunity_id,
  status: 'interested'|'applied'|'exam_scheduled'|'appeared'|'qualified'|'selected'|'rejected'|'withdrawn',
  application_number, roll_number, exam_city, notes,
  applied_at, created_at, updated_at
}
```

### `application_status_history` — Timeline of status changes
```typescript
{ application_id, status, note, created_at }
```

### `notifications`
```typescript
{
  user_id, opportunity_id (nullable),
  title, body,
  priority: 'low'|'medium'|'high'|'urgent',
  action_url, is_read, read_at, created_at
}
```

### `saved_searches`
```typescript
{ user_id, name, query, filters: Record<string,any>, notify_on_match, created_at }
```

### `user_roles`
```typescript
{ user_id, role: 'admin'|'moderator'|'user', created_at }
```

---

## Ready-Made Views

### `v_opportunities_summary`
Best for **list/card pages**. Adds computed fields:
- `deadline_status`: `'active'` | `'result_out'` | `'expired'`
- `days_left`: integer days until `application_end_date`

### `v_deadline_soon`
Best for **homepage widgets**. Returns items with deadline in next 7 days.

---

## Key Supabase Queries

```typescript
import { createClient } from '@supabase/supabase-js'
const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY)

// ── List page (paginated, by category) ──────────────────────
const { data } = await supabase
  .from('v_opportunities_summary')
  .select('*')
  .eq('category', 'latest_job')
  .order('scraped_at', { ascending: false })
  .range(0, 19)                         // page 1 (20 items)

// ── Full-text search ─────────────────────────────────────────
const { data } = await supabase
  .from('opportunities')
  .select('id, slug, category, title, organization, application_end_date')
  .textSearch('search_vector', query, { type: 'websearch' })
  .limit(20)

// ── Quick ILIKE search (for search-as-you-type) ──────────────
const { data } = await supabase
  .from('opportunities')
  .select('id, slug, title, category')
  .ilike('title', `%${q}%`)
  .limit(8)

// ── Detail page ──────────────────────────────────────────────
const { data: opp } = await supabase
  .from('opportunities')
  .select('*')
  .eq('slug', slug)
  .single()

// Increment view count
await supabase.rpc('increment_view', { opp_id: opp.id })
// (create this function: UPDATE opportunities SET view_count = view_count+1 WHERE id=$1)

// All links for detail page
const { data: links } = await supabase
  .from('opportunity_links')
  .select('*')
  .eq('opportunity_id', opp.id)
  .order('sort_order')

// ── Filter by open jobs ──────────────────────────────────────
const today = new Date().toISOString()
const { data } = await supabase
  .from('opportunities')
  .select('*')
  .eq('status', 'open')
  .gte('application_end_date', today)
  .order('application_end_date', { ascending: true })

// ── Homepage — upcoming deadlines ───────────────────────────
const { data } = await supabase
  .from('v_deadline_soon')
  .select('*')

// ── Bookmark ─────────────────────────────────────────────────
await supabase.from('bookmarks').upsert({
  user_id: session.user.id,
  opportunity_id: oppId,
})

// ── Get user bookmarks ───────────────────────────────────────
const { data } = await supabase
  .from('bookmarks')
  .select('*, opportunities(id, slug, title, category, application_end_date)')
  .eq('user_id', session.user.id)
  .order('created_at', { ascending: false })

// ── Track application ────────────────────────────────────────
await supabase.from('user_applications').upsert({
  user_id: session.user.id,
  opportunity_id: oppId,
  status: 'applied',
  application_number: 'ABC123',
})

// ── Mark notification read ───────────────────────────────────
await supabase.from('notifications')
  .update({ is_read: true, read_at: new Date().toISOString() })
  .eq('id', notifId)
  .eq('user_id', session.user.id)
```

---

## Pages to Build

| Page | Route | Data source |
|---|---|---|
| Homepage | `/` | `v_deadline_soon` + category counts + featured |
| Latest Jobs | `/jobs` | `v_opportunities_summary` where `category=latest_job` |
| Results | `/results` | same, `category=result` |
| Admit Cards | `/admit-card` | same |
| Answer Keys | `/answer-key` | same |
| Admissions | `/admission` | same |
| Syllabus | `/syllabus` | same |
| Detail | `/[category]/[slug]` | `opportunities` + `opportunity_links` |
| Search | `/search?q=...` | full-text on `search_vector` |
| My Bookmarks | `/my/bookmarks` (auth) | `bookmarks` join |
| My Applications | `/my/applications` (auth) | `user_applications` + history |
| My Alerts | `/my/alerts` (auth) | `saved_searches` |
| Notifications | `/my/notifications` (auth) | `notifications` |

---

## Auth

Use **Supabase Auth** (`@supabase/auth-ui-react` or build your own).
Enable: Email/Password + Google OAuth.

Row Level Security is already configured:
- Public can read all opportunities and links (no auth needed)
- Users can only read/write their OWN bookmarks, applications, notifications, saved searches
- Service role (scraper) can write opportunities

```env
NEXT_PUBLIC_SUPABASE_URL=https://bqfeywhfrhbgvolwulaj.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<your anon key from Supabase → Settings → API>
```

The `anon` key is **safe** to put in frontend code — RLS enforces access control.

---

## Category Colors & Icons (suggested)

```typescript
const CATEGORIES = {
  latest_job:  { label: 'Latest Jobs',  color: '#2563eb', icon: '💼' },
  result:      { label: 'Results',      color: '#16a34a', icon: '📊' },
  admit_card:  { label: 'Admit Cards',  color: '#ea580c', icon: '🎫' },
  answer_key:  { label: 'Answer Keys',  color: '#9333ea', icon: '🔑' },
  admission:   { label: 'Admissions',   color: '#dc2626', icon: '🏫' },
  syllabus:    { label: 'Syllabus',     color: '#0891b2', icon: '📚' },
}
```

---

## Important Notes

1. **`application_end_date`** is the #1 field for users — always show it prominently
   with urgency badges: "Last date today", "Closing in 3 days", "Closed".

2. **`vacancy_breakdown`** is a JSONB array of objects with varying keys — render
   it as a table: iterate rows, display key-value pairs.

3. **`fee_general / fee_obc / fee_sc_st`** are numeric (INR). Show as `₹500`.
   `null` means fee information wasn't available on the source page.

4. **All external links** (`apply_url`, `result_url`, etc.) should open in a new tab
   with `target="_blank" rel="noopener noreferrer"`.

5. **`is_featured`** and **`is_trending`** flags are admin-controlled via the
   Telegram bot (`/feature <slug>` command).

6. **Data freshness**: Scraper runs every 5 minutes. New items appear in DB
   within minutes of being posted on sarkariresult.com.cm.
   Show last-scraped timestamp from `scraper_runs` table.
