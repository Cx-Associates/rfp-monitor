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

function isAuthorized(req: Request) {
  const token = req.headers.get("x-rfp-admin-token") ?? "";
  return Boolean(ADMIN_TOKEN) && token === ADMIN_TOKEN;
}

function cleanString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length ? trimmed : null;
}

function cleanBoolean(value: unknown): boolean {
  return value === true;
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

  let payload: any;
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

    return jsonResponse({ record: data });
  }

  return jsonResponse({ error: "Unsupported action" }, 400);
});
