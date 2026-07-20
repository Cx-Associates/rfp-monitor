import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-rfp-admin-token",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const SUPABASE_URL = Deno.env.get("RFP_SUPABASE_URL") ?? "";
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("RFP_SUPABASE_SERVICE_ROLE_KEY") ?? "";
const ADMIN_TOKEN = Deno.env.get("RFP_ADMIN_TOKEN") ?? "";

const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...corsHeaders,
      "Content-Type": "application/json",
    },
  });
}

function cleanString(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value).trim();
}

function cleanBoolean(value: unknown): boolean {
  if (value === true) return true;
  if (value === false) return false;
  const text = cleanString(value).toLowerCase();
  return text === "true" || text === "1" || text === "yes" || text === "on";
}

function isAuthorized(req: Request) {
  const token = req.headers.get("x-rfp-admin-token") ?? "";
  return Boolean(ADMIN_TOKEN) && token === ADMIN_TOKEN;
}

function todayIsoDate(): string {
  return new Date().toISOString().slice(0, 10);
}

function addDaysIso(startDate: string, days: number): string {
  const parsed = /^\d{4}-\d{2}-\d{2}$/.test(startDate)
    ? new Date(`${startDate}T00:00:00Z`)
    : new Date();
  parsed.setUTCDate(parsed.getUTCDate() + days);
  return parsed.toISOString().slice(0, 10);
}

function visibleUntilForPromotion(deadline: string, firstSeen: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(deadline)) return deadline;
  return addDaysIso(firstSeen, 30);
}

function parseKeywords(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map(cleanString).filter(Boolean);
  }

  const text = cleanString(value);
  if (!text) return [];

  try {
    const parsed = JSON.parse(text);
    if (Array.isArray(parsed)) {
      return parsed.map(cleanString).filter(Boolean);
    }
  } catch {
    // Fall through to pipe/comma splitting.
  }

  return text
    .split("|")
    .flatMap((part) => part.split(","))
    .map((part) => part.trim())
    .filter(Boolean);
}

function promotionEligible(row: Record<string, unknown>, payload: Record<string, unknown>): boolean {
  const isManualReviewCandidate =
    cleanBoolean(payload.manual_review) || cleanBoolean(payload.manual_promoted);

  const fit = cleanString(row.reviewer_fit);
  const fitLower = fit.toLowerCase();

  const hasGoodFit = Boolean(fit) && fitLower !== "poor fit";
  const hasStatus = Boolean(cleanString(row.review_status));
  const hasNotes = Boolean(cleanString(row.technical_review_notes) || cleanString(row.admin_review_notes));
  const hasOwner = Boolean(cleanString(row.tech_owner) || cleanString(row.admin_owner));
  const hasReviewCheck =
    cleanBoolean(row.admin_reviewed) ||
    cleanBoolean(row.emv_technical_reviewed) ||
    cleanBoolean(row.commissioning_technical_reviewed);

  return isManualReviewCandidate && hasGoodFit && hasStatus && hasNotes && hasOwner && hasReviewCheck;
}

async function upsertManualPromotion(
  row: Record<string, unknown>,
  payload: Record<string, unknown>,
  reviewKey: string,
) {
  const monitorType = cleanString(payload.monitor_type) || "emv";
  const uniqueKey = cleanString(payload.unique_key) || reviewKey;
  const today = todayIsoDate();

  const existing = await supabase
    .from("opportunity_active")
    .select("first_seen")
    .eq("monitor_type", monitorType)
    .eq("unique_key", uniqueKey)
    .maybeSingle();

  if (existing.error) {
    return { error: existing.error };
  }

  const firstSeen = cleanString(existing.data?.first_seen) || today;
  const deadline = cleanString(payload.deadline);
  const visibleUntil = visibleUntilForPromotion(deadline, firstSeen);
  const relevanceScore = Number.parseInt(cleanString(payload.relevance_score) || "0", 10) || 0;

  const opportunity = {
    source: cleanString(row.source),
    notice_id: cleanString(row.notice_id),
    url: cleanString(row.url),
    title: cleanString(row.title),
    description: cleanString(payload.description),
    issuer: cleanString(payload.issuer) || cleanString(row.source) || "Unknown",
    posted_date: null,
    deadline: deadline || null,
    state: cleanString(payload.state),
    naics_code: null,
    set_aside: null,
    contact_name: null,
    contact_email: null,
    contact_phone: null,
    relevance_score: relevanceScore,
    matched_keywords: parseKeywords(payload.matched_keywords),
    confidence: cleanString(payload.confidence) || "Below threshold",
    promoted_from_manual_review: true,
    promotion_label: "Promoted from Manual Review",
    found_at: `${firstSeen}T00:00:00`,
    unique_key: uniqueKey,
  };

  const activeRow = {
    monitor_type: monitorType,
    unique_key: uniqueKey,
    first_seen: firstSeen,
    last_seen: today,
    visible_until: visibleUntil,
    source: cleanString(row.source),
    title: cleanString(row.title).slice(0, 500),
    deadline: deadline || null,
    opportunity,
  };

  const result = await supabase
    .from("opportunity_active")
    .upsert(activeRow, { onConflict: "monitor_type,unique_key" });

  return { error: result.error };
}

async function removeManualPromotion(payload: Record<string, unknown>, reviewKey: string) {
  const isManualReviewCandidate =
    cleanBoolean(payload.manual_review) || cleanBoolean(payload.manual_promoted);

  if (!isManualReviewCandidate) {
    return { error: null };
  }

  const monitorType = cleanString(payload.monitor_type) || "emv";
  const uniqueKey = cleanString(payload.unique_key) || reviewKey;

  if (!uniqueKey) {
    return { error: null };
  }

  const result = await supabase
    .from("opportunity_active")
    .delete()
    .eq("monitor_type", monitorType)
    .eq("unique_key", uniqueKey);

  return { error: result.error };
}

serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  if (req.method !== "POST") {
    return jsonResponse({ error: "Method not allowed" }, 405);
  }

  if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY || !ADMIN_TOKEN) {
    return jsonResponse({ error: "Server is missing required Supabase review secrets" }, 500);
  }

  let payload: Record<string, unknown>;
  try {
    payload = await req.json();
  } catch {
    return jsonResponse({ error: "Invalid JSON body" }, 400);
  }

  const action = payload?.action;

  if (action === "list") {
    const reviewKeys = Array.isArray(payload.review_keys)
      ? payload.review_keys.filter((x: unknown) => typeof x === "string" && x.trim().length > 0)
      : [];

    if (!reviewKeys.length) {
      return jsonResponse({ records: [] });
    }

    const { data, error } = await supabase
      .from("opportunity_review_status")
      .select("*")
      .in("review_key", reviewKeys);

    if (error) {
      return jsonResponse({ error: error.message }, 500);
    }

    return jsonResponse({ records: data ?? [] });
  }

  if (action === "save") {
    if (!isAuthorized(req)) {
      return jsonResponse({ error: "Unauthorized" }, 401);
    }

    const reviewKey = cleanString(payload.review_key);

    if (!reviewKey) {
      return jsonResponse({ error: "Missing review_key" }, 400);
    }

    const row = {
      review_key: reviewKey,
      source: cleanString(payload.source),
      notice_id: cleanString(payload.notice_id),
      title: cleanString(payload.title),
      url: cleanString(payload.url),

      review_status: cleanString(payload.review_status),
      reviewer_fit: cleanString(payload.reviewer_fit),
      tech_owner: cleanString(payload.tech_owner),
      admin_owner: cleanString(payload.admin_owner),

      admin_reviewed: cleanBoolean(payload.admin_reviewed),
      emv_technical_reviewed: cleanBoolean(payload.emv_technical_reviewed),
      commissioning_technical_reviewed: cleanBoolean(payload.commissioning_technical_reviewed),

      technical_review_notes: cleanString(payload.technical_review_notes),
      admin_review_notes: cleanString(payload.admin_review_notes),

      updated_by: cleanString(payload.updated_by),
      updated_at: new Date().toISOString(),
    };

    const { data, error } = await supabase
      .from("opportunity_review_status")
      .upsert(row, { onConflict: "review_key" })
      .select()
      .single();

    if (error) {
      return jsonResponse({ error: error.message }, 500);
    }

    const shouldPromote = promotionEligible(row, payload);

    if (shouldPromote) {
      const promotion = await upsertManualPromotion(row, payload, reviewKey);
      if (promotion.error) {
        return jsonResponse({ error: `Review saved, but promotion failed: ${promotion.error.message}` }, 500);
      }
    } else {
      const removal = await removeManualPromotion(payload, reviewKey);
      if (removal.error) {
        return jsonResponse({ error: `Review saved, but promotion cleanup failed: ${removal.error.message}` }, 500);
      }
    }

    return jsonResponse({ record: data, promotion_active: shouldPromote });
  }

  return jsonResponse({ error: "Unsupported action" }, 400);
});
