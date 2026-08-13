# breaking_news.source_content deployment

## Scope

The GNews collector already receives provider `content` once and keeps it as
`raw_content`. New rows preserve that exact value in the private
`breaking_news.source_content` column. The user-facing
`breaking_news.content` column remains the normalized Korean summary.

This change does not:

- make another GNews request;
- crawl an article URL;
- backfill historical rows;
- change collection timing, request count, deduplication, notifications, or
  public rendering.

## Migration ownership

This repository does not own Supabase migrations. The migration owner found
during the audit is:

`C:\Users\boxma\Desktop\hangon\backend\supabase\migrations`

At the time of inspection, `202608130001_pulse_asset_sector_seed.sql` already
existed locally. Use the next free migration timestamp. If no newer migration
has been added, the intended target file is:

`C:\Users\boxma\Desktop\hangon\backend\supabase\migrations\202608130002_breaking_news_source_content.sql`

Do not add an executable Supabase migration to this collector repository.

## Required frontend privacy prerequisite

The current frontend at
`C:\Users\boxma\Desktop\hangon\frontend\app\live\page.tsx`
uses `.select("*")` for both list queries and subscribes to the full Realtime
INSERT row. Adding a private column without changing those queries would send
`source_content` to the browser.

Before applying the migration:

1. Replace both `.select("*")` calls with the explicit public projection
   `id,title,content,importance_score,category,original_url,created_at`.
2. Add the same explicit column selection to the Realtime Postgres Changes
   subscription so `payload.new` contains only those fields.
3. Deploy and verify that frontend change.

The frontend repository is separate and was deliberately not modified here.

## Forward-only migration SQL

Put the following SQL in the migration-owner repository. The column grants are
required because RLS controls rows, not columns.

```sql
begin;

alter table public.breaking_news
  add column if not exists source_content text;

comment on column public.breaking_news.source_content
  is 'Raw article content received in the original provider response; internal AI use only';

-- Remove broad table SELECT from browser-facing roles, then grant only the
-- existing public contract. service_role is intentionally not restricted.
revoke select on table public.breaking_news from anon, authenticated;

grant select (
  id,
  title,
  content,
  importance_score,
  category,
  original_url,
  created_at
) on table public.breaking_news to anon, authenticated;

commit;
```

Before applying this permission change, confirm that no other browser client
depends on `select("*")` or on an omitted internal column such as
`pulse_story`. Server-side Pulse code uses explicit projections and the
service role, so its existing title/content flow is unchanged.

## Deployment order

1. Deploy the explicit frontend REST and Realtime projections described above.
2. Apply the reviewed DB migration in the migration-owner repository.
3. Verify that:
   - an explicit public-column query still succeeds for `anon` and
     `authenticated`;
   - selecting `source_content` as either public role is denied;
   - the collector service role can insert `source_content`.
4. Deploy or pull this collector revision.
5. Run `python gnews_tracker.py --dry-run` and confirm that the preview does
   not print raw content.
6. Restart the PM2 collector and inspect only count/status logs.

The DB migration must be applied before this collector revision is started.
Otherwise inserts will fail because the row payload contains a column that does
not yet exist.

## Data behavior

- Nonblank string `raw_content`: stored byte-for-byte as received.
- Missing, `None`, non-string, or whitespace-only `raw_content`: stored as
  SQL `NULL`.
- `normalized_content` is never copied into `source_content`.
- Existing rows remain unchanged and therefore have `source_content IS NULL`.
