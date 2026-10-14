"use client";

import React, { useState, useCallback } from "react";
import DynamicChart from "./DynamicChart";

interface Layout {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface ChartConfig {
  query_id: string;
  echarts_option: Record<string, unknown>;
  layout: Layout;
  title: string;
  subtitle: string;
  responsive: boolean;
}

interface FilterConfig {
  column: string;
  label: string;
  type: "select" | "date";
  values: string[];
}

interface DashboardGridProps {
  charts: ChartConfig[];
  sessionId: string;
  filters?: FilterConfig[];
  onSaveLayout?: (layouts: Record<string, Layout>) => void;
}

function colSpanClass(w: number): string {
  const map: Record<number, string> = {
    12: "col-span-12",
    6: "col-span-6",
    4: "col-span-4",
    3: "col-span-3",
  };
  return map[w] ?? "col-span-6";
}

export default function DashboardGrid({
  charts,
  sessionId,
  filters = [],
  onSaveLayout,
}: DashboardGridProps) {
  const [saved, setSaved] = useState(false);
  const [filterValues, setFilterValues] = useState<Record<string, string>>({});
  const [displayCharts, setDisplayCharts] = useState<ChartConfig[]>(charts);
  const [isFiltering, setIsFiltering] = useState(false);

  React.useEffect(() => {
    setDisplayCharts(charts);
  }, [charts]);

  const applyFilters = useCallback(async (newFilters: Record<string, string>) => {
    const active = Object.fromEntries(
      Object.entries(newFilters).filter(([_, v]) => v && v !== "all")
    );

    if (Object.keys(active).length === 0) {
      setDisplayCharts(charts);
      return;
    }

    setIsFiltering(true);
    try {
      const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
      const res = await fetch(`${API}/api/pipeline/${sessionId}/filter`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filters: active }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.charts && Array.isArray(data.charts) && data.charts.length > 0) {
          setDisplayCharts(data.charts);
        }
      }
    } catch (err) {
      console.error("Filter request failed:", err);
    } finally {
      setIsFiltering(false);
    }
  }, [charts, sessionId]);

  const handleSave = useCallback(async () => {
    try {
      const layouts: Record<string, Layout> = {};
      displayCharts.forEach((c) => { layouts[c.query_id] = c.layout; });
      if (onSaveLayout) onSaveLayout(layouts);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      console.error("Save layout failed", e);
    }
  }, [displayCharts, onSaveLayout]);

  const handleFilterChange = (col: string, val: string) => {
    const updated = { ...filterValues, [col]: val };
    setFilterValues(updated);
    applyFilters(updated);
  };

  const handleClearFilters = () => {
    setFilterValues({});
    setDisplayCharts(charts);
  };
  const hasActiveFilters = Object.values(filterValues).some((v) => v !== "" && v !== "all");

  // Sort charts by layout position (y then x)
  const sortedCharts = [...displayCharts].sort((a, b) => {
    const ay = a.layout?.y ?? 0;
    const by = b.layout?.y ?? 0;
    if (ay !== by) return ay - by;
    const ax = a.layout?.x ?? 0;
    const bx = b.layout?.x ?? 0;
    return ax - bx;
  });

  // Separate KPI cards from regular charts
  const kpiCharts = sortedCharts.filter((c) => {
    const opt = c.echarts_option as Record<string, unknown>;
    const chartType = String(opt._chart_type || opt.type || "bar");
    return chartType === "kpi_card";
  });

  const regularCharts = sortedCharts.filter((c) => {
    const opt = c.echarts_option as Record<string, unknown>;
    const chartType = String(opt._chart_type || opt.type || "bar");
    return chartType !== "kpi_card";
  });

  return (
    <div className="dashboard-grid-wrapper">
      {/* ── Filter Bar ──────────────────────────────────────────────── */}
      {filters.length > 0 && (
        <div className="filter-bar">
          <span className="filter-bar-label">Filter by</span>
          {filters.map((f) => {
            const uniqueValues = Array.from(new Set(f.values || []));
            return (
              <select
                key={f.column}
                className="filter-select"
                value={filterValues[f.column] || "all"}
                onChange={(e) => handleFilterChange(f.column, e.target.value)}
                aria-label={`Filter by ${f.label}`}
              >
                <option value="all">{f.label}: All</option>
                {uniqueValues.map((v) => (
                  <option key={`${f.column}-${v}`} value={v}>{v}</option>
                ))}
              </select>
            );
          })}
          {hasActiveFilters && (
            <button className="filter-clear-btn" onClick={handleClearFilters}>
              ✕ Clear
            </button>
          )}
        </div>
      )}

      {/* ── Controls row ─────────────────────────────────────────────── */}
      <div className="dashboard-grid-controls">
        <span className="grid-chart-count">
          {displayCharts.length} chart{displayCharts.length !== 1 ? "s" : ""}
          {kpiCharts.length > 0 && ` · ${kpiCharts.length} KPI${kpiCharts.length !== 1 ? "s" : ""}`}
          {isFiltering && <span style={{ color: "var(--abi-primary)", marginLeft: "8px" }}>⏳ Updating...</span>}
        </span>
        <button className="save-layout-btn" onClick={handleSave}>
          {saved ? "✓ Saved" : "Save Layout"}
        </button>
      </div>

      {/* ── KPI Row (always at top) ──────────────────────────────────── */}
      {kpiCharts.length > 0 && (
        <div className="kpi-row">
          {kpiCharts.slice(0, 4).map((chart, idx) => {
            const opt = chart.echarts_option as Record<string, unknown>;
            return (
              <DynamicChart
                key={`kpi-${chart.query_id}-${idx}`}
                echartsOption={opt}
                chartType="kpi_card"
                title={chart.title}
                queryId={chart.query_id}
                kpiIndex={idx}
              />
            );
          })}
        </div>
      )}

      {/* ── Main Charts Grid ─────────────────────────────────────────── */}
      {regularCharts.length > 0 && (
        <div className="dashboard-grid">
          {regularCharts.map((chart, idx) => {
            const opt = chart.echarts_option as Record<string, unknown>;
            const chartType = String(
              opt._chart_type ||
              (opt.type as string) ||
              ((opt.series as Array<{ type?: string }>)?.[0]?.type) ||
              "bar"
            );
            const insightSummary = String(opt._insight_summary || "");
            const widthClass = colSpanClass(chart.layout?.w ?? 6);

            return (
              <div
                key={`chart-${chart.query_id}-${idx}`}
                className={`dashboard-grid-item ${widthClass}`}
                id={`grid-item-${chart.query_id}`}
              >
                <DynamicChart
                  echartsOption={opt}
                  chartType={chartType}
                  title={chart.title}
                  subtitle={chart.subtitle}
                  insightSummary={insightSummary}
                  queryId={chart.query_id}
                />
              </div>
            );
          })}
        </div>
      )}

      {/* ── Empty state ─────────────────────────────────────────────── */}
      {charts.length === 0 && (
        <div className="no-charts">
          <div className="no-charts-icon">📊</div>
          <h3>No charts generated</h3>
          <p>The pipeline completed but did not produce any visualizations.</p>
        </div>
      )}
    </div>
  );
}