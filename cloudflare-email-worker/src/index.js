function parseAllowedSenders(raw) {
  return new Set(
    String(raw || "")
      .split(",")
      .map((value) => value.trim().toLowerCase())
      .filter(Boolean)
  );
}

function parseOptionalEnv(env, name, fallback = "") {
  return String(env[name] || fallback).trim();
}

const EMAIL_PATTERN = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi;

function extractEmailAddresses(rawText) {
  const matches = String(rawText || "").match(EMAIL_PATTERN) || [];
  const normalized = matches
    .map((value) => value.trim().replace(/[),;:]+$/g, "").toLowerCase())
    .filter(Boolean);
  return [...new Set(normalized)];
}

function normalizeRecipientsOverride(rawValue) {
  if (Array.isArray(rawValue)) {
    return extractEmailAddresses(rawValue.join("\n"));
  }
  return extractEmailAddresses(String(rawValue || ""));
}

async function readRawEmail(message) {
  try {
    return await new Response(message.raw).text();
  } catch {
    return "";
  }
}

function extractEmailBodyText(rawEmail) {
  const source = String(rawEmail || "");
  if (!source) {
    return "";
  }

  const separatorMatch = /\r?\n\r?\n/.exec(source);
  if (!separatorMatch || separatorMatch.index === undefined) {
    return source;
  }
  return source.slice(separatorMatch.index + separatorMatch[0].length);
}

function requireEnv(env, name) {
  const value = String(env[name] || "").trim();
  if (!value) {
    throw new Error(`Missing required Worker setting: ${name}`);
  }
  return value;
}

function hasSubjectToken(subject, token) {
  const cleanToken = String(token || "").trim().toLowerCase();
  if (!cleanToken) {
    return false;
  }
  return String(subject || "").toLowerCase().includes(cleanToken);
}

function tokenMatches(request, env) {
  const expected = requireEnv(env, "TRIGGER_TOKEN");
  const headerToken = String(request.headers.get("x-trigger-token") || "").trim();

  if (headerToken && headerToken === expected) {
    return true;
  }

  const authHeader = String(request.headers.get("authorization") || "").trim();
  if (authHeader.toLowerCase().startsWith("bearer ")) {
    const bearerToken = authHeader.slice(7).trim();
    if (bearerToken === expected) {
      return true;
    }
  }

  return false;
}

async function dispatchWorkflow({
  env,
  from,
  subject,
  dryRun = false,
  recipientsOverride = [],
}) {
  const owner = requireEnv(env, "GH_OWNER");
  const repo = requireEnv(env, "GH_REPO");
  const workflow = requireEnv(env, "GH_WORKFLOW");
  const token = requireEnv(env, "GH_TOKEN");
  const ref = String(env.GH_REF || "main").trim();

  const response = await fetch(
    `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflow}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "m2s-email-trigger-worker",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ref,
        inputs: {
          trigger_from: from,
          trigger_subject: subject.slice(0, 250),
          dry_run: dryRun ? "true" : "false",
          recipient_override: recipientsOverride.join("\n"),
        },
      }),
    }
  );

  if (!response.ok) {
    const errorBody = await response.text();
    throw new Error(
      `GitHub workflow dispatch failed (${response.status}): ${errorBody}`
    );
  }
}

async function handleIncomingEmail(message, env) {
  const from = String(message.from || "").trim().toLowerCase();
  const subject = String(message.headers.get("subject") || "").trim();
  const allowedSenders = parseAllowedSenders(env.ALLOWED_SENDERS);
  const standardTriggerToken = requireEnv(env, "TRIGGER_TOKEN");
  const publicResultsToken = parseOptionalEnv(env, "PUBLIC_RESULTS_SUBJECT_TOKEN", "RESULTATER");
  const isPublicResultsRequest = hasSubjectToken(subject, publicResultsToken);
  const isStandardRequest = hasSubjectToken(subject, standardTriggerToken);

  if (!from) {
    console.log("Ignoring sender: (missing)");
    return;
  }

  if (!isPublicResultsRequest && !allowedSenders.has(from)) {
    console.log(`Ignoring sender: ${from || "(missing)"}`);
    return;
  }

  if (!isPublicResultsRequest && !isStandardRequest) {
    console.log(
      "Ignoring email because no recognized trigger token was found in subject."
    );
    return;
  }

  let recipientsOverride = [];
  if (isPublicResultsRequest) {
    recipientsOverride = [from];
  } else {
    const rawEmail = await readRawEmail(message);
    const bodyText = extractEmailBodyText(rawEmail);
    recipientsOverride = extractEmailAddresses(bodyText);
  }

  await dispatchWorkflow({
    env,
    from,
    subject,
    dryRun: false,
    recipientsOverride,
  });
  console.log(
    `Workflow dispatched for sender ${from}. mode=${
      isPublicResultsRequest ? "public-results" : "standard"
    } recipient_override_count=${recipientsOverride.length}`
  );
}

async function handleHttpTrigger(request, env) {
  if (request.method !== "POST") {
    return new Response("Method Not Allowed", {
      status: 405,
      headers: { Allow: "POST" },
    });
  }

  const url = new URL(request.url);
  if (url.pathname !== "/trigger") {
    return new Response("Not Found", { status: 404 });
  }

  if (!tokenMatches(request, env)) {
    return new Response("Unauthorized", { status: 401 });
  }

  let payload;
  try {
    payload = await request.json();
  } catch {
    return new Response("Invalid JSON body", { status: 400 });
  }

  const from = String(payload.from || "").trim().toLowerCase();
  const subject = String(payload.subject || "").trim();
  const dryRun = Boolean(payload.dry_run);
  const standardTriggerToken = requireEnv(env, "TRIGGER_TOKEN");
  const publicResultsToken = parseOptionalEnv(env, "PUBLIC_RESULTS_SUBJECT_TOKEN", "RESULTATER");
  const isPublicResultsRequest = hasSubjectToken(subject, publicResultsToken);
  const isStandardRequest = hasSubjectToken(subject, standardTriggerToken);
  const bodyText = String(payload.body_text || payload.body || "").trim();
  const recipientsOverrideFromPayload = normalizeRecipientsOverride(payload.recipients_override);
  const recipientsOverride = isPublicResultsRequest
    ? [from]
    : recipientsOverrideFromPayload.length > 0
      ? recipientsOverrideFromPayload
      : extractEmailAddresses(bodyText);
  const allowedSenders = parseAllowedSenders(env.ALLOWED_SENDERS);

  if (!from) {
    return new Response("Missing sender", { status: 400 });
  }

  if (!isPublicResultsRequest && !allowedSenders.has(from)) {
    return new Response("Sender not allowed", { status: 403 });
  }

  if (!subject) {
    return new Response("Missing subject", { status: 400 });
  }

  if (!isPublicResultsRequest && !isStandardRequest) {
    return new Response("Missing trigger token in subject", { status: 400 });
  }

  await dispatchWorkflow({ env, from, subject, dryRun, recipientsOverride });
  return new Response("Triggered", { status: 202 });
}

export default {
  async fetch(request, env) {
    try {
      return await handleHttpTrigger(request, env);
    } catch (error) {
      console.error(`HTTP trigger failed: ${error.message}`);
      return new Response("Internal Error", { status: 500 });
    }
  },

  async email(message, env, ctx) {
    ctx.waitUntil(
      handleIncomingEmail(message, env).catch((error) => {
        console.error(`Email trigger failed: ${error.message}`);
      })
    );
  },
};
