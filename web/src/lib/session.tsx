import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

export type User = { id: string; name: string; title: string; roles: string[] };
type Theme = "light" | "dark";

type Session = {
  user: User | null;
  users: User[];
  setUserId: (id: string) => void;
  theme: Theme;
  toggleTheme: () => void;
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
};

const Ctx = createContext<Session | null>(null);

function read(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}
function write(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: keep in memory only */
  }
}

let currentUserId = read("verity-user", "aarav");
export const getUserId = () => currentUserId;

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", "X-Verity-User": currentUserId, ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const body = await res.json();
      msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [userId, setUserIdState] = useState(currentUserId);
  const [theme, setTheme] = useState<Theme>(() =>
    document.documentElement.classList.contains("dark") ? "dark" : "light",
  );
  const [sidebarCollapsed, setCollapsed] = useState(() => read("verity-sidebar", "0") === "1");
  const { data: users = [] } = useQuery({ queryKey: ["users"], queryFn: () => api<User[]>("/users") });

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    write("verity-theme", theme);
  }, [theme]);

  const setUserId = useCallback(
    (id: string) => {
      currentUserId = id;
      write("verity-user", id);
      setUserIdState(id);
      qc.invalidateQueries();
    },
    [qc],
  );

  const value = useMemo<Session>(
    () => ({
      user: users.find((u) => u.id === userId) ?? null,
      users,
      setUserId,
      theme,
      toggleTheme: () => setTheme((t) => (t === "dark" ? "light" : "dark")),
      sidebarCollapsed,
      toggleSidebar: () =>
        setCollapsed((c) => {
          write("verity-sidebar", c ? "0" : "1");
          return !c;
        }),
    }),
    [users, userId, setUserId, theme, sidebarCollapsed],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession() {
  const s = useContext(Ctx);
  if (!s) throw new Error("useSession outside provider");
  return s;
}

export function hasRole(user: User | null, role: string) {
  return !!user && user.roles.includes(role);
}
