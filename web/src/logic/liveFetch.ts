import { combineOverallFromRaces, combineRaces } from "./combine";
import {
  fetchClassResultsBatch,
  intersectRaceLabels,
  type ClassGroupConfig,
  type LiveFetchOptions,
} from "./scraper";
import {
  parseAvailableRaceLabelsFromResultPayload,
  parseCompletedRaceLabelsFromResultPayload,
  parseDiscardAfterRacesFromResultPayload,
  parseRaceRowsFromResultPayload,
} from "./parser";
import type { ParsedRaceRow, RaceLabel, ResultsSnapshot, SectionSnapshot } from "../types";

function raceNumber(label: RaceLabel): number {
  return Number.parseInt(label.slice(1), 10);
}

function formatNow(): string {
  const now = new Date();
  const dd = String(now.getDate()).padStart(2, "0");
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const yyyy = String(now.getFullYear());
  const hh = String(now.getHours()).padStart(2, "0");
  const min = String(now.getMinutes()).padStart(2, "0");
  return `${dd}-${mm}-${yyyy} ${hh}:${min}`;
}

function rosterFromPayload(payload: Record<string, unknown>): Set<string> {
  const roster = new Set<string>();
  const entries = payload.EntryResults;

  if (!Array.isArray(entries)) {
    return roster;
  }

  for (const entry of entries) {
    if (!entry || typeof entry !== "object") {
      continue;
    }

    const competitor = String((entry as Record<string, unknown>).TeamName ?? "").trim();
    if (competitor) {
      roster.add(competitor);
    }
  }

  return roster;
}

function effectiveMaxRace(groupMaxRace: number | null, alignedRaces: RaceLabel[]): number {
  const maxAvailable = Math.max(...alignedRaces.map(raceNumber));
  if (groupMaxRace !== null && groupMaxRace > maxAvailable) {
    throw new Error(`max-race ${groupMaxRace} exceeds available race ${maxAvailable} for this group.`);
  }
  return groupMaxRace ?? maxAvailable;
}

function chooseDiscardAfter(candidates: number[][]): number[] {
  const nonEmpty = candidates.filter((candidate) => candidate.length > 0);
  if (nonEmpty.length === 0) {
    return [];
  }

  return [...nonEmpty].sort((left, right) => right.length - left.length)[0];
}

function buildGroupSection(
  group: ClassGroupConfig,
  payloadByClass: Record<string, Record<string, unknown>>,
): SectionSnapshot | null {
  const availableByClass = group.classNames.map((className) =>
    parseAvailableRaceLabelsFromResultPayload(payloadByClass[className], null),
  );
  const alignedAll = intersectRaceLabels(availableByClass);
  if (alignedAll.length === 0) {
    return null;
  }

  const maxRace = effectiveMaxRace(group.maxRace, alignedAll);
  const aligned = alignedAll.filter((label) => raceNumber(label) <= maxRace);

  const completedByClass = group.classNames.map((className) =>
    parseCompletedRaceLabelsFromResultPayload(payloadByClass[className], maxRace),
  );
  const completedCommon = new Set<RaceLabel>(intersectRaceLabels(completedByClass));
  const selectedRaces = aligned.filter((label) => completedCommon.has(label));
  if (selectedRaces.length === 0) {
    return null;
  }

  const parsedByClass = Object.fromEntries(
    group.classNames.map((className) => [
      className,
      parseRaceRowsFromResultPayload(payloadByClass[className], className, selectedRaces),
    ]),
  ) as Record<string, Record<RaceLabel, ParsedRaceRow[]>>;

  const raceFrames: ParsedRaceRow[][] = [];
  for (const raceLabel of selectedRaces) {
    if (!group.classNames.every((className) => raceLabel in parsedByClass[className])) {
      continue;
    }

    for (const className of group.classNames) {
      raceFrames.push(parsedByClass[className][raceLabel]);
    }
  }

  if (raceFrames.length === 0) {
    return null;
  }

  const roster = new Set<string>();
  for (const className of group.classNames) {
    for (const competitor of rosterFromPayload(payloadByClass[className])) {
      roster.add(competitor);
    }
  }

  const discardCandidates = group.classNames.map((className) =>
    parseDiscardAfterRacesFromResultPayload(payloadByClass[className], maxRace),
  );
  const discardAfter = chooseDiscardAfter(discardCandidates);

  const combinedRaces = combineRaces(raceFrames);
  const combinedOverall = combineOverallFromRaces(
    combinedRaces,
    maxRace,
    [...roster].sort((left, right) => left.localeCompare(right)),
    discardAfter,
  );

  return {
    groupLabel: group.groupLabel,
    combinedRaces,
    combinedOverall,
  };
}

export async function buildSnapshotFromManage2Sail(options: LiveFetchOptions): Promise<ResultsSnapshot> {
  const uniqueClassNames = [...new Set(options.classGroups.flatMap((group) => group.classNames))];
  const classPayloads = await fetchClassResultsBatch(options.eventUrl, uniqueClassNames, options.proxyPrefix);

  const sections: SectionSnapshot[] = [];
  const sourceUrls: string[] = [];

  for (const group of options.classGroups) {
    const payloadByClass: Record<string, Record<string, unknown>> = {};
    for (const className of group.classNames) {
      const payload = classPayloads[className];
      if (!payload) {
        throw new Error(`No payload fetched for class '${className}'.`);
      }
      payloadByClass[className] = payload;
    }

    const section = buildGroupSection(group, payloadByClass);
    if (!section) {
      continue;
    }

    sections.push(section);
    for (const className of group.classNames) {
      sourceUrls.push(`${options.eventUrl} [${group.groupLabel} / ${className}]`);
    }
  }

  if (sections.length === 0) {
    throw new Error("No groups produced completed aligned races.");
  }

  return {
    generatedAt: formatNow(),
    sections,
    sourceUrls,
  };
}
