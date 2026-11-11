import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import * as Dialog from "@radix-ui/react-dialog";
import { Check, Search, X } from "lucide-react";
import { api, hasRole, useSession } from "../lib/session";
import { fmtInt, fmtNum, fmtPct, fmtTs, fmtUsd } from "../lib/format";
import { axis, baseOption, Chart, useChartTheme } from "../components/Chart";
import { DataTable } from "../components/DataTable";
import {
  Badge, Button, Card, EmptyState, ErrorState, Input, KeyValue, LoadingBlock, PageHeader, RagBadge, Select, SeverityBadge,
  Stat, Tabs,
} from "../components/ui";

type DimRow = { dimension: string; rules: number; rules_passed: number; records_checked: number; records_failed: number; pass_rate_pct: number };
type RuleRow = {
  rule_id: string; rule_name: string; table_name: string; column_name: string | null; dimension: string; check_type: string;
  severity: string; is_cde: boolean; action: string; owner: string; source: string; threshold_pct: number;
  records_checked: number; records_failed: number; failure_pct: number; pass_rate_pct: number; status: string;
  synthetic_failed: number; detail: string | null;
};
type CdeRow = { table_name: string; cde: string; rules: number; rules_passed: number; records_checked: number; records_failed: number; pass_rate_pct: number };
type Scorecard = {
  run_id: string; by_dimension: DimRow[]; by_table: (DimRow & { table_name: string })[]; by_cde: CdeRow[]; rules: RuleRow[];
  findings: { finding: string; value: string; detail: string }[];
};
type CatalogRow = {
  layer: string; table_name: string; column_name: string; data_type: string; description: string; business_owner: string;
  data_steward: string; source: string; sensitivity: string; pii_type: string; is_cde: boolean; cde_reason: string;
  dq_rules: string; curated: boolean;
};
type Edge = { source: string; target: string; step: string; transformation: string };
type Recon = {
  control_id: string; control_name: string; from_layer: string; to_layer: string; metric: string; expected: number;
  actual: number; difference: number; tolerance: number; status: string;
};
type Suggestion = {
  suggestion_id: string; table: string; column: string; check: string; params: Record<string, unknown>; dimension: string;
  evidence: string; confidence: string; would_fail_records: number; would_fail_pct: number; status: string;
  decided_by: string | null; decision_reason: string | null; decided_at?: string;
};

// ------------------------------------------------------------------ scorecard

function ScorecardTab() {
  const t = useChartTheme();
  const q = useQuery({ queryKey: ["scorecard"], queryFn: () => api<Scorecard>("/governance/scorecard") });
  const [filter, setFilter] = useState("");
  const [status, setStatus] = useState("all");
  if (q.isLoading) return <LoadingBlock rows={8} />;
  if (q.error) return <ErrorState error={q.error} onRetry={q.refetch} />;
  const d = q.data!;
  const rules = d.rules.filter((r) => status === "all" || r.status === status);
  const dims = [...d.by_dimension].sort((a, b) => b.pass_rate_pct - a.pass_rate_pct);
  const option = {
    ...baseOption(t),
    legend: undefined,
    tooltip: { ...baseOption(t).tooltip, valueFormatter: (v: unknown) => `${fmtNum(v as number, 4)}%` },
    grid: { left: 8, right: 64, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: "value" as const, min: Math.floor(Math.min(...dims.map((x) => x.pass_rate_pct)) / 5) * 5, max: 100, ...axis(t), axisLabel: { color: t.muted, formatter: "{value}%" } },
    yAxis: { type: "category" as const, data: dims.map((x) => x.dimension), ...axis(t), splitLine: { show: false } },
    series: [{
      type: "bar" as const, data: dims.map((x) => x.pass_rate_pct), barWidth: 14, itemStyle: { color: t.series[0], borderRadius: 2 },
      label: { show: true, position: "right" as const, color: t.text, formatter: (p: { value: unknown }) => `${fmtNum(p.value as number, 3)}%` },
    }],
  };
  const columns: ColumnDef<RuleRow, any>[] = [
    { accessorKey: "rule_id", header: "Rule", cell: (c) => <span className="font-medium whitespace-nowrap">{c.getValue()}</span> },
    { accessorKey: "rule_name", header: "Description", cell: (c) => <span className="block max-w-[320px]">{c.getValue()}</span> },
    { accessorKey: "dimension", header: "Dimension" },
    { accessorKey: "table_name", header: "Table" },
    { accessorKey: "severity", header: "Severity", cell: (c) => <SeverityBadge s={c.getValue()} /> },
    { accessorKey: "action", header: "Action", cell: (c) => <Badge tone={c.getValue() === "quarantine" ? "accent" : "neutral"}>{c.getValue()}</Badge> },
    { accessorKey: "records_failed", header: "Failed", cell: (c) => <span className="num">{fmtInt(c.getValue())}</span> },
    { accessorKey: "failure_pct", header: "Fail %", cell: (c) => <span className="num">{fmtPct(c.getValue(), 4)}</span> },
    { accessorKey: "threshold_pct", header: "Tolerance", cell: (c) => <span className="num">{fmtPct(c.getValue(), 2)}</span> },
    { accessorKey: "status", header: "Status", cell: (c) => <RagBadge rag={c.getValue()} /> },
  ];
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Pass rate by DQ dimension" actions={<span className="text-xs text-muted">Records passing / records checked</span>}>
          <Chart option={option} height={240} label="Bar chart of data quality pass rate by dimension" />
        </Card>
        <Card title="Data discovery findings" bodyClass="p-0">
          <ul className="divide-y divide-border">
            {d.findings.slice(0, 5).map((f) => (
              <li key={f.finding} className="px-4 py-2.5">
                <div className="text-sm font-medium text-text">{f.finding}</div>
                <div className="text-xs text-muted">{f.detail}</div>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <Card
        title={`Rule results (${d.rules.length} rules, run ${d.run_id})`}
        actions={
          <>
            <Select label="Filter by status" value={status} onChange={setStatus} options={[
              { value: "all", label: "All statuses" }, { value: "FAIL", label: "Failing" }, { value: "PASS", label: "Passing" },
            ]} />
            <Input placeholder="Filter rules" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter rules" className="w-48" />
          </>
        }
      >
        <DataTable data={rules} columns={columns} filter={filter} pageSize={12} dense />
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ catalog + CDEs

function useCatalog() {
  return useQuery({ queryKey: ["catalog"], queryFn: () => api<CatalogRow[]>("/governance/catalog") });
}

function CatalogTab() {
  const q = useCatalog();
  const [filter, setFilter] = useState("");
  const [layer, setLayer] = useState("all");
  const [sel, setSel] = useState<CatalogRow | null>(null);
  const data = useMemo(() => (q.data ?? []).filter((r) => layer === "all" || r.layer === layer), [q.data, layer]);
  if (q.isLoading) return <LoadingBlock rows={8} />;
  if (q.error) return <ErrorState error={q.error} onRetry={q.refetch} />;
  const columns: ColumnDef<CatalogRow, any>[] = [
    { accessorKey: "table_name", header: "Table", cell: (c) => <span className="whitespace-nowrap text-muted">{c.getValue()}</span> },
    { accessorKey: "column_name", header: "Column", cell: (c) => <span className="font-medium">{c.getValue()}</span> },
    { accessorKey: "data_type", header: "Type", cell: (c) => <span className="text-xs text-muted">{c.getValue()}</span> },
    { accessorKey: "description", header: "Business definition", cell: (c) => <span className="block max-w-[380px]">{c.getValue()}</span> },
    { accessorKey: "sensitivity", header: "Classification", cell: (c) => <Badge tone={c.getValue() === "Restricted" ? "red" : c.getValue() === "Confidential" ? "amber" : "neutral"}>{c.getValue()}</Badge> },
    { accessorKey: "is_cde", header: "CDE", cell: (c) => (c.getValue() ? <Badge tone="accent">CDE</Badge> : null) },
    { accessorKey: "data_steward", header: "Steward", cell: (c) => <span className="text-xs">{c.getValue()}</span> },
  ];
  const layers = [...new Set((q.data ?? []).map((r) => r.layer))];
  return (
    <Card
      title={`Data catalog (${q.data!.length} columns)`}
      actions={
        <>
          <Select label="Filter by layer" value={layer} onChange={setLayer} options={[{ value: "all", label: "All layers" }, ...layers.map((l) => ({ value: l, label: l }))]} />
          <div className="relative">
            <Search className="pointer-events-none absolute top-2.5 left-2.5 h-4 w-4 text-muted" aria-hidden />
            <Input placeholder="Search columns, definitions, owners" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Search catalog" className="w-72 pl-8" />
          </div>
        </>
      }
    >
      <DataTable data={data} columns={columns} filter={filter} pageSize={12} onRowClick={setSel} dense />
      <Dialog.Root open={!!sel} onOpenChange={(o) => !o && setSel(null)}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-40 bg-black/40" />
          <Dialog.Content className="fixed top-1/2 left-1/2 z-50 w-[min(640px,92vw)] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-surface p-6" aria-describedby={undefined}>
            {sel && (
              <>
                <Dialog.Title className="mb-4 text-base font-semibold">{sel.table_name}.{sel.column_name}</Dialog.Title>
                <KeyValue items={[
                  ["Definition", sel.description], ["Data type", sel.data_type], ["Layer", sel.layer],
                  ["Business owner", sel.business_owner], ["Data steward", sel.data_steward], ["Source", sel.source],
                  ["Classification", sel.sensitivity], ["PII type", sel.pii_type || "None"],
                  ["Critical Data Element", sel.is_cde ? `Yes. ${sel.cde_reason}` : "No"], ["DQ rules", sel.dq_rules || "None"],
                ]} />
                <div className="mt-6 flex justify-end"><Dialog.Close asChild><Button>Close</Button></Dialog.Close></div>
              </>
            )}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </Card>
  );
}

function CdeTab() {
  const cat = useCatalog();
  const sc = useQuery({ queryKey: ["scorecard"], queryFn: () => api<Scorecard>("/governance/scorecard") });
  if (cat.isLoading || sc.isLoading) return <LoadingBlock rows={8} />;
  if (cat.error || sc.error) return <ErrorState error={cat.error ?? sc.error} />;
  const cdes = cat.data!.filter((r) => r.is_cde);
  const scoreCols: ColumnDef<CdeRow, any>[] = [
    { accessorKey: "cde", header: "CDE", cell: (c) => <span className="font-medium">{c.getValue()}</span> },
    { accessorKey: "table_name", header: "Table" },
    { accessorKey: "rules", header: "Rules", cell: (c) => <span className="num">{c.getValue()}</span> },
    { accessorKey: "records_failed", header: "Failed records", cell: (c) => <span className="num">{fmtInt(c.getValue())}</span> },
    { accessorKey: "pass_rate_pct", header: "Pass rate", cell: (c) => <span className="num">{fmtPct(c.getValue(), 4)}</span> },
    { id: "rag", header: "Status", cell: (c) => <RagBadge rag={c.row.original.pass_rate_pct >= 99.9 ? "Green" : c.row.original.pass_rate_pct >= 99 ? "Amber" : "Red"} /> },
  ];
  const regCols: ColumnDef<CatalogRow, any>[] = [
    { accessorKey: "table_name", header: "Table", cell: (c) => <span className="text-muted">{c.getValue()}</span> },
    { accessorKey: "column_name", header: "Column", cell: (c) => <span className="font-medium">{c.getValue()}</span> },
    { accessorKey: "cde_reason", header: "Why it is critical", cell: (c) => <span className="block max-w-[420px]">{c.getValue()}</span> },
    { accessorKey: "business_owner", header: "Owner", cell: (c) => <span className="text-xs">{c.getValue()}</span> },
    { accessorKey: "dq_rules", header: "DQ rules", cell: (c) => <span className="text-xs text-muted">{c.getValue()}</span> },
  ];
  return (
    <div className="space-y-4">
      <Card title="CDE quality scorecard" actions={<span className="text-xs text-muted">Green at 99.9% or above, Amber at 99% or above</span>}>
        <DataTable data={sc.data!.by_cde} columns={scoreCols} pageSize={15} dense />
      </Card>
      <Card title={`Critical Data Element register (${cdes.length})`}>
        <DataTable data={cdes} columns={regCols} pageSize={10} dense />
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ lineage

const LAYERS = ["source", "bronze", "silver", "gold", "monitoring", "governance"];

function layerOf(name: string) {
  const s = name.split(".")[0];
  return LAYERS.includes(s) ? s : "source";
}

function shortName(name: string) {
  return layerOf(name) === "source" ? name.replace(/^source: /, "") : name.split(".").slice(1).join(".");
}

function LineageTab() {
  const t = useChartTheme();
  const q = useQuery({ queryKey: ["lineage"], queryFn: () => api<Edge[]>("/governance/lineage") });
  const [sel, setSel] = useState<string | null>("gold.fact_transactions");
  const graph = useMemo(() => {
    const edges = q.data ?? [];
    const names = [...new Set(edges.flatMap((e) => [e.source, e.target]))];
    // One column per layer, in the order data flows through the platform.
    const cols: Record<number, string[]> = {};
    names.forEach((n) => (cols[LAYERS.indexOf(layerOf(n))] ??= []).push(n));
    const pos: Record<string, [number, number]> = {};
    Object.entries(cols).forEach(([d, ns]) => ns.forEach((n, i) => (pos[n] = [Number(d) * 210, i * 58 - (ns.length - 1) * 29])));
    const walk = (start: string, dir: "up" | "down") => {
      const seen = new Set<string>();
      const stack = [start];
      while (stack.length) {
        const n = stack.pop()!;
        for (const e of edges) {
          const next = dir === "down" ? (e.source === n ? e.target : null) : (e.target === n ? e.source : null);
          if (next && !seen.has(next)) { seen.add(next); stack.push(next); }
        }
      }
      return seen;
    };
    return { edges, names, pos, up: sel ? walk(sel, "up") : new Set<string>(), down: sel ? walk(sel, "down") : new Set<string>() };
  }, [q.data, sel]);
  if (q.isLoading) return <LoadingBlock rows={8} />;
  if (q.error) return <ErrorState error={q.error} onRetry={q.refetch} />;
  if (!graph.edges.length) return <Card><EmptyState title="No lineage recorded" hint="Run the pipeline to capture lineage." /></Card>;
  const related = (n: string) => !sel || n === sel || graph.up.has(n) || graph.down.has(n);
  const option = {
    tooltip: { show: true, trigger: "item" as const, backgroundColor: t.surface, borderColor: t.border, textStyle: { color: t.text, fontSize: 12 },
      formatter: (p: any) =>
        p.dataType === "node" ? p.name ?? "" : `${p.data?.source} to ${p.data?.target}` },
    series: [{
      type: "graph" as const, layout: "none" as const, roam: true, zoom: 0.95,
      edgeSymbol: ["none", "arrow"], edgeSymbolSize: 7,
      data: graph.names.map((n) => ({
        name: n, x: graph.pos[n][0], y: graph.pos[n][1], symbol: "roundRect", symbolSize: [172, 38],
        itemStyle: {
          color: n === sel ? t.accent : t.surface,
          borderColor: n === sel ? t.accent : layerOf(n) === "gold" ? t.series[0] : t.border,
          borderWidth: 1.5, opacity: related(n) ? 1 : 0.3,
        },
        label: {
          show: true, opacity: related(n) ? 1 : 0.4, overflow: "truncate" as const, width: 160,
          formatter: `{l|${layerOf(n).toUpperCase()}}\n{n|${shortName(n)}}`,
          rich: {
            l: { fontSize: 9, color: n === sel ? t.surface : t.muted, lineHeight: 12 },
            n: { fontSize: 11, color: n === sel ? t.surface : t.text, lineHeight: 14 },
          },
        },
      })),
      links: graph.edges.map((e) => ({
        source: e.source, target: e.target,
        lineStyle: { color: related(e.source) && related(e.target) && sel ? t.series[0] : t.border, width: 1.2, curveness: 0.15, opacity: related(e.source) && related(e.target) ? 0.9 : 0.25 },
      })),
    }],
  };
  const inbound = graph.edges.filter((e) => e.target === sel);
  const outbound = graph.edges.filter((e) => e.source === sel);
  return (
    <div className="space-y-4">
      <Card title="Data lineage" actions={<span className="text-xs text-muted">Click a dataset to trace upstream and downstream. Drag or scroll to zoom.</span>} bodyClass="overflow-x-auto p-2">
        <div className="min-w-[1080px]">
          <Chart option={option} height={500} label="Lineage graph from source files to governance and monitoring outputs"
            onEvents={{ click: (p) => { const d = p as { dataType?: string; name?: string }; if (d.dataType === "node" && d.name) setSel(d.name === sel ? null : d.name); } }} />
        </div>
      </Card>
      <Card title={sel ?? "Select a dataset"}>
        {sel ? (
          <div className="grid gap-6 text-sm lg:grid-cols-3">
            <KeyValue items={[["Layer", layerOf(sel)], ["Upstream datasets", graph.up.size], ["Downstream datasets", graph.down.size]]} />
            <div>
              <h3 className="mb-2 text-xs font-medium text-muted">Fed by</h3>
              {inbound.length ? inbound.map((e) => (
                <div key={e.source} className="mb-2 rounded-md border border-border p-2">
                  <div className="font-medium">{e.source}</div>
                  <div className="text-xs text-muted">{e.step}: {e.transformation}</div>
                </div>
              )) : <p className="text-xs text-muted">Origin dataset</p>}
            </div>
            <div>
              <h3 className="mb-2 text-xs font-medium text-muted">Feeds (impact if this dataset breaks)</h3>
              {outbound.length ? outbound.map((e) => (
                <div key={e.target} className="mb-2 rounded-md border border-border p-2">
                  <div className="font-medium">{e.target}</div>
                  <div className="text-xs text-muted">{e.step}: {e.transformation}</div>
                </div>
              )) : <p className="text-xs text-muted">End of the flow</p>}
            </div>
          </div>
        ) : <p className="text-sm text-muted">Click a node in the graph.</p>}
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ reconciliation

function ReconTab() {
  const q = useQuery({ queryKey: ["recon"], queryFn: () => api<Recon[]>("/governance/reconciliation") });
  if (q.isLoading) return <LoadingBlock rows={8} />;
  if (q.error) return <ErrorState error={q.error} onRetry={q.refetch} />;
  const money = (r: Recon, v: number) => (r.metric.toLowerCase().includes("amount") ? fmtUsd(v) : fmtInt(v));
  const columns: ColumnDef<Recon, any>[] = [
    { accessorKey: "control_id", header: "Control", cell: (c) => <span className="font-medium whitespace-nowrap">{c.getValue()}</span> },
    { accessorKey: "control_name", header: "Check", cell: (c) => <span className="block max-w-[300px]">{c.getValue()}</span> },
    { id: "flow", header: "From / to", cell: (c) => <span className="text-xs whitespace-nowrap text-muted">{c.row.original.from_layer} to {c.row.original.to_layer}</span> },
    { accessorKey: "metric", header: "Metric" },
    { accessorKey: "expected", header: "Expected", cell: (c) => <span className="num">{money(c.row.original, c.getValue())}</span> },
    { accessorKey: "actual", header: "Actual", cell: (c) => <span className="num">{money(c.row.original, c.getValue())}</span> },
    { accessorKey: "difference", header: "Difference", cell: (c) => <span className="num">{money(c.row.original, c.getValue())}</span> },
    { accessorKey: "tolerance", header: "Tolerance", cell: (c) => <span className="num">{fmtNum(c.getValue(), 2)}</span> },
    { accessorKey: "status", header: "Result", cell: (c) => <RagBadge rag={c.getValue()} /> },
  ];
  const passed = q.data!.filter((r) => r.status === "PASS").length;
  return (
    <Card title={`Reconciliation controls: ${passed} of ${q.data!.length} passed`} actions={<span className="text-xs text-muted">Every record and dollar is accounted for between layers</span>}>
      <DataTable data={q.data!} columns={columns} pageSize={10} />
    </Card>
  );
}

// ------------------------------------------------------------------ rule suggestions (human in the loop)

function SuggestionsTab() {
  const { user } = useSession();
  const qc = useQueryClient();
  const isSteward = hasRole(user, "steward");
  const q = useQuery({ queryKey: ["suggestions"], queryFn: () => api<Suggestion[]>("/governance/suggestions") });
  const [pending, setPending] = useState<{ s: Suggestion; decision: "approved" | "rejected" } | null>(null);
  const [reason, setReason] = useState("");
  const [filter, setFilter] = useState("pending");
  const m = useMutation({
    mutationFn: (v: { id: string; decision: string; reason: string }) =>
      api(`/governance/suggestions/${v.id}/decision`, { method: "POST", body: JSON.stringify({ decision: v.decision, reason: v.reason }) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["suggestions"] });
      qc.invalidateQueries({ queryKey: ["audit"] });
      setPending(null);
      setReason("");
    },
  });
  if (q.isLoading) return <LoadingBlock rows={8} />;
  if (q.error) return <ErrorState error={q.error} onRetry={q.refetch} />;
  const data = q.data!.filter((s) => filter === "all" || s.status === filter);
  const counts = q.data!.reduce<Record<string, number>>((a, s) => ({ ...a, [s.status]: (a[s.status] ?? 0) + 1 }), {});
  const columns: ColumnDef<Suggestion, any>[] = [
    { accessorKey: "suggestion_id", header: "ID", cell: (c) => <span className="font-medium whitespace-nowrap">{c.getValue()}</span> },
    { id: "rule", header: "Proposed rule", cell: (c) => (
      <span className="whitespace-nowrap">{c.row.original.column} <span className="text-muted">{c.row.original.check}</span>
        {Object.keys(c.row.original.params ?? {}).length > 0 && <span className="ml-1 block max-w-[220px] truncate text-xs text-muted">{JSON.stringify(c.row.original.params)}</span>}
      </span>) },
    { accessorKey: "dimension", header: "Dimension" },
    { accessorKey: "evidence", header: "Evidence from profiling", cell: (c) => <span className="block max-w-[300px] text-xs">{c.getValue()}</span> },
    { accessorKey: "would_fail_records", header: "Impact preview", cell: (c) => (
      <span className="num whitespace-nowrap">{fmtInt(c.getValue())} <span className="text-xs text-muted">({fmtPct(c.row.original.would_fail_pct, 4)})</span></span>) },
    { accessorKey: "status", header: "Status", cell: (c) => {
      const s = c.row.original;
      return (
        <div>
          <Badge tone={s.status === "approved" ? "green" : s.status === "rejected" ? "red" : "neutral"} className="capitalize">{s.status}</Badge>
          {s.decided_by && <div className="mt-1 text-xs whitespace-nowrap text-muted">{s.decided_by}{s.decided_at ? `, ${fmtTs(s.decided_at).slice(0, 16)}` : ""}</div>}
        </div>);
    } },
    { id: "actions", header: "Decision", cell: (c) => {
      const s = c.row.original;
      if (s.status !== "pending") return <span className="block max-w-[200px] text-xs text-muted">{s.decision_reason}</span>;
      const title = isSteward ? undefined : "Requires the Data Steward role";
      return (
        <div className="flex gap-2" title={title}>
          <Button size="sm" disabled={!isSteward} onClick={() => setPending({ s, decision: "approved" })}><Check className="h-3.5 w-3.5" aria-hidden />Approve</Button>
          <Button size="sm" variant="danger" disabled={!isSteward} onClick={() => setPending({ s, decision: "rejected" })}><X className="h-3.5 w-3.5" aria-hidden />Reject</Button>
        </div>);
    } },
  ];
  return (
    <div className="space-y-4">
      <Card bodyClass="py-3">
        <p className="text-sm text-muted">
          Profiling proposes rules automatically; a data steward decides. Each decision records who, when and why in
          <code className="mx-1 rounded bg-surface-2 px-1 text-xs">config/steward_decisions.yaml</code>
          and the audit trail. Approved rules run from the next pipeline run.
          {!isSteward && <span className="ml-1 font-medium text-text">You are acting as {user?.name} ({user?.title}); switch to the Data Steward to decide.</span>}
        </p>
      </Card>
      <Card
        title={`Rule suggestions: ${counts.pending ?? 0} pending, ${counts.approved ?? 0} approved, ${counts.rejected ?? 0} rejected`}
        actions={<Select label="Filter suggestions" value={filter} onChange={setFilter} options={[
          { value: "pending", label: "Pending" }, { value: "approved", label: "Approved" }, { value: "rejected", label: "Rejected" }, { value: "all", label: "All" },
        ]} />}
      >
        <DataTable data={data} columns={columns} pageSize={10} empty="Nothing in this queue" />
      </Card>
      <Dialog.Root open={!!pending} onOpenChange={(o) => { if (!o) { setPending(null); m.reset(); } }}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-40 bg-black/40" />
          <Dialog.Content className="fixed top-1/2 left-1/2 z-50 w-[min(520px,92vw)] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-surface p-6" aria-describedby={undefined}>
            {pending && (
              <form onSubmit={(e) => { e.preventDefault(); m.mutate({ id: pending.s.suggestion_id, decision: pending.decision, reason }); }}>
                <Dialog.Title className="text-base font-semibold">{pending.decision === "approved" ? "Approve" : "Reject"} {pending.s.suggestion_id}</Dialog.Title>
                <p className="mt-1 text-sm text-muted">{pending.s.column} {pending.s.check}: would fail {fmtInt(pending.s.would_fail_records)} records today.</p>
                <label className="mt-4 block text-xs font-medium text-muted" htmlFor="reason">Reason (required, recorded in the audit trail)</label>
                <textarea id="reason" value={reason} onChange={(e) => setReason(e.target.value)} rows={3} required
                  className="mt-1 w-full rounded-md border border-border bg-surface p-2 text-sm text-text focus:border-accent focus:outline-none" />
                {m.error && <p role="alert" className="mt-2 text-sm text-red">{(m.error as Error).message}</p>}
                <div className="mt-4 flex justify-end gap-2">
                  <Dialog.Close asChild><Button type="button">Cancel</Button></Dialog.Close>
                  <Button type="submit" variant="primary" disabled={!reason.trim() || m.isPending}>Record decision</Button>
                </div>
              </form>
            )}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

// ------------------------------------------------------------------ page

type OverviewLite = {
  dq: { cde_pass_rate_pct: number; rules_total: number; rules_passed: number; injected_defect_detection_pct: number };
  reconciliation: { controls: number; breaks: number }; contracts: { clauses: number; clauses_passed: number; products: number };
  catalog: { columns: number; curated_pct: number; cdes: number }; data: { quarantined_rows: number; quarantine_pct: number };
};

export default function Governance() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "scorecard";
  const ov = useQuery({ queryKey: ["overview"], queryFn: () => api<OverviewLite>("/overview") });
  const o = ov.data;
  return (
    <>
      <PageHeader title="Data Governance" subtitle="Data quality, metadata, lineage and controls for the card transactions data product, from the latest governed pipeline run." />
      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-5">
        <Stat label="CDE pass rate" value={o ? fmtPct(o.dq.cde_pass_rate_pct, 4) : "–"} sub="Critical Data Elements" />
        <Stat label="DQ rules passing" value={o ? `${o.dq.rules_passed} / ${o.dq.rules_total}` : "–"} sub={o ? `${fmtPct(o.dq.injected_defect_detection_pct, 0)} of injected defects caught` : undefined} />
        <Stat label="Quarantined records" value={o ? fmtInt(o.data.quarantined_rows) : "–"} sub={o ? `${fmtPct(o.data.quarantine_pct, 4)} of bronze` : undefined} />
        <Stat label="Reconciliation" value={o ? `${o.reconciliation.controls - o.reconciliation.breaks} / ${o.reconciliation.controls}` : "–"} sub="controls passed" />
        <Stat label="Catalog" value={o ? `${o.catalog.columns} columns` : "–"} sub={o ? `${fmtPct(o.catalog.curated_pct, 1)} curated, ${o.catalog.cdes} CDEs` : undefined} />
      </div>
      <Tabs value={tab} onChange={(v) => setParams({ tab: v }, { replace: true })} tabs={[
        { id: "scorecard", label: "DQ scorecard", content: <ScorecardTab /> },
        { id: "catalog", label: "Catalog", content: <CatalogTab /> },
        { id: "cde", label: "Critical Data Elements", content: <CdeTab /> },
        { id: "lineage", label: "Lineage", content: <LineageTab /> },
        { id: "reconciliation", label: "Reconciliation", content: <ReconTab /> },
        { id: "suggestions", label: "Rule suggestions", content: <SuggestionsTab /> },
      ]} />
    </>
  );
}
