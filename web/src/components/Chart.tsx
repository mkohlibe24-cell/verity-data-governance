import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useMemo } from "react";
import { useReducedMotion } from "motion/react";
import { useSession } from "../lib/session";

/** Read the current design tokens so charts follow light/dark mode. */
export function useChartTheme() {
  const { theme } = useSession();
  return useMemo(() => {
    const css = getComputedStyle(document.documentElement);
    const v = (n: string) => css.getPropertyValue(n).trim();
    return {
      text: v("--text"),
      muted: v("--text-muted"),
      grid: v("--chart-grid"),
      border: v("--border"),
      surface: v("--surface"),
      series: [v("--chart-1"), v("--chart-2"), v("--chart-3"), v("--chart-4")],
      green: v("--green"),
      amber: v("--amber"),
      red: v("--red"),
      accent: v("--accent"),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme]);
}

export function baseOption(t: ReturnType<typeof useChartTheme>): EChartsOption {
  return {
    textStyle: { fontFamily: "IBM Plex Sans, sans-serif", color: t.muted, fontSize: 12 },
    grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      backgroundColor: t.surface,
      borderColor: t.border,
      textStyle: { color: t.text, fontSize: 12 },
      extraCssText: "box-shadow:none;border-radius:6px;",
    },
    legend: { top: 0, left: 0, icon: "roundRect", itemWidth: 10, itemHeight: 10, textStyle: { color: t.muted } },
  };
}

export function axis(t: ReturnType<typeof useChartTheme>) {
  return {
    axisLine: { lineStyle: { color: t.border } },
    axisTick: { show: false },
    axisLabel: { color: t.muted },
    splitLine: { lineStyle: { color: t.grid } },
  };
}

export function Chart({ option, height = 260, label, onEvents }: {
  option: EChartsOption; height?: number; label: string; onEvents?: Record<string, (p: unknown) => void>;
}) {
  const reduce = useReducedMotion();
  return (
    <div role="img" aria-label={label}>
      <ReactECharts
        option={{ animation: !reduce, animationDuration: 250, ...option }}
        style={{ height, width: "100%" }}
        notMerge
        lazyUpdate
        onEvents={onEvents}
      />
    </div>
  );
}
