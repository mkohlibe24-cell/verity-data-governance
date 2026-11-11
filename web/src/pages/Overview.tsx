import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { api } from "../lib/session";
import { fmtInt, fmtNum, fmtPct, fmtTs, fmtUsd, fmtValue } from "../lib/format";
import { axis, baseOption, Chart, useChartTheme } from "../components/Chart";
import { Card, ErrorState, KeyValue, PageHeader, RagBadge, SlideOver, Skeleton } from "../components/ui";

type Kri = {
  kri_id: string; name: string; category: string; period: string; value: number; unit: string; direction: string;
  amber_threshold: number; red_threshold: number; rag: string; formula: string; rationale: string; escalation: string; owner: string;
};
type Trend = { kri_id: string; period: string; value: number; rag: string };
type Cmp = { alerts: number; precision: number; recall: number; f1: number; pr_auc?: number; fraud_amount_missed: number };
type Overview = {
  run: { run_id: string; started_at: string; ended_at: string; duration_seconds: number; status: string; bronze_rows: number;
    quarantined_rows: number; silver_rows: number; gold_fact_rows: number; alerts: number; open_issues: number };
  batch_as_of: string; report_month: string;
  data: { source_rows: number; synthetic_rows: number; bronze_rows: number; quarantined_rows: number; gold_customers: number; gold_merchants: number };
  issues: { open: number; open_by_severity: Record<string, number> };
  monitoring: { alerts: number; alerts_by_priority: Record<string, number> };
  ml: { comparison_test: Record<string, Cmp>; threshold: Record<string, number> };
  kris: Kri[]; kri_trend: Trend[]; alerts_monthly: { month: string; priority: string; n: number }[];
  previous_run: string | null; kri_previous: { kri_id: string; value: number; rag: string }[];
};

function threshText(k: Kri) {
  const op = k.direction === "higher_is_better" ? "below" : "above";
  return `Amber ${op} ${fmtValue(k.amber_threshold, k.unit)}, Red ${op} ${fmtValue(k.red_threshold, k.unit)}`;
}

/** Run-level KRIs have no monthly history, so compare with the previous full pipeline run instead. */
function deltaNote(k: Kri, prev?: { value: number }) {
  if (!prev) return "First run: nothing to compare yet";
  const diff = k.value - prev.value;
  if (Math.abs(diff) < 1e-9) return "No change vs previous run";
  return `${diff > 0 ? "Up" : "Down"} ${fmtValue(Math.abs(diff), k.unit)} vs previous run`;
}

function Spark({ data, color }: { data: number[]; color: string }) {
  if (data.length < 2) return null;
  const min = Math.min(...data), max = Math.max(...data);
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * 100},${max === min ? 12 : 22 - ((v - min) / (max - min)) * 20}`).join(" ");
  return (
    <svg viewBox="0 0 100 24" preserveAspectRatio="none" className="h-6 w-full" aria-hidden>
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function TrendChart({ k, trend }: { k: Kri; trend: Trend[] }) {
  const t = useChartTheme();
  const opt = {
    ...baseOption(t),
    legend: undefined,
    grid: { left: 8, right: 96, top: 16, bottom: 8, containLabel: true },
    xAxis: { type: "category" as const, data: trend.map((p) => p.period), ...axis(t), splitLine: { show: false } },
    tooltip: { ...baseOption(t).tooltip, valueFormatter: (v: unknown) => fmtValue(v as number, k.unit) },
    yAxis: {
      type: "value" as const, scale: true, ...axis(t),
      // Ticks at least 0.01 apart so two-decimal percentage labels never repeat.
      ...(k.unit === "%" ? { minInterval: 0.01, axisLabel: { color: t.muted, formatter: (v: number) => fmtPct(v) } } : {}),
    },
    series: [{
      type: "line" as const, name: k.name, data: trend.map((p) => p.value), symbol: "circle", symbolSize: 5,
      lineStyle: { color: t.series[0], width: 2 }, itemStyle: { color: t.series[0] },
      markLine: {
        symbol: "none", silent: true,
        label: { position: "end" as const, color: t.muted, fontSize: 11 },
        data: [
          { yAxis: k.amber_threshold, name: "Amber", lineStyle: { color: t.amber, type: "dashed" as const }, label: { formatter: `Amber ${fmtValue(k.amber_threshold, k.unit)}` } },
          { yAxis: k.red_threshold, name: "Red", lineStyle: { color: t.red, type: "dashed" as const }, label: { formatter: `Red ${fmtValue(k.red_threshold, k.unit)}` } },
        ],
      },
    }],
  };
  return <Chart option={opt} height={220} label={`Monthly trend of ${k.name} with amber and red thresholds`} />;
}

export default function Overview() {
  const t = useChartTheme();
  const q = useQuery({ queryKey: ["overview"], queryFn: () => api<Overview>("/overview") });
  const [open, setOpen] = useState<Kri | null>(null);
  if (q.isLoading)
    return (
      <>
        <PageHeader title="Overview" />
        <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">{Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-32" />)}</div>
      </>
    );
  if (q.error) return <ErrorState error={q.error} onRetry={q.refetch} />;
  const d = q.data!;
  const trendOf = (id: string) => d.kri_trend.filter((p) => p.kri_id === id);
  const breaches = d.kris.filter((k) => k.rag !== "Green");
  const months = [...new Set(d.alerts_monthly.map((a) => a.month))];
  const prios = ["P1", "P2", "P3", "P4"];
  const prioColor: Record<string, string> = { P1: t.red, P2: t.amber, P3: t.series[2], P4: t.series[1] };
  const alertsOpt = {
    ...baseOption(t),
    xAxis: { type: "category" as const, data: months, ...axis(t), splitLine: { show: false } },
    yAxis: { type: "value" as const, ...axis(t) },
    series: prios.map((p) => ({
      type: "bar" as const, name: p, stack: "a", barWidth: "60%",
      itemStyle: { color: prioColor[p] },
      data: months.map((m) => d.alerts_monthly.find((a) => a.month === m && a.priority === p)?.n ?? 0),
    })),
  };
  const kri01 = d.kris.find((k) => k.kri_id === "KRI-01")!;
  const kri04 = d.kris.find((k) => k.kri_id === "KRI-04")!;
  const cmp = Object.entries(d.ml.comparison_test ?? {});
  return (
    <>
      <PageHeader
        title="Overview"
        subtitle={<>Key Risk Indicators for the card transactions data product. Run <span className="num font-medium text-text">{d.run.run_id}</span>, batch as of {d.batch_as_of}, report month {d.report_month}.</>}
      />

      {breaches.length > 0 && (
        <div role="status" className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-red-soft px-4 py-3 text-sm text-red">
          <span>
            <span className="font-semibold">{breaches.length} KRI {breaches.length === 1 ? "breach" : "breaches"}:</span>{" "}
            {breaches.map((k) => `${k.kri_id} ${k.name} (${fmtValue(k.value, k.unit)}, ${k.rag})`).join("; ")}.
            {" "}Escalated to the issue log.
          </span>
          <Link to="/issues" className="inline-flex items-center gap-1 font-medium underline-offset-2 hover:underline">
            View issues <ArrowRight className="h-3.5 w-3.5" aria-hidden />
          </Link>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        {d.kris.map((k) => {
          const tr = trendOf(k.kri_id);
          return (
            <button
              key={k.kri_id}
              onClick={() => setOpen(k)}
              className="flex flex-col rounded-lg border border-border bg-surface p-4 text-left transition-colors duration-150 hover:border-border-strong"
              aria-label={`${k.kri_id} ${k.name}: ${fmtValue(k.value, k.unit)}, ${k.rag}. Open details`}
            >
              <div className="flex w-full items-start justify-between gap-2">
                <span className="text-xs font-medium text-muted">{k.kri_id} · {k.category}</span>
                <RagBadge rag={k.rag} />
              </div>
              <div className="mt-1 text-sm font-medium text-text">{k.name}</div>
              <div className="num mt-2 text-2xl font-semibold text-text">{fmtValue(k.value, k.unit)}</div>
              <div className="mt-1 text-xs text-muted">{threshText(k)}</div>
              <div className="mt-auto w-full pt-3">
                <div className="flex h-6 items-center border-t border-border pt-2">
                  {tr.length > 1
                    ? <Spark data={tr.map((p) => p.value)} color={t.series[1]} />
                    : <span className="text-xs font-medium text-text">{deltaNote(k, d.kri_previous.find((p) => p.kri_id === k.kri_id))}</span>}
                </div>
                <div className="num mt-2 text-xs text-muted">
                  {tr.length > 1 ? `Monthly, ${tr[0].period} to ${tr[tr.length - 1].period}` : "Measured per pipeline run"}
                </div>
              </div>
            </button>
          );
        })}
      </div>

      <div className="mt-6 grid gap-4 xl:grid-cols-2">
        <Card title="KRI-01 CDE data quality pass rate by month" actions={<span className="text-xs text-muted">{threshText(kri01)}</span>}>
          <TrendChart k={kri01} trend={trendOf("KRI-01")} />
        </Card>
        <Card title="KRI-04 Quarantine rate by month" actions={<span className="text-xs text-muted">{threshText(kri04)}</span>}>
          <TrendChart k={kri04} trend={trendOf("KRI-04")} />
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[3fr_2fr]">
        <Card title="Monitoring alerts by month and priority" actions={<Link to="/monitoring" className="text-xs font-medium text-accent hover:underline">Open alert queue</Link>}>
          <Chart option={alertsOpt} height={260} label="Stacked bar chart of alerts per month by priority" />
        </Card>
        <Card title="Data flow, latest run">
          <KeyValue items={[
            ["Source records", fmtInt(d.data.source_rows)],
            ["Tagged synthetic records", fmtInt(d.data.synthetic_rows)],
            ["Bronze (landed)", fmtInt(d.data.bronze_rows)],
            ["Quarantined", fmtInt(d.data.quarantined_rows)],
            ["Gold fact rows", fmtInt(d.run.gold_fact_rows)],
            ["Customers / merchants", `${fmtInt(d.data.gold_customers)} / ${fmtInt(d.data.gold_merchants)}`],
            ["Alerts raised", fmtInt(d.monitoring.alerts)],
            ["Open issues", fmtInt(d.issues.open)],
            ["Run finished", `${fmtTs(d.run.ended_at)} (${fmtNum(d.run.duration_seconds, 0)} s)`],
          ]} />
        </Card>
      </div>

      <Card className="mt-4" title="Detection approaches on the test period (Jul to Dec 2020)" bodyClass="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-surface-2 text-xs text-muted">
              <tr>
                {["Approach", "Alerts", "Precision", "Recall", "PR-AUC", "Fraud $ missed"].map((h) => (
                  <th key={h} scope="col" className="px-4 py-2 text-left font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {cmp.map(([name, c]) => (
                <tr key={name} className="border-t border-border">
                  <td className="px-4 py-2 font-medium">{name}</td>
                  <td className="num px-4 py-2">{fmtInt(c.alerts)}</td>
                  <td className="num px-4 py-2">{fmtPct(c.precision * 100)}</td>
                  <td className="num px-4 py-2">{fmtPct(c.recall * 100)}</td>
                  <td className="num px-4 py-2">{c.pr_auc != null ? fmtNum(c.pr_auc, 3) : "–"}</td>
                  <td className="num px-4 py-2">{fmtUsd(c.fraud_amount_missed)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="border-t border-border px-4 py-2 text-xs text-muted">
          LightGBM threshold {d.ml.threshold?.chosen_by_cost_on_validation} chosen by cost on June 2020 validation data
          (false positive ${d.ml.threshold?.cost_false_positive_usd}, missed fraud = its amount), within a capacity of {d.ml.threshold?.analyst_capacity_alerts_per_day} alerts per day.
          Public simulated data; real-world scores would be lower.
        </p>
      </Card>

      <SlideOver open={!!open} onClose={() => setOpen(null)} title={open ? `${open.kri_id} ${open.name}` : ""} subtitle={open?.category}>
        {open && (
          <div className="space-y-6">
            <div className="flex items-center gap-3">
              <span className="num text-3xl font-semibold">{fmtValue(open.value, open.unit)}</span>
              <RagBadge rag={open.rag} />
            </div>
            <KeyValue items={[
              ["Period", open.period], ["Formula", open.formula], ["Thresholds", threshText(open)],
              ["Why it matters", open.rationale], ["Escalation", open.escalation], ["Owner", open.owner],
            ]} />
            {trendOf(open.kri_id).length > 1 && (
              <div>
                <h3 className="mb-2 text-xs font-medium text-muted">Monthly trend</h3>
                <TrendChart k={open} trend={trendOf(open.kri_id)} />
              </div>
            )}
          </div>
        )}
      </SlideOver>
    </>
  );
}
