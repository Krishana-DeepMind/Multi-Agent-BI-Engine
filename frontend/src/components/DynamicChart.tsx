"use client";

import React, { useRef, useCallback, useState } from "react";
import dynamic from "next/dynamic";
import * as echarts from "echarts";

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });

interface DynamicChartProps {
  echartsOption: Record<string, unknown>;
  chartType: string;
  title?: string;
  subtitle?: string;
  insightSummary?: string;
  queryId?: string;
  kpiIndex?: number;
}

// ── AutoBI Coordinated Accessible Chart Palette ────────────────────────────
// Crimson for primary / highlights, Teal for secondary / benchmarks,
// Blue for neutral comparisons, Purple for categories, Amber for warnings,
// Green for positive outcomes, Slate for neutral / missing / unknown.
export const AUTOBI_PALETTE = [
  "#991B1B", // 0. Primary Series / Enterprise Crimson
  "#0D9488", // 1. Secondary Series / Teal
  "#2563EB", // 2. Neutral Comparisons / Cobalt Blue
  "#7C3AED", // 3. Demographics & Categories / Purple
  "#D97706", // 4. Warnings & Notable Variations / Amber
  "#16A34A", // 5. Positive Outcomes / Emerald Green
  "#64748B", // 6. Contextual / Neutral Slate Grey
  "#BE123C", // 7. Rose
  "#0891B2", // 8. Cyan
  "#4F46E5", // 9. Indigo
];

// KPI accent colors per position — coordinated and restrained
const KPI_COLORS = ["#991B1B", "#0D9488", "#2563EB", "#7C3AED"];

// Intentional category color mappings for demographic and status dimensions
const CATEGORY_COLORS: Record<string, string> = {
  // Genders & Demographics
  female: "#0D9488",              // Teal
  male: "#2563EB",                // Blue
  "non-binary": "#7C3AED",        // Purple
  "non binary": "#7C3AED",        // Purple
  other: "#D97706",               // Amber
  "prefer not to say": "#8B5CF6", // Soft Violet
  unknown: "#94A3B8",             // Neutral Slate Grey
  // Statuses & Outcomes
  active: "#16A34A",
  inactive: "#64748B",
  resigned: "#D97706",
  terminated: "#991B1B",
  "on leave": "#2563EB",
  pending: "#F59E0B",
  completed: "#16A34A",
};

const CHART_HEIGHTS: Record<string, number> = {
  kpi_card:   120,
  data_table: 400,
  heatmap:    350,
  scatter:    320,
  pie:        300,
  donut:      300,
  histogram:  320,
  bar:        320,
  line:       320,
  area:       320,
};

function formatKpiValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  const s = String(value).trim();
  if (s.includes("%") || s.includes("$") || s.includes("€") || s.includes("£") || s.includes("₹") || s.includes("¥") || s.endsWith("M") || s.endsWith("K") || s.endsWith("B")) {
    return s;
  }
  const num = parseFloat(s);
  if (isNaN(num)) return s || "—";
  if (num === 0) return "0";
  if (num >= 1_000_000_000) return `${(num / 1_000_000_000).toFixed(1)}B`;
  if (num >= 1_000_000)     return `${(num / 1_000_000).toFixed(1)}M`;
  if (num >= 1_000)         return `${(num / 1_000).toFixed(1)}K`;
  return num % 1 === 0
    ? num.toLocaleString("en-US")
    : num.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

interface UnitInfo {
  unit: string;
  isPercentage: boolean;
  isMonetary: boolean;
}

function inferSeriesUnit(seriesName: string, title?: string, axisName?: string): UnitInfo {
  const sName = seriesName || "";
  const axName = axisName || "";
  const tName = title || "";

  // 0. Check dimensionless statistics & z-scores FIRST
  const isDimensionless = (
    /z[-_\s\u2010-\u2015]?score|correlation|p[-_\s]?value|multiplier|std[-_\s]?dev|deviation/i.test(sName) ||
    /z[-_\s\u2010-\u2015]?score|correlation|p[-_\s]?value|multiplier|std[-_\s]?dev|deviation/i.test(axName) ||
    /z[-_\s\u2010-\u2015]?score|correlation|p[-_\s]?value|multiplier|std[-_\s]?dev|deviation/i.test(tName)
  );
  if (isDimensionless) {
    return { unit: "", isPercentage: false, isMonetary: false };
  }

  // Count/headcount/volume/frequency check
  const hasCurrencySymbol = sName.includes("$") || sName.includes("€") || sName.includes("£") || sName.includes("₹") || sName.includes("¥") ||
    axName.includes("$") || axName.includes("€") || axName.includes("£") || axName.includes("₹") || axName.includes("¥");

  const hasPercentSymbol = sName.includes("%") || axName.includes("%") || tName.includes("%") ||
    /\b(pct|percent|percentage)\b/i.test(sName) || /\b(pct|percent|percentage)\b/i.test(axName);

  // 1. Explicit Currency Check FIRST (e.g. "Gross Margin ($K)", "Revenue ($M)")
  // If an explicit currency symbol or bracketed currency code is present and NO percent symbol, it is strictly monetary.
  if (hasCurrencySymbol && !hasPercentSymbol) {
    const fullText = (sName + " " + axName + " " + tName).toLowerCase();
    const sym = fullText.includes("€") || fullText.includes("eur") ? "€"
      : fullText.includes("£") || fullText.includes("gbp") ? "£"
      : fullText.includes("₹") || fullText.includes("inr") ? "₹"
      : fullText.includes("¥") || fullText.includes("jpy") || fullText.includes("cny") ? "¥"
      : "$";
    return { unit: sym, isPercentage: false, isMonetary: true };
  }

  const isCount = (
    /\b(count|headcount|records?|employees?|orders?|items?|frequency|volume|cases|tenure|age|quantity|units?|downloads?|clicks?|views?|visits?|subscribers?)\b/i.test(sName) ||
    /\b(count|headcount|records?|employees?|orders?|items?|frequency|volume|cases|tenure|age|quantity|units?|downloads?|clicks?|views?|visits?|subscribers?)\b/i.test(axName)
  ) && !hasCurrencySymbol;
  if (isCount) {
    return { unit: "", isPercentage: false, isMonetary: false };
  }

  // 2. Percentage / Margin / Rate / Share / Ratio check (only when not monetary)
  const isPercentage = (
    hasPercentSymbol ||
    /\b(margin|rate|ratio|pct|percent|percentage|share|yield|conversion|churn|attrition|discount)\b/i.test(sName) ||
    /\b(margin|rate|ratio|pct|percent|percentage|share|yield|conversion|churn|attrition|discount)\b/i.test(axName) ||
    /\b(margin|rate|percentage|share|ratio)\b/i.test(tName)
  );
  if (isPercentage) {
    return { unit: "%", isPercentage: true, isMonetary: false };
  }

  // 3. Monetary words in series or axis name without symbol (e.g. "salary", "revenue", "spend")
  const isMonetary = (
    /\b(usd|eur|gbp|inr|jpy|cny)\b/i.test(sName) ||
    /\b(usd|eur|gbp|inr|jpy|cny)\b/i.test(axName) ||
    /\b(salary|salaries|revenue|revenues|costs?|prices?|compensation|spend|budget|bills?|billing|billed|fees?|charges?|wages?|bonuses?|payroll|sales|profits?|paid|arr|mrr|ebitda)\b/i.test(sName) ||
    /\b(salary|salaries|revenue|revenues|costs?|prices?|compensation|spend|budget|bills?|billing|billed|fees?|charges?|wages?|bonuses?|payroll|sales|profits?|paid|arr|mrr|ebitda)\b/i.test(axName) ||
    /\b(salary|salaries|revenue|revenues|costs?|prices?|compensation|spend|budget|bills?|billing|billed|fees?|charges?|wages?|bonuses?|payroll|sales|profits?|paid|arr|mrr|ebitda)\b/i.test(tName)
  );
  if (isMonetary) {
    const fullText = (sName + " " + axName + " " + tName).toLowerCase();
    const sym = fullText.includes("€") || fullText.includes("eur") ? "€"
      : fullText.includes("£") || fullText.includes("gbp") ? "£"
      : fullText.includes("₹") || fullText.includes("inr") ? "₹"
      : fullText.includes("¥") || fullText.includes("jpy") || fullText.includes("cny") ? "¥"
      : "$";
    return { unit: sym, isPercentage: false, isMonetary: true };
  }

  // 3. Parenthetical or bracketed unit: e.g. "Average CO2 (ppm)", "Pressure (hPa)", "Temp (°C)"
  const bracketMatch = (sName + " " + axName + " " + tName).match(/[\(\[]([A-Za-z0-9°µ$%€£/_-]+)[\)\]]/);
  if (bracketMatch && bracketMatch[1]) {
    const u = bracketMatch[1].trim();
    if (u.toLowerCase() === "pct" || u.toLowerCase() === "percent" || u === "%") {
      return { unit: "%", isPercentage: true, isMonetary: false };
    }
    return { unit: u, isPercentage: false, isMonetary: false };
  }

  // 4. Underscore or space suffix in series name: e.g. co2_ppm -> ppm
  const suffixMatch = sName.toLowerCase().match(/(?:_|\b)([a-zA-Z]{1,5}|°[cCfF])$/);
  if (suffixMatch && suffixMatch[1]) {
    const s = suffixMatch[1];
    const knownUnits: Record<string, string> = {
      ppm: "ppm", ppb: "ppb", hpa: "hPa", kpa: "kPa", pa: "Pa",
      c: "°C", f: "°F", kelvin: "K", kg: "kg", g: "g", mg: "mg",
      lbs: "lbs", oz: "oz", km: "km", ms: "ms", sec: "s", s: "s",
      min: "min", hr: "hr", hrs: "hrs", days: "days",
      hz: "Hz", khz: "kHz", mhz: "MHz", bpm: "bpm", rpm: "rpm", db: "dB", aqi: "AQI"
    };
    if (knownUnits[s]) {
      return { unit: knownUnits[s], isPercentage: false, isMonetary: false };
    }
  }

  return { unit: "", isPercentage: false, isMonetary: false };
}

function formatValueWithUnit(val: number, unitInfo: UnitInfo): string {
  if (isNaN(val)) return "0";
  if (unitInfo.isPercentage) {
    const displayVal = (Math.abs(val) <= 1.5 && Math.abs(val) > 0.0001) ? val * 100 : val;
    return `${displayVal.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
  }
  if (unitInfo.isMonetary) {
    const sign = val < 0 ? "-" : "";
    const absVal = Math.abs(val);
    if (absVal >= 1_000_000) return `${sign}${unitInfo.unit}${(absVal / 1_000_000).toFixed(1)}M`;
    if (absVal >= 1_000) return `${sign}${unitInfo.unit}${(absVal / 1_000).toFixed(0)}K`;
    return `${sign}${unitInfo.unit}${absVal.toLocaleString()}`;
  }
  let baseStr = "";
  const sign = val < 0 ? "-" : "";
  const absVal = Math.abs(val);
  if (absVal >= 1_000_000) {
    baseStr = `${sign}${(absVal / 1_000_000).toFixed(1)}M`;
  } else if (absVal >= 1_000) {
    baseStr = `${sign}${(absVal / 1_000).toFixed(0)}K`;
  } else {
    baseStr = absVal % 1 === 0 ? `${sign}${absVal.toLocaleString()}` : `${sign}${absVal < 10 ? absVal.toFixed(2) : absVal.toFixed(1)}`;
  }
  return unitInfo.unit ? `${baseStr} ${unitInfo.unit}` : baseStr;
}

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
  const sqlQuery = (option.sql_query || option.sql || option.query) as string | undefined;

  // Multi-metric KPI (from split_kpi_cards or raw)
  const metrics = option.metrics as Array<{ label: string; value: string; raw_value: unknown; sql?: string }> | undefined;
  if (metrics && metrics.length > 0) {
    if (metrics.length === 1) {
      const m = metrics[0];
      const mSql = m.sql || sqlQuery;
      return (
        <div className="kpi-card" data-kpi-index={kpiIndex} style={{ borderTop: `2px solid ${color}` }} title={mSql ? `SQL: ${mSql}` : undefined}>
          <div className="kpi-label">{m.label}</div>
          <div className="kpi-value">{formatKpiValue(m.value ?? m.raw_value)}</div>
          {mSql && (
            <div className="kpi-sql text-xs text-muted-foreground/60 font-mono mt-1 truncate" style={{ fontSize: "0.68rem" }} title={mSql}>
              {mSql}
            </div>
          )}
        </div>
      );
    }
    return (
      <div className="kpi-card" data-kpi-index={kpiIndex} style={{ borderTop: `2px solid ${color}` }}>
        <div className="kpi-multi-grid" style={{ display: "grid", gridTemplateColumns: `repeat(${Math.min(metrics.length, 3)}, 1fr)`, gap: "8px" }}>
          {metrics.map((m, i) => (
            <div key={i} className="kpi-sub-metric" title={m.sql || sqlQuery ? `SQL: ${m.sql || sqlQuery}` : undefined}>
              <div className="kpi-label" style={{ fontSize: "0.75rem" }}>{m.label}</div>
              <div className="kpi-value" style={{ fontSize: "1.25rem" }}>
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
  const isAvailable = rawVal !== undefined && rawVal !== null && String(rawVal).trim() !== "";
  const val = isAvailable ? formatKpiValue(rawVal) : "—";
  const label = (option.label as string) || title || "Metric";
  return (
    <div className="kpi-card" data-kpi-index={kpiIndex} style={{ borderTop: `2px solid ${color}` }} title={sqlQuery ? `SQL: ${sqlQuery}` : undefined}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{val}</div>
      {sqlQuery && (
        <div className="kpi-sql text-xs text-muted-foreground/60 font-mono mt-1 truncate" style={{ fontSize: "0.68rem" }} title={sqlQuery}>
          {sqlQuery}
        </div>
      )}
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
            {sortedRows.length === 0 ? (
              <tr>
                <td colSpan={columns.length || 1} className="data-table-td" style={{ textAlign: "center", color: "#64748B", padding: "28px 12px" }}>
                  <div style={{ fontWeight: 600, color: "#0F172A", marginBottom: "4px" }}>0 Rows Returned</div>
                  <div style={{ fontSize: "0.8rem", color: "#94A3B8" }}>
                    Query executed successfully against dataset schema, but found no qualifying records for current criteria.
                  </div>
                </td>
              </tr>
            ) : (
              sortedRows.map((row, i) => (
                <tr key={i} className="data-table-row">
                  {columns.map((col) => (
                    <td key={col} className="data-table-td">
                      {String(row[col] ?? "—")}
                    </td>
                  ))}
                </tr>
              ))
            )}
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
  const [error, setError] = useState<string | null>(null);
  const [userSelectedType, setUserSelectedType] = useState<string>(chartType);

  // Sync userSelectedType when chartType prop updates
  React.useEffect(() => {
    setUserSelectedType(chartType);
  }, [chartType]);

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

  // ── Extract categories and values for robust rendering and conversion ─────
  const extracted = React.useMemo(() => {
    let cats: string[] = [];
    let vals: Array<{ name: string; value: number }> = [];
    let seriesName = "Value";

    // 1. Resolve xAxis: could be object or array of objects
    const xAxisRaw = echartsOption.xAxis;
    const xAxis = Array.isArray(xAxisRaw)
      ? (xAxisRaw[0] as { data?: string[]; type?: string; name?: string } | undefined)
      : (xAxisRaw as { data?: string[]; type?: string; name?: string } | undefined);

    // 2. Resolve yAxis: could be object or array of objects (for horizontal charts)
    const yAxisRaw = echartsOption.yAxis;
    const yAxis = Array.isArray(yAxisRaw)
      ? (yAxisRaw[0] as { data?: string[]; type?: string; name?: string } | undefined)
      : (yAxisRaw as { data?: string[]; type?: string; name?: string } | undefined);

    const seriesList = (echartsOption.series as Array<{ name?: string; data?: unknown[]; type?: string; yAxisIndex?: number }>) ?? [];

    if (seriesList[0] && seriesList[0].name) {
      seriesName = seriesList[0].name;
    }

    const rawData = seriesList[0]?.data ?? [];

    // Find category axis if either xAxis or yAxis has category data
    const categoryAxis = (xAxis && Array.isArray(xAxis.data) && xAxis.data.length > 0)
      ? xAxis
      : (yAxis && Array.isArray(yAxis.data) && yAxis.data.length > 0)
      ? yAxis
      : null;

    if (categoryAxis && Array.isArray(categoryAxis.data) && categoryAxis.data.length > 0) {
      cats = categoryAxis.data.map(String);
      vals = cats.map((c, i) => {
        const d = rawData[i];
        let num = 0;
        if (typeof d === "number") {
          num = d;
        } else if (Array.isArray(d)) {
          num = Number(d[1] ?? d[0] ?? 0);
        } else if (typeof d === "object" && d !== null && "value" in d) {
          num = Number((d as { value: unknown }).value);
        } else {
          num = parseFloat(String(d ?? 0));
        }
        return { name: c, value: isNaN(num) ? 0 : num };
      });
    } else if (Array.isArray(rawData) && rawData.length > 0) {
      // Handles pairs [x, y], {name, value}, or numbers
      vals = rawData.map((d, i) => {
        if (Array.isArray(d)) {
          const label = d[0] !== undefined && d[0] !== null && String(d[0]).trim() !== ""
            ? String(d[0])
            : `Record ${i + 1}`;
          return { name: label, value: Number(d[1] ?? 0) };
        }
        if (typeof d === "object" && d !== null && "name" in d) {
          const item = d as { name: string; value: number };
          const label = item.name !== undefined && item.name !== null && String(item.name).trim() !== ""
            ? String(item.name)
            : `Record ${i + 1}`;
          return { name: label, value: Number(item.value ?? 0) };
        }
        if (typeof d === "number") {
          return { name: `Record ${i + 1}`, value: d };
        }
        return { name: "", value: 0 };
      }).filter((v) => v.name !== "");
      cats = vals.map((v) => v.name);
    }

    return { categories: cats, values: vals, seriesName, rawSeriesList: seriesList };
  }, [echartsOption]);

  // ── Compatible chart types for switcher ──────────────────────────────────
  const fullContextText = `${title || ""} ${subtitle || ""} ${extracted.seriesName || ""} ${queryId || ""}`.toLowerCase();
  // Only block donut/pie for metrics that are truly non-summable (pure averages/rates, not distribution questions)
  const isAverageOrRateMetric = /\b(avg|average|mean|median|readmission_rate|attrition_rate_pct|pct_of_|z_score|std_dev|per_employee|per_capita)\b/i.test(fullContextText) ||
    /\b(rate\s+pct|rate_pct|rate\s+by|ratio\s+pct)\b/i.test(fullContextText);

  // Auto-revert from misleading donut/pie if metric is an average, rate, or percentage
  const activeType = (isAverageOrRateMetric && (userSelectedType === "donut" || userSelectedType === "pie"))
    ? (chartType !== "donut" && chartType !== "pie" ? chartType : "bar")
    : userSelectedType;

  const compatibleTypes = React.useMemo(() => {
    const base = chartType.toLowerCase();
    const list: Array<{ type: string; label: string }> = [];
    const hasNegative = extracted.values.some((v) => v.value < 0);
    const sumVal = extracted.values.reduce((s, v) => s + v.value, 0);

    if (["bar", "line", "area", "pie", "donut", "histogram"].includes(base)) {
      list.push({ type: "bar", label: "Bar" });
      list.push({ type: "line", label: "Line" });
      list.push({ type: "area", label: "Area" });
      if (
        extracted.categories.length >= 2 &&
        extracted.categories.length <= 10 &&
        !hasNegative &&
        sumVal > 0 &&
        !isAverageOrRateMetric
      ) {
        list.push({ type: "donut", label: "Donut" });
        list.push({ type: "pie", label: "Pie" });
      }
      list.push({ type: "data_table", label: "Table" });
    } else if (base === "scatter") {
      list.push({ type: "scatter", label: "Scatter" });
      list.push({ type: "bar", label: "Bar" });
      list.push({ type: "data_table", label: "Table" });
    } else if (base === "heatmap") {
      list.push({ type: "heatmap", label: "Heatmap" });
      list.push({ type: "bar", label: "Bar" });
      list.push({ type: "data_table", label: "Table" });
    } else if (base === "data_table") {
      list.push({ type: "data_table", label: "Table" });
      if (extracted.categories.length > 0) {
        list.push({ type: "bar", label: "Bar" });
      }
    }
    return list;
  }, [chartType, extracted.categories.length, extracted.values, isAverageOrRateMetric]);

  const height = CHART_HEIGHTS[activeType] ?? 300;

  // ── KPI Card ─────────────────────────────────────────────────────────────
  const isKpiCard = (
    chartType === "kpi_card" ||
    activeType === "kpi_card" ||
    (echartsOption as any).chart_type === "kpi_card" ||
    (echartsOption as any)._chart_type === "kpi_card" ||
    (echartsOption as any).type === "kpi_card" ||
    ((echartsOption.value !== undefined || (echartsOption as any).raw_value !== undefined) && !Array.isArray(echartsOption.series)) ||
    ((echartsOption as any).metrics !== undefined && !Array.isArray(echartsOption.series))
  );

  if (isKpiCard) {
    return (
      <KpiCard
        option={echartsOption}
        title={title}
        kpiIndex={kpiIndex}
      />
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

  // ── Empty state if series data is missing, empty, or flat 0.0 ──────────────
  const series = (echartsOption.series as Array<{ data?: unknown[] }>) ?? [];
  const hasData = Array.isArray(series) && series.length > 0 && series.some((s) => Array.isArray(s.data) && s.data.length > 0);
  const isAllZero = hasData && series.every((s) =>
    Array.isArray(s.data) && s.data.length > 0 && s.data.every((item) => {
      const v = typeof item === "object" && item !== null && "value" in item ? (item as any).value : item;
      return typeof v === "number" && v === 0;
    })
  );

  if ((!hasData || isAllZero) && chartType !== "data_table") {
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
            color: "#71717A",
            backgroundColor: "#F8F9FA",
            borderRadius: "6px",
            border: "1px dashed #E4E4E7",
            margin: "8px 0"
          }}
        >
          <div style={{ fontSize: "1.1rem", fontWeight: 700, color: isAllZero ? "#64748B" : "#991B1B", marginBottom: "6px" }}>
            {isAllZero ? "0.0% Baseline" : "0 Records"}
          </div>
          <div style={{ fontWeight: 600, color: "#18181B", fontSize: "0.85rem" }}>
            {isAllZero ? "All observed categories have 0.0 value" : "No qualifying records found"}
          </div>
          <div style={{ fontSize: "0.78rem", color: "#71717A", marginTop: "4px", maxWidth: "80%", textAlign: "center" }}>
            {insightSummary || (isAllZero ? "No variance observed in dataset for this metric" : "Query returned no matching records to display")}
          </div>
        </div>
        {insightSummary && (
          <div className="chart-insight">
            <span className="chart-insight-badge">INSIGHT</span>
            <span>{insightSummary}</span>
          </div>
        )}
      </div>
    );
  }

  // ── Render Data Table if activeType is data_table ────────────────────────
  if (activeType === "data_table") {
    const existingCols = (echartsOption.columns as string[]) ?? [];
    const existingRows = (echartsOption.rows as Record<string, unknown>[]) ?? [];
    const tableOption = (existingCols.length > 0)
      ? { columns: existingCols, rows: existingRows }
      : {
          columns: ["Dimension", extracted.seriesName],
          rows: extracted.values.map((v) => ({
            Dimension: v.name,
            [extracted.seriesName]: v.value,
          })),
        };
    return (
      <div className="chart-card" id={`chart-${queryId ?? "unknown"}`}>
        <div className="chart-header">
          <div className="chart-meta">
            {title    && <div className="chart-title">{title}</div>}
            {subtitle && <div className="chart-subtitle">{subtitle}</div>}
          </div>
          <div className="chart-actions">
            {compatibleTypes.length > 1 && (
              <div className="chart-type-switcher">
                {compatibleTypes.map((t) => (
                  <button
                    key={t.type}
                    className={`switcher-btn ${activeType === t.type ? "active" : ""}`}
                    onClick={() => setUserSelectedType(t.type)}
                    title={`Switch to ${t.label}`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            )}
            <button className="chart-download-btn" onClick={handleDownload} title="Download as PNG">
              ↓ PNG
            </button>
          </div>
        </div>
        <DataTable option={tableOption} />
        {insightSummary && (
          <div className="chart-insight">
            <span className="chart-insight-badge">INSIGHT</span>
            <span>{insightSummary}</span>
          </div>
        )}
      </div>
    );
  }

  // ── Identify Analytical Metric Context ───────────────────────────────────
  const rawYAxis = echartsOption.yAxis;
  const firstYAxisName = Array.isArray(rawYAxis)
    ? (rawYAxis[0] as any)?.name
    : (rawYAxis as any)?.name;

  const primaryUnit = inferSeriesUnit(extracted.seriesName, title, firstYAxisName);

  const metaText = `${title ?? ""} ${subtitle ?? ""} ${extracted.seriesName} ${queryId ?? ""}`.toLowerCase();
  const isComparisonBenchmark = metaText.includes("vs company average") ||
    metaText.includes("pct_of_company_avg") ||
    metaText.includes("vs overall average") ||
    metaText.includes("vs average") ||
    metaText.includes("benchmark");

  const isPieOrDonut = ["pie", "donut"].includes(activeType);
  const isScatter = activeType === "scatter";
  const isCartesian = !isPieOrDonut && !isScatter;

  // ── Construct Intentional Series Data & Colors ────────────────────────────
  const avgBenchmarkVal = extracted.values.length > 0
    ? extracted.values.reduce((s, x) => s + x.value, 0) / extracted.values.length
    : 0;

  const styledValues = extracted.values.map((v, idx) => {
    const catLower = v.name.trim().toLowerCase();
    const catColor = CATEGORY_COLORS[catLower];
    const itemColor = catColor || (
      isComparisonBenchmark
        ? (v.value >= avgBenchmarkVal ? "#0D9488" : "#2563EB")
        : AUTOBI_PALETTE[idx % AUTOBI_PALETTE.length]
    );

    return {
      name: v.name,
      value: v.value,
      itemStyle: {
        color: itemColor,
        borderRadius: activeType === "bar" ? [4, 4, 0, 0] : undefined,
      },
    };
  });

  // Reference lines for comparison charts (grounded in actual units and values)
  let markLine: Record<string, unknown> | undefined = undefined;
  if (isComparisonBenchmark) {
    const backendMarkLine = (extracted.rawSeriesList[0] as any)?.markLine;
    if (backendMarkLine) {
      markLine = backendMarkLine;
    } else if (extracted.values.length > 0) {
      const seriesLower = (extracted.seriesName || "").toLowerCase();
      const titleLower = (title || "").toLowerCase();
      const hasNegativeVals = extracted.values.some(v => v.value < 0);
      const isDeviation = hasNegativeVals || seriesLower.includes("diff") || seriesLower.includes("deviation") || seriesLower.includes("variance") || titleLower.includes("diff") || titleLower.includes("deviation");
      const isIndex = !hasNegativeVals && (seriesLower.includes("index") || titleLower.includes("index") || (avgBenchmarkVal >= 80 && avgBenchmarkVal <= 130 && !seriesLower.includes("pct")));

      let markVal: number;
      let markLabel: string;

      if (isDeviation) {
        markVal = 0;
        markLabel = "Overall Average (0%)";
      } else if (isIndex) {
        markVal = 100;
        markLabel = "Benchmark (100)";
      } else if (primaryUnit.isPercentage) {
        markVal = Number(avgBenchmarkVal.toFixed(1));
        markLabel = `Avg (${markVal}%)`;
      } else {
        markVal = Number(avgBenchmarkVal.toFixed(1));
        markLabel = `Avg (${formatValueWithUnit(markVal, primaryUnit)})`;
      }

      markLine = {
        symbol: "none",
        silent: true,
        data: [
          {
            yAxis: markVal,
            lineStyle: {
              type: "dashed",
              color: "#0D9488",
              width: 1.5,
            },
            label: {
              position: "end",
              formatter: markLabel,
              color: "#0D9488",
              fontSize: 10,
              fontWeight: "bold",
            },
          },
        ],
      };
    }
  }

  let adaptedSeries: unknown[] = [];
  const originalSeriesList = (echartsOption.series as Array<Record<string, unknown>>) ?? [];

  if (activeType === chartType && originalSeriesList.length > 1) {
    // Multi-series chart from backend (e.g. dual axis Bar + Line)
    adaptedSeries = originalSeriesList.map((s, idx) => {
      const sType = (s.type as string) || "bar";
      const sColor = AUTOBI_PALETTE[idx % AUTOBI_PALETTE.length];
      return {
        ...s,
        smooth: false,
        itemStyle: {
          ...(s.itemStyle as Record<string, unknown> || {}),
          color: sColor,
          borderRadius: sType === "bar" ? [4, 4, 0, 0] : undefined,
        },
        lineStyle: sType === "line" ? { width: 2.5, color: sColor } : undefined,
      };
    });
  } else if (activeType === "donut" || activeType === "pie") {
    adaptedSeries = [{
      name: extracted.seriesName,
      type: "pie",
      radius: activeType === "donut" ? ["42%", "68%"] : ["0%", "65%"],
      center: ["32%", "50%"],
      avoidLabelOverlap: true,
      itemStyle: {
        borderRadius: activeType === "donut" ? 4 : 0,
        borderColor: "#FFFFFF",
        borderWidth: 2,
      },
      label: {
        show: false,
      },
      labelLine: {
        show: false,
      },
      emphasis: {
        scale: true,
        scaleSize: 6,
        label: {
          show: true,
          fontSize: 13,
          fontWeight: "bold",
          color: "#0F172A",
          formatter: "{b}: {d}%",
        },
      },
      data: styledValues,
    }];
  } else if (activeType === "bar" || activeType === "histogram") {
    const hasDemographicCategories = extracted.categories.some((c) => CATEGORY_COLORS[c.trim().toLowerCase()]);
    const useMultiHue = hasDemographicCategories || isComparisonBenchmark || (activeType === "bar" && extracted.values.length <= 8);
    adaptedSeries = [{
      name: extracted.seriesName,
      type: "bar",
      barCategoryGap: activeType === "histogram" ? "2%" : "20%",
      markLine,
      data: useMultiHue
        ? styledValues
        : extracted.values.map((v) => ({
            value: v.value,
            itemStyle: { color: AUTOBI_PALETTE[0], borderRadius: [4, 4, 0, 0] },
          })),
    }];
  } else if (activeType === "line") {
    adaptedSeries = [{
      name: extracted.seriesName,
      type: "line",
      smooth: false,
      showSymbol: true,
      symbol: "circle",
      symbolSize: 6,
      lineStyle: { width: 2.5, color: AUTOBI_PALETTE[0] },
      itemStyle: { color: AUTOBI_PALETTE[0] },
      data: extracted.values.map((v) => v.value),
    }];
  } else if (activeType === "area") {
    adaptedSeries = [{
      name: extracted.seriesName,
      type: "line",
      smooth: false,
      showSymbol: false,
      symbol: "circle",
      symbolSize: 4,
      lineStyle: { width: 2, color: AUTOBI_PALETTE[0] },
      itemStyle: { color: AUTOBI_PALETTE[0] },
      areaStyle: { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "rgba(153,27,27,0.25)" }, { offset: 1, color: "rgba(153,27,27,0.02)" }] } },
      data: extracted.values.map((v) => v.value),
    }];
  } else if (activeType === "scatter") {
    // Scatter: pass original series directly (they already have [x,y] pairs and type:"scatter")
    // Just ensure colors and symbolSize are applied
    adaptedSeries = originalSeriesList.map((s: any, idx: number) => ({
      ...s,
      type: "scatter",
      symbolSize: s.symbolSize ?? 8,
      itemStyle: { ...(s.itemStyle || {}), color: AUTOBI_PALETTE[idx % AUTOBI_PALETTE.length] },
    }));
  } else if (activeType === "heatmap") {
    const xCategories = (echartsOption.xAxis as any)?.data || extracted.categories || [];
    const yCategories = (echartsOption.yAxis as any)?.data || [];
    const xLen = Math.max(xCategories.length, 1);
    const yLen = Math.max(yCategories.length, 1);

    adaptedSeries = originalSeriesList.map((s: any) => {
      const rawData = Array.isArray(s?.data) ? s.data : [];
      const formattedData = rawData.map((d: any, idx: number) => {
        if (Array.isArray(d) && d.length >= 3) return d;
        if (typeof d === "number") {
          const xIdx = idx % xLen;
          const yIdx = Math.floor(idx / xLen) % yLen;
          return [xIdx, yIdx, d];
        }
        if (typeof d === "object" && d !== null && "value" in d) {
          if (Array.isArray(d.value) && d.value.length >= 3) return d.value;
          const xIdx = idx % xLen;
          const yIdx = Math.floor(idx / xLen) % yLen;
          return [xIdx, yIdx, Number(d.value ?? 0)];
        }
        return [idx % xLen, 0, 0];
      });
      return {
        ...s,
        type: "heatmap",
        data: formattedData,
        emphasis: {
          itemStyle: {
            shadowBlur: 10,
            shadowColor: "rgba(0, 0, 0, 0.5)",
          },
        },
      };
    });
  } else {
    adaptedSeries = originalSeriesList;
  }

  let cartesianYAxis: unknown;
  if (activeType === chartType && Array.isArray(echartsOption.yAxis) && echartsOption.yAxis.length > 1) {
    cartesianYAxis = (echartsOption.yAxis as Array<Record<string, unknown>>).map((ax, i) => {
      const sName = String(originalSeriesList[i]?.name || ax.name || "");
      const sUnit = inferSeriesUnit(sName, title, (ax.name as string) || undefined);
      return {
        ...ax,
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: i === 0 ? "#F1F5F9" : "transparent", type: "dashed" } },
        axisLabel: {
          color: "#64748B",
          fontSize: 11,
          formatter: (val: number) => {
            if (sUnit.isPercentage) {
              const displayVal = (Math.abs(val) <= 1.5 && Math.abs(val) > 0.0001) ? val * 100 : val;
              return `${displayVal}%`;
            }
            if (sUnit.isMonetary) {
              const sign = val < 0 ? "-" : "";
              const absVal = Math.abs(val);
              if (absVal >= 1_000_000) return `${sign}${sUnit.unit}${(absVal / 1_000_000).toFixed(1)}M`;
              if (absVal >= 1_000) return `${sign}${sUnit.unit}${(absVal / 1_000).toFixed(0)}K`;
              return `${sign}${sUnit.unit}${absVal}`;
            }
            const sign = val < 0 ? "-" : "";
            const absVal = Math.abs(val);
            if (absVal >= 1_000_000) return `${sign}${(absVal / 1_000_000).toFixed(1)}M`;
            if (absVal >= 1_000) return `${sign}${(absVal / 1_000).toFixed(0)}K`;
            return absVal % 1 === 0 ? `${sign}${absVal}` : `${sign}${absVal < 10 ? absVal.toFixed(1) : absVal.toFixed(0)}`;
          },
        },
      };
    });
  } else {
    cartesianYAxis = {
      type: "value",
      name: primaryUnit.unit && !primaryUnit.isMonetary && !primaryUnit.isPercentage ? primaryUnit.unit : undefined,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        color: "#64748B",
        fontSize: 11,
        formatter: (val: number) => {
          if (primaryUnit.isPercentage) {
            const displayVal = (Math.abs(val) <= 1.5 && Math.abs(val) > 0.0001) ? val * 100 : val;
            return `${displayVal}%`;
          }
          if (primaryUnit.isMonetary) {
            const sign = val < 0 ? "-" : "";
            const absVal = Math.abs(val);
            if (absVal >= 1_000_000) return `${sign}${primaryUnit.unit}${(absVal / 1_000_000).toFixed(1)}M`;
            if (absVal >= 1_000) return `${sign}${primaryUnit.unit}${(absVal / 1_000).toFixed(0)}K`;
            return `${sign}${primaryUnit.unit}${absVal}`;
          }
          const sign = val < 0 ? "-" : "";
          const absVal = Math.abs(val);
          if (absVal >= 1_000_000) return `${sign}${(absVal / 1_000_000).toFixed(1)}M`;
          if (absVal >= 1_000) return `${sign}${(absVal / 1_000).toFixed(0)}K`;
          return absVal % 1 === 0 ? `${sign}${absVal}` : `${sign}${absVal < 10 ? absVal.toFixed(1) : absVal.toFixed(0)}`;
        },
      },
      splitLine: { lineStyle: { color: "#F1F5F9", type: "dashed" } },
    };
  }

  const hasLongLabels = extracted.categories.some((c) => c.length > 7);
  const shouldRotateLabels = extracted.categories.length > 4 || hasLongLabels;
  const needsDataZoom = isCartesian && (
    Boolean(echartsOption.dataZoom) ||
    extracted.categories.length > 25
  );

  const dataZoomConfig = needsDataZoom
    ? (Array.isArray(echartsOption.dataZoom) && echartsOption.dataZoom.length > 0
        ? echartsOption.dataZoom
        : [
            {
              type: "slider",
              show: true,
              xAxisIndex: [0],
              bottom: 4,
              height: 18,
              borderColor: "transparent",
              backgroundColor: "#F1F5F9",
              fillerColor: "rgba(153, 27, 27, 0.15)",
              handleStyle: { color: "#991B1B" },
              textStyle: { color: "#64748B", fontSize: 10 },
              start: 0,
              end: 100,
            },
            {
              type: "inside",
              xAxisIndex: [0],
              zoomOnMouseWheel: true,
              moveOnMouseMove: true,
            }
          ])
    : undefined;

  const cartesianDefaults = isCartesian ? {
    grid: {
      left: shouldRotateLabels ? 64 : "5%",
      right: shouldRotateLabels ? 36 : "4%",
      bottom: needsDataZoom ? 76 : (shouldRotateLabels ? 70 : 48),
      top: "12%",
      containLabel: true,
    },
    xAxis: {
      type: "category",
      data: extracted.categories,
      axisLine: { lineStyle: { color: "#E2E8F0" } },
      axisTick: { lineStyle: { color: "#CBD5E1" } },
      axisLabel: {
        color: "#64748B",
        rotate: shouldRotateLabels ? (extracted.categories.length > 25 ? 35 : 20) : 0,
        align: shouldRotateLabels ? "right" : "center",
        verticalAlign: shouldRotateLabels ? "middle" : "top",
        fontSize: 11,
        interval: extracted.categories.length > 15 ? "auto" : 0,
        margin: 10,
        hideOverlap: true,
        formatter: (val: string) => {
          if (!val) return "";
          return val.length > 22 ? `${val.slice(0, 20)}…` : val;
        },
      },
      splitLine: { show: false },
    },
    yAxis: cartesianYAxis,
  } : {};

  const isHeatmap = activeType === "heatmap" || adaptedSeries.some((s: any) => s?.type === "heatmap");

  // VisualMap calculation for heatmap (strictly required by ECharts: 'Heatmap must use with visualMap')
  let visualMapOption = (echartsOption.visualMap as Record<string, unknown> | undefined);
  if (isHeatmap && !visualMapOption) {
    const allVals: number[] = [];
    adaptedSeries.forEach((s: any) => {
      if (Array.isArray(s?.data)) {
        s.data.forEach((item: any) => {
          if (Array.isArray(item) && typeof item[2] === "number" && !isNaN(item[2])) {
            allVals.push(item[2]);
          } else if (typeof item === "number" && !isNaN(item)) {
            allVals.push(item);
          }
        });
      }
    });
    const minVal = allVals.length > 0 ? Math.min(...allVals) : 0;
    let maxVal = allVals.length > 0 ? Math.max(...allVals) : 100;
    if (minVal === maxVal) maxVal = minVal + 10;

    visualMapOption = {
      min: minVal,
      max: maxVal,
      calculable: true,
      orient: "horizontal",
      left: "center",
      bottom: 0,
      inRange: {
        color: ["#F8FAFC", "#EF4444", "#991B1B"],
      },
      textStyle: { color: "#64748B", fontSize: 10 },
    };
  }

  const mergedOption: Record<string, unknown> = {
    backgroundColor: "transparent",
    textStyle: { color: "#1E293B", fontFamily: "Inter, system-ui, sans-serif", fontSize: 12 },
    tooltip: isHeatmap
      ? {
          position: "top",
          formatter: (p: any) => {
            const val = Array.isArray(p.data) ? p.data[2] : p.value;
            const xCats = (echartsOption.xAxis as any)?.data || extracted.categories;
            const yCats = (echartsOption.yAxis as any)?.data || [];
            const xName = xCats[p.data?.[0]] ?? p.name ?? "";
            const yName = yCats[p.data?.[1]] ?? "";
            return `<div style="font-weight:600;color:#0F172A;">${xName}${yName ? ` × ${yName}` : ""}</div><div>Value: <b>${val}</b></div>`;
          },
        }
      : isScatter
      ? {
          trigger: "item",
          formatter: (p: any) => {
            const xName = (echartsOption.xAxis as any)?.name || "X";
            const yName = (echartsOption.yAxis as any)?.name || "Y";
            const xVal = Array.isArray(p.data) ? p.data[0] : p.value?.[0] ?? "";
            const yVal = Array.isArray(p.data) ? p.data[1] : p.value?.[1] ?? "";
            return `<div style="font-weight:600;color:#0F172A;">${p.seriesName || ""}</div><div>${xName}: <b>${xVal}</b></div><div>${yName}: <b>${yVal}</b></div>`;
          },
          backgroundColor: "#FFFFFF",
          borderColor: "#E2E8F0",
          borderWidth: 1,
          extraCssText: "box-shadow: 0 4px 12px rgba(0,0,0,0.08); border-radius: 8px; padding: 10px 12px;",
        }
      : (isPieOrDonut
      ? {
          trigger: "item",
          formatter: (p: any) => {
            const u = primaryUnit;
            const valFormatted = formatValueWithUnit(Number(p.value), u);
            const pct = p.percent != null ? `${p.percent}%` : "";
            const dotColor = p.color || "#991B1B";
            return `
              <div style="font-weight:600;margin-bottom:6px;color:#0F172A;border-bottom:1px solid #F1F5F9;padding-bottom:4px;">${p.name}</div>
              <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:2px;">
                <div style="display:flex;align-items:center;gap:6px;">
                  <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background-color:${dotColor}"></span>
                  <span style="color:#64748B;font-size:11px;">${p.seriesName || extracted.seriesName || "Share"}:</span>
                </div>
                <span style="font-weight:600;color:#0F172A;font-size:12px;">${valFormatted} ${pct ? `(${pct})` : ""}</span>
              </div>
            `;
          },
        }
      : {
          trigger: "axis",
          formatter: (params: unknown) => {
            if (!Array.isArray(params) || params.length === 0) return "";
            const headerName = (params[0] as any)?.name || "";
            const rowsHtml = (params as any[]).map((p) => {
              const sName = String(p.seriesName || extracted.seriesName || "");
              const pUnit = inferSeriesUnit(sName, title, undefined);
              const fVal = formatValueWithUnit(Number(p.value), pUnit);
              const dotColor = p.color || "#991B1B";
              return `
                <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:4px;">
                  <div style="display:flex;align-items:center;gap:6px;">
                    <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background-color:${dotColor}"></span>
                    <span style="color:#64748B;font-size:11px;">${sName}:</span>
                  </div>
                  <span style="font-weight:600;color:#0F172A;font-size:12px;">${fVal}</span>
                </div>
              `;
            }).join("");

            return `
              <div style="font-weight:600;margin-bottom:6px;color:#0F172A;border-bottom:1px solid #F1F5F9;padding-bottom:4px;">${headerName}</div>
              ${rowsHtml}
            `;
          },
          backgroundColor: "#FFFFFF",
          borderColor: "#E2E8F0",
          borderWidth: 1,
          textStyle: { color: "#1E293B" },
          extraCssText: "box-shadow: 0 4px 12px rgba(0,0,0,0.08); border-radius: 8px; padding: 10px 12px;",
        }),
    legend: isHeatmap
      ? { show: false }
      : isPieOrDonut
      ? {
          show: true,
          type: "scroll",
          orient: "vertical",
          right: "2%",
          top: "middle",
          textStyle: { color: "#64748B", fontSize: 11 },
          icon: "circle",
          itemGap: 10,
          formatter: (name: string) => {
            const vObj = extracted.values.find((x) => x.name === name);
            if (!vObj) return name;
            const total = extracted.values.reduce((s, x) => s + x.value, 0);
            const pct = total > 0 ? ((vObj.value / total) * 100).toFixed(1) : "0";
            return name.length > 15 ? `${name.slice(0, 13)}… (${pct}%)` : `${name} (${pct}%)`;
          },
        }
      : {
          show: true,
          orient: "horizontal",
          bottom: 4,
          left: "center",
          textStyle: { color: "#475569", fontSize: 11, fontWeight: 500 },
          icon: "circle",
          itemWidth: 8,
          itemHeight: 8,
          itemGap: 16,
        },
    ...cartesianDefaults,
    dataZoom: isHeatmap ? undefined : dataZoomConfig,
    series: adaptedSeries,
    title: { show: false },
    color: AUTOBI_PALETTE,
    ...(visualMapOption ? { visualMap: visualMapOption } : {}),
  };

  if (isPieOrDonut) {
    delete mergedOption.xAxis;
    delete mergedOption.yAxis;
  } else if (isHeatmap) {
    if (echartsOption.xAxis) mergedOption.xAxis = echartsOption.xAxis;
    if (echartsOption.yAxis) mergedOption.yAxis = echartsOption.yAxis;
  } else if (isScatter) {
    // Scatter must use value axes from backend — override any cartesian category axes
    mergedOption.xAxis = echartsOption.xAxis ?? { type: "value" };
    mergedOption.yAxis = echartsOption.yAxis ?? { type: "value" };
  }

  return (
    <div className="chart-card" id={`chart-${queryId ?? "unknown"}`}>
      <div className="chart-header">
        <div className="chart-meta">
          {title    && <div className="chart-title">{title}</div>}
          {subtitle && <div className="chart-subtitle">{subtitle}</div>}
        </div>
        <div className="chart-actions">
          {compatibleTypes.length > 1 && (
            <div className="chart-type-switcher">
              {compatibleTypes.map((t) => (
                <button
                  key={t.type}
                  className={`switcher-btn ${activeType === t.type ? "active" : ""}`}
                  onClick={() => setUserSelectedType(t.type)}
                  title={`Switch to ${t.label}`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          )}
          <button className="chart-download-btn" onClick={handleDownload} title="Download as PNG">
            ↓ PNG
          </button>
        </div>
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
          <span className="chart-insight-badge">INSIGHT</span>
          <span>{insightSummary}</span>
        </div>
      )}
    </div>
  );
}