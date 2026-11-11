import { Command } from "cmdk";
import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Moon, Search, UserRound } from "lucide-react";
import { useSession } from "../lib/session";
import { NAV } from "./Layout";

export function useCommandPalette() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return { open, setOpen };
}

const SECTIONS = [
  { label: "DQ scorecard", to: "/governance?tab=scorecard" },
  { label: "Data catalog", to: "/governance?tab=catalog" },
  { label: "Critical Data Elements", to: "/governance?tab=cde" },
  { label: "Lineage", to: "/governance?tab=lineage" },
  { label: "Reconciliation", to: "/governance?tab=reconciliation" },
  { label: "Rule suggestions (steward queue)", to: "/governance?tab=suggestions" },
  { label: "Monitoring rules and model", to: "/monitoring?tab=rules" },
  { label: "Audit trail", to: "/monitoring?tab=audit" },
];

const item =
  "flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-sm text-text outline-none";

export function CommandPalette({ open, setOpen }: { open: boolean; setOpen: (o: boolean) => void }) {
  const navigate = useNavigate();
  const { users, setUserId, toggleTheme, theme } = useSession();
  const [search, setSearch] = useState("");
  const go = (to: string) => {
    navigate(to);
    setOpen(false);
    setSearch("");
  };
  const alertId = /^alt-?\d+$/i.test(search.trim())
    ? `ALT-${search.trim().replace(/\D/g, "").padStart(7, "0")}`
    : null;
  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40" />
        <Dialog.Content
          className="fixed top-[15%] left-1/2 z-50 w-[min(640px,92vw)] -translate-x-1/2 overflow-hidden rounded-lg border border-border bg-surface shadow-xl"
          aria-describedby={undefined}
        >
          <Dialog.Title className="sr-only">Command palette</Dialog.Title>
          <Command label="Command palette" loop>
            <div className="flex items-center gap-2 border-b border-border px-4">
              <Search className="h-4 w-4 text-muted" aria-hidden />
              <Command.Input
                value={search}
                onValueChange={setSearch}
                placeholder="Type a page, an alert ID (ALT-0001196) or an action"
                className="h-12 flex-1 bg-transparent text-sm text-text outline-none placeholder:text-muted"
              />
            </div>
            <Command.List className="max-h-[360px] overflow-y-auto p-2">
              <Command.Empty className="px-3 py-6 text-center text-sm text-muted">No results</Command.Empty>
              {alertId && (
                <Command.Group heading="Alert">
                  <Command.Item value={`open ${alertId}`} onSelect={() => go(`/monitoring?alert=${alertId}`)} className={item}>
                    <ArrowRight className="h-4 w-4 text-muted" aria-hidden />Open {alertId}
                  </Command.Item>
                </Command.Group>
              )}
              <Command.Group heading="Pages">
                {NAV.map(({ to, label, icon: Icon }) => (
                  <Command.Item key={to} value={label} onSelect={() => go(to)} className={item}>
                    <Icon className="h-4 w-4 text-muted" aria-hidden />{label}
                  </Command.Item>
                ))}
              </Command.Group>
              <Command.Group heading="Sections">
                {SECTIONS.map((s) => (
                  <Command.Item key={s.to} value={s.label} onSelect={() => go(s.to)} className={item}>
                    <ArrowRight className="h-4 w-4 text-muted" aria-hidden />{s.label}
                  </Command.Item>
                ))}
              </Command.Group>
              <Command.Group heading="Actions">
                <Command.Item value="toggle theme dark light" onSelect={() => { toggleTheme(); setOpen(false); }} className={item}>
                  <Moon className="h-4 w-4 text-muted" aria-hidden />Switch to {theme === "dark" ? "light" : "dark"} theme
                </Command.Item>
                {users.map((u) => (
                  <Command.Item key={u.id} value={`switch user ${u.name} ${u.title}`} onSelect={() => { setUserId(u.id); setOpen(false); }} className={item}>
                    <UserRound className="h-4 w-4 text-muted" aria-hidden />Act as {u.name}
                    <span className="ml-auto text-xs text-muted">{u.title}</span>
                  </Command.Item>
                ))}
              </Command.Group>
            </Command.List>
          </Command>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
