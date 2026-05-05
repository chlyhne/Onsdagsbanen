function parseAllowedSenders(raw) {
  return new Set(
    String(raw || "")
      .split(",")
      .map((value) => value.trim().toLowerCase())
      .filter(Boolean)
  );
}

function requireEnv(env, name) {
  const value = String(env[name] || "").trim();
  if (!value) {
    throw new Error(`Missing required Worker setting: ${name}`);
  }
  return value;
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

async function dispatchWorkflow({ env, from, subject, dryRun = false }) {
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

  if (!from || !allowedSenders.has(from)) {
    console.log(`Ignoring sender: ${from || "(missing)"}`);
    return;
  }

  const triggerToken = requireEnv(env, "TRIGGER_TOKEN");
  const subjectLower = subject.toLowerCase();
  const tokenLower = triggerToken.toLowerCase();
  if (!subjectLower.includes(tokenLower)) {
    console.log("Ignoring email because trigger token was not found in subject.");
    return;
  }

  await dispatchWorkflow({ env, from, subject, dryRun: false });
  console.log(`Workflow dispatched for sender ${from}.`);
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
  const allowedSenders = parseAllowedSenders(env.ALLOWED_SENDERS);

  if (!from || !allowedSenders.has(from)) {
    return new Response("Sender not allowed", { status: 403 });
  }

  if (!subject) {
    return new Response("Missing subject", { status: 400 });
  }

  await dispatchWorkflow({ env, from, subject, dryRun });
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
