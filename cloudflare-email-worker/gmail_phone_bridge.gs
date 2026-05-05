// Gmail -> Cloudflare Worker bridge for no-domain setups.
// Deploy as a Google Apps Script and run pollAndTrigger on a time trigger.

const M2S_CONFIG = {
  workerTriggerUrl: "https://m2s-email-trigger.hummesse.workers.dev/trigger",
  triggerToken: "REPLACE_WITH_TRIGGER_TOKEN",
  // Only messages from these senders are accepted.
  allowedSenders: ["hummesse@gmail.com"],
  // Incoming subject must contain this token.
  requiredSubjectToken: "M2S RUN",
  // Gmail label used to avoid reprocessing the same mail.
  processedLabel: "m2s-processed",
  // Optional: set true while validating setup.
  dryRun: true,
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

function buildSearchQuery_() {
  const token = M2S_CONFIG.requiredSubjectToken.replace(/"/g, "\\\"");
  const label = M2S_CONFIG.processedLabel.replace(/"/g, "\\\"");
  return `is:unread subject:"${token}" -label:"${label}" newer_than:7d`;
}

function triggerWorker_(fromAddress, subject) {
  const payload = {
    from: fromAddress,
    subject: subject,
    dry_run: Boolean(M2S_CONFIG.dryRun),
  };

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
  if (status < 200 || status >= 300) {
    throw new Error(`Worker trigger failed with HTTP ${status}: ${response.getContentText()}`);
  }
}

function pollAndTrigger() {
  if (!M2S_CONFIG.triggerToken || M2S_CONFIG.triggerToken === "REPLACE_WITH_TRIGGER_TOKEN") {
    throw new Error("Set M2S_CONFIG.triggerToken before running pollAndTrigger.");
  }

  const allowed = new Set(M2S_CONFIG.allowedSenders.map((x) => String(x).toLowerCase()));
  const processed = getOrCreateLabel_(M2S_CONFIG.processedLabel);
  const query = buildSearchQuery_();
  const threads = GmailApp.search(query, 0, 20);

  for (const thread of threads) {
    const messages = thread.getMessages();
    const message = messages[messages.length - 1];
    const fromAddress = extractEmailAddress(message.getFrom());
    const subject = String(message.getSubject() || "").trim();

    if (!allowed.has(fromAddress)) {
      thread.addLabel(processed);
      thread.markRead();
      continue;
    }

    triggerWorker_(fromAddress, subject);
    thread.addLabel(processed);
    thread.markRead();
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
