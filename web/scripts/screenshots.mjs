// Screenshot review loop: every page at 1440px and 1024px, light and dark.
// Usage: node scripts/screenshots.mjs [outDir] [pages...]   (dev server on :5173, API on :8000)
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const out = process.argv[2] ?? "../docs/screenshots";
const only = process.argv.slice(3);
const base = process.env.BASE_URL ?? "http://localhost:5173";
const pages = [
  ["overview", "/"],
  ["governance-scorecard", "/governance?tab=scorecard"],
  ["governance-catalog", "/governance?tab=catalog"],
  ["governance-cde", "/governance?tab=cde"],
  ["governance-reconciliation", "/governance?tab=reconciliation"],
  ["governance-lineage", "/governance?tab=lineage"],
  ["governance-suggestions", "/governance?tab=suggestions"],
  ["issues", "/issues"],
  ["monitoring", "/monitoring"],
  ["monitoring-alert", "/monitoring?alert=ALT-0001196"],
  ["monitoring-rules", "/monitoring?tab=rules"],
  ["monitoring-audit", "/monitoring?tab=audit"],
  ["reports", "/reports"],
].filter(([n]) => only.length === 0 || only.includes(n));

mkdirSync(out, { recursive: true });
const browser = await chromium.launch();
for (const theme of ["light", "dark"]) {
  for (const width of [1440, 1024]) {
    const ctx = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: theme });
    await ctx.addInitScript((t) => {
      localStorage.setItem("verity-theme", t);
      localStorage.setItem("verity-user", "neha");
    }, theme);
    const page = await ctx.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    for (const [name, path] of pages) {
      await page.goto(base + path, { waitUntil: "networkidle" });
      await page.waitForTimeout(700);
      const file = `${out}/${name}-${width}-${theme}.png`;
      await page.screenshot({ path: file, fullPage: false });
      console.log(file, errors.length ? `ERRORS: ${errors.join(" | ")}` : "");
      errors.length = 0;
    }
    if (only.length === 0 || only.includes("command-palette")) {
      await page.goto(base + "/", { waitUntil: "networkidle" });
      await page.keyboard.press("Control+k");
      await page.keyboard.type("alt-1196");
      await page.waitForTimeout(400);
      const file = `${out}/command-palette-${width}-${theme}.png`;
      await page.screenshot({ path: file });
      console.log(file);
    }
    await ctx.close();
  }
}
await browser.close();
