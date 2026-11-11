import clsx from "clsx";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import * as Tooltip from "@radix-ui/react-tooltip";
import * as Dropdown from "@radix-ui/react-dropdown-menu";
import { motion, useReducedMotion } from "motion/react";
import {
  Activity, Check, ChevronDown, FileSpreadsheet, LayoutDashboard, ListChecks, Moon, PanelLeftClose,
  PanelLeftOpen, Search, ShieldCheck, Sun, UserRound,
} from "lucide-react";
import { useSession } from "../lib/session";
import { CommandPalette, useCommandPalette } from "./CommandPalette";

export const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard },
  { to: "/governance", label: "Data Governance", icon: ShieldCheck },
  { to: "/issues", label: "Issues", icon: ListChecks },
  { to: "/monitoring", label: "Monitoring", icon: Activity },
  { to: "/reports", label: "Reports", icon: FileSpreadsheet },
];

function Sidebar() {
  const { sidebarCollapsed: c, toggleSidebar } = useSession();
  return (
    <aside
      className={clsx(
        "flex shrink-0 flex-col bg-sidebar text-sidebar-text transition-[width] duration-200",
        c ? "w-16" : "w-60",
      )}
      aria-label="Main navigation"
    >
      <div className="flex h-14 items-center gap-3 px-4">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-white/15 text-sm font-semibold text-white">
          V
        </div>
        {!c && (
          <div className="min-w-0">
            <div className="text-sm font-semibold text-white">Verity</div>
            <div className="truncate text-xs text-sidebar-text">Data Governance</div>
          </div>
        )}
      </div>
      <nav className="mt-2 flex-1 space-y-1 px-2">
        {NAV.map(({ to, label, icon: Icon }) => {
          const link = (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                clsx(
                  "flex h-9 items-center gap-3 rounded-md px-3 text-sm transition-colors duration-150",
                  isActive ? "bg-sidebar-active font-medium text-white" : "hover:bg-sidebar-active/60 hover:text-white",
                )
              }
            >
              <Icon className="h-4 w-4 shrink-0" aria-hidden />
              {c ? <span className="sr-only">{label}</span> : label}
            </NavLink>
          );
          return c ? (
            <Tooltip.Root key={to}>
              <Tooltip.Trigger asChild>{link}</Tooltip.Trigger>
              <Tooltip.Portal>
                <Tooltip.Content side="right" sideOffset={8} className="rounded bg-text px-2 py-1 text-xs text-bg">
                  {label}
                </Tooltip.Content>
              </Tooltip.Portal>
            </Tooltip.Root>
          ) : link;
        })}
      </nav>
      <div className="border-t border-white/10 p-2">
        <button
          onClick={toggleSidebar}
          className="flex h-9 w-full items-center gap-3 rounded-md px-3 text-sm hover:bg-sidebar-active/60 hover:text-white"
          aria-label={c ? "Expand sidebar" : "Collapse sidebar"}
        >
          {c ? <PanelLeftOpen className="h-4 w-4" aria-hidden /> : <PanelLeftClose className="h-4 w-4" aria-hidden />}
          {!c && "Collapse"}
        </button>
      </div>
    </aside>
  );
}

function RoleSwitcher() {
  const { user, users, setUserId } = useSession();
  return (
    <Dropdown.Root>
      <Dropdown.Trigger asChild>
        <button
          className="flex h-9 items-center gap-2 rounded-md border border-border bg-surface px-3 text-sm text-text hover:bg-surface-2"
          aria-label="Switch demo user and role"
        >
          <UserRound className="h-4 w-4 text-muted" aria-hidden />
          <span className="hidden text-left sm:block">
            <span className="block leading-tight font-medium whitespace-nowrap">{user?.name ?? "Select user"}</span>
          </span>
          <span className="hidden text-xs whitespace-nowrap text-muted xl:inline">{user?.title}</span>
          <ChevronDown className="h-3.5 w-3.5 text-muted" aria-hidden />
        </button>
      </Dropdown.Trigger>
      <Dropdown.Portal>
        <Dropdown.Content align="end" sideOffset={6} className="z-50 w-72 rounded-lg border border-border bg-surface p-1 shadow-lg">
          <Dropdown.Label className="px-3 py-2 text-xs text-muted">Demo users (roles are enforced by the API)</Dropdown.Label>
          {users.map((u) => (
            <Dropdown.Item
              key={u.id}
              onSelect={() => setUserId(u.id)}
              className="flex cursor-pointer items-center justify-between gap-2 rounded-md px-3 py-2 text-sm text-text outline-none data-[highlighted]:bg-surface-2"
            >
              <span>
                <span className="block font-medium">{u.name}</span>
                <span className="block text-xs text-muted">{u.title}</span>
              </span>
              {u.id === user?.id && <Check className="h-4 w-4 text-accent" aria-hidden />}
            </Dropdown.Item>
          ))}
        </Dropdown.Content>
      </Dropdown.Portal>
    </Dropdown.Root>
  );
}

function TopBar({ openPalette }: { openPalette: () => void }) {
  const { theme, toggleTheme } = useSession();
  return (
    <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-border bg-surface px-6">
      <button
        onClick={openPalette}
        className="flex h-9 w-full max-w-md items-center gap-2 rounded-md border border-border bg-bg px-3 text-sm text-muted hover:border-border-strong"
        aria-label="Open command palette"
      >
        <Search className="h-4 w-4" aria-hidden />
        <span className="flex-1 text-left">Search pages, alerts, actions</span>
        <kbd className="rounded border border-border bg-surface px-1.5 text-xs">Ctrl K</kbd>
      </button>
      <div className="flex items-center gap-2">
        <RoleSwitcher />
        <button
          onClick={toggleTheme}
          className="flex h-9 w-9 items-center justify-center rounded-md border border-border bg-surface text-muted hover:bg-surface-2 hover:text-text"
          aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
        >
          {theme === "dark" ? <Sun className="h-4 w-4" aria-hidden /> : <Moon className="h-4 w-4" aria-hidden />}
        </button>
      </div>
    </header>
  );
}

export function Layout() {
  const palette = useCommandPalette();
  const location = useLocation();
  const reduce = useReducedMotion();
  return (
    <Tooltip.Provider delayDuration={200}>
      <div className="flex h-full">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar openPalette={() => palette.setOpen(true)} />
          <main className="flex-1 overflow-y-auto">
            <motion.div
              key={location.pathname}
              initial={{ opacity: reduce ? 1 : 0, y: reduce ? 0 : 4 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: reduce ? 0 : 0.18 }}
              className="mx-auto max-w-[1440px] px-6 py-6"
            >
              <Outlet />
            </motion.div>
          </main>
        </div>
        <CommandPalette open={palette.open} setOpen={palette.setOpen} />
      </div>
    </Tooltip.Provider>
  );
}
