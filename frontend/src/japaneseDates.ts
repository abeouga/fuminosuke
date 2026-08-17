import type { FieldReading } from "./types";

export type JapaneseEra = "" | "heisei" | "reiwa";

export function sanitizeJapaneseEraDateInput(value: string) {
  return value
    .normalize("NFKC")
    .replace(/[^\d]+/g, " ")
    .replace(/^\s+/, "")
    .split(" ")
    .slice(0, 3)
    .join(" ");
}

export function japaneseEraDateParts(value: string | null) {
  if (!value) return null;
  const groups = sanitizeJapaneseEraDateInput(value).trim().split(/\s+/);
  if (groups.length !== 3 || groups.some((group) => !/^\d+$/.test(group))) return null;
  const [year, month, day] = groups.map(Number);
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > 31) return null;
  return [year, month, day] as const;
}

export function formatJapaneseEraDate(
  value: string | null,
  era: Exclude<JapaneseEra, "">,
) {
  const parts = japaneseEraDateParts(value);
  if (!parts) return null;
  const [eraYear, month, day] = parts;
  const westernYear = eraYear + (era === "heisei" ? 1988 : 2018);
  const converted = validDate(westernYear, month, day);
  if (!converted) return null;
  const stamp = westernYear * 10000 + month * 100 + day;
  if (era === "heisei" && (stamp < 19890108 || stamp > 20190430)) return null;
  if (era === "reiwa" && stamp < 20190501) return null;
  return formatWesternDate(converted);
}

export function inferJapaneseEraFromDigits(raw: string | null): JapaneseEra {
  if (!raw) return "";
  const groups = sanitizeJapaneseEraDateInput(raw).trim().split(/\s+/);
  if (groups.length !== 3) return "";
  if (groups[0].length === 1) return "reiwa";
  if (groups[0].length === 2) return "heisei";
  return "";
}

export function inferJapaneseEra(
  raw: string | null,
  normalized: string | null,
): JapaneseEra {
  if (normalized) {
    if (formatJapaneseEraDate(raw, "heisei") === normalizeJapaneseDateDisplay(null, normalized)) {
      return "heisei";
    }
    if (formatJapaneseEraDate(raw, "reiwa") === normalizeJapaneseDateDisplay(null, normalized)) {
      return "reiwa";
    }
  }
  return inferJapaneseEraFromDigits(raw);
}

export function automaticJapaneseEraDatePatch(raw: string): Partial<FieldReading> {
  const era = inferJapaneseEraFromDigits(raw);
  const normalized = westernYearDate(raw) ?? (era ? formatJapaneseEraDate(raw, era) : null);
  return normalized
    ? {
      raw: sanitizeJapaneseEraDateInput(raw),
      normalized,
      status: "readable",
      reviewReason: null,
    }
    : {
      raw: sanitizeJapaneseEraDateInput(raw),
      normalized: null,
      status: "uncertain",
      reviewReason: era || !japaneseEraDateParts(raw)
        ? "BIRTH_DATE_NUMBERS_INVALID"
        : "BIRTH_DATE_ERA_REQUIRED",
    };
}

export function normalizeJapaneseDateDisplay(
  raw: string | null | undefined,
  normalized: string | null | undefined,
) {
  const normalizedWestern = westernYearDate(normalized ?? null);
  if (normalizedWestern) return normalizedWestern;
  const rawWestern = westernYearDate(raw ?? null);
  if (rawWestern) return rawWestern;
  if (!raw) return null;
  const era = inferJapaneseEraFromDigits(raw);
  return era ? formatJapaneseEraDate(raw, era) : null;
}

function westernYearDate(value: string | null) {
  if (!value) return null;
  const sanitized = sanitizeJapaneseEraDateInput(value).trim();
  const groups = sanitized.split(/\s+/);
  if (groups.length !== 3 || groups[0].length !== 4) return null;
  const [year, month, day] = groups.map(Number);
  const converted = validDate(year, month, day);
  return converted ? formatWesternDate(converted) : null;
}

function validDate(year: number, month: number, day: number) {
  const converted = new Date(Date.UTC(year, month - 1, day));
  return converted.getUTCFullYear() === year
    && converted.getUTCMonth() === month - 1
    && converted.getUTCDate() === day
    ? converted
    : null;
}

function formatWesternDate(value: Date) {
  return `${value.getUTCFullYear()}年${value.getUTCMonth() + 1}月${value.getUTCDate()}日`;
}
