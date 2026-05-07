import type { CombinedOverallRow, RaceLabel, ResultsSnapshot, SectionSnapshot } from "./types";
import { buildSnapshotFromManage2Sail } from "./logic/liveFetch";

const FIXED_EVENT_URL = "https://www.manage2sail.com/da-DK/event/Onsdagsbanen2026#!/";
const FIXED_CLASS_GROUPS = [
  {
    groupLabel: "Stor Bane",
    classNames: ["Stor bane 1", "Stor bane 2"],
    maxRace: null,
  },
  {
    groupLabel: "Lille Bane",
    classNames: ["Lille bane 1", "Lille bane 2"],
    maxRace: null,
  },
];

const PROXY_PREFIX_CANDIDATES = [
  "",
  "https://api.codetabs.com/v1/proxy?quest=",
  "https://api.allorigins.win/raw?url=",
];

interface Theme {
  titleColor: string;
  headerColor: string;
  rowColors: [string, string];
}

function hslToHex(hue: number, saturation: number, lightness: number): string {
  const h = hue / 360;
  const s = saturation / 100;
  const l = lightness / 100;

  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h * 6) % 2) - 1));
  const m = l - c / 2;

  let r = 0;
  let g = 0;
  let b = 0;

  if (h < 1 / 6) {
    r = c;
    g = x;
  } else if (h < 2 / 6) {
    r = x;
    g = c;
  } else if (h < 3 / 6) {
    g = c;
    b = x;
  } else if (h < 4 / 6) {
    g = x;
    b = c;
  } else if (h < 5 / 6) {
    r = x;
    b = c;
  } else {
    r = c;
    b = x;
  }

  const toHex = (channel: number) => Math.round((channel + m) * 255).toString(16).padStart(2, "0");
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

function themeForIndex(index: number): Theme {
  const goldenAngle = 137.508;
  const hue = (208 + index * goldenAngle) % 360;

  return {
    titleColor: hslToHex(hue, 65, 28),
    headerColor: hslToHex(hue, 55, 34),
    rowColors: [hslToHex(hue, 35, 97), hslToHex(hue, 35, 92)],
  };
}

function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function titleCaseWords(value: unknown): string {
  const text = String(value ?? "").trim();
  if (!text) {
    return "";
  }

  return text
    .split(/\s+/)
    .map((word) => word.slice(0, 1).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

function raceColumnsForRows(rows: CombinedOverallRow[], raceColumns: RaceLabel[]): RaceLabel[] {
  return raceColumns.filter((raceLabel) => rows.some((row) => row.racePoints[raceLabel] !== null));
}

function humanizeError(error: unknown): string {
  const base = error instanceof Error ? error.message : "Unknown error.";
  const corsLike = /Failed to fetch|CORS|NetworkError|Load failed/i.test(base);
  if (!corsLike) {
    return base;
  }
  return `${base} This is CORS blocking in the browser (not XSS). Live fetch failed after direct and automatic proxy attempts. A dedicated server-side proxy endpoint is required for reliable refresh.`;
}

function renderOverallTable(section: SectionSnapshot, theme: Theme): string {
  const rows = section.combinedOverall.rows;
  const discardMap = section.combinedOverall.discardedRacesByCompetitor;
  const raceColumns = raceColumnsForRows(rows, section.combinedOverall.raceColumns);

  const headerCells = ["Plac.", "Deltager", "Badtype", ...raceColumns, "Point"]
    .map((label) => `<th>${escapeHtml(label)}</th>`)
    .join("");

  const bodyRows = rows
    .map((row, index) => {
      const raceCells = raceColumns
        .map((raceLabel) => {
          const value = row.racePoints[raceLabel];
          const discarded = discardMap[row.competitor]?.includes(raceLabel) ?? false;
          const className = discarded ? "discarded" : "";
          return `<td class="${className}">${escapeHtml(value ?? "")}</td>`;
        })
        .join("");

      const rowColor = theme.rowColors[index % 2];
      return `<tr style="background:${rowColor}">
        <td>${escapeHtml(row.combinedRank ?? "")}</td>
        <td class="left">${escapeHtml(titleCaseWords(row.competitor))}</td>
        <td class="left">${escapeHtml(titleCaseWords(row.boatType))}</td>
        ${raceCells}
        <td class="strong">${escapeHtml(row.combinedPoints ?? "")}</td>
      </tr>`;
    })
    .join("");

  return `<table class="results-table">
    <thead style="background:${theme.headerColor}"><tr>${headerCells}</tr></thead>
    <tbody>${bodyRows}</tbody>
  </table>`;
}

export async function bootstrapApp(root: HTMLElement | null): Promise<void> {
  if (!root) {
    return;
  }

  root.innerHTML = `
    <div class="page">
      <header class="hero">
        <h1>M2S Combined Results</h1>
        <p>Onsdagsbanen 2026: Stor bane 1+2 and Lille bane 1+2 combined.</p>
      </header>

      <section id="group-tabs" class="group-tabs"></section>
      <section id="group-output" class="panel output"></section>

      <section class="panel controls">
        <div class="button-row">
          <button id="refresh-button" class="refresh-button">Refresh results</button>
        </div>

        <p id="status" class="status">Loading live results...</p>
      </section>
    </div>
  `;

  const refreshButton = root.querySelector<HTMLButtonElement>("#refresh-button");
  const statusElement = root.querySelector<HTMLElement>("#status");
  const tabsElement = root.querySelector<HTMLElement>("#group-tabs");
  const outputElement = root.querySelector<HTMLElement>("#group-output");

  if (!refreshButton || !statusElement || !tabsElement || !outputElement) {
    throw new Error("App shell elements were not created correctly.");
  }

  let snapshot: ResultsSnapshot | null = null;
  let activeGroupIndex = 0;

  const render = () => {
    if (!snapshot || snapshot.sections.length === 0) {
      tabsElement.innerHTML = "";
      outputElement.innerHTML = "<p>No section data found in snapshot.</p>";
      return;
    }

    if (activeGroupIndex >= snapshot.sections.length) {
      activeGroupIndex = 0;
    }

    tabsElement.innerHTML = snapshot.sections
      .map((section, index) => {
        const theme = themeForIndex(index);
        const activeClass = index === activeGroupIndex ? "active" : "";
        return `<button class="group-tab ${activeClass}" data-index="${index}" style="--tab-color:${theme.headerColor}">${escapeHtml(section.groupLabel)}</button>`;
      })
      .join("");

    const currentSection = snapshot.sections[activeGroupIndex];
    const theme = themeForIndex(activeGroupIndex);

    outputElement.innerHTML = `
      <h2 style="color:${theme.titleColor}">Samlet kombineret stilling: ${escapeHtml(currentSection.groupLabel)}</h2>
      <p class="generated">Genereret: ${escapeHtml(snapshot.generatedAt)}</p>
      ${renderOverallTable(currentSection, theme)}
    `;

    tabsElement.querySelectorAll<HTMLButtonElement>(".group-tab").forEach((button) => {
      button.addEventListener("click", () => {
        const indexText = button.dataset.index;
        if (!indexText) {
          return;
        }
        activeGroupIndex = Number.parseInt(indexText, 10);
        render();
      });
    });
  };

  const loadLiveResults = async () => {
    statusElement.textContent = "Fetching live results from Manage2Sail...";
    let lastError: unknown = null;

    for (const proxyPrefix of PROXY_PREFIX_CANDIDATES) {
      try {
        snapshot = await buildSnapshotFromManage2Sail({
          eventUrl: FIXED_EVENT_URL,
          classGroups: FIXED_CLASS_GROUPS,
          proxyPrefix,
        });
        statusElement.textContent = proxyPrefix
          ? "Live results fetched successfully (proxy mode)."
          : "Live results fetched successfully.";
        render();
        return;
      } catch (error) {
        lastError = error;
      }
    }
    tabsElement.innerHTML = "";
    outputElement.innerHTML = "<p>Live fetch failed. No fallback data is loaded.</p>";
    statusElement.textContent = humanizeError(lastError);
  };

  refreshButton.addEventListener("click", async () => {
    await loadLiveResults();
  });

  await loadLiveResults();
}
