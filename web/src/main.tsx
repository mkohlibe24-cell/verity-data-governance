import { StrictMode, lazy, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./index.css";
import { SessionProvider } from "./lib/session";
import { Layout } from "./components/Layout";
import { LoadingBlock } from "./components/ui";

const Overview = lazy(() => import("./pages/Overview"));
const Governance = lazy(() => import("./pages/Governance"));
const Issues = lazy(() => import("./pages/Issues"));
const Monitoring = lazy(() => import("./pages/Monitoring"));
const Reports = lazy(() => import("./pages/Reports"));

const qc = new QueryClient({
  defaultOptions: { queries: { staleTime: 60_000, retry: 1, refetchOnWindowFocus: false } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <SessionProvider>
        <BrowserRouter>
          <Suspense fallback={<div className="p-6"><LoadingBlock /></div>}>
            <Routes>
              <Route element={<Layout />}>
                <Route index element={<Overview />} />
                <Route path="governance" element={<Governance />} />
                <Route path="issues" element={<Issues />} />
                <Route path="monitoring" element={<Monitoring />} />
                <Route path="reports" element={<Reports />} />
              </Route>
            </Routes>
          </Suspense>
        </BrowserRouter>
      </SessionProvider>
    </QueryClientProvider>
  </StrictMode>,
);
