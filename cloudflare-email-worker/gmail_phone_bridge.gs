// Gmail -> Cloudflare Worker bridge for no-domain setups.
// Deploy as a Google Apps Script and run pollAndTrigger on a time trigger.

const M2S_CONFIG = {
  workerTriggerUrl: "https://m2s-email-trigger.hummesse.workers.dev/trigger",
  triggerToken: "REPLACE_WITH_TRIGGER_TOKEN",
  // Subject tokens accepted by the worker.
  resultaterSubjectToken: "resultater",
  appendSubjectToken: "append",
  // Allow every sender to request result emails (subject=resultater).
  allowAnySenderForResultater: true,
  // Optional sender allow-list when allowAnySenderForResultater is false.
  allowedSendersForResultater: ["hummesse@gmail.com"],
  // Only this sender may use append mode.
  appendSender: "hummesse@gmail.com",
  // Gmail label used to avoid reprocessing the same mail.
  processedLabel: "m2s-processed",
  // Set true to avoid sending result emails for resultater mode while testing.
  dryRun: false,
};

function extractEmailAddress(rawFrom) {
  const source = String(rawFrom || "").trim();
  const bracketMatch = source.match(/<([^>]+)>/);
  if (bracketMatch && bracketMatch[1]) {
    return bracketMatch[1].toLowerCase();
  }
  return source.toLowerCase();
}

function getOrCreateLabel_(labelName) {
  const existing = GmailApp.getUserLabelByName(labelName);
  if (existing) {
    return existing;
  }
  return GmailApp.createLabel(labelName);
}

function buildSearchQueryForToken_(subjectToken) {
  const token = String(subjectToken || "").replace(/"/g, "\\\"");
  const label = M2S_CONFIG.processedLabel.replace(/"/g, "\\\"");
  return `is:unread subject:"${token}" -label:"${label}" newer_than:7d`;
}

function normalizeSubject_(subject) {
  return String(subject || "").trim().toLowerCase();
}

function getModeFromSubject_(subject) {
  const normalized = normalizeSubject_(subject);
  if (normalized === normalizeSubject_(M2S_CONFIG.resultaterSubjectToken)) {
    return "resultater";
  }
  if (normalized === normalizeSubject_(M2S_CONFIG.appendSubjectToken)) {
    return "append";
  }
  return "";
}

function isAllowedSenderForMode_(mode, fromAddress, allowedResultaterSenders) {
  if (mode === "append") {
    return fromAddress === normalizeSubject_(M2S_CONFIG.appendSender);
  }
  if (mode === "resultater") {
    if (Boolean(M2S_CONFIG.allowAnySenderForResultater)) {
      return true;
    }
    return allowedResultaterSenders.has(fromAddress);
  }
  return false;
}

function collectCandidateThreads_() {
  const queries = [
    buildSearchQueryForToken_(M2S_CONFIG.resultaterSubjectToken),
    buildSearchQueryForToken_(M2S_CONFIG.appendSubjectToken),
  ];

  const byId = new Map();
  for (const query of queries) {
    const threads = GmailApp.search(query, 0, 20);
    console.log(`Search query=${query}`);
    console.log(`Threads found for query=${threads.length}`);
    for (const thread of threads) {
      byId.set(thread.getId(), thread);
    }
  }

  return [...byId.values()];
}

function extractEmailsFromBody_(plainBody) {
  const matches = String(plainBody || "").match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi) || [];
  const normalized = matches
    .map((value) => value.trim().replace(/[),;:]+$/g, "").toLowerCase())
    .filter((value) => value.length > 0);
  return [...new Set(normalized)];
}

function triggerWorker_(fromAddress, subject, recipientsOverride) {
  const payload = {
    from: fromAddress,
    subject: subject,
    dry_run: Boolean(M2S_CONFIG.dryRun),
    recipients_override: recipientsOverride,
  };

  console.log(
    `Triggering Worker for from=${fromAddress}, dry_run=${payload.dry_run}, recipient_override_count=${recipientsOverride.length}`
  );

  const response = UrlFetchApp.fetch(M2S_CONFIG.workerTriggerUrl, {
    method: "post",
    contentType: "application/json",
    headers: {
      "x-trigger-token": M2S_CONFIG.triggerToken,
    },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
  });

  const status = response.getResponseCode();
  console.log(`Worker HTTP status=${status}`);
  if (status < 200 || status >= 300) {
    throw new Error(`Worker trigger failed with HTTP ${status}: ${response.getContentText()}`);
  }
}

function pollAndTrigger() {
  if (!M2S_CONFIG.triggerToken || M2S_CONFIG.triggerToken === "REPLACE_WITH_TRIGGER_TOKEN") {
    throw new Error("Set M2S_CONFIG.triggerToken before running pollAndTrigger.");
  }

  const allowedResultaterSenders = new Set(
    M2S_CONFIG.allowedSendersForResultater.map((x) => String(x).toLowerCase())
  );
  const processed = getOrCreateLabel_(M2S_CONFIG.processedLabel);
  const threads = collectCandidateThreads_();

  console.log(`Unique candidate threads=${threads.length}`);
  if (threads.length === 0) {
    console.log("No matching unread messages found.");
    return;
  }

  for (const thread of threads) {
    const messages = thread.getMessages();
    const message = messages[messages.length - 1];
    const fromAddress = extractEmailAddress(message.getFrom());
    const subject = String(message.getSubject() || "").trim();
    const mode = getModeFromSubject_(subject);
    const plainBody = String(message.getPlainBody() || "");
    const recipientsOverride = extractEmailsFromBody_(plainBody);

    console.log(
      `Processing message from=${fromAddress}, subject=${subject}, mode=${mode || "ignored"}`
    );

    if (!mode) {
      console.log(
        `Subject not exact match. Expected exactly '${M2S_CONFIG.resultaterSubjectToken}' or '${M2S_CONFIG.appendSubjectToken}'. Marking as processed.`
      );
      thread.addLabel(processed);
      thread.markRead();
      continue;
    }

    if (!isAllowedSenderForMode_(mode, fromAddress, allowedResultaterSenders)) {
      console.log(`Sender not allowed for mode=${mode}: ${fromAddress}. Marking as processed.`);
      thread.addLabel(processed);
      thread.markRead();
      continue;
    }

    if (mode === "append" && recipientsOverride.length === 0) {
      console.log("Append mode requires at least one email in body. Marking as processed.");
      thread.addLabel(processed);
      thread.markRead();
      continue;
    }

    triggerWorker_(fromAddress, subject, recipientsOverride);
    thread.addLabel(processed);
    thread.markRead();
    console.log("Triggered successfully and marked thread as processed.");
  }
}

function installMinuteTrigger() {
  const handler = "pollAndTrigger";
  const existing = ScriptApp.getProjectTriggers().filter((t) => t.getHandlerFunction() === handler);
  for (const trigger of existing) {
    ScriptApp.deleteTrigger(trigger);
  }

  ScriptApp.newTrigger(handler)
    .timeBased()
    .everyMinutes(1)
    .create();
}
