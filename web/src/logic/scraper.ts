import type { RaceLabel } from "../types";

interface RegattaRef {
  name: string;
  id: string;
}

export interface ClassGroupConfig {
  groupLabel: string;
  classNames: string[];
  maxRace: number | null;
}

export interface LiveFetchOptions {
  eventUrl: string;
  classGroups: ClassGroupConfig[];
  proxyPrefix: string;
}

function normalizeText(text: string): string {
  return text
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}_]+/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function resultsUrl(url: string): string {
  const trimmed = url.trim();
  if (trimmed.includes("#!/")) {
    return `${trimmed.split("#!/", 1)[0]}#!/results`;
  }
  return `${trimmed.replace(/\/+$/, "")}#!/results`;
}

function applyProxy(url: string, proxyPrefix: string): string {
  const trimmed = proxyPrefix.trim();
  if (!trimmed) {
    return url;
  }
  if (trimmed.includes("{url}")) {
    return trimmed.replace("{url}", encodeURIComponent(url));
  }
  return `${trimmed}${encodeURIComponent(url)}`;
}

async function httpGetText(url: string, proxyPrefix: string): Promise<string> {
  const finalUrl = applyProxy(url, proxyPrefix);
  const response = await fetch(finalUrl, {
    headers: {
      Accept: "text/html,application/json",
    },
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status} while fetching ${url}`);
  }

  return await response.text();
}

async function httpGetJson(url: string, proxyPrefix: string): Promise<Record<string, unknown>> {
  const text = await httpGetText(url, proxyPrefix);
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error(`Expected JSON payload from ${url}`);
  }

  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`Expected JSON object from ${url}`);
  }

  return parsed as Record<string, unknown>;
}

function extractEventId(html: string): string {
  const match = html.match(/window\.SailingInfo\.eventId\s*=\s*'([^']+)'/);
  if (!match) {
    throw new Error("Could not locate event ID in page HTML.");
  }
  return match[1];
}

function extractBootstrapData(html: string): Record<string, unknown> {
  const match =
    html.match(/window\.boostrapedResourceData\s*=\s*(\{[\s\S]*?\})\s*;\s*<\/script>/i) ??
    html.match(/window\.bootstrapedResourceData\s*=\s*(\{[\s\S]*?\})\s*;\s*<\/script>/i) ??
    html.match(/window\.boostrapedResourceData\s*=\s*(\{[\s\S]*?\})\s*;/i);

  if (!match) {
    throw new Error("Could not locate bootstrap data in page HTML.");
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(match[1]);
  } catch {
    throw new Error("Bootstrap data is not valid JSON.");
  }

  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Bootstrap data is not a JSON object.");
  }

  return parsed as Record<string, unknown>;
}

function extractRegattaMap(bootstrapData: Record<string, unknown>): Map<string, RegattaRef> {
  const regattas = bootstrapData.Regatta;
  if (!Array.isArray(regattas)) {
    throw new Error("Bootstrap data does not contain a regatta list.");
  }

  const mapping = new Map<string, RegattaRef>();
  for (const item of regattas) {
    if (!item || typeof item !== "object") {
      continue;
    }

    const typed = item as Record<string, unknown>;
    const name = String(typed.Name ?? "").trim();
    const id = String(typed.Id ?? "").trim();
    if (!name || !id) {
      continue;
    }

    mapping.set(normalizeText(name), { name, id });
  }

  if (mapping.size === 0) {
    throw new Error("No regattas with Name/Id found in bootstrap data.");
  }

  return mapping;
}

function resolveClassRequests(classNames: string[], regattaMap: Map<string, RegattaRef>): Map<string, RegattaRef> {
  const resolved = new Map<string, RegattaRef>();

  for (const className of classNames) {
    const requested = String(className).trim();
    if (!requested) {
      continue;
    }

    const key = normalizeText(requested);
    const regatta = regattaMap.get(key);
    if (!regatta) {
      throw new Error(`Class '${requested}' was not found in event regatta list.`);
    }

    resolved.set(requested, regatta);
  }

  if (resolved.size === 0) {
    throw new Error("No valid class names were provided.");
  }

  return resolved;
}

export async function fetchEventBootstrap(eventUrl: string, proxyPrefix: string): Promise<{
  eventId: string;
  regattaMap: Map<string, RegattaRef>;
}> {
  const html = await httpGetText(resultsUrl(eventUrl), proxyPrefix);
  const eventId = extractEventId(html);
  const bootstrap = extractBootstrapData(html);
  const regattaMap = extractRegattaMap(bootstrap);
  return { eventId, regattaMap };
}

export async function fetchClassResultsBatch(
  eventUrl: string,
  classNames: string[],
  proxyPrefix: string,
): Promise<Record<string, Record<string, unknown>>> {
  const { eventId, regattaMap } = await fetchEventBootstrap(eventUrl, proxyPrefix);
  const requestsByClass = resolveClassRequests(classNames, regattaMap);

  const orderedClassNames = classNames.filter((className) => requestsByClass.has(className));
  const pairs = await Promise.all(
    orderedClassNames.map(async (className) => {
      const regatta = requestsByClass.get(className) as RegattaRef;
      const endpoint = `https://www.manage2sail.com/api/event/${eventId}/regattaresult/${regatta.id}`;
      const payload = await httpGetJson(endpoint, proxyPrefix);
      return [className, payload] as const;
    }),
  );

  return Object.fromEntries(pairs);
}

export function parseClassGroups(rawSpec: string, fallbackMaxRace: number | null): ClassGroupConfig[] {
  const lines = rawSpec
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);

  if (lines.length === 0) {
    throw new Error("At least one class group line is required.");
  }

  const groups: ClassGroupConfig[] = [];

  for (const [index, line] of lines.entries()) {
    const hasLabel = line.includes(":");
    const parts = hasLabel ? line.split(":", 2) : ["", line];
    const labelPart = parts[0] ?? "";
    const classPart = parts[1] ?? "";
    const classNames = classPart
      .split(",")
      .map((item) => item.trim())
      .filter((item) => item.length > 0);

    if (classNames.length < 2) {
      throw new Error(`Group line ${index + 1} must contain at least two class names.`);
    }

    const defaultLabel = classNames[0].replace(/\s+\d+$/, "").trim();
    const groupLabel = (hasLabel ? labelPart : defaultLabel || `Group ${index + 1}`).trim();

    groups.push({
      groupLabel: groupLabel || `Group ${index + 1}`,
      classNames,
      maxRace: fallbackMaxRace,
    });
  }

  return groups;
}

export function intersectRaceLabels(labelGroups: RaceLabel[][]): RaceLabel[] {
  if (labelGroups.length === 0) {
    return [];
  }

  const [first, ...rest] = labelGroups;
  const intersection = new Set<RaceLabel>(first);

  for (const labels of rest) {
    const current = new Set<RaceLabel>(labels);
    for (const label of [...intersection]) {
      if (!current.has(label)) {
        intersection.delete(label);
      }
    }
  }

  return [...intersection].sort((left, right) => Number.parseInt(left.slice(1), 10) - Number.parseInt(right.slice(1), 10));
}
