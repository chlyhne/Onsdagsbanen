import type { ParsedRaceRow, RaceLabel } from "../types";

function isInAllowedRaceRange(raceIndex: number, maxRace: number | null): boolean {
  return raceIndex >= 1 && (maxRace === null || raceIndex <= maxRace);
}

function parseTimeToSeconds(value: unknown): number | null {
  if (value === null || value === undefined) {
    return null;
  }

  const text = String(value).trim();
  if (!text) {
    return null;
  }

  const match = text.match(/\d{1,2}:\d{2}(?::\d{2})?/);
  if (!match) {
    return null;
  }

  const parts = match[0].split(":").map((part) => Number.parseInt(part, 10));
  if (parts.some(Number.isNaN)) {
    return null;
  }

  if (parts.length === 3) {
    const [hours, minutes, seconds] = parts;
    return hours * 3600 + minutes * 60 + seconds;
  }

  const [minutes, seconds] = parts;
  return minutes * 60 + seconds;
}

function cleanText(value: unknown): string {
  if (value === null || value === undefined) {
    return "";
  }

  const text = String(value).trim();
  if (!text || text.toLowerCase() === "nan" || text.toLowerCase() === "none") {
    return "";
  }

  return text.replace(/\s+/g, " ");
}

export function parseAvailableRaceLabelsFromResultPayload(
  payload: Record<string, unknown>,
  maxRace: number | null = null,
): RaceLabel[] {
  const raceNumbers = new Set<number>();
  const raceNames = payload.RaceNames;

  if (Array.isArray(raceNames)) {
    for (const item of raceNames) {
      if (!item || typeof item !== "object") {
        continue;
      }
      const raceIndex = (item as { RaceIndex?: unknown }).RaceIndex;
      if (typeof raceIndex === "number" && Number.isInteger(raceIndex) && isInAllowedRaceRange(raceIndex, maxRace)) {
        raceNumbers.add(raceIndex);
      }
    }
  }

  return [...raceNumbers].sort((left, right) => left - right).map((raceNumber) => `R${raceNumber}` as RaceLabel);
}

export function parseCompletedRaceLabelsFromResultPayload(
  payload: Record<string, unknown>,
  maxRace: number | null = null,
): RaceLabel[] {
  const countsByRace = new Map<number, number>();
  const entries = payload.EntryResults;

  if (!Array.isArray(entries)) {
    return [];
  }

  for (const entry of entries) {
    if (!entry || typeof entry !== "object") {
      continue;
    }

    const raceResults = (entry as { EntryRaceResults?: unknown }).EntryRaceResults;
    if (!Array.isArray(raceResults)) {
      continue;
    }

    for (const raceResult of raceResults) {
      if (!raceResult || typeof raceResult !== "object") {
        continue;
      }

      const typedRace = raceResult as Record<string, unknown>;
      const raceIndex = typedRace.OverallRaceIndex;
      if (typeof raceIndex !== "number" || !Number.isInteger(raceIndex) || !isInAllowedRaceRange(raceIndex, maxRace)) {
        continue;
      }

      const correctedMs = typedRace.CorrectedTimeMs;
      const correctedText = typedRace.CorrectedTime;
      const hasCorrected = typeof correctedMs === "number" || parseTimeToSeconds(correctedText) !== null;
      if (!hasCorrected) {
        continue;
      }

      countsByRace.set(raceIndex, (countsByRace.get(raceIndex) ?? 0) + 1);
    }
  }

  return [...countsByRace.entries()]
    .filter(([, count]) => count > 0)
    .map(([raceIndex]) => raceIndex)
    .sort((left, right) => left - right)
    .map((raceIndex) => `R${raceIndex}` as RaceLabel);
}

export function parseDiscardAfterRacesFromResultPayload(
  payload: Record<string, unknown>,
  maxRace: number | null = null,
): number[] {
  const discardsText = String(payload.Discards ?? "");
  const numbers = [...discardsText.matchAll(/\d+/g)].map((match) => Number.parseInt(match[0], 10));
  return [...new Set(numbers.filter((value) => Number.isInteger(value) && isInAllowedRaceRange(value, maxRace)))].sort(
    (left, right) => left - right,
  );
}

export function parseRaceRowsFromResultPayload(
  payload: Record<string, unknown>,
  seriesLabel: string,
  raceLabels: RaceLabel[],
): Record<RaceLabel, ParsedRaceRow[]> {
  const requested = [...new Set(raceLabels.filter((label) => /^R\d+$/.test(label)))];
  if (requested.length === 0) {
    return {} as Record<RaceLabel, ParsedRaceRow[]>;
  }

  const requestedIndices = new Map<number, RaceLabel>();
  for (const label of requested) {
    requestedIndices.set(Number.parseInt(label.slice(1), 10), label);
  }

  const rowsByLabel = new Map<RaceLabel, ParsedRaceRow[]>();
  for (const label of requested) {
    rowsByLabel.set(label, []);
  }

  const entries = payload.EntryResults;
  if (!Array.isArray(entries)) {
    return {} as Record<RaceLabel, ParsedRaceRow[]>;
  }

  for (const entry of entries) {
    if (!entry || typeof entry !== "object") {
      continue;
    }

    const typedEntry = entry as Record<string, unknown>;
    const competitor = cleanText(typedEntry.TeamName);
    if (!competitor) {
      continue;
    }

    const boatName = cleanText(typedEntry.BoatName);
    const boatType = cleanText(typedEntry.BoatType);

    const raceResults = typedEntry.EntryRaceResults;
    if (!Array.isArray(raceResults)) {
      continue;
    }

    for (const raceResult of raceResults) {
      if (!raceResult || typeof raceResult !== "object") {
        continue;
      }

      const typedRace = raceResult as Record<string, unknown>;
      const raceIndex = typedRace.OverallRaceIndex;
      if (typeof raceIndex !== "number" || !Number.isInteger(raceIndex) || !requestedIndices.has(raceIndex)) {
        continue;
      }

      const correctedMs = typedRace.CorrectedTimeMs;
      const beregnetSeconds =
        typeof correctedMs === "number" ? correctedMs / 1000 : parseTimeToSeconds(typedRace.CorrectedTime);

      if (beregnetSeconds === null) {
        continue;
      }

      const sailedSeconds = parseTimeToSeconds(typedRace.RaceTime ?? typedRace.RaceTimeForCalculation);
      const hdcpRaw = typedRace.Hdcp;
      const hdcp = typeof hdcpRaw === "number" ? hdcpRaw : null;

      const raceLabel = requestedIndices.get(raceIndex) as RaceLabel;
      rowsByLabel.get(raceLabel)?.push({
        series: seriesLabel,
        race: raceLabel,
        competitor,
        boatName,
        boatType,
        hdcp,
        beregnetSeconds,
        sailedSeconds,
      });
    }
  }

  const result: Record<RaceLabel, ParsedRaceRow[]> = {} as Record<RaceLabel, ParsedRaceRow[]>;
  for (const [label, rows] of rowsByLabel.entries()) {
    if (rows.length > 0) {
      result[label] = rows;
    }
  }

  return result;
}
