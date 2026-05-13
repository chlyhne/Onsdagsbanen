const EMAIL_PATTERN = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi;
const HUMMESSE_SENDER = "hummesse@gmail.com";
const RESULTS_SUBJECT = "resultater";
const APPEND_SUBJECT = "append";
const DELETE_SUBJECT = "delete";
const UNSUBSCRIBE_SUBJECT = "afmeld resultater";
const DUTY_SUBJECT_PREFIX = "dommertjans";
const DUTY_SUBJECT_PATTERN = /^dommertjans\s+r(\d+)\s+(\d+)$/i;
const RESULTATER_GUARD_TIMEZONE = "Europe/Copenhagen";
const RESULTATER_GUARD_WEEKDAY = "wed";
const RESULTATER_GUARD_HOUR = 19;

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

function isExactResultaterSubject(subject) {
  return String(subject || "").trim().toLowerCase() === RESULTS_SUBJECT;
}

function isExactAppendSubject(subject) {
  return String(subject || "").trim().toLowerCase() === APPEND_SUBJECT;
}

function isExactDeleteSubject(subject) {
  return String(subject || "").trim().toLowerCase() === DELETE_SUBJECT;
}

function isExactUnsubscribeSubject(subject) {
  return String(subject || "").trim().toLowerCase() === UNSUBSCRIBE_SUBJECT;
}

function parseDutySubject(subject) {
  const normalized = String(subject || "").trim();
  const match = normalized.match(DUTY_SUBJECT_PATTERN);
  if (!match) {
    return null;
  }
  return {
    raceLabel: `R${match[1]}`,
    participantNumber: String(Number(match[2])),
  };
}

function isWithinHummesseResultaterWindow(now = new Date()) {
  const formatter = new Intl.DateTimeFormat("en-GB", {
    timeZone: RESULTATER_GUARD_TIMEZONE,
    weekday: "short",
    hour: "2-digit",
    hourCycle: "h23",
  });
  const parts = Object.fromEntries(
    formatter
      .formatToParts(now)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value])
  );
  const weekday = String(parts.weekday || "").trim().toLowerCase();
  const hour = Number.parseInt(String(parts.hour || "-1"), 10);
  return weekday === RESULTATER_GUARD_WEEKDAY && Number.isFinite(hour) && hour >= RESULTATER_GUARD_HOUR;
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
  persistRecipients = false,
  appendOnly = false,
  deleteMode = false,
  sendUnsubscribeConfirmation = false,
  unsubscribeRecipient = "",
  dutyMode = false,
  dutyRace = "",
  dutyParticipantNumber = "",
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
          persist_recipients: persistRecipients ? "true" : "false",
          append_only: appendOnly ? "true" : "false",
          delete_mode: deleteMode ? "true" : "false",
          send_unsubscribe_confirmation: sendUnsubscribeConfirmation ? "true" : "false",
          unsubscribe_recipient: String(unsubscribeRecipient || "").trim().toLowerCase(),
          duty_mode: dutyMode ? "true" : "false",
          duty_race: String(dutyRace || "").trim().toUpperCase(),
          duty_participant_number: String(dutyParticipantNumber || "").trim(),
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

  if (!from) {
    console.log("Ignoring sender: (missing)");
    return;
  }

  const isResultater = isExactResultaterSubject(subject);
  const isAppend = isExactAppendSubject(subject);
  const isDelete = isExactDeleteSubject(subject);
  const isUnsubscribe = isExactUnsubscribeSubject(subject);
  const dutyCommand = parseDutySubject(subject);
  const isDuty = Boolean(dutyCommand);
  if (!isResultater && !isAppend && !isDelete && !isUnsubscribe && !isDuty) {
    console.log("Ignoring email because subject was not a supported command.");
    return;
  }

  if ((isAppend || isDelete || isDuty) && from !== HUMMESSE_SENDER) {
    console.log("Ignoring admin-only email because sender was not hummesse@gmail.com.");
    return;
  }

  if (isUnsubscribe && from === HUMMESSE_SENDER) {
    console.log("Ignoring afmeld resultater for hummesse@gmail.com.");
    return;
  }

  if (isResultater && from === HUMMESSE_SENDER && !isWithinHummesseResultaterWindow()) {
    console.log(
      "Ignoring resultater from hummesse@gmail.com outside Wednesday after 19:00 Europe/Copenhagen."
    );
    return;
  }

  let recipientsOverride = [];
  let persistRecipients = false;
  let appendOnly = false;
  let deleteMode = false;
  let sendUnsubscribeConfirmation = false;
  let unsubscribeRecipient = "";
  let dutyMode = false;
  let dutyRace = "";
  let dutyParticipantNumber = "";
  if (isUnsubscribe) {
    recipientsOverride = [from];
    persistRecipients = true;
    appendOnly = true;
    deleteMode = true;
    sendUnsubscribeConfirmation = true;
    unsubscribeRecipient = from;
  } else if (isDuty) {
    dutyMode = true;
    appendOnly = true;
    dutyRace = dutyCommand.raceLabel;
    dutyParticipantNumber = dutyCommand.participantNumber;
  } else if (from === HUMMESSE_SENDER) {
    const rawEmail = await readRawEmail(message);
    const bodyText = extractEmailBodyText(rawEmail);
    recipientsOverride = extractEmailAddresses(bodyText);
    persistRecipients = recipientsOverride.length > 0;
    appendOnly = isAppend || isDelete;
    deleteMode = isDelete;

    if ((isAppend || isDelete) && recipientsOverride.length === 0) {
      console.log("Ignoring append/delete email from hummesse because no email addresses were found in body.");
      return;
    }
  } else {
    recipientsOverride = [from];
    persistRecipients = true;
  }

  await dispatchWorkflow({
    env,
    from,
    subject,
    dryRun: appendOnly,
    recipientsOverride,
    persistRecipients,
    appendOnly,
    deleteMode,
    sendUnsubscribeConfirmation,
    unsubscribeRecipient,
    dutyMode,
    dutyRace,
    dutyParticipantNumber,
  });
  console.log(
    `Workflow dispatched for sender ${from}. mode=${
      dutyMode
        ? `duty:${dutyRace}:${dutyParticipantNumber}`
        :
      isUnsubscribe
        ? "unsubscribe"
        : deleteMode
        ? "delete-only"
        : appendOnly
        ? "append-only"
        : from === HUMMESSE_SENDER
        ? "hummesse-special"
        : "sender-only"
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
  const bodyText = String(payload.body_text || payload.body || "").trim();
  const isResultater = isExactResultaterSubject(subject);
  const isAppend = isExactAppendSubject(subject);
  const isDelete = isExactDeleteSubject(subject);
  const isUnsubscribe = isExactUnsubscribeSubject(subject);
  const dutyCommand = parseDutySubject(subject);
  const isDuty = Boolean(dutyCommand);

  if (!isResultater && !isAppend && !isDelete && !isUnsubscribe && !isDuty) {
    return new Response("Subject must be a supported command", { status: 400 });
  }

  if ((isAppend || isDelete || isDuty) && from !== HUMMESSE_SENDER) {
    return new Response("Only hummesse@gmail.com may use admin commands", { status: 403 });
  }

  if (isUnsubscribe && from === HUMMESSE_SENDER) {
    return new Response("hummesse@gmail.com cannot use subject 'afmeld resultater'", { status: 403 });
  }

  if (isResultater && from === HUMMESSE_SENDER && !isWithinHummesseResultaterWindow()) {
    return new Response(
      "hummesse@gmail.com may only use subject 'resultater' on Wednesdays after 19:00 Europe/Copenhagen",
      { status: 403 }
    );
  }

  const recipientsOverrideFromPayload = normalizeRecipientsOverride(payload.recipients_override);
  const recipientsOverride = isUnsubscribe
    ? [from]
    :
    from === HUMMESSE_SENDER
      ? recipientsOverrideFromPayload.length > 0
        ? recipientsOverrideFromPayload
        : extractEmailAddresses(bodyText)
      : [from];
  const persistRecipients = recipientsOverride.length > 0;
  const appendOnly = isAppend || isDelete || isUnsubscribe;
  const deleteMode = isDelete || isUnsubscribe;
  const sendUnsubscribeConfirmation = isUnsubscribe;
  const unsubscribeRecipient = isUnsubscribe ? from : "";
  const dutyMode = isDuty;
  const dutyRace = isDuty ? dutyCommand.raceLabel : "";
  const dutyParticipantNumber = isDuty ? dutyCommand.participantNumber : "";

  if (!from) {
    return new Response("Missing sender", { status: 400 });
  }

  if (!subject) {
    return new Response("Missing subject", { status: 400 });
  }

  if ((isAppend || isDelete) && recipientsOverride.length === 0) {
    return new Response("Append/delete mode requires at least one email address in body or recipients_override", { status: 400 });
  }

  await dispatchWorkflow({
    env,
    from,
    subject,
    dryRun: appendOnly || dutyMode ? true : dryRun,
    recipientsOverride,
    persistRecipients,
    appendOnly: appendOnly || dutyMode,
    deleteMode,
    sendUnsubscribeConfirmation,
    unsubscribeRecipient,
    dutyMode,
    dutyRace,
    dutyParticipantNumber,
  });
  return new Response("Triggered", { status: 202 });
}

export default {
  async fetch(request, env) {
    try {
      return await handleHttpTrigger(request, env);
    } catch (error) {
      const details =
        error && typeof error.message === "string"
          ? error.message
          : String(error);
      console.error(`HTTP trigger failed: ${details}`);
      return new Response(`Internal Error: ${details}`, { status: 500 });
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
