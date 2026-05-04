export type RaceLabel = `R${number}`;

export interface ParsedRaceRow {
  series: string;
  race: RaceLabel;
  competitor: string;
  boatName: string;
  boatType: string;
  hdcp: number | null;
  beregnetSeconds: number;
  sailedSeconds: number | null;
}

export interface CombinedRaceRow {
  race: RaceLabel;
  raceRank: number;
  competitor: string;
  boatName: string;
  boatType: string;
  hdcp: number | null;
  sailedTime: string;
  beregnetTime: string;
  delta: string;
  interval: string;
  points: number;
  seriesCount: number;
}

export interface CombinedOverallRow {
  combinedRank: number | null;
  competitor: string;
  boatType: string;
  racePoints: Record<RaceLabel, number | null>;
  combinedPoints: number | null;
  raceCount: number;
}

export interface CombinedOverallResult {
  rows: CombinedOverallRow[];
  raceColumns: RaceLabel[];
  discardedRacesByCompetitor: Record<string, RaceLabel[]>;
  discardAfter: number[];
  discardCount: number;
}

export interface SectionSnapshot {
  groupLabel: string;
  combinedRaces: CombinedRaceRow[];
  combinedOverall: CombinedOverallResult;
}

export interface ResultsSnapshot {
  generatedAt: string;
  sections: SectionSnapshot[];
  sourceUrls: string[];
}
