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

async function dispatchWorkflow({ env, from, subject }) {
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

  await dispatchWorkflow({ env, from, subject });
  console.log(`Workflow dispatched for sender ${from}.`);
}

export default {
  async email(message, env, ctx) {
    ctx.waitUntil(
      handleIncomingEmail(message, env).catch((error) => {
        console.error(`Email trigger failed: ${error.message}`);
      })
    );
  },
};
