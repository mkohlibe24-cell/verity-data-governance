import clsx from "clsx";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { AlertTriangle, Inbox, RotateCw, X } from "lucide-react";
import { ApiError } from "../lib/session";

export function Card({ title, actions, children, className, bodyClass }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; bodyClass?: string;
}) {
  return (
    <section className={clsx("rounded-lg border border-border bg-surface", className)}>
      {(title || actions) && (
        <header className="flex min-h-12 items-center justify-between gap-4 border-b border-border px-4 py-2">
          <h2 className="text-sm font-semibold text-text">{title}</h2>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={clsx("p-4", bodyClass)}>{children}</div>
    </section>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-text">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: Rag }) {
  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-muted">{label}</span>
        {tone && <RagBadge rag={tone} />}
      </div>
      <div className="num mt-2 text-2xl font-semibold text-text">{value}</div>
      {sub && <div className="mt-1 text-xs text-muted">{sub}</div>}
    </div>
  );
}

export type Rag = "Green" | "Amber" | "Red";
const ragClass: Record<string, string> = {
  Green: "bg-green-soft text-green",
  Amber: "bg-amber-soft text-amber",
  Red: "bg-red-soft text-red",
  PASS: "bg-green-soft text-green",
  FAIL: "bg-red-soft text-red",
  WARN: "bg-amber-soft text-amber",
};

export function RagBadge({ rag }: { rag: string }) {
  return (
    <span className={clsx("inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-xs font-medium", ragClass[rag] ?? "bg-surface-2 text-muted")}>
      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-current" />
      {rag}
    </span>
  );
}

export function Badge({ children, tone = "neutral", className }: {
  children: ReactNode; tone?: "neutral" | "accent" | "green" | "amber" | "red"; className?: string;
}) {
  const t = {
    neutral: "bg-surface-2 text-muted border-border",
    accent: "bg-accent-soft text-accent border-transparent",
    green: "bg-green-soft text-green border-transparent",
    amber: "bg-amber-soft text-amber border-transparent",
    red: "bg-red-soft text-red border-transparent",
  }[tone];
  return <span className={clsx("inline-flex items-center rounded border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap", t, className)}>{children}</span>;
}

export function PriorityBadge({ p }: { p: string }) {
  const tone = p === "P1" ? "red" : p === "P2" ? "amber" : "neutral";
  return <Badge tone={tone}>{p}</Badge>;
}

export function StatusBadge({ s }: { s: string }) {
  const tone = s === "Closed" ? "green" : s === "Pending approval" ? "amber" : s === "Returned" ? "red" : "neutral";
  return <Badge tone={tone}>{s}</Badge>;
}

export function SeverityBadge({ s }: { s: string }) {
  const tone = s === "critical" ? "red" : s === "high" ? "amber" : "neutral";
  return <Badge tone={tone} className="capitalize">{s}</Badge>;
}

export function Button({ variant = "secondary", size = "md", className, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger"; size?: "sm" | "md";
}) {
  return (
    <button
      {...props}
      className={clsx(
        "inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-8 px-3 text-xs" : "h-9 px-4 text-sm",
        variant === "primary" && "bg-accent text-accent-fg hover:bg-accent-hover",
        variant === "secondary" && "border border-border bg-surface text-text hover:bg-surface-2",
        variant === "ghost" && "text-muted hover:bg-surface-2 hover:text-text",
        variant === "danger" && "border border-border bg-surface text-red hover:bg-red-soft",
        className,
      )}
    />
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("animate-pulse rounded bg-surface-2", className)} />;
}

export function LoadingBlock({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
      <Inbox className="h-6 w-6 text-muted" aria-hidden />
      <p className="text-sm font-medium text-text">{title}</p>
      {hint && <p className="max-w-sm text-xs text-muted">{hint}</p>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const msg = error instanceof ApiError ? `${error.status}: ${error.message}` : error instanceof Error ? error.message : "Unknown error";
  return (
    <div role="alert" className="flex flex-col items-center justify-center gap-2 py-10 text-center">
      <AlertTriangle className="h-6 w-6 text-red" aria-hidden />
      <p className="text-sm font-medium text-text">Could not load this data</p>
      <p className="max-w-md text-xs text-muted">{msg}</p>
      {onRetry && <Button size="sm" onClick={onRetry}><RotateCw className="h-3.5 w-3.5" aria-hidden />Retry</Button>}
    </div>
  );
}

export function Tabs({ tabs, value, onChange }: {
  tabs: { id: string; label: string; content: ReactNode }[]; value: string; onChange: (v: string) => void;
}) {
  return (
    <TabsPrimitive.Root value={value} onValueChange={onChange}>
      <TabsPrimitive.List className="mb-4 flex gap-1 overflow-x-auto border-b border-border" aria-label="Sections">
        {tabs.map((t) => (
          <TabsPrimitive.Trigger
            key={t.id}
            value={t.id}
            className="-mb-px border-b-2 border-transparent px-3 py-2 text-sm font-medium whitespace-nowrap text-muted transition-colors duration-150 hover:text-text data-[state=active]:border-accent data-[state=active]:text-text"
          >
            {t.label}
          </TabsPrimitive.Trigger>
        ))}
      </TabsPrimitive.List>
      {tabs.map((t) => (
        <TabsPrimitive.Content key={t.id} value={t.id} className="outline-none">
          {t.content}
        </TabsPrimitive.Content>
      ))}
    </TabsPrimitive.Root>
  );
}

export function SlideOver({ open, onClose, title, subtitle, children, width = "max-w-2xl" }: {
  open: boolean; onClose: () => void; title: ReactNode; subtitle?: ReactNode; children: ReactNode; width?: string;
}) {
  const reduce = useReducedMotion();
  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <AnimatePresence>
        {open && (
          <Dialog.Portal forceMount>
            <Dialog.Overlay asChild>
              <motion.div
                className="fixed inset-0 z-40 bg-black/40"
                initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                transition={{ duration: reduce ? 0 : 0.18 }}
              />
            </Dialog.Overlay>
            <Dialog.Content asChild aria-describedby={undefined}>
              <motion.aside
                className={clsx("fixed inset-y-0 right-0 z-50 flex w-full flex-col border-l border-border bg-surface shadow-xl outline-none", width)}
                initial={{ x: reduce ? 0 : 48, opacity: 0 }} animate={{ x: 0, opacity: 1 }} exit={{ x: reduce ? 0 : 48, opacity: 0 }}
                transition={{ duration: reduce ? 0 : 0.22, ease: [0.2, 0, 0, 1] }}
              >
                <header className="flex items-start justify-between gap-4 border-b border-border px-6 py-4">
                  <div>
                    <Dialog.Title className="text-base font-semibold text-text">{title}</Dialog.Title>
                    {subtitle && <div className="mt-1 text-xs text-muted">{subtitle}</div>}
                  </div>
                  <Dialog.Close asChild>
                    <Button variant="ghost" size="sm" aria-label="Close panel"><X className="h-4 w-4" aria-hidden /></Button>
                  </Dialog.Close>
                </header>
                <div className="flex-1 overflow-y-auto px-6 py-4">{children}</div>
              </motion.aside>
            </Dialog.Content>
          </Dialog.Portal>
        )}
      </AnimatePresence>
    </Dialog.Root>
  );
}

export function KeyValue({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(120px,auto)_1fr] gap-x-4 gap-y-2 text-sm">
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="num text-text">{v ?? "–"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={clsx("h-9 rounded-md border border-border bg-surface px-3 text-sm text-text placeholder:text-muted focus:border-accent focus:outline-none", props.className)}
    />
  );
}

export function Select({ value, onChange, options, label, className }: {
  value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; label: string; className?: string;
}) {
  return (
    <select
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={clsx("h-9 rounded-md border border-border bg-surface px-2 text-sm text-text focus:border-accent focus:outline-none", className)}
    >
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}
