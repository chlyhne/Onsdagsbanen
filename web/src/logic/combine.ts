import type {
  CombinedOverallResult,
  CombinedOverallRow,
  CombinedRaceRow,
  ParsedRaceRow,
  RaceLabel,
} from "../types";

function formatSeconds(totalSeconds: number): string {
  const secondsInt = Math.round(totalSeconds);
  const hours = Math.floor(secondsInt / 3600);
  const remainder = secondsInt % 3600;
  const minutes = Math.floor(remainder / 60);
  const seconds = remainder % 60;
  return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function raceSortKey(label: RaceLabel): number {
  return Number.parseInt(label.slice(1), 10);
}

export function combineRaces(raceFrames: ParsedRaceRow[][]): CombinedRaceRow[] {
  if (raceFrames.length === 0) {
    return [];
  }

  const combined = raceFrames.flat();
  const grouped = new Map<
    string,
    {
      race: RaceLabel;
      competitor: string;
      beregnetSeconds: number;
      sailedSeconds: number | null;
      boatName: string;
      boatType: string;
      hdcp: number | null;
      seriesSet: Set<string>;
    }
  >();

  for (const row of combined) {
    if (!row.race || !row.competitor || !Number.isFinite(row.beregnetSeconds)) {
      throw new Error("Combined race inputs are missing required values.");
    }

    const key = `${row.race}||${row.competitor}`;
    const existing = grouped.get(key);

    if (!existing) {
      grouped.set(key, {
        race: row.race,
        competitor: row.competitor,
        beregnetSeconds: row.beregnetSeconds,
        sailedSeconds: row.sailedSeconds,
        boatName: row.boatName,
        boatType: row.boatType,
        hdcp: row.hdcp,
        seriesSet: new Set(row.series ? [row.series] : []),
      });
      continue;
    }

    existing.beregnetSeconds = Math.min(existing.beregnetSeconds, row.beregnetSeconds);
    if (row.sailedSeconds !== null) {
      existing.sailedSeconds =
        existing.sailedSeconds === null ? row.sailedSeconds : Math.min(existing.sailedSeconds, row.sailedSeconds);
    }
    if (row.series) {
      existing.seriesSet.add(row.series);
    }
  }

  const ordered = [...grouped.values()].sort((left, right) => {
    const raceDiff = raceSortKey(left.race) - raceSortKey(right.race);
    if (raceDiff !== 0) {
      return raceDiff;
    }
    const timeDiff = left.beregnetSeconds - right.beregnetSeconds;
    if (timeDiff !== 0) {
      return timeDiff;
    }
    return left.competitor.localeCompare(right.competitor);
  });

  const output: CombinedRaceRow[] = [];
  let currentRace: RaceLabel | null = null;
  let raceMin = 0;
  let previousSeconds = 0;
  let rowIndexInRace = 0;
  let currentRank = 1;

  for (const row of ordered) {
    let intervalSeconds = 0;

    if (currentRace !== row.race) {
      currentRace = row.race;
      raceMin = row.beregnetSeconds;
      previousSeconds = row.beregnetSeconds;
      rowIndexInRace = 0;
      currentRank = 1;
    } else {
      rowIndexInRace += 1;
      intervalSeconds = row.beregnetSeconds - previousSeconds;
      if (row.beregnetSeconds > previousSeconds) {
        currentRank = rowIndexInRace + 1;
      }
      previousSeconds = row.beregnetSeconds;
    }

    const deltaSeconds = row.beregnetSeconds - raceMin;

    output.push({
      race: row.race,
      raceRank: currentRank,
      competitor: row.competitor,
      boatName: row.boatName,
      boatType: row.boatType,
      hdcp: row.hdcp,
      sailedTime: row.sailedSeconds === null ? "" : formatSeconds(row.sailedSeconds),
      beregnetTime: formatSeconds(row.beregnetSeconds),
      delta: formatSeconds(deltaSeconds),
      interval: formatSeconds(intervalSeconds),
      points: currentRank,
      seriesCount: row.seriesSet.size,
    });
  }

  return output;
}

export function combineOverallFromRaces(
  combinedRaces: CombinedRaceRow[],
  maxRace = 18,
  allCompetitors: string[] | null = null,
  discardAfter: number[] | null = null,
): CombinedOverallResult {
  const raceColumns: RaceLabel[] = Array.from({ length: maxRace }, (_, index) => `R${index + 1}` as RaceLabel);

  if (combinedRaces.length === 0) {
    return {
      rows: [],
      raceColumns,
      discardedRacesByCompetitor: {},
      discardAfter: [],
      discardCount: 0,
    };
  }

  const boatTypeByCompetitor = new Map<string, string>();
  for (const row of combinedRaces) {
    if (!boatTypeByCompetitor.has(row.competitor) && row.boatType.trim()) {
      boatTypeByCompetitor.set(row.competitor, row.boatType.trim());
    }
  }

  const pointsByRace = new Map<RaceLabel, Set<string>>();
  const pivot = new Map<string, Map<RaceLabel, number>>();

  for (const row of combinedRaces) {
    if (!Number.isFinite(row.points)) {
      throw new Error("Combined race table must include a points value for every row.");
    }

    if (!pointsByRace.has(row.race)) {
      pointsByRace.set(row.race, new Set<string>());
    }
    pointsByRace.get(row.race)?.add(row.competitor);

    if (!pivot.has(row.competitor)) {
      pivot.set(row.competitor, new Map<RaceLabel, number>());
    }

    const competitorMap = pivot.get(row.competitor) as Map<RaceLabel, number>;
    const rounded = Math.round(row.points);
    const current = competitorMap.get(row.race);
    competitorMap.set(row.race, current === undefined ? rounded : Math.min(current, rounded));
  }

  const competitors = allCompetitors
    ? [...new Set(allCompetitors.map((item) => item.trim()).filter((item) => item.length > 0))].sort((a, b) =>
        a.localeCompare(b),
      )
    : [...pivot.keys()].sort((a, b) => a.localeCompare(b));

  for (const competitor of competitors) {
    if (!pivot.has(competitor)) {
      pivot.set(competitor, new Map<RaceLabel, number>());
    }
  }

  const rosterSize = competitors.length;
  const dnsPointsByRace = new Map<RaceLabel, number>();
  for (const [raceLabel, raceCompetitors] of pointsByRace.entries()) {
    dnsPointsByRace.set(raceLabel, Math.max(raceCompetitors.size + 1, rosterSize) + 1);
  }

  const sailedRaces = raceColumns.filter((label) => dnsPointsByRace.has(label));
  for (const competitor of competitors) {
    const competitorMap = pivot.get(competitor) as Map<RaceLabel, number>;
    for (const raceLabel of sailedRaces) {
      if (!competitorMap.has(raceLabel)) {
        competitorMap.set(raceLabel, dnsPointsByRace.get(raceLabel) as number);
      }
    }
  }

  const cleanedDiscardAfter = [...new Set((discardAfter ?? []).map((value) => Math.trunc(value)).filter((value) => value > 0))].sort(
    (left, right) => left - right,
  );
  const discardCount = cleanedDiscardAfter.filter((threshold) => threshold <= sailedRaces.length).length;

  const discardedRacesByCompetitor: Record<string, RaceLabel[]> = {};
  const rows: CombinedOverallRow[] = [];

  for (const competitor of competitors) {
    const competitorMap = pivot.get(competitor) as Map<RaceLabel, number>;

    const rowValues = sailedRaces
      .map((raceLabel) => ({ raceLabel, value: competitorMap.get(raceLabel) ?? null }))
      .filter((item): item is { raceLabel: RaceLabel; value: number } => item.value !== null);

    let discardedLabels: RaceLabel[] = [];
    if (discardCount > 0 && rowValues.length > 0) {
      discardedLabels = [...rowValues]
        .sort((left, right) => {
          if (right.value !== left.value) {
            return right.value - left.value;
          }
          return raceSortKey(right.raceLabel) - raceSortKey(left.raceLabel);
        })
        .slice(0, Math.min(discardCount, rowValues.length))
        .map((item) => item.raceLabel)
        .sort((left, right) => raceSortKey(left) - raceSortKey(right));
    }

    const discardedSet = new Set(discardedLabels);
    const kept = rowValues.filter((item) => !discardedSet.has(item.raceLabel)).map((item) => item.value);

    const racePoints: Record<RaceLabel, number | null> = {} as Record<RaceLabel, number | null>;
    for (const raceLabel of raceColumns) {
      racePoints[raceLabel] = competitorMap.get(raceLabel) ?? null;
    }

    discardedRacesByCompetitor[competitor] = discardedLabels;
    rows.push({
      combinedRank: null,
      competitor,
      boatType: boatTypeByCompetitor.get(competitor) ?? "",
      racePoints,
      combinedPoints: kept.length > 0 ? kept.reduce((sum, value) => sum + value, 0) : null,
      raceCount: kept.length,
    });
  }

  rows.sort((left, right) => {
    if (left.combinedPoints === null && right.combinedPoints === null) {
      return left.competitor.localeCompare(right.competitor);
    }
    if (left.combinedPoints === null) {
      return 1;
    }
    if (right.combinedPoints === null) {
      return -1;
    }
    if (left.combinedPoints !== right.combinedPoints) {
      return left.combinedPoints - right.combinedPoints;
    }
    return left.competitor.localeCompare(right.competitor);
  });

  let lastPoints: number | null = null;
  let currentRank = 0;
  let scoredRows = 0;

  for (const row of rows) {
    if (row.combinedPoints === null) {
      row.combinedRank = null;
      continue;
    }

    scoredRows += 1;
    if (lastPoints === null || row.combinedPoints > lastPoints) {
      currentRank = scoredRows;
      lastPoints = row.combinedPoints;
    }
    row.combinedRank = currentRank;
  }

  return {
    rows,
    raceColumns,
    discardedRacesByCompetitor,
    discardAfter: cleanedDiscardAfter,
    discardCount,
  };
}
