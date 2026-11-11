import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { api } from "../lib/session";
import { fmtInt, fmtPct, pctText } from "../lib/format";
import { DataTable } from "../components/DataTable";
import { Badge, Card, ErrorState, Input, KeyValue, LoadingBlock, PageHeader, Select, SeverityBadge, SlideOver, Stat } from "../components/ui";

type Issue = {
  issue_id: string; source_type: string; source_ref: string; title: string; description: string; severity: string; owner: string;
  root_cause: string; five_whys: string; remediation_plan: string; records_affected: number; synthetic_share_pct: number | null;
  opened_at: string; due_date: string; status: string; remediation_progress_pct: number; first_seen_run: string; last_seen_run: string;
  closed_at: string | null;
};

const SEV = ["critical", "high", "medium", "low"];

export default function Issues() {
  const q = useQuery({ queryKey: ["issues"], queryFn: () => api<Issue[]>("/issues") });
  const [filter, setFilter] = useState("");
  const [severity, setSeverity] = useState("");
  const [origin, setOrigin] = useState("");
  const [open, setOpen] = useState<Issue | null>(null);
  const data = useMemo(() => (q.data ?? []).filter((i) =>
    (!severity || i.severity === severity) &&
    (!origin || (origin === "source" ? (i.synthetic_share_pct ?? 0) < 100 : i.synthetic_share_pct === 100))), [q.data, severity, origin]);
  if (q.isLoading) return <><PageHeader title="Issues" /><LoadingBlock rows={10} /></>;
  if (q.error) return <ErrorState error={q.error} onRetry={q.refetch} />;
  const all = q.data!;
  const openIssues = all.filter((i) => i.status === "Open");
  const sourceIssues = all.filter((i) => i.source_type === "DQ rule" && (i.synthetic_share_pct ?? 0) < 100);
  const columns: ColumnDef<Issue, any>[] = [
    { accessorKey: "issue_id", header: "Issue", cell: (c) => <span className="font-medium whitespace-nowrap">{c.getValue()}</span> },
    { accessorKey: "severity", header: "Severity", cell: (c) => <SeverityBadge s={c.getValue()} />, sortingFn: (a, b) => SEV.indexOf(a.original.severity) - SEV.indexOf(b.original.severity) },
    { accessorKey: "title", header: "Title", cell: (c) => <span className="block max-w-[360px] min-w-[240px]">{c.getValue()}</span> },
    { accessorKey: "source_type", header: "Raised by", cell: (c) => <span className="text-xs whitespace-nowrap">{c.getValue()} <span className="text-muted">{c.row.original.source_ref.split(":")[1]}</span></span> },
    { accessorKey: "records_affected", header: "Records", cell: (c) => <span className="num">{fmtInt(c.getValue())}</span> },
    { accessorKey: "synthetic_share_pct", header: "Origin", cell: (c) => {
      const v = c.getValue() as number | null;
      if (c.row.original.source_type !== "DQ rule") return <span className="text-xs text-muted">–</span>;
      return v === 100 ? <Badge>Injected defects</Badge> : <Badge tone="amber">Source data</Badge>;
    } },
    { accessorKey: "owner", header: "Owner", cell: (c) => <span className="block min-w-[140px] text-xs">{c.getValue()}</span> },
    { accessorKey: "due_date", header: "Due", cell: (c) => <span className="num whitespace-nowrap">{c.getValue()}</span> },
    { accessorKey: "status", header: "Status", cell: (c) => <Badge tone={c.getValue() === "Open" ? "neutral" : "green"}>{c.getValue()}</Badge> },
  ];
  const whys = (open?.five_whys ?? "").split(" | ").filter(Boolean);
  return (
    <>
      <PageHeader title="Issues" subtitle="Data quality, reconciliation, contract and KRI failures are logged automatically with an owner, due date and root cause. Issues close themselves when the next run passes." />
      <div className="mb-6 grid grid-cols-2 gap-4 xl:grid-cols-4">
        <Stat label="Open issues" value={fmtInt(openIssues.length)} sub={`${fmtInt(all.length)} logged in total`} />
        <Stat label="Critical / high" value={`${openIssues.filter((i) => i.severity === "critical").length} / ${openIssues.filter((i) => i.severity === "high").length}`} sub="open, by severity" />
        <Stat label="Genuine source-data issues" value={fmtInt(sourceIssues.length)} sub="DQ issues not caused by injected test defects" />
        <Stat label="With 5-Whys root cause" value={fmtInt(all.filter((i) => i.five_whys).length)} sub="documented analyses" />
      </div>
      <Card
        title={`Issue log (${data.length})`}
        actions={
          <>
            <Select label="Severity" value={severity} onChange={setSeverity} options={[{ value: "", label: "All severities" }, ...SEV.map((s) => ({ value: s, label: s[0].toUpperCase() + s.slice(1) }))]} />
            <Select label="Origin" value={origin} onChange={setOrigin} options={[{ value: "", label: "All origins" }, { value: "source", label: "Source data" }, { value: "injected", label: "Injected defects" }]} />
            <Input placeholder="Filter issues" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter issues" className="w-48" />
          </>
        }
      >
        <DataTable data={data} columns={columns} filter={filter} pageSize={15} onRowClick={setOpen} empty="No issues match these filters" />
      </Card>
      <SlideOver open={!!open} onClose={() => setOpen(null)} title={open ? `${open.issue_id} ${open.title}` : ""} subtitle={open ? `${open.source_type} ${open.source_ref} · opened ${open.opened_at}` : undefined}>
        {open && (
          <div className="space-y-6 text-sm">
            <div className="flex flex-wrap gap-2"><SeverityBadge s={open.severity} /><Badge>{open.status}</Badge></div>
            <p>{pctText(open.description)}</p>
            <KeyValue items={[
              ["Owner", open.owner], ["Due date", open.due_date], ["Records affected", fmtInt(open.records_affected)],
              ["From injected defects", open.synthetic_share_pct == null ? "–" : fmtPct(open.synthetic_share_pct)],
              ["Remediation progress", fmtPct(open.remediation_progress_pct)],
              ["First / last seen", `${open.first_seen_run} / ${open.last_seen_run}`],
            ]} />
            <div>
              <h3 className="mb-1 text-xs font-medium text-muted">Root cause</h3>
              <p>{open.root_cause}</p>
            </div>
            {whys.length > 0 && (
              <div>
                <h3 className="mb-2 text-xs font-medium text-muted">5 Whys</h3>
                <ol className="space-y-2">
                  {whys.map((w, i) => {
                    const [qn, ...rest] = w.split("? ");
                    return (
                      <li key={i} className="flex gap-3">
                        <span className="num flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface-2 text-xs font-medium">{i + 1}</span>
                        <span><span className="font-medium">{qn}?</span> <span className="text-muted">{rest.join("? ")}</span></span>
                      </li>
                    );
                  })}
                </ol>
              </div>
            )}
            <div>
              <h3 className="mb-1 text-xs font-medium text-muted">Remediation plan</h3>
              <p>{open.remediation_plan}</p>
            </div>
          </div>
        )}
      </SlideOver>
    </>
  );
}
