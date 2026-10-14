"use client";

import React, { useRef, useCallback, useState } from "react";
import ReactECharts from "echarts-for-react";
import * as echarts from "echarts";

interface DynamicChartProps {
  echartsOption: Record<string, unknown>;
  chartType: string;
  title?: string;
  subtitle?: string;
  insightSummary?: string;
  queryId?: string;
  kpiIndex?: number;
}

// AutoBI centralized chart palette — NOT LLM-generated
const AUTOBI_PALETTE = [
  "#4F6EF7", "#22C997", "#FF8C42", "#E05D6F", "#9B6DFF",
  "#20B2AA", "#F4C542", "#6EC6FF", "#FF6B9D", "#95DE64",
];

const CHART_HEIGHTS: Record<string, number> = {
  kpi_card:   120,
  data_table: 400,
  heatmap:    350,
  scatter:    300,
  pie:        300,
  bar:        300,
  line:       300,
  area:       300,
};

function formatKpiValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  const s = String(value).trim();
  if (s.includes("%") || s.includes("$") || s.endsWith("M") || s.endsWith("K") || s.endsWith("B")) {
    return s;
  }
  const num = parseFloat(s);
  if (isNaN(num)) return s || "—";
  if (num >= 1_000_000_000) return `${(num / 1_000_000_000).toFixed(1)}B`;
  if (num >= 1_000_000)     return `${(num / 1_000_000).toFixed(1)}M`;
  if (num >= 1_000)         return `${(num / 1_000).toFixed(1)}K`;
  return num % 1 === 0
    ? num.toLocaleString("en-IN")
    : num.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

// KPI accent colors per position
const KPI_COLORS = ["#4F6EF7", "#22C997", "#FF8C42", "#E05D6F"];

function KpiCard({
  option,
  title,
  kpiIndex = 0,
}: {
  option: Record<string, unknown>;
  title?: string;
  kpiIndex?: number;
}) {
  const color = KPI_COLORS[kpiIndex % KPI_COLORS.length];

  // Multi-metric KPI (from split_kpi_cards or raw)
  const metrics = option.metrics as Array<{ label: string; value: string; raw_value: unknown }> | undefined;
  if (metrics && metrics.length > 0) {
    if (metrics.length === 1) {
      const m = metrics[0];
      return (
        <div className="kpi-card" data-kpi-index={kpiIndex} style={{ borderTop: `3px solid ${color}` }}>
          <div className="kpi-label">{m.label}</div>
          <div className="kpi-value" style={{ color }}>{formatKpiValue(m.value ?? m.raw_value)}</div>
        </div>
      );
    }
    return (
      <div className="kpi-card" data-kpi-index={kpiIndex} style={{ borderTop: `3px solid ${color}` }}>
        <div className="kpi-multi-grid" style={{ display: "grid", gridTemplateColumns: `repeat(${Math.min(metrics.length, 3)}, 1fr)`, gap: "8px" }}>
          {metrics.map((m, i) => (
            <div key={i} className="kpi-sub-metric">
              <div className="kpi-label" style={{ fontSize: "0.75rem" }}>{m.label}</div>
              <div className="kpi-value" style={{ color: KPI_COLORS[(kpiIndex + i) % KPI_COLORS.length], fontSize: "1.25rem" }}>
                {formatKpiValue(m.value ?? m.raw_value)}
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  // Single-metric KPI
  const rawVal = option.value ?? option.raw_value;
  const val = formatKpiValue(rawVal);
  const label = (option.label as string) || title || "Metric";
  return (
    <div className="kpi-card" data-kpi-index={kpiIndex} style={{ borderTop: `3px solid ${color}` }}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value" style={{ color }}>{val}</div>
    </div>
  );
}

function DataTable({
  option,
  title,
}: {
  option: Record<string, unknown>;
  title?: string;
}) {
  const columns = (option.columns as string[]) ?? [];
  const rows    = (option.rows as Record<string, unknown>[]) ?? [];
  const [sortCol, setSortCol] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const sortedRows = React.useMemo(() => {
    if (!sortCol) return rows;
    return [...rows].sort((a, b) => {
      const av = a[sortCol] ?? "";
      const bv = b[sortCol] ?? "";
      const aNum = parseFloat(String(av));
      const bNum = parseFloat(String(bv));
      if (!isNaN(aNum) && !isNaN(bNum))
        return sortDir === "asc" ? aNum - bNum : bNum - aNum;
      return sortDir === "asc"
        ? String(av).localeCompare(String(bv))
        : String(bv).localeCompare(String(av));
    });
  }, [rows, sortCol, sortDir]);

  const handleSort = (col: string) => {
    if (sortCol === col) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortCol(col); setSortDir("asc"); }
  };

  return (
    <div className="data-table-wrapper">
      {title && <div className="data-table-title">{title}</div>}
      <div className="data-table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              {columns.map((col) => (
                <th key={col} onClick={() => handleSort(col)} className="data-table-th">
                  {col.replace(/_/g, " ")} {sortCol === col ? (sortDir === "asc" ? "↑" : "↓") : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sortedRows.map((row, i) => (
              <tr key={i} className="data-table-row">
                {columns.map((col) => (
                  <td key={col} className="data-table-td">
                    {String(row[col] ?? "—")}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="data-table-footer">{rows.length} rows</div>
    </div>
  );
}

export default function DynamicChart({
  echartsOption,
  chartType,
  title,
  subtitle,
  insightSummary,
  queryId,
  kpiIndex = 0,
}: DynamicChartProps) {
  const chartRef = useRef<echarts.ECharts | null>(null);
  const [error, setError]   = useState<string | null>(null);

  const onChartReady = useCallback((instance: echarts.ECharts) => {
    chartRef.current = instance;
  }, []);

  const handleDownload = () => {
    if (!chartRef.current) return;
    try {
      const url = chartRef.current.getDataURL({
        type: "png",
        pixelRatio: 2,
        backgroundColor: "#FFFFFF",
      });
      const a = document.createElement("a");
      a.href = url;
      a.download = `chart-${queryId ?? "export"}.png`;
      a.click();
    } catch (e) {
      console.error("Export PNG failed", e);
    }
  };

  const height = CHART_HEIGHTS[chartType] ?? 300;

  // ── KPI Card ─────────────────────────────────────────────────────────────
  if (chartType === "kpi_card") {
    return (
      <KpiCard
        option={echartsOption}
        title={title}
        kpiIndex={kpiIndex}
      />
    );
  }

  // ── Data Table ───────────────────────────────────────────────────────────
  if (chartType === "data_table") {
    return (
      <div className="chart-card">
        <DataTable option={echartsOption} title={title} />
        {insightSummary && (
          <div className="chart-insight">
            <span className="chart-insight-icon">💡</span>
            {insightSummary}
          </div>
        )}
      </div>
    );
  }

  // ── Error state ───────────────────────────────────────────────────────────
  if (error) {
    return (
      <div className="chart-card chart-error">
        <div className="chart-error-icon">⚠️</div>
        <div className="chart-error-msg">Visualization could not be rendered</div>
        <div className="chart-error-detail">{error}</div>
      </div>
    );
  }

  // ── Empty state if series data is missing or empty ─────────────────────────
  const series = (echartsOption.series as Array<{ data?: unknown[] }>) ?? [];
  const hasData = Array.isArray(series) && series.length > 0 && series.some((s) => Array.isArray(s.data) && s.data.length > 0);
  if (!hasData) {
    return (
      <div className="chart-card" id={`chart-${queryId ?? "unknown"}`}>
        <div className="chart-header">
          <div className="chart-meta">
            {title    && <div className="chart-title">{title}</div>}
            {subtitle && <div className="chart-subtitle">{subtitle}</div>}
          </div>
        </div>
        <div
          className="chart-empty-state"
          style={{
            height: `${height}px`,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            color: "#64748B",
            backgroundColor: "#F8FAFC",
            borderRadius: "6px",
            margin: "8px 0"
          }}
        >
          <div style={{ fontSize: "2rem", marginBottom: "8px" }}>📊</div>
          <div style={{ fontWeight: 600, color: "#1E293B" }}>No plottable data</div>
          <div style={{ fontSize: "0.85rem", color: "#64748B", marginTop: "4px" }}>Query returned no records to visualize</div>
        </div>
        {insightSummary && (
          <div className="chart-insight">
            <span className="chart-insight-icon">💡</span>
            {insightSummary}
          </div>
        )}
      </div>
    );
  }

  // ── Build merged ECharts option with AutoBI light theme ──────────────────
  const isCartesian = !["pie", "radar", "gauge", "funnel"].includes(chartType);
  const cartesianDefaults = isCartesian ? {
    grid: {
      left: "3%",
      right: "4%",
      bottom: "10%",
      top: "12%",
      containLabel: true,
    },
    xAxis: {
      type: "category",
      axisLine: { lineStyle: { color: "#E2E8F0" } },
      axisTick: { lineStyle: { color: "#E2E8F0" } },
      axisLabel: { color: "#64748B" },
      splitLine: { lineStyle: { color: "#F1F5F9" } },
    },
    yAxis: {
      type: "value",
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: "#64748B" },
      splitLine: { lineStyle: { color: "#F1F5F9", type: "dashed" } },
    },
  } : {};

  const mergedOption: Record<string, unknown> = {
    backgroundColor: "transparent",
    textStyle: { color: "#1E293B", fontFamily: "Inter, system-ui, sans-serif", fontSize: 12 },
    tooltip: {
      trigger: chartType === "pie" ? "item" : "axis",
      backgroundColor: "#FFFFFF",
      borderColor: "#E2E8F0",
      borderWidth: 1,
      textStyle: { color: "#1E293B" },
      extraCssText: "box-shadow: 0 4px 12px rgba(0,0,0,0.08); border-radius: 8px;",
    },
    legend: {
      textStyle: { color: "#64748B" },
      icon: "circle",
    },
    ...cartesianDefaults,
    ...echartsOption,
    // Hide internal title to prevent duplicate text overlapping the chart axis
    title: { show: false },
    // Enforce AutoBI palette — overrides any LLM-generated colors
    color: AUTOBI_PALETTE,
  };

  if (!isCartesian) {
    delete mergedOption.xAxis;
    delete mergedOption.yAxis;
  }

  return (
    <div className="chart-card" id={`chart-${queryId ?? "unknown"}`}>
      <div className="chart-header">
        <div className="chart-meta">
          {title    && <div className="chart-title">{title}</div>}
          {subtitle && <div className="chart-subtitle">{subtitle}</div>}
        </div>
        <button className="chart-download-btn" onClick={handleDownload} title="Download as PNG">
          ↓ PNG
        </button>
      </div>
      <ReactECharts
        option={mergedOption}
        style={{ height: `${height}px`, width: "100%" }}
        onChartReady={onChartReady}
        onEvents={{
          error: (e: Error) => setError(String(e)),
        }}
        opts={{ renderer: "canvas" }}
        notMerge={true}
      />
      {insightSummary && (
        <div className="chart-insight">
          <span className="chart-insight-icon">💡</span>
          {insightSummary}
        </div>
      )}
    </div>
  );
}