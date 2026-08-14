/**
 * scout-sync/index.ts  —  v2
 * ─────────────────────────────────────────────────────────────
 * One-way incremental sync: platform (GEO) DB  ->  Scout agentic DB.
 * Runs every 30 minutes via pg_cron.
 *
 * v2 works against the CURRENT Scout schema with no DDL. Specifically:
 *
 *   1. sov_weekly / ai_responses have no unique constraint on their natural
 *      key (PK is `id` only), so ON CONFLICT on the natural key raises 42P10.
 *      -> resolve natural key -> existing id via a lookup pass, then
 *         upsert on "id" for hits and plain-insert for misses.
 *
 *   2. report_data, queries_groups_data, keywords_groups_data and
 *      company_scraped_data_cache have `id GENERATED ALWAYS AS IDENTITY`,
 *      which rejects any statement supplying `id` (428C9).
 *      -> strip `id`, dedupe with insert-if-absent on a natural key.
 *
 *   3. ai_monitoring.client_id FK -> onboarding(client_id) and
 *      ai_responses.client_id FK -> clients(client_id).
 *      -> both batches are filtered against the client_ids actually
 *         present in Scout. Parents are always synced first.
 *
 *   4. clients.{client_name,company_domain,company_website} and
 *      clusters.cluster_name are NOT NULL in Scout while the upstream columns
 *      are nullable -> coalesced at transform time. Scout's ai_monitoring.sov
 *      has no GEO counterpart and is never selected; its NOT NULL DEFAULT '{}'
 *      covers inserts and existing values survive updates untouched.
 *
 *   5. Cursor columns that are nullable upstream (updated_at) silently hide
 *      rows from `.gt()`. -> two-pass cursor (non-null on updated_at,
 *      null-tail on created_at), or a full sweep for small dimension tables.
 *
 *   6. The watermark now only advances when a stream wrote cleanly. A failed
 *      batch holds the cursor so the rows are retried next run instead of
 *      being skipped forever. Heartbeat (status/updated_at) still moves.
 *
 *   7. Keyset pagination (dateCol, id) instead of OFFSET — no skipped or
 *      duplicated rows on timestamp ties — plus a per-stream row cap and a
 *      wall-clock budget so a cold backfill degrades instead of timing out.
 *
 * Env vars:
 *   PLATFORM_SUPABASE_URL / PLATFORM_SUPABASE_SERVICE_KEY
 *   SCOUT_SUPABASE_URL    / SCOUT_SUPABASE_SERVICE_KEY
 *   MAX_ROWS_PER_STREAM (optional, default 20000)
 *   SYNC_DEADLINE_MS    (optional, default 110000)
 *
 * POST body (all optional):
 *   { "streams": ["ai_monitoring_mirror"], "maxRows": 100000, "deadlineMs": 200000 }
 *   -> run a subset of streams with a bigger budget, for controlled backfills.
 */

import { createClient, SupabaseClient } from "https://esm.sh/@supabase/supabase-js@2";

type Row = Record<string, unknown>;

const EPOCH        = "2000-01-01T00:00:00+00:00";
const PAGE_SIZE    = 500;   // read page
const WRITE_SIZE   = 200;   // write batch
const LOOKUP_PAGE  = 1000;  // key-index page
const CHUNK_IN     = 50;    // client_ids per .in() filter

const DEFAULT_MAX_ROWS   = Number(Deno.env.get("MAX_ROWS_PER_STREAM") ?? 20000);
const DEFAULT_DEADLINE   = Number(Deno.env.get("SYNC_DEADLINE_MS") ?? 110_000);

// ── Budget ────────────────────────────────────────────────────

class Budget {
  private readonly end: number;
  constructor(ms: number) { this.end = Date.now() + ms; }
  expired(): boolean { return Date.now() >= this.end; }
  get remainingMs(): number { return Math.max(0, this.end - Date.now()); }
}

// ── Small helpers ─────────────────────────────────────────────

function nowIso(): string { return new Date().toISOString(); }

function chunk<T>(arr: T[], n: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < arr.length; i += n) out.push(arr.slice(i, i + n));
  return out;
}

function mondayOf(d: Date): string {
  const day = new Date(d);
  day.setUTCHours(0, 0, 0, 0);
  const diff = day.getUTCDay() === 0 ? -6 : 1 - day.getUTCDay();
  day.setUTCDate(day.getUTCDate() + diff);
  return day.toISOString().split("T")[0];
}

function str(v: unknown): string { return v == null ? "" : String(v); }

// ── Dry run ───────────────────────────────────────────────────

/**
 * Wraps the Scout client so every terminal write (insert/upsert/update/delete)
 * resolves as a no-op success while reads pass through untouched. Lets a run
 * exercise the full fetch -> transform -> key-lookup path, and report exactly
 * what it *would* write, without mutating Scout or advancing any cursor.
 */
function readOnlyScout(scout: SupabaseClient): SupabaseClient {
  const noop = async () => ({ data: null, error: null });
  const bind = (t: unknown, v: unknown) => (typeof v === "function" ? (v as Function).bind(t) : v);
  return new Proxy(scout as unknown as Record<string, unknown>, {
    get(target, prop, recv) {
      if (prop !== "from") return bind(target, Reflect.get(target, prop, recv));
      return (table: string) => new Proxy((target.from as Function).call(target, table), {
        get(qb, p) {
          if (p === "insert" || p === "upsert" || p === "update" || p === "delete") return noop;
          return bind(qb, (qb as Record<string | symbol, unknown>)[p]);
        },
      });
    },
  }) as unknown as SupabaseClient;
}

// ── sync_state ────────────────────────────────────────────────

async function getSyncState(scout: SupabaseClient, key: string): Promise<string> {
  const { data, error } = await scout
    .from("sync_state").select("last_synced_at").eq("table_name", key).maybeSingle();
  if (error || !data) return EPOCH;
  return (data.last_synced_at as string) ?? EPOCH;
}

async function setSyncState(
  scout: SupabaseClient, key: string, watermark: string,
  status: string, errorMessage: string | null,
): Promise<void> {
  const { error } = await scout.from("sync_state").upsert(
    {
      table_name:    key,
      last_synced_at: watermark,
      status,
      error_message: errorMessage,
      updated_at:    nowIso(),
    },
    { onConflict: "table_name" },
  );
  if (error) console.error(`sync_state ${key}: ${error.message}`);
}

// ── Reads ─────────────────────────────────────────────────────

/**
 * Keyset-paginated incremental read. Orders by (dateCol, idCol) and seeks with
 * `dateCol > c OR (dateCol = c AND id > lastId)`, so rows sharing a timestamp
 * are never skipped or re-read.
 *
 * Returns `complete=false` when the row cap or the time budget was hit; in that
 * case the trailing partial timestamp group is trimmed so the caller can park
 * the watermark on a clean boundary.
 */
async function fetchIncremental(
  client: SupabaseClient,
  table: string,
  columns: string,
  dateCol: string,
  since: string,
  maxRows: number,
  budget: Budget,
  opts: { idCol?: string; filter?: (q: any) => any } = {},
): Promise<{ rows: Row[]; lastTs: string | null; complete: boolean }> {
  const idCol = opts.idCol ?? "id";
  const rows: Row[] = [];
  let cursorTs = since;
  let cursorId: string | null = null;
  let complete = false;

  while (true) {
    if (budget.expired()) break;

    let q = client.from(table).select(columns)
      .order(dateCol, { ascending: true })
      .order(idCol,   { ascending: true })
      .limit(PAGE_SIZE);

    q = cursorId === null
      ? q.gt(dateCol, cursorTs)
      : q.or(`${dateCol}.gt."${cursorTs}",and(${dateCol}.eq."${cursorTs}",${idCol}.gt."${cursorId}")`);

    if (opts.filter) q = opts.filter(q);

    const { data, error } = await q;
    if (error) throw new Error(`fetch ${table}: ${error.message}`);

    const page = (data ?? []) as Row[];
    rows.push(...page);

    if (page.length < PAGE_SIZE) { complete = true; break; }

    const last = page[page.length - 1];
    cursorTs = str(last[dateCol]);
    cursorId = str(last[idCol]);

    if (rows.length >= maxRows) break;
  }

  if (complete || rows.length === 0) {
    return { rows, lastTs: rows.length ? str(rows[rows.length - 1][dateCol]) : null, complete };
  }

  // Capped: trim the trailing timestamp group so the watermark lands cleanly.
  const tailTs = str(rows[rows.length - 1][dateCol]);
  let cut = rows.length;
  while (cut > 0 && str(rows[cut - 1][dateCol]) === tailTs) cut--;
  if (cut === 0) {
    console.warn(`[scout-sync] ${table}: >${rows.length} rows share ${tailTs}; advancing anyway`);
    return { rows, lastTs: tailTs, complete: false };
  }
  rows.length = cut;
  return { rows, lastTs: str(rows[cut - 1][dateCol]), complete: false };
}

/** Full sweep for small dimension tables whose cursor columns are unreliable. */
async function fetchAllRows(
  client: SupabaseClient, table: string, columns: string, orderCol: string, budget: Budget,
): Promise<Row[]> {
  const rows: Row[] = [];
  let offset = 0;
  while (!budget.expired()) {
    const { data, error } = await client.from(table).select(columns)
      .order(orderCol, { ascending: true })
      .range(offset, offset + PAGE_SIZE - 1);
    if (error) throw new Error(`fetch ${table}: ${error.message}`);
    const page = (data ?? []) as Row[];
    rows.push(...page);
    if (page.length < PAGE_SIZE) break;
    offset += PAGE_SIZE;
  }
  return rows;
}

/** Build key -> id index over Scout rows, used where no unique constraint exists. */
async function loadKeyIndex(
  scout: SupabaseClient, table: string, cols: string,
  keyOf: (r: Row) => string, filter: (q: any) => any, budget: Budget,
): Promise<Map<string, unknown>> {
  const map = new Map<string, unknown>();
  let offset = 0;
  while (!budget.expired()) {
    const { data, error } = await filter(scout.from(table).select(cols))
      .order("id", { ascending: true })
      .range(offset, offset + LOOKUP_PAGE - 1);
    if (error) throw new Error(`lookup ${table}: ${error.message}`);
    const page = (data ?? []) as Row[];
    for (const r of page) map.set(keyOf(r), r.id);
    if (page.length < LOOKUP_PAGE) break;
    offset += LOOKUP_PAGE;
  }
  return map;
}

/** All client_ids present in a Scout parent table (for FK-safe filtering). */
async function loadIdSet(
  scout: SupabaseClient, table: string, col: string, budget: Budget,
): Promise<Set<string>> {
  const set = new Set<string>();
  let offset = 0;
  while (!budget.expired()) {
    const { data, error } = await scout.from(table).select(col)
      .order(col, { ascending: true })
      .range(offset, offset + LOOKUP_PAGE - 1);
    if (error) throw new Error(`idset ${table}.${col}: ${error.message}`);
    const page = (data ?? []) as Row[];
    for (const r of page) if (r[col] != null) set.add(String(r[col]));
    if (page.length < LOOKUP_PAGE) break;
    offset += LOOKUP_PAGE;
  }
  return set;
}

// ── Writes ────────────────────────────────────────────────────

type WriteResult = { written: number; failed: number };

function merge(a: WriteResult, b: WriteResult): WriteResult {
  return { written: a.written + b.written, failed: a.failed + b.failed };
}

async function upsertBatches(
  scout: SupabaseClient, table: string, rows: Row[], onConflict: string,
): Promise<WriteResult> {
  let written = 0, failed = 0;
  for (const batch of chunk(rows, WRITE_SIZE)) {
    const { error } = await scout.from(table).upsert(batch, { onConflict });
    if (error) {
      console.error(`upsert ${table}: ${error.message}`);
      failed += batch.length;
    } else written += batch.length;
  }
  return { written, failed };
}

async function insertBatches(
  scout: SupabaseClient, table: string, rows: Row[],
): Promise<WriteResult> {
  let written = 0, failed = 0;
  for (const batch of chunk(rows, WRITE_SIZE)) {
    const { error } = await scout.from(table).insert(batch);
    if (error) {
      console.error(`insert ${table}: ${error.message}`);
      failed += batch.length;
    } else written += batch.length;
  }
  return { written, failed };
}

/**
 * Upsert against a table whose natural key has NO unique constraint.
 * Resolves key -> existing id, updates by PK, inserts the rest.
 * Safe because the PK (`id`) is uuid DEFAULT, not GENERATED ALWAYS.
 */
async function upsertByNaturalKey(
  scout: SupabaseClient, table: string, rows: Row[],
  keyCols: string[], lookupFilter: (q: any) => any, budget: Budget,
): Promise<WriteResult> {
  if (rows.length === 0) return { written: 0, failed: 0 };
  const keyOf = (r: Row) => keyCols.map((c) => str(r[c])).join("\u0001");

  const index = await loadKeyIndex(
    scout, table, ["id", ...keyCols].join(","), keyOf, lookupFilter, budget,
  );

  const updates: Row[] = [];
  const inserts: Row[] = [];
  const staged = new Set<string>();

  for (const r of rows) {
    const k = keyOf(r);
    if (staged.has(k)) continue;           // in-batch dedupe
    staged.add(k);
    const existingId = index.get(k);
    if (existingId != null) updates.push({ ...r, id: existingId });
    else inserts.push(r);
  }

  const a = updates.length ? await upsertBatches(scout, table, updates, "id") : { written: 0, failed: 0 };
  const b = inserts.length ? await insertBatches(scout, table, inserts) : { written: 0, failed: 0 };
  return merge(a, b);
}

/**
 * Insert-only sync for mirrors whose target `id` is GENERATED ALWAYS.
 * The source id cannot be written, so identity is a natural key and rows are
 * inserted once. (These sources are append-only; their cursor is created_at,
 * so in-place edits would not be detected upstream either.)
 */
async function insertIfAbsent(
  scout: SupabaseClient, table: string, rows: Row[],
  keyCols: string[], dateCol: string, budget: Budget,
): Promise<WriteResult> {
  if (rows.length === 0) return { written: 0, failed: 0 };
  const keyOf = (r: Row) => keyCols.map((c) => str(r[c])).join("\u0001");

  const minDate = rows.reduce(
    (m, r) => (str(r[dateCol]) < m ? str(r[dateCol]) : m), str(rows[0][dateCol]),
  );

  const index = await loadKeyIndex(
    scout, table, ["id", ...keyCols].join(","), keyOf,
    (q) => q.gte(dateCol, minDate), budget,
  );

  const inserts: Row[] = [];
  const staged = new Set<string>();
  for (const r of rows) {
    const k = keyOf(r);
    if (staged.has(k) || index.has(k)) continue;
    staged.add(k);
    const { id: _drop, ...payload } = r;   // identity column: never send id
    inserts.push(payload);
  }
  return inserts.length ? await insertBatches(scout, table, inserts) : { written: 0, failed: 0 };
}

// ── Transforms ────────────────────────────────────────────────
// NOT NULL targets are coalesced; a null here fails the whole 200-row batch.

function transformClients(rows: Row[]): Row[] {
  const seen = new Map<string, Row>();
  for (const r of rows) {
    const id = r.client_id as string;
    if (!id) continue;
    seen.set(id, {
      client_id:       id,
      client_name:     (r.company_name ?? r.company_domain ?? "") as string,   // NOT NULL
      company_domain:  (r.company_domain ?? "") as string,                     // NOT NULL
      company_website: (r.company_website ?? "") as string,                    // NOT NULL
      industry:        r.industry ?? null,
      target_region:   r.target_region ?? "us",
      competitors:     r.competitors ?? [],
      competitors_url: r.competitors_url ?? null,
      topics:          r.topics ?? [],
      synced_at:       nowIso(),
    });
  }
  return [...seen.values()];
}

function transformClusters(rows: Row[]): Row[] {
  const bucket = new Map<string, { ts: string; row: Row }>();
  for (const r of rows) {
    const clientId  = r.client_id as string;
    const clusterId = r.cluster_id as string;
    if (!clientId || !clusterId) continue;
    const key = `${clientId}|${clusterId}`;
    const ts  = str(r.updated_at ?? r.created_at);
    const ex  = bucket.get(key);
    if (!ex || ts > ex.ts) {
      bucket.set(key, {
        ts,
        row: {
          cluster_id:   clusterId,
          client_id:    clientId,
          cluster_name: (r.cluster_name ?? "") as string,   // NOT NULL
          synced_at:    nowIso(),
        },
      });
    }
  }
  return [...bucket.values()].map((v) => v.row);
}

function transformSovWeekly(rows: Row[]): Row[] {
  const bucket = new Map<string, { ts: string; row: Row }>();
  for (const r of rows) {
    const clientId  = r.client_id as string;
    const clusterId = r.cluster_id as string;
    const topRaw    = r.top_companies as Record<string, Record<string, { total_visibility: number }>> | null;
    const createdAt = r.created_at as string;
    const updatedAt = str(r.updated_at ?? createdAt);
    if (!clientId || !clusterId || !topRaw || !createdAt) continue;

    const baseMonday = mondayOf(new Date(createdAt));

    for (const [weekKey, companies] of Object.entries(topRaw)) {
      if (typeof companies !== "object" || companies === null) continue;
      const digits = weekKey.replace(/\D/g, "");
      if (digits.length === 0 || digits.length > 3) continue;   // guard non "weekN" keys
      const n = parseInt(digits, 10);
      if (isNaN(n) || n < 1) continue;

      const weekDate = new Date(baseMonday);
      weekDate.setUTCDate(weekDate.getUTCDate() - (n - 1) * 7);
      const weekStr = weekDate.toISOString().split("T")[0];

      const scored = Object.entries(companies)
        .filter(([, v]) => typeof v === "object" && v?.total_visibility != null)
        .map(([name, v]) => ({
          name,
          sov_score: Math.round((v as { total_visibility: number }).total_visibility * 10000) / 100,
        }))
        .sort((a, b) => b.sov_score - a.sov_score)
        .map((item, i) => ({ ...item, rank: i + 1 }));

      const key = `${clientId}|${clusterId}|${weekStr}`;
      const ex  = bucket.get(key);
      if (!ex || updatedAt > ex.ts) {
        bucket.set(key, {
          ts: updatedAt,
          row: {
            client_id:        clientId,
            cluster_id:       clusterId,
            cluster_name:     r.cluster_name ?? null,
            week_date:        weekStr,
            top_companies:    scored,
            selected_queries: r.selected_queries ?? null,
            top_sources:      r.top_sources ?? null,
            service:          r.service ?? null,
            synced_at:        nowIso(),
          },
        });
      }
    }
  }
  return [...bucket.values()].map((v) => v.row);
}

function transformAiResponses(rows: Row[]): Row[] {
  const seen = new Set<string>();
  const out: Row[] = [];
  for (const r of rows) {
    const clientId = r.client_id as string;
    if (!clientId) continue;
    const createdAt = r.created_at as string;
    if (!createdAt) continue;

    const clusterId = (r.cluster_id ?? "") as string;          // NOT NULL
    const weekDate  = mondayOf(new Date(createdAt));
    const rp        = (r.request_payload ?? {}) as Row;
    // '' rather than null: the natural key has no unique index, so NULLs
    // would make dedupe ambiguous on both sides of the comparison.
    const platform  = str(rp.service);
    const query     = str(rp.base_query);

    const citationsData = (r.citations_data ?? {}) as Row;

    const key = `${clientId}|${clusterId}|${platform}|${query}|${weekDate}`;
    if (seen.has(key)) continue;
    seen.add(key);

    out.push({
      client_id:      clientId,
      cluster_id:     clusterId,
      cluster_name:   r.cluster_name ?? null,
      week_date:      weekDate,
      platform,
      query,
      citations_data: citationsData,
      answers_list:   r.answers_list ?? [],
      citations_list: Object.keys(citationsData),
      synced_at:      nowIso(),
    });
  }
  return out;
}

// ── Mirror specs ──────────────────────────────────────────────

type MirrorSpec = {
  stateKey: string;
  source: string;
  target: string;
  columns: string;
  /** upsert_id: target id is uuid DEFAULT, source id can be carried.
   *  insert_if_absent: target id is GENERATED ALWAYS, id must be dropped. */
  mode: "upsert_id" | "insert_if_absent";
  /** cursor strategy. "updated_at_nullable" runs a second pass over rows whose
   *  updated_at is NULL, cursored on created_at, because `.gt()` drops NULLs. */
  cursor: "incremental" | "updated_at_nullable" | "full";
  dateCol: string;
  onConflict?: string;
  keyCols?: string[];
  orderCol?: string;
  /** Parent table in Scout whose client_id must exist (FK guard). */
  fkParent?: { table: string; col: string };
  fixRow?: (r: Row) => Row | null;
};

const MIRRORS: MirrorSpec[] = [
  {
    stateKey: "report_data", source: "report_data", target: "report_data",
    columns: "id,client_id,report_id,ai_visibility_data,website_files_data,schema_data,created_at,company_website,company_name,final_points",
    mode: "insert_if_absent", cursor: "incremental", dateCol: "created_at",
    keyCols: ["client_id", "report_id", "created_at"],
  },
  {
    // Must precede ai_monitoring_mirror: it is the FK parent.
    stateKey: "onboarding_mirror", source: "onboarding", target: "onboarding",
    columns: "id,client_id,company_domain,company_name,target_region,created_at,updated_at,status,industry,target_audience,competitors,topics,category_tags,primary_goal,tone_preference,average_order_value,conversion_rate,estimated_ctr,currency,client_plan,company_website,demo_ready,company_description,competitors_url,lead_source",
    mode: "upsert_id", cursor: "full", dateCol: "updated_at",
    onConflict: "id", orderCol: "id",
  },
  {
    stateKey: "ai_monitoring_mirror", source: "ai_monitoring", target: "ai_monitoring",
    // NOTE: GEO's ai_monitoring has NO `sov` column — only Scout's mirror does.
    // Selecting it made the whole request 42703 and this stream never synced a
    // single row. Omitted here; Scout's NOT NULL DEFAULT '{}' fills it on insert
    // and leaves any existing value untouched on update.
    columns: "id,user_id,request_payload,citations_data,companies_data,answers_list,citations_list,created_at,client_id,cluster_id,cluster_name",
    mode: "upsert_id", cursor: "incremental", dateCol: "created_at",
    onConflict: "id",
    fkParent: { table: "onboarding", col: "client_id" },
  },
  {
    stateKey: "user_metrics", source: "user_metrics", target: "user_metrics",
    columns: "client_id,average_order_value,conversion_rate,estimated_ctr,ga4_property_id,gsc_site_url,created_at,updated_at",
    mode: "upsert_id", cursor: "full", dateCol: "updated_at",
    onConflict: "client_id", orderCol: "client_id",
  },
  {
    stateKey: "queries_groups_data", source: "queries_groups_data", target: "queries_groups_data",
    columns: "id,created_at,user_id,queries_groups_data,client_id,updated_at",
    mode: "insert_if_absent", cursor: "full", dateCol: "created_at",
    keyCols: ["client_id", "created_at"], orderCol: "id",
  },
  {
    stateKey: "keywords_groups_data", source: "keywords_groups_data", target: "keywords_groups_data",
    columns: "id,created_at,user_id,keywords_groups_data,client_id",
    mode: "insert_if_absent", cursor: "full", dateCol: "created_at",
    keyCols: ["client_id", "created_at"], orderCol: "id",
  },
  {
    stateKey: "company_scraped_data_cache", source: "company_scraped_data_cache", target: "company_scraped_data_cache",
    columns: "id,created_at,url,source,summary,count,last_refreshed",
    mode: "insert_if_absent", cursor: "incremental", dateCol: "created_at",
    keyCols: ["url", "created_at"],
  },
  {
    stateKey: "selection_events", source: "selection_events", target: "selection_events",
    columns: "id,client_id,event_type,platform,query_text,response_text,was_selected,competitors_mentioned,created_at,attribution_event_id,revenue_attributed",
    mode: "upsert_id", cursor: "incremental", dateCol: "created_at",
    onConflict: "id",
  },
  {
    stateKey: "ga4_metrics", source: "ga4_metrics", target: "ga4_metrics",
    columns: "id,property_id,metric_date,source,medium,campaign,landing_page,country,device,sessions,engaged_sessions,total_users,new_users,conversions,revenue,created_at,updated_at",
    // GEO: updated_at is nullable (DEFAULT now() only covers inserts), and GEO's
    // ga4_metrics has no PK, so an explicit NULL can land. Keep the null tail.
    mode: "upsert_id", cursor: "updated_at_nullable", dateCol: "updated_at",
    onConflict: "id",
  },
  {
    stateKey: "gsc_query_page_metrics", source: "gsc_query_page_metrics", target: "gsc_query_page_metrics",
    columns: "id,site_url,metric_date,query,page,clicks,impressions,ctr,position,country,device,created_at,updated_at",
    // GEO: updated_at is NOT NULL DEFAULT now() -> single cursor is safe.
    mode: "upsert_id", cursor: "incremental", dateCol: "updated_at",
    onConflict: "id",
  },
];

// ── Stream runner ─────────────────────────────────────────────

type StreamStat = {
  fetched: number; written: number; failed: number;
  complete: boolean; watermark: string; status: string; note?: string;
};

/**
 * Watermark rule: advance to runStart only when the stream drained cleanly.
 * A capped run parks on the last clean timestamp; a failed batch holds the old
 * cursor so nothing is skipped. status/updated_at always move (heartbeat).
 */
function resolveWatermark(
  since: string, runStart: string, lastTs: string | null, complete: boolean, failed: number,
): { watermark: string; status: string } {
  if (failed > 0) return { watermark: since, status: "partial" };
  if (complete)   return { watermark: runStart, status: "ok" };
  return { watermark: lastTs ?? since, status: "capped" };
}

// ── Main handler ──────────────────────────────────────────────

Deno.serve(async (req) => {
  if (req.method !== "POST") return new Response("method not allowed", { status: 405 });

  let body: { streams?: string[]; maxRows?: number; deadlineMs?: number; dryRun?: boolean } = {};
  try { body = await req.json(); } catch { /* empty body is fine */ }

  const dry = Boolean(body.dryRun);

  const maxRows = Number(body.maxRows ?? DEFAULT_MAX_ROWS);
  const budget  = new Budget(Number(body.deadlineMs ?? DEFAULT_DEADLINE));
  const only    = body.streams && body.streams.length ? new Set(body.streams) : null;
  const wants   = (s: string) => !only || only.has(s);

  const runStart = nowIso();
  const log: string[] = [`[scout-sync] v2 start ${runStart}${dry ? " (DRY RUN — no writes)" : ""}`];
  const summary: Record<string, StreamStat> = {};
  let currentStream = "";

  const scoutFor = () => createClient(
    Deno.env.get("SCOUT_SUPABASE_URL")!, Deno.env.get("SCOUT_SUPABASE_SERVICE_KEY")!,
  );

  try {
    const platform = createClient(
      Deno.env.get("PLATFORM_SUPABASE_URL")!, Deno.env.get("PLATFORM_SUPABASE_SERVICE_KEY")!,
    );
    const scout = dry ? readOnlyScout(scoutFor()) : scoutFor();

    // ── 1. clients (full sweep of onboarding) ─────────────────
    // Full sweep, not cursored: onboarding.updated_at is nullable upstream, so
    // `.gt(updated_at, since)` silently hides rows that were never updated.
    // The table is one row per client, so a sweep is cheap and always correct.
    if (wants("onboarding")) {
      currentStream = "onboarding";
      const since = await getSyncState(scout, "onboarding");
      const raw = await fetchAllRows(platform, "onboarding",
        "client_id,company_name,company_domain,company_website,industry,target_region,competitors,competitors_url,topics,updated_at,created_at",
        "client_id", budget);
      const rows = transformClients(raw);
      const res  = rows.length ? await upsertBatches(scout, "clients", rows, "client_id") : { written: 0, failed: 0 };
      const wm   = resolveWatermark(since, runStart, null, true, res.failed);
      summary.clients = { fetched: raw.length, ...res, complete: true, ...wm };
      log.push(`clients: fetched=${raw.length} written=${res.written} failed=${res.failed}`);
      await setSyncState(scout, "onboarding", wm.watermark, wm.status,
        res.failed ? `${res.failed} rows failed` : null);
    }

    // Client ids that exist in Scout — required by the FKs on clusters,
    // sov_weekly and ai_responses.
    const validClients = await loadIdSet(scout, "clients", "client_id", budget);
    log.push(`clients in scout: ${validClients.size}`);

    // ── 2. clusters + sov_weekly ──────────────────────────────
    if (wants("cluster_visibility_scores")) {
      currentStream = "cluster_visibility_scores";
      const since = await getSyncState(scout, "cluster_visibility_scores");
      const cols  = "id,client_id,cluster_id,cluster_name,top_companies,selected_queries,top_sources,service,created_at,updated_at";

      // Pass A: rows with a real updated_at.  Pass B: null-updated_at tail on created_at.
      const a = await fetchIncremental(platform, "cluster_visibility_scores", cols,
        "updated_at", since, maxRows, budget, { filter: (q) => q.not("updated_at", "is", null) });
      const sinceB = await getSyncState(scout, "cluster_visibility_scores:nullcursor");
      const b = await fetchIncremental(platform, "cluster_visibility_scores", cols,
        "created_at", sinceB, maxRows, budget, { filter: (q) => q.is("updated_at", null) });

      const raw = [...a.rows, ...b.rows];
      const filtered = raw.filter((r) => validClients.has(str(r.client_id)));
      if (raw.length !== filtered.length) log.push(`sov: dropped ${raw.length - filtered.length} orphan rows`);

      const clusterRows = transformClusters(filtered);
      const clRes = clusterRows.length
        ? await upsertBatches(scout, "clusters", clusterRows, "cluster_id,client_id")
        : { written: 0, failed: 0 };

      // sov_weekly has no unique index on (client_id, cluster_id, week_date).
      const sovRows = transformSovWeekly(filtered);
      const minWeek = sovRows.reduce((m, r) => (str(r.week_date) < m ? str(r.week_date) : m),
        sovRows.length ? str(sovRows[0].week_date) : "9999-12-31");
      let sovRes: WriteResult = { written: 0, failed: 0 };
      for (const ids of chunk([...new Set(sovRows.map((r) => str(r.client_id)))], CHUNK_IN)) {
        const slice = sovRows.filter((r) => ids.includes(str(r.client_id)));
        sovRes = merge(sovRes, await upsertByNaturalKey(
          scout, "sov_weekly", slice, ["client_id", "cluster_id", "week_date"],
          (q) => q.in("client_id", ids).gte("week_date", minWeek), budget,
        ));
      }

      const complete = a.complete && b.complete;
      const failed   = clRes.failed + sovRes.failed;
      const wmA = resolveWatermark(since,  runStart, a.lastTs, a.complete, failed);
      const wmB = resolveWatermark(sinceB, runStart, b.lastTs, b.complete, failed);

      summary.clusters   = { fetched: filtered.length, ...clRes,  complete, ...wmA };
      summary.sov_weekly = { fetched: sovRows.length,  ...sovRes, complete, ...wmA };
      log.push(`sov: fetched=${raw.length} clusters=${clRes.written} sov=${sovRes.written} failed=${failed}`);

      await setSyncState(scout, "cluster_visibility_scores", wmA.watermark, wmA.status,
        failed ? `${failed} rows failed` : null);
      await setSyncState(scout, "cluster_visibility_scores:nullcursor", wmB.watermark, wmB.status, null);
    }

    // ── 3. ai_responses (transformed from ai_monitoring) ──────
    if (wants("ai_monitoring")) {
      currentStream = "ai_monitoring";
      const since = await getSyncState(scout, "ai_monitoring");
      const { rows: raw, lastTs, complete } = await fetchIncremental(
        platform, "ai_monitoring",
        "id,client_id,cluster_id,cluster_name,citations_data,answers_list,request_payload,created_at",
        "created_at", since, maxRows, budget,
      );

      // FK: ai_responses.client_id -> clients.client_id
      const rows = transformAiResponses(raw).filter((r) => validClients.has(str(r.client_id)));
      const dropped = raw.length - rows.length;

      const minWeek = rows.reduce((m, r) => (str(r.week_date) < m ? str(r.week_date) : m),
        rows.length ? str(rows[0].week_date) : "9999-12-31");
      let res: WriteResult = { written: 0, failed: 0 };
      for (const ids of chunk([...new Set(rows.map((r) => str(r.client_id)))], CHUNK_IN)) {
        const slice = rows.filter((r) => ids.includes(str(r.client_id)));
        res = merge(res, await upsertByNaturalKey(
          scout, "ai_responses", slice,
          ["client_id", "cluster_id", "platform", "query", "week_date"],
          (q) => q.in("client_id", ids).gte("week_date", minWeek), budget,
        ));
      }

      const wm = resolveWatermark(since, runStart, lastTs, complete, res.failed);
      summary.ai_responses = { fetched: raw.length, ...res, complete, ...wm,
        note: dropped ? `${dropped} rows dropped (no matching client)` : undefined };
      log.push(`ai_responses: fetched=${raw.length} written=${res.written} failed=${res.failed} dropped=${dropped}`);
      await setSyncState(scout, "ai_monitoring", wm.watermark, wm.status,
        res.failed ? `${res.failed} rows failed` : null);
    }

    // ── 4. raw mirrors ────────────────────────────────────────
    for (const m of MIRRORS) {
      if (!wants(m.stateKey)) continue;
      if (budget.expired()) { log.push(`${m.target}: skipped (budget)`); continue; }
      currentStream = m.stateKey;

      const since = await getSyncState(scout, m.stateKey);
      let raw: Row[] = [];
      let lastTs: string | null = null;
      let complete = true;
      let sinceB = "", lastTsB: string | null = null, completeB = true;

      if (m.cursor === "full") {
        raw = await fetchAllRows(platform, m.source, m.columns, m.orderCol ?? "id", budget);
      } else if (m.cursor === "updated_at_nullable") {
        // updated_at is nullable upstream and `.gt()` drops NULLs entirely,
        // so rows that were only ever inserted need their own created_at cursor.
        const a = await fetchIncremental(platform, m.source, m.columns, "updated_at",
          since, maxRows, budget, { filter: (q) => q.not("updated_at", "is", null) });
        sinceB = await getSyncState(scout, `${m.stateKey}:nullcursor`);
        const b = await fetchIncremental(platform, m.source, m.columns, "created_at",
          sinceB, maxRows, budget, { filter: (q) => q.is("updated_at", null) });
        raw = [...a.rows, ...b.rows];
        lastTs = a.lastTs; complete = a.complete;
        lastTsB = b.lastTs; completeB = b.complete;
      } else {
        const a = await fetchIncremental(platform, m.source, m.columns, m.dateCol,
          since, maxRows, budget);
        raw = a.rows; lastTs = a.lastTs; complete = a.complete;
      }

      let rows = raw;
      if (m.fixRow) rows = rows.map(m.fixRow).filter((r): r is Row => r !== null);

      let dropped = 0;
      if (m.fkParent) {
        const parents = await loadIdSet(scout, m.fkParent.table, m.fkParent.col, budget);
        const before = rows.length;
        rows = rows.filter((r) => parents.has(str(r[m.fkParent!.col])));
        dropped = before - rows.length;
      }

      const res = m.mode === "upsert_id"
        ? (rows.length ? await upsertBatches(scout, m.target, rows, m.onConflict!) : { written: 0, failed: 0 })
        : await insertIfAbsent(scout, m.target, rows, m.keyCols!, m.dateCol === "updated_at" ? "created_at" : m.dateCol, budget);

      const wm = resolveWatermark(since, runStart, lastTs, complete && completeB, res.failed);
      summary[m.target] = { fetched: raw.length, ...res, complete: complete && completeB, ...wm,
        note: dropped ? `${dropped} rows dropped (FK parent missing)` : undefined };
      log.push(`${m.target}: fetched=${raw.length} written=${res.written} failed=${res.failed}${dropped ? ` dropped=${dropped}` : ""}`);

      await setSyncState(scout, m.stateKey, wm.watermark, wm.status,
        res.failed ? `${res.failed} rows failed` : null);
      if (m.cursor === "updated_at_nullable") {
        const wmB = resolveWatermark(sinceB, runStart, lastTsB, completeB, res.failed);
        await setSyncState(scout, `${m.stateKey}:nullcursor`, wmB.watermark, wmB.status, null);
      }
    }

    const anyFailed = Object.values(summary).some((s) => s.failed > 0);
    const anyCapped = Object.values(summary).some((s) => !s.complete);
    log.push(`[scout-sync] done failed=${anyFailed} capped=${anyCapped} budgetLeftMs=${budget.remainingMs}`);
    console.log(log.join("\n"));

    return new Response(JSON.stringify({ ok: !anyFailed, dryRun: dry, capped: anyCapped, summary, log }), {
      headers: { "Content-Type": "application/json" }, status: 200,
    });

  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`[scout-sync] ERROR in ${currentStream}: ${msg}`);
    try {
      const scoutErr = scoutFor();
      if (currentStream && !dry) {
        // Hold the cursor where it was; only the heartbeat moves.
        const since = await getSyncState(scoutErr, currentStream);
        await setSyncState(scoutErr, currentStream, since, "error", msg);
      }
    } catch { /* best effort */ }
    return new Response(JSON.stringify({ ok: false, stream: currentStream, error: msg, log }), {
      headers: { "Content-Type": "application/json" }, status: 500,
    });
  }
});