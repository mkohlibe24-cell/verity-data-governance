import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
import clsx from "clsx";
import { ChevronLeft, ChevronRight, Search, ShieldAlert } from "lucide-react";
import { api, hasRole, useSession } from "../lib/session";
import { fmtInt, fmtNum, fmtPct, fmtTs, fmtUsd } from "../lib/format";
import { axis, baseOption, Chart, useChartTheme } from "../components/Chart";
import { DataTable } from "../components/DataTable";
import {
  Badge, Button, Card, EmptyState, ErrorState, Input, KeyValue, LoadingBlock, PageHeader, PriorityBadge, Select, SlideOver,
  StatusBadge, Tabs,
} from "../components/ui";

type AlertRow = {
  alert_id: string; txn_ts: string; amount: number; category: string; priority: string; combined_score: number;
  rule_score: number | null; ml_score: number | null; rules_fired: string[] | null; detection_source: string;
  is_synthetic: boolean; card_token: string; status: string;
};
type AlertPage = {
  total: number; page: number; page_size: number; items: AlertRow[];
  priority_counts: { priority: string; n: number }[]; status_counts: Record<string, number>;
};
type Case = {
  alert_id: string; status: string; disposition?: string; maker_id?: string; maker_name?: string; maker_note?: string;
  proposed_at?: string; checker_name?: string; checker_note?: string; decided_at?: string;
};
type AuditEvent = {
  event_id: number; ts: string; actor_name: string; actor_roles: string; entity_type: string; entity_id: string;
  action: string; from_status: string | null; to_status: string | null; detail: Record<string, string>;
};
type Detail = {
  alert: AlertRow & { trans_num: string; ml_reasons: string | null; rule_reasons: string[] | null; is_fraud: number; synthetic_tag: string | null; merchant_key: string };
  transaction: {
    merchant_name: string; card_number_masked: string; age_band: string; gender: string; city: string; state: string; zip3: string;
    distance_from_home_km: number; dq_flag_count: number; source_system: string; card_txn_count: number;
  } | null;
  recent: { txn_ts: string; amount: number; category: string; merchant_name: string; distance_from_home_km: number; is_alerted: boolean }[];
  rule_hits: { rule_id: string; name: string; reason: string; severity: string; base_score: number }[];
  case: Case; audit: AuditEvent[]; dispositions: string[];
};

function useDebounced<T>(v: T, ms = 300) {
  const [d, setD] = useState(v);
  useEffect(() => {
    const id = setTimeout(() => setD(v), ms);
    return () => clearTimeout(id);
  }, [v, ms]);
  return d;
}

// ------------------------------------------------------------------ alert queue

function AlertQueue({ onOpen }: { onOpen: (id: string) => void }) {
  const [priority, setPriority] = useState("");
  const [status, setStatus] = useState("");
  const [source, setSource] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const q = useDebounced(search);
  useEffect(() => setPage(0), [priority, status, source, q]);
  const params = new URLSearchParams({ page: String(page), page_size: "20" });
  if (priority) params.set("priority", priority);
  if (status) params.set("status", status);
  if (source) params.set("source", source);
  if (q) params.set("q", q);
  const res = useQuery({
    queryKey: ["alerts", params.toString()],
    queryFn: () => api<AlertPage>(`/alerts?${params}`),
    placeholderData: keepPreviousData,
  });
  const columns = useMemo<ColumnDef<AlertRow, any>[]>(() => [
    { accessorKey: "alert_id", header: "Alert", cell: (c) => <span className="font-medium whitespace-nowrap">{c.getValue()}</span> },
    { accessorKey: "priority", header: "Priority", cell: (c) => <PriorityBadge p={c.getValue()} /> },
    { accessorKey: "combined_score", header: "Score", cell: (c) => <span className="num">{fmtNum(c.getValue(), 0)}</span> },
    { accessorKey: "txn_ts", header: "Transaction time", cell: (c) => <span className="num whitespace-nowrap">{fmtTs(c.getValue())}</span> },
    { accessorKey: "amount", header: "Amount", cell: (c) => <span className="num">{fmtUsd(c.getValue())}</span> },
    { accessorKey: "category", header: "Category" },
    { accessorKey: "rules_fired", header: "Rules", cell: (c) => <span className="text-xs whitespace-nowrap">{(c.getValue() ?? []).join(", ") || "–"}</span> },
    { accessorKey: "ml_score", header: "ML score", cell: (c) => <span className="num">{c.getValue() == null ? "–" : fmtNum(c.getValue(), 3)}</span> },
    { accessorKey: "detection_source", header: "Source", cell: (c) => <span className="text-xs whitespace-nowrap text-muted">{c.getValue()}</span> },
    { accessorKey: "status", header: "Status", cell: (c) => <StatusBadge s={c.getValue()} /> },
  ], []);
  const table = useReactTable({ data: res.data?.items ?? [], columns, getCoreRowModel: getCoreRowModel(), manualPagination: true });
  const total = res.data?.total ?? 0;
  const sc = res.data?.status_counts ?? {};
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2" role="group" aria-label="Filter by case status">
        {["", "New", "Pending approval", "Returned", "Closed"].map((s) => (
          <button key={s || "all"} onClick={() => setStatus(s)}
            className={clsx("h-8 rounded-md border px-3 text-xs font-medium transition-colors duration-150",
              status === s ? "border-accent bg-accent-soft text-accent" : "border-border bg-surface text-muted hover:text-text")}
            aria-pressed={status === s}>
            {s || "All"} {s && <span className="num ml-1">{fmtInt(sc[s] ?? 0)}</span>}
          </button>
        ))}
      </div>
      <Card
        title={`Alert queue (${fmtInt(total)})`}
        actions={
          <>
            <Select label="Priority" value={priority} onChange={setPriority} options={[{ value: "", label: "All priorities" }, ...["P1", "P2", "P3", "P4"].map((p) => ({ value: p, label: p }))]} />
            <Select label="Detection source" value={source} onChange={setSource} options={[{ value: "", label: "All sources" }, ...["Rules + ML", "Rules only", "ML only"].map((p) => ({ value: p, label: p }))]} />
            <div className="relative">
              <Search className="pointer-events-none absolute top-2.5 left-2.5 h-4 w-4 text-muted" aria-hidden />
              <Input placeholder="Alert ID, category, card token" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search alerts" className="w-60 pl-8" />
            </div>
          </>
        }
      >
        {res.error ? <ErrorState error={res.error} onRetry={res.refetch} /> : res.isLoading ? <LoadingBlock rows={10} /> : (
          <>
            <div className={clsx("overflow-x-auto rounded-md border border-border transition-opacity duration-150", res.isFetching && "opacity-70")}>
              <table className="w-full text-sm">
                <thead className="bg-surface-2">
                  {table.getHeaderGroups().map((hg) => (
                    <tr key={hg.id}>{hg.headers.map((h) => (
                      <th key={h.id} scope="col" className="border-b border-border px-3 py-2 text-left text-xs font-medium whitespace-nowrap text-muted">
                        {flexRender(h.column.columnDef.header, h.getContext())}
                      </th>))}
                    </tr>
                  ))}
                </thead>
                <tbody>
                  {table.getRowModel().rows.map((r) => (
                    <tr key={r.id} tabIndex={0} onClick={() => onOpen(r.original.alert_id)} onKeyDown={(e) => e.key === "Enter" && onOpen(r.original.alert_id)}
                      className="cursor-pointer border-b border-border last:border-0 hover:bg-surface-2">
                      {r.getVisibleCells().map((c) => <td key={c.id} className="px-3 py-2">{flexRender(c.column.columnDef.cell, c.getContext())}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
              {total === 0 && <EmptyState title="No alerts match these filters" />}
            </div>
            <div className="mt-3 flex items-center justify-between text-xs text-muted">
              <span className="num">{total ? `${fmtInt(page * 20 + 1)}–${fmtInt(Math.min((page + 1) * 20, total))} of ${fmtInt(total)}` : ""}</span>
              <div className="flex gap-2">
                <Button size="sm" onClick={() => setPage((p) => p - 1)} disabled={page === 0} aria-label="Previous page"><ChevronLeft className="h-3.5 w-3.5" aria-hidden /></Button>
                <Button size="sm" onClick={() => setPage((p) => p + 1)} disabled={(page + 1) * 20 >= total} aria-label="Next page"><ChevronRight className="h-3.5 w-3.5" aria-hidden /></Button>
              </div>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ case decision (maker-checker)

function CasePanel({ d }: { d: Detail }) {
  const { user } = useSession();
  const qc = useQueryClient();
  const [disposition, setDisposition] = useState(d.dispositions[0]);
  const [note, setNote] = useState("");
  const c = d.case;
  const m = useMutation({
    mutationFn: (v: { action: "propose" | "approve" | "return" }) =>
      api(`/alerts/${d.alert.alert_id}/${v.action}`, {
        method: "POST", body: JSON.stringify(v.action === "propose" ? { disposition, note } : { note }),
      }),
    onSuccess: () => {
      setNote("");
      qc.invalidateQueries({ queryKey: ["alert", d.alert.alert_id] });
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: ["audit"] });
    },
  });
  const isAnalyst = hasRole(user, "analyst");
  const isReviewer = hasRole(user, "reviewer");
  const isMaker = c.maker_id === user?.id;
  const noteBox = (
    <>
      <label htmlFor="case-note" className="mt-3 block text-xs font-medium text-muted">Note (required, kept in the audit trail)</label>
      <textarea id="case-note" rows={3} value={note} onChange={(e) => setNote(e.target.value)}
        className="mt-1 w-full rounded-md border border-border bg-surface p-2 text-sm text-text focus:border-accent focus:outline-none" />
    </>
  );
  return (
    <Card title="Case decision (maker-checker)" actions={<StatusBadge s={c.status} />}>
      {(c.status === "Pending approval" || c.status === "Closed" || c.status === "Returned") && c.maker_name && (
        <div className="mb-3 rounded-md border border-border bg-surface-2 p-3 text-sm">
          <div><span className="text-muted">Proposed by </span><span className="font-medium">{c.maker_name}</span> <span className="text-xs text-muted">{fmtTs(c.proposed_at)}</span></div>
          <div className="mt-1"><span className="text-muted">Disposition </span><span className="font-medium">{c.disposition}</span></div>
          <div className="mt-1 text-muted">{c.maker_note}</div>
          {c.checker_name && (
            <div className="mt-2 border-t border-border pt-2">
              <span className="text-muted">{c.status === "Closed" ? "Approved" : "Returned"} by </span>
              <span className="font-medium">{c.checker_name}</span> <span className="text-xs text-muted">{fmtTs(c.decided_at)}</span>
              <div className="mt-1 text-muted">{c.checker_note}</div>
            </div>
          )}
        </div>
      )}
      {(c.status === "New" || c.status === "Returned") && (
        isAnalyst ? (
          <form onSubmit={(e) => { e.preventDefault(); m.mutate({ action: "propose" }); }}>
            <label htmlFor="disp" className="block text-xs font-medium text-muted">Proposed disposition</label>
            <select id="disp" value={disposition} onChange={(e) => setDisposition(e.target.value)}
              className="mt-1 h-9 w-full rounded-md border border-border bg-surface px-2 text-sm text-text">
              {d.dispositions.map((x) => <option key={x}>{x}</option>)}
            </select>
            {noteBox}
            <div className="mt-3 flex justify-end"><Button type="submit" variant="primary" disabled={!note.trim() || m.isPending}>Submit for approval</Button></div>
          </form>
        ) : <p className="text-sm text-muted">Waiting for an analyst to propose a disposition. You are acting as {user?.name} ({user?.title}).</p>
      )}
      {c.status === "Pending approval" && (
        isReviewer && !isMaker ? (
          <form onSubmit={(e) => e.preventDefault()}>
            {noteBox}
            <div className="mt-3 flex justify-end gap-2">
              <Button type="button" variant="danger" disabled={!note.trim() || m.isPending} onClick={() => m.mutate({ action: "return" })}>Return to analyst</Button>
              <Button type="button" variant="primary" disabled={!note.trim() || m.isPending} onClick={() => m.mutate({ action: "approve" })}>Approve and close</Button>
            </div>
          </form>
        ) : (
          <div className="flex gap-2 rounded-md border border-border bg-amber-soft p-3 text-sm text-amber">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            <span>
              {isMaker
                ? "You proposed this disposition, so a different reviewer must approve it. The API rejects self-approval (segregation of duties)."
                : "Awaiting a reviewer. Only users with the reviewer role can approve or return."}
            </span>
          </div>
        )
      )}
      {m.error && <p role="alert" className="mt-3 text-sm text-red">{(m.error as Error).message}</p>}
    </Card>
  );
}

function AuditList({ events }: { events: AuditEvent[] }) {
  if (!events.length) return <p className="text-sm text-muted">No actions recorded yet.</p>;
  return (
    <ol className="space-y-3">
      {events.map((e) => (
        <li key={e.event_id} className="border-l-2 border-border pl-3 text-sm">
          <div><span className="font-medium">{e.actor_name}</span> <span className="text-muted">{e.action.toLowerCase()}</span></div>
          <div className="num text-xs text-muted">{fmtTs(e.ts)} · roles: {e.actor_roles}{e.from_status ? ` · ${e.from_status} to ${e.to_status}` : ""}</div>
          {e.detail?.note && <div className="mt-1 text-xs">{e.detail.note}</div>}
        </li>
      ))}
    </ol>
  );
}

function AlertDetail({ id, onClose }: { id: string | null; onClose: () => void }) {
  const q = useQuery({ queryKey: ["alert", id], queryFn: () => api<Detail>(`/alerts/${id}`), enabled: !!id });
  const d = q.data;
  return (
    <SlideOver open={!!id} onClose={onClose} width="max-w-3xl"
      title={<span className="flex items-center gap-2">{id} {d && <PriorityBadge p={d.alert.priority} />}</span>}
      subtitle={d ? `Score ${fmtNum(d.alert.combined_score, 0)} · ${d.alert.detection_source}${d.alert.is_synthetic ? " · tagged synthetic scenario" : ""}` : undefined}>
      {q.isLoading ? <LoadingBlock rows={10} /> : q.error ? <ErrorState error={q.error} onRetry={q.refetch} /> : d && (
        <div className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <Card title="Transaction (PII masked)">
              <KeyValue items={[
                ["Time", fmtTs(d.alert.txn_ts)], ["Amount", fmtUsd(d.alert.amount)], ["Category", d.alert.category],
                ["Merchant", d.transaction?.merchant_name], ["Card", d.transaction?.card_number_masked],
                ["Cardholder", d.transaction ? `${d.transaction.age_band}, ${d.transaction.city}, ${d.transaction.state} ${d.transaction.zip3}xx` : "–"],
                ["Distance from home", d.transaction ? `${fmtNum(d.transaction.distance_from_home_km, 1)} km` : "–"],
                ["DQ flags on record", d.transaction?.dq_flag_count ?? 0],
              ]} />
            </Card>
            <Card title="Why it was flagged">
              <div className="space-y-3 text-sm">
                {d.rule_hits.map((h) => (
                  <div key={h.rule_id}>
                    <div className="flex items-center gap-2"><Badge tone="accent">{h.rule_id}</Badge><span className="font-medium">{h.name}</span></div>
                    <div className="mt-1 text-xs text-muted">{h.reason}</div>
                  </div>
                ))}
                {d.alert.ml_score != null && (
                  <div>
                    <div className="flex items-center gap-2"><Badge tone="accent">ML</Badge><span className="font-medium">LightGBM score {fmtNum(d.alert.ml_score, 3)}</span></div>
                    {d.alert.ml_reasons && (
                      <ul className="mt-1 list-disc pl-5 text-xs text-muted">
                        {d.alert.ml_reasons.split("; ").map((r) => <li key={r}>{r}</li>)}
                      </ul>
                    )}
                  </div>
                )}
                {!d.rule_hits.length && d.alert.ml_score == null && <p className="text-muted">No reasons recorded.</p>}
              </div>
            </Card>
          </div>
          <CasePanel d={d} />
          <Card title="Recent activity on this card" bodyClass="p-0">
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-xs text-muted">
                <tr>{["Time", "Amount", "Category", "Merchant", "km from home"].map((h) => <th key={h} scope="col" className="px-3 py-2 text-left font-medium">{h}</th>)}</tr>
              </thead>
              <tbody>
                {d.recent.map((r, i) => (
                  <tr key={i} className={clsx("border-t border-border", r.is_alerted && "bg-accent-soft font-medium")}>
                    <td className="num px-3 py-1.5 whitespace-nowrap">{fmtTs(r.txn_ts)}</td>
                    <td className="num px-3 py-1.5">{fmtUsd(r.amount)}</td>
                    <td className="px-3 py-1.5">{r.category}</td>
                    <td className="max-w-[200px] truncate px-3 py-1.5">{r.merchant_name}</td>
                    <td className="num px-3 py-1.5">{fmtNum(r.distance_from_home_km, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          <Card title="Audit trail for this alert"><AuditList events={d.audit} /></Card>
        </div>
      )}
    </SlideOver>
  );
}

// ------------------------------------------------------------------ rules and model

type RulesData = {
  rules: { rule_id: string; name: string; hits_all_periods: number; hits_test: number; fraud_hits_test: number; precision_test_pct: number;
    recall_test_pct: number; unique_fraud_catches_test: number; risk_addressed: string; logic: string; threshold_rationale: string; severity: string; owner: string }[];
  drivers: { feature: string; mean_abs_shap: number; feature_label: string }[];
  threshold: Record<string, number>; accuracy_note: string;
};

function RulesTab() {
  const t = useChartTheme();
  const q = useQuery({ queryKey: ["mon-rules"], queryFn: () => api<RulesData>("/monitoring/rules") });
  if (q.isLoading) return <LoadingBlock rows={8} />;
  if (q.error) return <ErrorState error={q.error} onRetry={q.refetch} />;
  const d = q.data!;
  const top = [...d.drivers].slice(0, 10).reverse();
  const opt = {
    ...baseOption(t), legend: undefined,
    tooltip: { ...baseOption(t).tooltip, trigger: "axis" as const },
    grid: { left: 8, right: 24, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: "value" as const, ...axis(t) },
    yAxis: { type: "category" as const, data: top.map((x) => x.feature_label), ...axis(t), splitLine: { show: false } },
    series: [{ type: "bar" as const, data: top.map((x) => Number(x.mean_abs_shap.toFixed(4))), barWidth: 12, itemStyle: { color: t.series[0], borderRadius: 2 } }],
  };
  const columns: ColumnDef<RulesData["rules"][number], any>[] = [
    { accessorKey: "rule_id", header: "Rule", cell: (c) => <span className="font-medium">{c.getValue()}</span> },
    { accessorKey: "name", header: "Name", cell: (c) => <div className="max-w-[260px]"><div className="font-medium">{c.getValue()}</div><div className="text-xs text-muted">{c.row.original.risk_addressed}</div></div> },
    { accessorKey: "hits_test", header: "Hits (test)", cell: (c) => <span className="num">{fmtInt(c.getValue())}</span> },
    { accessorKey: "precision_test_pct", header: "Precision", cell: (c) => <span className="num">{fmtPct(c.getValue(), 1)}</span> },
    { accessorKey: "recall_test_pct", header: "Recall", cell: (c) => <span className="num">{fmtPct(c.getValue(), 1)}</span> },
    { accessorKey: "unique_fraud_catches_test", header: "Unique catches", cell: (c) => <span className="num">{fmtInt(c.getValue())}</span> },
    { accessorKey: "threshold_rationale", header: "Threshold rationale", cell: (c) => <span className="block max-w-[320px] text-xs text-muted">{c.getValue()}</span> },
  ];
  return (
    <div className="space-y-4">
      <Card title="Monitoring rules: performance on the test period (Jul to Dec 2020)">
        <DataTable data={d.rules} columns={columns} pageSize={8} />
      </Card>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Top model drivers (mean absolute SHAP, test period)">
          <Chart option={opt} height={300} label="Bar chart of the ten most influential model features" />
        </Card>
        <Card title="Threshold and evaluation choices">
          <KeyValue items={[
            ["Threshold (chosen by cost)", d.threshold?.chosen_by_cost_on_validation],
            ["F1-optimal threshold", d.threshold?.f1_optimal_on_validation],
            ["Cost of a false positive", fmtUsd(d.threshold?.cost_false_positive_usd)],
            ["Analyst capacity", `${d.threshold?.analyst_capacity_alerts_per_day} alerts per day`],
            ["Validation alerts per day", d.threshold?.validation_alerts_per_day_at_threshold],
            ["Isolation Forest threshold", fmtNum(d.threshold?.isolation_forest_threshold, 3)],
            ["Why not accuracy", d.accuracy_note],
          ]} />
        </Card>
      </div>
    </div>
  );
}

function AuditTab() {
  const q = useQuery({ queryKey: ["audit"], queryFn: () => api<AuditEvent[]>("/audit?limit=500") });
  if (q.isLoading) return <LoadingBlock rows={8} />;
  if (q.error) return <ErrorState error={q.error} onRetry={q.refetch} />;
  const columns: ColumnDef<AuditEvent, any>[] = [
    { accessorKey: "event_id", header: "#", cell: (c) => <span className="num text-muted">{c.getValue()}</span> },
    { accessorKey: "ts", header: "Time", cell: (c) => <span className="num whitespace-nowrap">{fmtTs(c.getValue())}</span> },
    { accessorKey: "actor_name", header: "User", cell: (c) => <div><div className="font-medium">{c.getValue()}</div><div className="text-xs text-muted">{c.row.original.actor_roles}</div></div> },
    { accessorKey: "entity_id", header: "Record", cell: (c) => <span className="whitespace-nowrap">{c.getValue()} <span className="text-xs text-muted">({c.row.original.entity_type.replace("_", " ")})</span></span> },
    { accessorKey: "action", header: "Action" },
    { id: "change", header: "Status change", cell: (c) => <span className="text-xs whitespace-nowrap text-muted">{c.row.original.from_status ? `${c.row.original.from_status} to ${c.row.original.to_status}` : "–"}</span> },
    { id: "note", header: "Note / reason", cell: (c) => <span className="block max-w-[320px] text-xs">{c.row.original.detail?.note ?? c.row.original.detail?.reason ?? ""}</span> },
  ];
  return (
    <Card title="Audit trail (append-only)" actions={<span className="text-xs text-muted">Every case and steward decision, newest first</span>}>
      <DataTable data={q.data!} columns={columns} pageSize={15} empty="No actions recorded yet" />
    </Card>
  );
}

export default function Monitoring() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "queue";
  const alert = params.get("alert");
  const setParam = (k: string, v: string | null) => {
    const p = new URLSearchParams(params);
    if (v) p.set(k, v); else p.delete(k);
    setParams(p, { replace: true });
  };
  return (
    <>
      <PageHeader title="Monitoring" subtitle="Transaction monitoring alerts from 8 SQL rules and a LightGBM model, worked under maker-checker: an analyst proposes, a different reviewer approves." />
      <Tabs value={tab} onChange={(v) => setParam("tab", v)} tabs={[
        { id: "queue", label: "Alert queue", content: <AlertQueue onOpen={(id) => setParam("alert", id)} /> },
        { id: "rules", label: "Rules and model", content: <RulesTab /> },
        { id: "audit", label: "Audit trail", content: <AuditTab /> },
      ]} />
      <AlertDetail id={alert} onClose={() => setParam("alert", null)} />
    </>
  );
}
