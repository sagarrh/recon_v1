/*
  Frontend-aligned Recon Agent report payload (read only)
  =======================================================

  This query intentionally returns ONLY data rendered by the current client
  frontend. It does not return raw monitoring feeds, prompt logs, full GEO
  leaderboards, internal reports, Slack reports, validation notes, pipeline
  diagnostics, onboarding, or website-readiness data.

  The application supplies an exact clients.client_id, optional report week,
  and SOV-history window. It executes this query in a read-only transaction.
*/

WITH
params AS (
  SELECT
    %s::uuid AS client_id,
    %s::date AS report_week,
    %s::integer AS sov_history_weeks
),

client_profile AS (
  SELECT c.*, p.report_week, p.sov_history_weeks
  FROM clients c
  JOIN params p ON p.client_id = c.client_id
),

reporting_period AS (
  SELECT
    cp.*,
    COALESCE(
      cp.report_week,
      (
        SELECT MAX(
          DATE_TRUNC('week', am.created_at AT TIME ZONE 'UTC')::date
        )
        FROM ai_monitoring am
        WHERE am.client_id = cp.client_id
          AND jsonb_typeof(am.sov) = 'object'
          AND am.sov <> '{}'::jsonb
      ),
      (
        SELECT MAX(st.week_date)
        FROM sov_tracking st
        WHERE st.client_id = cp.client_id
      ),
      CURRENT_DATE
    ) AS as_of_week
  FROM client_profile cp
),

client_clusters AS (
  SELECT c.cluster_id, c.cluster_name
  FROM clusters c
  JOIN reporting_period rp ON rp.client_id = c.client_id
),

/*
  Canonical client-facing SOV from ai_monitoring
  ==============================================

  ai_monitoring.sov is the single source for BOTH current SOV and SOV history.
  This prevents a dated sov_tracking snapshot from conflicting with an undated
  calculated_sov array.

  Method:
    - one ai_monitoring row = one monitored AI answer;
    - company SOV comes from that row's `sov` JSON object;
    - tracked companies absent from an answer are counted as 0%%;
    - weekly SOV is the average across all answers in that topic/week;
    - common legal suffix aliases are normalized for matching;
    - the output is explicitly dated and identifies its source/methodology.
*/

tracked_entities AS (
  SELECT DISTINCT ON (normalized_name)
    canonical_name,
    is_client,
    normalized_name
  FROM (
    SELECT
      entity.canonical_name,
      entity.is_client,
      REGEXP_REPLACE(
        REGEXP_REPLACE(
          LOWER(entity.canonical_name),
          E'\\m(incorporated|inc|llc|llp|lp|ltd|limited|plc|company|co|usa|us)\\M',
          ' ',
          'g'
        ),
        '[^a-z0-9]+',
        '',
        'g'
      ) AS normalized_name
    FROM (
      SELECT
        rp.client_name AS canonical_name,
        TRUE AS is_client
      FROM reporting_period rp

      UNION ALL

      SELECT
        competitor.value AS canonical_name,
        FALSE AS is_client
      FROM reporting_period rp
      CROSS JOIN LATERAL jsonb_array_elements_text(
        COALESCE(to_jsonb(rp.competitors), '[]'::jsonb)
      ) AS competitor(value)
    ) entity
    WHERE entity.canonical_name IS NOT NULL
      AND BTRIM(entity.canonical_name) <> ''
  ) normalized
  WHERE normalized_name <> ''
  ORDER BY normalized_name, is_client DESC
),

ai_monitoring_runs AS (
  SELECT
    am.id AS monitoring_id,
    am.cluster_id,
    COALESCE(am.cluster_name, cc.cluster_name) AS cluster_name,
    DATE_TRUNC('week', am.created_at AT TIME ZONE 'UTC')::date AS week_date,
    NULLIF(am.request_payload ->> 'service', '') AS platform,
    am.sov
  FROM ai_monitoring am
  JOIN reporting_period rp ON rp.client_id = am.client_id
  LEFT JOIN client_clusters cc ON cc.cluster_id = am.cluster_id
  WHERE jsonb_typeof(am.sov) = 'object'
    AND am.sov <> '{}'::jsonb
    AND DATE_TRUNC('week', am.created_at AT TIME ZONE 'UTC')::date <= rp.as_of_week
    AND DATE_TRUNC('week', am.created_at AT TIME ZONE 'UTC')::date
        >= rp.as_of_week - (rp.sov_history_weeks * 7)
),

run_company_sov_raw AS (
  SELECT
    run.monitoring_id,
    run.cluster_id,
    run.cluster_name,
    run.week_date,
    run.platform,
    company.key AS source_company_name,
    REGEXP_REPLACE(
      REGEXP_REPLACE(
        LOWER(company.key),
        E'\\m(incorporated|inc|llc|llp|lp|ltd|limited|plc|company|co|usa|us)\\M',
        ' ',
        'g'
      ),
      '[^a-z0-9]+',
      '',
      'g'
    ) AS normalized_company_name,
    CASE
      WHEN jsonb_typeof(company.value) = 'number'
        THEN GREATEST((company.value #>> '{}')::numeric, 0::numeric)
      WHEN jsonb_typeof(company.value) = 'string'
       AND (company.value #>> '{}') ~ '^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)$'
        THEN GREATEST((company.value #>> '{}')::numeric, 0::numeric)
      ELSE 0::numeric
    END AS sov
  FROM ai_monitoring_runs run
  CROSS JOIN LATERAL jsonb_each(run.sov) AS company(key, value)
),

run_company_sov AS (
  SELECT
    raw.monitoring_id,
    raw.cluster_id,
    raw.cluster_name,
    raw.week_date,
    raw.platform,
    raw.normalized_company_name,
    COALESCE(MAX(tracked.canonical_name), MIN(raw.source_company_name)) AS company_name,
    COALESCE(BOOL_OR(tracked.is_client), FALSE) AS is_client,
    LEAST(SUM(raw.sov), 100::numeric) AS sov
  FROM run_company_sov_raw raw
  LEFT JOIN tracked_entities tracked
    ON tracked.normalized_name = raw.normalized_company_name
  WHERE raw.normalized_company_name <> ''
  GROUP BY
    raw.monitoring_id,
    raw.cluster_id,
    raw.cluster_name,
    raw.week_date,
    raw.platform,
    raw.normalized_company_name
),

run_tracked_sov AS (
  SELECT
    run.monitoring_id,
    run.cluster_id,
    run.cluster_name,
    run.week_date,
    run.platform,
    tracked.canonical_name AS company_name,
    tracked.normalized_name,
    tracked.is_client,
    COALESCE(company.sov, 0::numeric) AS sov
  FROM ai_monitoring_runs run
  CROSS JOIN tracked_entities tracked
  LEFT JOIN run_company_sov company
    ON company.monitoring_id = run.monitoring_id
   AND company.cluster_id IS NOT DISTINCT FROM run.cluster_id
   AND company.normalized_company_name = tracked.normalized_name
),

weekly_tracked_sov AS (
  SELECT
    tracked.cluster_id,
    tracked.cluster_name,
    tracked.week_date,
    tracked.company_name,
    tracked.is_client,
    ROUND(AVG(tracked.sov), 2) AS current_sov,
    COUNT(DISTINCT tracked.monitoring_id)::integer AS answers_analyzed,
    ARRAY_REMOVE(
      ARRAY_AGG(DISTINCT tracked.platform ORDER BY tracked.platform),
      NULL
    ) AS platforms_analyzed
  FROM run_tracked_sov tracked
  GROUP BY
    tracked.cluster_id,
    tracked.cluster_name,
    tracked.week_date,
    tracked.company_name,
    tracked.is_client
),

weekly_sov_with_client AS (
  SELECT
    weekly.*,
    MAX(weekly.current_sov) FILTER (WHERE weekly.is_client) OVER (
      PARTITION BY weekly.cluster_id, weekly.week_date
    ) AS client_sov_this_week
  FROM weekly_tracked_sov weekly
),

weekly_sov_with_change AS (
  SELECT
    weekly.*,
    LAG(weekly.current_sov) OVER (
      PARTITION BY weekly.cluster_id, weekly.company_name
      ORDER BY weekly.week_date
    ) AS previous_sov
  FROM weekly_sov_with_client weekly
),

sov_history AS (
  SELECT
    weekly.cluster_id,
    weekly.cluster_name,
    weekly.company_name AS competitor_name,
    weekly.week_date,
    weekly.current_sov,
    weekly.client_sov_this_week,
    CASE
      WHEN weekly.previous_sov IS NULL THEN NULL
      ELSE ROUND(weekly.current_sov - weekly.previous_sov, 2)
    END AS sov_delta_pp,
    CASE
      WHEN weekly.previous_sov IS NULL THEN 'no_comparison'
      WHEN weekly.current_sov - weekly.previous_sov > 0 THEN 'gain'
      WHEN weekly.current_sov - weekly.previous_sov < 0 THEN 'loss'
      ELSE 'stable'
    END AS alert_flag,
    NULL::boolean AS is_significant,
    NULL::numeric AS z_score,
    weekly.answers_analyzed,
    weekly.platforms_analyzed,
    'ai_monitoring.sov'::text AS source,
    'prompt_level_weekly_average_v1'::text AS methodology_version
  FROM weekly_sov_with_change weekly
  WHERE NOT weekly.is_client
),

latest_sov_week_per_cluster AS (
  SELECT cluster_id, MAX(week_date) AS week_date
  FROM sov_history
  GROUP BY cluster_id
),

sov_summary AS (
  SELECT
    history.cluster_id,
    history.cluster_name,
    history.competitor_name,
    history.current_sov,
    history.client_sov_this_week,
    history.sov_delta_pp,
    history.z_score,
    history.alert_flag,
    history.week_date,
    history.answers_analyzed,
    history.platforms_analyzed,
    history.source,
    history.methodology_version
  FROM sov_history history
  JOIN latest_sov_week_per_cluster latest
    ON latest.cluster_id IS NOT DISTINCT FROM history.cluster_id
   AND latest.week_date = history.week_date
),

weekly_market_sov AS (
  SELECT
    company.cluster_id,
    company.cluster_name,
    company.week_date,
    company.company_name,
    company.is_client,
    ROUND(
      SUM(company.sov)
      /
      NULLIF(
        (
          SELECT COUNT(DISTINCT run.monitoring_id)
          FROM ai_monitoring_runs run
          WHERE run.cluster_id IS NOT DISTINCT FROM company.cluster_id
            AND run.week_date = company.week_date
        ),
        0
      ),
      2
    ) AS sov
  FROM run_company_sov company
  GROUP BY
    company.cluster_id,
    company.cluster_name,
    company.week_date,
    company.company_name,
    company.is_client
),

latest_market_week_per_cluster AS (
  SELECT cluster_id, MAX(week_date) AS week_date
  FROM weekly_market_sov
  GROUP BY cluster_id
),

market_snapshot AS (
  SELECT
    market.cluster_id,
    market.cluster_name,
    market.week_date,
    market.company_name,
    market.is_client,
    market.sov,
    CASE
      WHEN market.sov > 0 THEN DENSE_RANK() OVER (
        PARTITION BY market.cluster_id
        ORDER BY market.sov DESC
      )
      ELSE NULL
    END AS rank
  FROM weekly_market_sov market
  JOIN latest_market_week_per_cluster latest
    ON latest.cluster_id IS NOT DISTINCT FROM market.cluster_id
   AND latest.week_date = market.week_date
),

calculated_sov_summary AS (
  SELECT
    snapshot.cluster_id,
    snapshot.cluster_name,
    snapshot.week_date,
    snapshot.company_name,
    snapshot.is_client,
    snapshot.sov,
    snapshot.rank
  FROM market_snapshot snapshot
),

tracked_competitor_names AS (
  SELECT
    cluster_id,
    ARRAY_AGG(DISTINCT LOWER(competitor_name)) AS names
  FROM sov_history
  GROUP BY cluster_id
),

sov_weekly_deduped AS (
  SELECT cluster_id, week_date, top_companies
  FROM (
    SELECT
      sw.*,
      ROW_NUMBER() OVER (
        PARTITION BY sw.client_id, sw.cluster_id, sw.week_date
        ORDER BY sw.synced_at DESC, sw.id DESC
      ) AS row_number
    FROM sov_weekly sw
    JOIN reporting_period rp ON rp.client_id = sw.client_id
    WHERE sw.week_date <= rp.as_of_week
      AND sw.week_date >= rp.as_of_week - (rp.sov_history_weeks * 7)
  ) ranked
  WHERE row_number = 1
),

raw_geo_for_tracked_competitors AS (
  SELECT
    sw.cluster_id,
    sw.week_date,
    company.value ->> 'name' AS company_name,
    NULLIF(company.value ->> 'rank', '')::integer AS company_rank,
    NULLIF(company.value ->> 'sov_score', '')::numeric AS sov_score
  FROM sov_weekly_deduped sw
  JOIN tracked_competitor_names tracked
    ON tracked.cluster_id IS NOT DISTINCT FROM sw.cluster_id
  CROSS JOIN LATERAL jsonb_array_elements(
    COALESCE(sw.top_companies, '[]'::jsonb)
  ) AS company(value)
  WHERE LOWER(company.value ->> 'name') = ANY(tracked.names)
),

/* These are the exact signal fields rendered in the Signals tab, plus its evidence cards. */
signal_rows AS (
  SELECT
    dl.id,
    dl.run_id,
    dl.client_id,
    dl.cluster_id,
    COALESCE(dl.cluster_label, cc.cluster_name) AS cluster_name,
    dl.week_date,
    dl.triage_severity,
    dl.noise,
    dl.graduation_regime,
    dl.primary_competitor,
    dl.delta_client_pp,
    primary_field.delta_pp AS primary_competitor_delta_pp,
    evidence.website_changes,
    evidence.third_party_signals,
    evidence.ai_citation_changes
  FROM scout_decision_log dl
  JOIN reporting_period rp ON rp.client_id = dl.client_id
  LEFT JOIN client_clusters cc ON cc.cluster_id = dl.cluster_id
  LEFT JOIN LATERAL (
    SELECT NULLIF(field.value ->> 'delta_pp', '')::numeric AS delta_pp
    FROM jsonb_array_elements(COALESCE(dl.field, '[]'::jsonb)) AS field(value)
    WHERE field.value ->> 'competitor' = dl.primary_competitor
    LIMIT 1
  ) primary_field ON TRUE
  LEFT JOIN LATERAL (
    SELECT
      investigation.website_changes,
      investigation.third_party_signals,
      investigation.ai_citation_changes
    FROM investigations investigation
    WHERE investigation.run_id = dl.run_id
      AND investigation.client_id = dl.client_id
      AND investigation.cluster_id = dl.cluster_id
      AND investigation.competitor_name IS NOT DISTINCT FROM dl.primary_competitor
    ORDER BY investigation.created_at DESC, investigation.id DESC
    LIMIT 1
  ) evidence ON TRUE
  WHERE dl.week_date <= rp.as_of_week
),

visible_signals AS (
  SELECT *
  FROM signal_rows
  ORDER BY week_date DESC NULLS LAST, id DESC
  LIMIT 200
),

latest_outcome_per_recommendation AS (
  SELECT
    ranked.recommendation_id,
    ranked.execution_status,
    ranked.executed,
    ranked.measured_at,
    ranked.recovered,
    ranked.sov_delta_pp
  FROM (
    SELECT
      outcome.*,
      ROW_NUMBER() OVER (
        PARTITION BY outcome.recommendation_id
        ORDER BY outcome.measured_at DESC NULLS LAST, outcome.created_at DESC NULLS LAST, outcome.id DESC
      ) AS row_number
    FROM scout_outcomes outcome
    JOIN recommendations source_recommendation
      ON source_recommendation.id = outcome.recommendation_id
    JOIN reporting_period rp ON rp.client_id = source_recommendation.client_id
  ) ranked
  WHERE ranked.row_number = 1
),

latest_client_summary_per_recommendation AS (
  SELECT recommendation_id, client_summary
  FROM (
    SELECT
      report.recommendation_id,
      report.client_summary,
      ROW_NUMBER() OVER (
        PARTITION BY report.recommendation_id
        ORDER BY report.created_at DESC, report.id DESC
      ) AS row_number
    FROM reports report
    JOIN reporting_period rp ON rp.client_id = report.client_id
    WHERE report.validation_status IS DISTINCT FROM 'quarantined'
  ) ranked
  WHERE ranked.row_number = 1
),

recommendation_rows AS (
  SELECT
    rec.id,
    rec.investigation_id,
    rec.week_date,
    rec.cluster_id,
    COALESCE(rec.cluster_label, cc.cluster_name) AS cluster_name,
    rec.competitor_name,
    rec.rec_type,
    rec.priority,
    rec.confidence,
    rec.shift_type,
    rec.summary,
    rec.probable_cause,
    rec.gap_analysis,
    rec.evidence_summary,
    rec.action_bullets,
    rec.timeline,
    rec.window_weeks,
    severity.triage_severity,
    report.client_summary,
    outcome.execution_status,
    outcome.executed,
    outcome.measured_at,
    outcome.recovered,
    outcome.sov_delta_pp AS outcome_sov_delta_pp,
    ROW_NUMBER() OVER (
      ORDER BY
        CASE rec.priority
          WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4
        END,
        rec.week_date DESC,
        rec.created_at DESC
    ) AS display_position
  FROM recommendations rec
  JOIN reporting_period rp ON rp.client_id = rec.client_id
  LEFT JOIN client_clusters cc ON cc.cluster_id = rec.cluster_id
  LEFT JOIN LATERAL (
    SELECT decision.triage_severity
    FROM scout_decision_log decision
    WHERE decision.run_id = rec.run_id
      AND decision.client_id = rec.client_id
      AND decision.cluster_id = rec.cluster_id
    ORDER BY decision.created_at DESC NULLS LAST, decision.id DESC
    LIMIT 1
  ) severity ON TRUE
  LEFT JOIN latest_client_summary_per_recommendation report ON report.recommendation_id = rec.id
  LEFT JOIN latest_outcome_per_recommendation outcome ON outcome.recommendation_id = rec.id
  WHERE rec.week_date <= rp.as_of_week
),

recent_runs AS (
  SELECT run.run_id, run.status, run.sync_date, run.started_at, run.triggers_fired
  FROM cycle_runs run
  JOIN (
    SELECT DISTINCT dl.run_id
    FROM scout_decision_log dl
    JOIN reporting_period rp ON rp.client_id = dl.client_id
  ) client_runs ON client_runs.run_id = run.run_id
  ORDER BY run.started_at DESC
  LIMIT 5
)

SELECT jsonb_build_object(
  'client', jsonb_strip_nulls(
    jsonb_build_object(
      'client_id', rp.client_id,
      'client_name', rp.client_name,
      'company_domain', rp.company_domain,
      'industry', rp.industry,
      'target_region', rp.target_region,
      'competitors', rp.competitors
    )
  ),
  'clusters', COALESCE(
    (
      SELECT jsonb_agg(
        jsonb_build_object('cluster_id', cc.cluster_id, 'cluster_name', cc.cluster_name)
        ORDER BY cc.cluster_name
      )
      FROM client_clusters cc
    ),
    '[]'::jsonb
  ),
  'sov', jsonb_build_object(
    'source', 'ai_monitoring.sov',
    'methodology_version', 'prompt_level_weekly_average_v1',
    'methodology',
      'SOV is averaged across all monitored AI answers in the same topic and week. A tracked company absent from an answer is counted as 0%% for that answer.',
    'summary', COALESCE(
      (
        SELECT jsonb_agg(
          jsonb_strip_nulls(
            jsonb_build_object(
              'cluster_id', summary.cluster_id,
              'cluster_name', summary.cluster_name,
              'competitor_name', summary.competitor_name,
              'current_sov', summary.current_sov,
              'client_sov_this_week', summary.client_sov_this_week,
              'sov_delta_pp', summary.sov_delta_pp,
              'alert_flag', summary.alert_flag,
              'week_date', summary.week_date,
              'answers_analyzed', summary.answers_analyzed,
              'platforms_analyzed', summary.platforms_analyzed
            )
          )
          ORDER BY
            summary.cluster_name,
            summary.current_sov DESC,
            summary.competitor_name
        )
        FROM sov_summary summary
      ),
      '[]'::jsonb
    ),
    'history', COALESCE(
      (
        SELECT jsonb_agg(
          jsonb_strip_nulls(
            jsonb_build_object(
              'cluster_id', history.cluster_id,
              'cluster_name', history.cluster_name,
              'week_date', history.week_date,
              'competitor_name', history.competitor_name,
              'current_sov', history.current_sov,
              'client_sov_this_week', history.client_sov_this_week,
              'sov_delta_pp', history.sov_delta_pp,
              'alert_flag', history.alert_flag,
              'answers_analyzed', history.answers_analyzed,
              'platforms_analyzed', history.platforms_analyzed
            )
          )
          ORDER BY
            history.cluster_name,
            history.week_date,
            history.current_sov DESC,
            history.competitor_name
        )
        FROM sov_history history
      ),
      '[]'::jsonb
    ),
    'market_snapshot', COALESCE(
      (
        SELECT jsonb_agg(
          jsonb_strip_nulls(
            jsonb_build_object(
              'cluster_id', snapshot.cluster_id,
              'cluster_name', snapshot.cluster_name,
              'week_date', snapshot.week_date,
              'company_name', snapshot.company_name,
              'is_client', snapshot.is_client,
              'sov', snapshot.sov,
              'rank', snapshot.rank
            )
          )
          ORDER BY
            snapshot.cluster_name,
            snapshot.sov DESC,
            snapshot.company_name
        )
        FROM calculated_sov_summary snapshot
      ),
      '[]'::jsonb
    ),
    'raw_geo_for_tracked_competitors', COALESCE(
      (
        SELECT jsonb_agg(
          jsonb_build_object(
            'cluster_id', raw_geo.cluster_id,
            'cluster_name', cc.cluster_name,
            'week_date', raw_geo.week_date,
            'company_name', raw_geo.company_name,
            'rank', raw_geo.company_rank,
            'sov_score', raw_geo.sov_score
          )
          ORDER BY cc.cluster_name, raw_geo.week_date, raw_geo.company_name
        )
        FROM raw_geo_for_tracked_competitors raw_geo
        LEFT JOIN client_clusters cc
          ON cc.cluster_id IS NOT DISTINCT FROM raw_geo.cluster_id
      ),
      '[]'::jsonb
    )
  ),
  'signals', COALESCE(
    (
      SELECT jsonb_agg(
        jsonb_strip_nulls(
          jsonb_build_object(
            'id', signal.id,
            'run_id', signal.run_id,
            'client_id', signal.client_id,
            'cluster_id', signal.cluster_id,
            'cluster_name', signal.cluster_name,
            'week_date', signal.week_date,
            'triage_severity', signal.triage_severity,
            'noise', signal.noise,
            'graduation_regime', signal.graduation_regime,
            'primary_competitor', signal.primary_competitor,
            'primary_competitor_delta_pp', signal.primary_competitor_delta_pp,
            'delta_client_pp', signal.delta_client_pp,
            'evidence', jsonb_strip_nulls(
              jsonb_build_object(
                'website_changes', signal.website_changes,
                'third_party_signals', signal.third_party_signals,
                'ai_citation_changes', signal.ai_citation_changes
              )
            )
          )
        )
        ORDER BY signal.week_date DESC, signal.id DESC
      )
      FROM visible_signals signal
    ),
    '[]'::jsonb
  ),
  'executive_summary', (
    SELECT recommendation.evidence_summary
    FROM recommendation_rows recommendation
    WHERE recommendation.display_position = 1
  ),
  'recommendations', COALESCE(
    (
      SELECT jsonb_agg(
        jsonb_strip_nulls(
          jsonb_build_object(
            'id', recommendation.id,
            'investigation_id', recommendation.investigation_id,
            'week_date', recommendation.week_date,
            'cluster_id', recommendation.cluster_id,
            'cluster_name', recommendation.cluster_name,
            'competitor_name', recommendation.competitor_name,
            'rec_type', recommendation.rec_type,
            'priority', recommendation.priority,
            'triage_severity', recommendation.triage_severity,
            'confidence', recommendation.confidence,
            'shift_type', recommendation.shift_type,
            'summary', recommendation.summary,
            'probable_cause', recommendation.probable_cause,
            'gap_analysis', recommendation.gap_analysis,
            'evidence_summary', recommendation.evidence_summary,
            'action_bullets', recommendation.action_bullets,
            'timeline', recommendation.timeline,
            'window_weeks', recommendation.window_weeks,
            'client_summary', recommendation.client_summary,
            'outcome', jsonb_strip_nulls(
              jsonb_build_object(
                'execution_status', recommendation.execution_status,
                'executed', recommendation.executed,
                'measured_at', recommendation.measured_at,
                'recovered', recommendation.recovered,
                'sov_delta_pp', recommendation.outcome_sov_delta_pp
              )
            )
          )
        )
        ORDER BY recommendation.display_position
      )
      FROM recommendation_rows recommendation
    ),
    '[]'::jsonb
  ),
  'run_history', COALESCE(
    (
      SELECT jsonb_agg(
        jsonb_build_object(
          'run_id', run.run_id,
          'status', run.status,
          'sync_date', run.sync_date,
          'started_at', run.started_at,
          'triggers_fired', run.triggers_fired
        )
        ORDER BY run.started_at DESC
      )
      FROM recent_runs run
    ),
    '[]'::jsonb
  )
) AS client_facing_recon_report
FROM reporting_period rp;
