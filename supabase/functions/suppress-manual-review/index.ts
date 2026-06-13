const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-rfp-admin-token",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...corsHeaders,
      "Content-Type": "application/json",
    },
  });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  if (req.method !== "POST") {
    return jsonResponse({ ok: false, error: "Method not allowed" }, 405);
  }

  const expectedToken = Deno.env.get("RFP_ADMIN_TOKEN") || "";
  const providedToken = req.headers.get("x-rfp-admin-token") || "";

  if (!expectedToken || providedToken !== expectedToken) {
    return jsonResponse({ ok: false, error: "Unauthorized" }, 401);
  }

  const supabaseUrl = Deno.env.get("RFP_SUPABASE_URL") || "";
  const serviceRoleKey = Deno.env.get("RFP_SUPABASE_SERVICE_ROLE_KEY") || "";

  if (!supabaseUrl || !serviceRoleKey) {
    return jsonResponse(
      { ok: false, error: "Server missing Supabase configuration" },
      500,
    );
  }

  let payload: Record<string, unknown>;
  try {
    payload = await req.json();
  } catch (_err) {
    return jsonResponse({ ok: false, error: "Invalid JSON body" }, 400);
  }

  const monitorType = String(payload.monitor_type || "emv").trim();
  const uniqueKey = String(payload.unique_key || "").trim();
  const source = String(payload.source || "").trim();
  const title = String(payload.title || "").trim();
  const reason = String(payload.reason || "manual_dashboard_dismissal").trim();
  const suppressedBy = String(payload.suppressed_by || "dashboard").trim();

  if (!monitorType || !uniqueKey || !source || !title) {
    return jsonResponse(
      {
        ok: false,
        error: "Missing required field: monitor_type, unique_key, source, or title",
      },
      400,
    );
  }

  const row = {
    monitor_type: monitorType,
    unique_key: uniqueKey,
    suppressed_at: new Date().toISOString(),
    source,
    title,
    reason,
    suppressed_by: suppressedBy,
  };

  const response = await fetch(
    `${supabaseUrl}/rest/v1/manual_review_suppressed?on_conflict=monitor_type,unique_key`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "apikey": serviceRoleKey,
        "Authorization": `Bearer ${serviceRoleKey}`,
        "Prefer": "resolution=merge-duplicates,return=representation",
      },
      body: JSON.stringify(row),
    },
  );

  const responseText = await response.text();

  if (!response.ok) {
    return jsonResponse(
      {
        ok: false,
        error: "Supabase upsert failed",
        status: response.status,
        details: responseText,
      },
      500,
    );
  }

  let data: unknown = null;
  try {
    data = responseText ? JSON.parse(responseText) : null;
  } catch (_err) {
    data = responseText;
  }

  return jsonResponse({ ok: true, data });
});
