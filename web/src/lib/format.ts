const nf = new Intl.NumberFormat("en-US");

export function fmtInt(v: number | null | undefined): string {
  return v == null ? "–" : nf.format(Math.round(v));
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  return v == null
    ? "–"
    : new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v);
}

export function fmtPct(v: number | null | undefined, digits = 2): string {
  return v == null ? "–" : `${fmtNum(v, digits)}%`;
}

export function fmtUsd(v: number | null | undefined): string {
  return v == null
    ? "–"
    : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 }).format(v);
}

export function fmtTs(v: string | null | undefined): string {
  if (!v) return "–";
  return v.replace("T", " ").slice(0, 19);
}

export function fmtBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

export function fmtValue(v: number | null | undefined, unit: string): string {
  if (v == null) return "–";
  if (unit === "%") return fmtPct(v, v >= 99 ? 4 : 2);
  if (unit === "bps") return `${fmtNum(v, 2)} bps`;
  if (unit === "count") return fmtInt(v);
  return `${fmtNum(v, 2)} ${unit}`;
}
