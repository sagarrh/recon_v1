/**
 * scout-sync/index.ts
 * ─────────────────────────────────────────────────────────────
 * Supabase Edge Function — incremental sync from platform (GEO) DB
 * to the Scout agentic DB. Runs every 30 minutes via pg_cron.
 *
 * Only processes rows where the platform cursor column (updated_at,
 * or created_at where a table has none) > last_synced_at, so
 * unchanged data is never touched.
 *
 * Targets:
 *   Transformed: clients, clusters, sov_weekly, ai_responses
 *   Raw mirrors (read by the Scout GEO-reuse bridge): report_data,
 *     onboarding, ai_monitoring, user_metrics, queries_groups_data,
 *     keywords_groups_data, company_scraped_data_cache, selection_events,
 *     ga4_metrics, gsc_query_page_metrics (Tier-1 revenue)
 *
 * Env vars (Supabase Dashboard → Edge Functions → Secrets):
 *   PLATFORM_SUPABASE_URL
 *   PLATFORM_SUPABASE_SERVICE_KEY
 *   SCOUT_SUPABASE_URL
 *   SCOUT_SUPABASE_SERVICE_KEY
 *
 * Schedule (pg_cron, every 30 min):
 *   select cron.schedule(
 *     'scout-sync', '*\/30 * * * *',
 *     $$ select net.http_post(
 *          url := 'https://<scout-ref>.functions.supabase.co/scout-sync',
 *          headers := '{"Authorization":"Bearer <SCOUT_SERVICE_KEY>","Content-Type":"application/json"}'::jsonb,
 *          body := '{}'::jsonb) $$);
 */

import { createClient, SupabaseClient } from "https://esm.sh/@supabase/supabase-js@2";

const PAGE_SIZE   = 500;
const UPSERT_SIZE = 200;

// ── Helpers ──────────────────────────────────────────────────

function mondayOf(d: Date): string {
  const day = new Date(d);
  day.setUTCHours(0, 0, 0, 0);
  const diff = day.getUTCDay() === 0 ? -6 : 1 - day.getUTCDay();
  day.setUTCDate(day.getUTCDate() + diff);
  return day.toISOString().split("T")[0];
}

function nowIso(): string {
  return new Date().toISOString();
}

async function fetchAll(
  client: SupabaseClient,
  table: string,
  columns: string,
  since: string,
  clientId?: string,
  dateCol: string = "updated_at"
): Promise<Record<string, unknown>[]> {
  const rows: Record<string, unknown>[] = [];
  let offset = 0;
  while (true) {
    let q = client
      .from(table)
      .select(columns)
      .gt(dateCol, since)
      .order(dateCol, { ascending: true })
      .range(offset, offset + PAGE_SIZE - 1);
    if (clientId) q = q.eq("client_id", clientId);
    const { data, error } = await q;
    if (error) throw new Error(`fetch ${table}: ${error.message}`);
    rows.push(...(data ?? []));
    if ((data ?? []).length < PAGE_SIZE) break;
    offset += PAGE_SIZE;
  }
  return rows;
}

async function upsertAll(
  client: SupabaseClient,
  table: string,
  rows: Record<string, unknown>[],
  onConflict: string
): Promise<{ written: number; failed: number }> {
  let written = 0, failed = 0;
  for (let i = 0; i < rows.length; i += UPSERT_SIZE) {
    const batch = rows.slice(i, i + UPSERT_SIZE);
    const { error } = await client
      .from(table)
      .upsert(batch, { onConflict });
    if (error) {
      console.error(`upsert ${table} batch ${i}: ${error.message}`);
      failed += batch.length;
    } else {
      written += batch.length;
    }
  }
  return { written, failed };
}

async function getSyncState(
  scout: SupabaseClient,
  tableName: string
): Promise<string> {
  const { data, error } = await scout
    .from("sync_state")
    .select("last_synced_at")
    .eq("table_name", tableName)
    .single();
  if (error || !data) return "2000-01-01T00:00:00+00:00";
  return data.last_synced_at as string;
}

async function setSyncState(
  scout: SupabaseClient,
  tableName: string,
  ts: string,
  status: string = "ok",
  errorMessage: string | null = null
): Promise<void> {
  // R1-2: heartbeat upserts on EVERY run (status proves "I ran", not "I changed data").
  // status / error_message / updated_at columns are added by sync_state_status.sql.
  await scout
    .from("sync_state")
    .upsert(
      { table_name: tableName, last_synced_at: ts, status, error_message: errorMessage, updated_at: new Date().toISOString() },
      { onConflict: "table_name" }
    );
}

// ── Transform functions ───────────────────────────────────────

function transformClients(rows: Record<string, unknown>[]): Record<string, unknown>[] {
  const seen = new Map<string, Record<string, unknown>>();
  for (const r of rows) {
    const id = r.client_id as string;
    if (!id) continue;
    seen.set(id, {
      client_id:       id,
      client_name:     r.company_name,
      company_domain:  r.company_domain,
      company_website: r.company_website,
      industry:        r.industry,
      target_region:   r.target_region ?? "us",
      competitors:     r.competitors ?? [],
      competitors_url: r.competitors_url,
      topics:          r.topics ?? [],
      synced_at:       nowIso(),
    });
  }
  return [...seen.values()];
}

function transformClusters(rows: Record<string, unknown>[]): Record<string, unknown>[] {
  const bucket = new Map<string, { ts: string; row: Record<string, unknown> }>();
  for (const r of rows) {
    const clientId  = r.client_id as string;
    const clusterId = r.cluster_id as string;
    if (!clientId || !clusterId) continue;
    const key = `${clientId}|${clusterId}`;
    const ts  = (r.updated_at ?? r.created_at ?? "") as string;
    const ex  = bucket.get(key);
    if (!ex || ts > ex.ts) {
      bucket.set(key, {
        ts,
        row: { cluster_id: clusterId, client_id: clientId, cluster_name: r.cluster_name, synced_at: nowIso() },
      });
    }
  }
  return [...bucket.values()].map((v) => v.row);
}

function transformSovWeekly(rows: Record<string, unknown>[]): Record<string, unknown>[] {
  const bucket = new Map<string, { ts: string; row: Record<string, unknown> }>();
  for (const r of rows) {
    const clientId   = r.client_id as string;
    const clusterId  = r.cluster_id as string;
    const topRaw     = r.top_companies as Record<string, Record<string, { total_visibility: number }>> | null;
    const createdAt  = r.created_at as string;
    const updatedAt  = (r.updated_at ?? createdAt) as string;
    if (!clientId || !clusterId || !topRaw || !createdAt) continue;

    const baseMonday = mondayOf(new Date(createdAt));

    for (const [weekKey, companies] of Object.entries(topRaw)) {
      if (typeof companies !== "object") continue;
      const n = parseInt(weekKey.replace(/\D/g, ""), 10);
      if (isNaN(n) || n < 1) continue;

      const weekDate = new Date(baseMonday);
      weekDate.setUTCDate(weekDate.getUTCDate() - (n - 1) * 7);
      const weekStr = weekDate.toISOString().split("T")[0];

      const scored = Object.entries(companies)
        .filter(([, v]) => typeof v === "object" && v?.total_visibility != null)
        .map(([name, v]) => ({ name, sov_score: Math.round((v as {total_visibility:number}).total_visibility * 10000) / 100 }))
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
            cluster_name:     r.cluster_name,
            week_date:        weekStr,
            top_companies:    scored,
            selected_queries: r.selected_queries,
            top_sources:      r.top_sources,
            service:          r.service,
            synced_at:        nowIso(),
          },
        });
      }
    }
  }
  return [...bucket.values()].map((v) => v.row);
}

function transformAiResponses(rows: Record<string, unknown>[]): Record<string, unknown>[] {
  const seen = new Set<string>();
  const out: Record<string, unknown>[] = [];
  for (const r of rows) {
    const clientId  = r.client_id as string;
    if (!clientId) continue;
    const clusterId = (r.cluster_id ?? "") as string;
    const createdAt = r.created_at as string;
    if (!createdAt) continue;
    const weekDate  = mondayOf(new Date(createdAt));

    const rp        = (r.request_payload ?? {}) as Record<string, unknown>;
    const platform  = (rp.service ?? null) as string | null;
    const query     = (rp.base_query ?? null) as string | null;

    const citationsData  = (r.citations_data ?? {}) as Record<string, unknown>;
    const citationsList  = Object.keys(citationsData);
    const answersList    = r.answers_list ?? [];

    const key = `${clientId}|${clusterId}|${platform}|${query}|${weekDate}`;
    if (seen.has(key)) continue;
    seen.add(key);

    out.push({
      client_id:      clientId,
      cluster_id:     clusterId,
      cluster_name:   r.cluster_name,
      week_date:      weekDate,
      platform,
      query,
      citations_data: citationsData,
      answers_list:   answersList,
      citations_list: citationsList,
      synced_at:      nowIso(),
    });
  }
  return out;
}

// ── Raw-mirror table specs (read by the Scout GEO-reuse bridge) ──
// Straight copy of GEO source rows into identically-shaped Scout mirror tables.
// `dateCol` is the incremental cursor; `stateKey` namespaces the sync_state row
// (must NOT collide with the transformed syncs above, e.g. "onboarding").
// user_metrics copies the ga4_property_id/gsc_site_url handles (the revenue bridge needs them) but still
// EXCLUDES the ga4_refresh_token/gsc_refresh_token secrets. ga4_metrics/gsc_query_page_metrics copy the source
// uuid id and upsert onConflict:"id" — never on the nullable natural key (NULLs are distinct -> silent dupes).
const MIRRORS: {
  stateKey: string; source: string; target: string;
  columns: string; onConflict: string; dateCol: string;
}[] = [
  {
    stateKey: "report_data", source: "report_data", target: "report_data",
    columns: "id,client_id,report_id,ai_visibility_data,website_files_data,schema_data,created_at,company_website,company_name,final_points",
    onConflict: "id", dateCol: "created_at",
  },
  {
    stateKey: "onboarding_mirror", source: "onboarding", target: "onboarding",
    columns: "id,client_id,company_domain,company_name,target_region,created_at,updated_at,status,industry,target_audience,competitors,topics,category_tags,primary_goal,tone_preference,average_order_value,conversion_rate,estimated_ctr,currency,client_plan,company_website,demo_ready,company_description,competitors_url,lead_source",
    onConflict: "id", dateCol: "updated_at",
  },
  {
    // Full raw mirror of platform ai_monitoring. This is intentionally separate
    // from the transformed ai_responses stream above, so existing ai_responses
    // behavior remains unchanged and the mirror gets its own historical backfill cursor.
    stateKey: "ai_monitoring_mirror", source: "ai_monitoring", target: "ai_monitoring",
    columns: "id,user_id,request_payload,citations_data,companies_data,answers_list,citations_list,created_at,client_id,cluster_id,cluster_name,sov",
    onConflict: "id", dateCol: "created_at",
  },
  {
    stateKey: "user_metrics", source: "user_metrics", target: "user_metrics",
    columns: "client_id,average_order_value,conversion_rate,estimated_ctr,ga4_property_id,gsc_site_url,created_at,updated_at",
    onConflict: "client_id", dateCol: "updated_at",
  },
  {
    stateKey: "queries_groups_data", source: "queries_groups_data", target: "queries_groups_data",
    columns: "id,created_at,user_id,queries_groups_data,client_id,updated_at",
    onConflict: "id", dateCol: "created_at",
  },
  {
    stateKey: "keywords_groups_data", source: "keywords_groups_data", target: "keywords_groups_data",
    columns: "id,created_at,user_id,keywords_groups_data,client_id",
    onConflict: "id", dateCol: "created_at",
  },
  {
    stateKey: "company_scraped_data_cache", source: "company_scraped_data_cache", target: "company_scraped_data_cache",
    columns: "id,created_at,url,source,summary,count,last_refreshed",
    onConflict: "id", dateCol: "created_at",
  },
  {
    stateKey: "selection_events", source: "selection_events", target: "selection_events",
    columns: "id,client_id,event_type,platform,query_text,response_text,was_selected,competitors_mentioned,created_at,attribution_event_id,revenue_attributed",
    onConflict: "id", dateCol: "created_at",
  },
  {
    stateKey: "ga4_metrics", source: "ga4_metrics", target: "ga4_metrics",
    columns: "id,property_id,metric_date,source,medium,campaign,landing_page,country,device,sessions,engaged_sessions,total_users,new_users,conversions,revenue,created_at,updated_at",
    onConflict: "id", dateCol: "updated_at",
  },
  {
    stateKey: "gsc_query_page_metrics", source: "gsc_query_page_metrics", target: "gsc_query_page_metrics",
    columns: "id,site_url,metric_date,query,page,clicks,impressions,ctr,position,country,device,created_at,updated_at",
    onConflict: "id", dateCol: "updated_at",
  },
];

// ── Main handler ──────────────────────────────────────────────

Deno.serve(async (req) => {
  // Only accept POST
  if (req.method !== "POST") {
    return new Response("method not allowed", { status: 405 });
  }

  const runStart = new Date().toISOString();
  const log: string[] = [`[scout-sync] started at ${runStart}`];
  let currentStream = "";   // R1-2: stream in progress, used to stamp an error heartbeat in catch

  try {
    const platform = createClient(
      Deno.env.get("PLATFORM_SUPABASE_URL")!,
      Deno.env.get("PLATFORM_SUPABASE_SERVICE_KEY")!
    );
    const scout = createClient(
      Deno.env.get("SCOUT_SUPABASE_URL")!,
      Deno.env.get("SCOUT_SUPABASE_SERVICE_KEY")!
    );

    const summary: Record<string, { fetched: number; written: number; failed: number }> = {};

    // ── 1. clients (from onboarding) ──────────────────────────
    {
      currentStream = "onboarding";
      const since = await getSyncState(scout, "onboarding");
      log.push(`clients: syncing since ${since}`);
      const raw = await fetchAll(platform, "onboarding",
        "client_id,company_name,company_domain,company_website,industry,target_region,competitors,competitors_url,topics,updated_at,created_at",
        since
      );
      const rows = transformClients(raw);
      log.push(`clients: fetched=${raw.length} transformed=${rows.length}`);
      const { written, failed } = rows.length > 0
        ? await upsertAll(scout, "clients", rows, "client_id")
        : { written: 0, failed: 0 };
      summary.clients = { fetched: raw.length, written, failed };
      await setSyncState(scout, "onboarding", runStart);   // R1-2: unconditional heartbeat
    }

    // ── 2. clusters + sov_weekly (from cluster_visibility_scores) ──
    {
      currentStream = "cluster_visibility_scores";
      const since = await getSyncState(scout, "cluster_visibility_scores");
      log.push(`sov: syncing since ${since}`);
      const raw = await fetchAll(platform, "cluster_visibility_scores",
        "client_id,cluster_id,cluster_name,top_companies,selected_queries,top_sources,service,created_at,updated_at",
        since
      );
      log.push(`sov: fetched=${raw.length}`);

      // Get valid client_ids from scout to filter orphans
      const { data: validClients } = await scout.from("clients").select("client_id");
      const validIds = new Set((validClients ?? []).map((c: {client_id: string}) => c.client_id));
      const filtered = raw.filter((r) => validIds.has(r.client_id as string));
      const orphans  = raw.length - filtered.length;
      if (orphans > 0) log.push(`sov: skipped ${orphans} orphaned rows`);

      // Clusters
      const clusterRows = transformClusters(filtered);
      const clRes = clusterRows.length > 0
        ? await upsertAll(scout, "clusters", clusterRows, "cluster_id,client_id")
        : { written: 0, failed: 0 };
      summary.clusters = { fetched: filtered.length, ...clRes };

      // SOV weekly
      const sovRows = transformSovWeekly(filtered);
      const sovRes  = sovRows.length > 0
        ? await upsertAll(scout, "sov_weekly", sovRows, "client_id,cluster_id,week_date")
        : { written: 0, failed: 0 };
      summary.sov_weekly = { fetched: filtered.length, written: sovRes.written, failed: sovRes.failed };

      await setSyncState(scout, "cluster_visibility_scores", runStart);   // R1-2: unconditional heartbeat
    }

    // ── 3. ai_responses (from ai_monitoring) ──────────────────
    {
      currentStream = "ai_monitoring";
      const since = await getSyncState(scout, "ai_monitoring");
      log.push(`ai: syncing since ${since}`);
      const raw = await fetchAll(platform, "ai_monitoring",
        "client_id,cluster_id,cluster_name,citations_data,answers_list,request_payload,created_at",
        since,
        undefined,
        "created_at"   // ai_monitoring has no updated_at
      );
      const rows = transformAiResponses(raw);
      log.push(`ai: fetched=${raw.length} transformed=${rows.length}`);
      const { written, failed } = rows.length > 0
        ? await upsertAll(scout, "ai_responses", rows, "client_id,cluster_id,platform,query,week_date")
        : { written: 0, failed: 0 };
      summary.ai_responses = { fetched: raw.length, written, failed };
      await setSyncState(scout, "ai_monitoring", runStart);   // R1-2: unconditional heartbeat
    }

    // ── 4. raw mirrors for the GEO-reuse bridge ───────────────
    // Straight copy (no transform) into identically-shaped Scout tables.
    for (const m of MIRRORS) {
      currentStream = m.stateKey;
      const since = await getSyncState(scout, m.stateKey);
      log.push(`${m.target}: syncing since ${since}`);
      const raw = await fetchAll(platform, m.source, m.columns, since, undefined, m.dateCol);
      log.push(`${m.target}: fetched=${raw.length}`);
      const { written, failed } = raw.length > 0
        ? await upsertAll(scout, m.target, raw, m.onConflict)
        : { written: 0, failed: 0 };
      summary[m.target] = { fetched: raw.length, written, failed };
      await setSyncState(scout, m.stateKey, runStart);   // R1-2: unconditional heartbeat
    }

    log.push(`[scout-sync] completed`);
    console.log(log.join("\n"));

    return new Response(JSON.stringify({ ok: true, summary, log }), {
      headers: { "Content-Type": "application/json" },
      status: 200,
    });

  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`[scout-sync] ERROR: ${msg}`);
    // R1-2: stamp the in-progress stream as errored so R1-1's freshness gate can tell dead from stale.
    try {
      const scoutErr = createClient(Deno.env.get("SCOUT_SUPABASE_URL")!, Deno.env.get("SCOUT_SUPABASE_SERVICE_KEY")!);
      if (currentStream) await setSyncState(scoutErr, currentStream, runStart, "error", msg);
    } catch (_e) { /* best-effort error heartbeat */ }
    return new Response(JSON.stringify({ ok: false, error: msg }), {
      headers: { "Content-Type": "application/json" },
      status: 500,
    });
  }
});
