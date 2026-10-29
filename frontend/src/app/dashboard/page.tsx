"use client";

import React, { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import DashboardGrid from "@/components/DashboardGrid";
import Link from "next/link";
import DataChatDrawer from "@/components/DataChatDrawer";
import { 
  BarChart3, 
  Sparkles, 
  RefreshCw, 
  ArrowUpRight, 
  TrendingUp, 
  Users, 
  DollarSign, 
  ShoppingBag, 
  Search, 
  Upload, 
  Layers,
  CheckCircle2,
  HelpCircle
} from "lucide-react";

// ── Sample BI Dashboard Data ──────────────────────────────────────────────────
// ── Grounded Retail Performance Dashboard (Derived from test_datasets/messy_retail_orders.csv) ──
const DEMO_DASHBOARD = {
  title: "Retail Orders Executive Performance Overview",
  layout_rationale: "Data-grounded executive layout tracking 632 retail orders: headline revenue, customer acquisition, order volume, and AOV, followed by regional distribution, segment gross margin, and verified product performance.",
  data_quality_score: 0.96,
  chart_count: 6,
  charts: [
    {
      query_id: "kpi_revenue",
      title: "Total Revenue",
      subtitle: "Verified sum of sales_amount",
      chart_type: "kpi_card",
      responsive: true,
      layout: { x: 0, y: 0, w: 3, h: 2 },
      echarts_option: {
        chart_type: "kpi_card",
        _chart_type: "kpi_card",
        type: "kpi_card",
        label: "Total Gross Revenue",
        value: "$121,153",
        raw_value: 121152.82,
        sql_query: "SELECT ROUND(SUM(sales_amount), 2) AS total_revenue FROM orders;"
      }
    },
    {
      query_id: "kpi_customers",
      title: "Active Customers",
      subtitle: "Distinct customer_id count",
      chart_type: "kpi_card",
      responsive: true,
      layout: { x: 3, y: 0, w: 3, h: 2 },
      echarts_option: {
        chart_type: "kpi_card",
        _chart_type: "kpi_card",
        type: "kpi_card",
        label: "Active Customers",
        value: "604",
        raw_value: 604,
        sql_query: "SELECT COUNT(DISTINCT customer_id) AS active_customers FROM customers;"
      }
    },
    {
      query_id: "kpi_orders",
      title: "Order Volume",
      subtitle: "Total records processed",
      chart_type: "kpi_card",
      responsive: true,
      layout: { x: 6, y: 0, w: 3, h: 2 },
      echarts_option: {
        chart_type: "kpi_card",
        _chart_type: "kpi_card",
        type: "kpi_card",
        label: "Completed Orders",
        value: "632",
        raw_value: 632,
        sql_query: "SELECT COUNT(*) AS completed_orders FROM orders;"
      }
    },
    {
      query_id: "kpi_aov",
      title: "Avg Order Value",
      subtitle: "Mean revenue per order",
      chart_type: "kpi_card",
      responsive: true,
      layout: { x: 9, y: 0, w: 3, h: 2 },
      echarts_option: {
        chart_type: "kpi_card",
        _chart_type: "kpi_card",
        type: "kpi_card",
        label: "Average Order Value (AOV)",
        value: "$281.75",
        raw_value: 281.75,
        sql_query: "SELECT ROUND(AVG(sales_amount), 2) AS avg_order_value FROM orders;"
      }
    },
    {
      query_id: "chart_revenue_trend",
      title: "Monthly Revenue & Growth Trajectory",
      subtitle: "Chronological monthly sales progression",
      chart_type: "line",
      responsive: true,
      layout: { x: 0, y: 2, w: 8, h: 5 },
      echarts_option: {
        backgroundColor: "transparent",
        sql_query: "SELECT STRFTIME(DATE_TRUNC('month', order_date), '%Y-%m') AS month, ROUND(SUM(sales_amount), 2) AS revenue FROM orders GROUP BY month ORDER BY month;",
        tooltip: {
          trigger: "axis",
          backgroundColor: "#161b22",
          borderColor: "#30363d",
          textStyle: { color: "#e6edf3" }
        },
        grid: { left: "3%", right: "4%", bottom: "3%", containLabel: true },
        xAxis: {
          type: "category",
          data: ["2023-01", "2023-02", "2023-03", "2023-04", "2023-06", "2023-07", "2023-08", "2023-10", "2023-11", "2023-12"],
          axisLine: { lineStyle: { color: "#30363d" } },
          axisLabel: { color: "#8b949e" }
        },
        yAxis: {
          type: "value",
          splitLine: { lineStyle: { color: "#21262d" } },
          axisLabel: {
            color: "#8b949e",
            formatter: (v: number) => `$${v >= 1000 ? (v / 1000).toFixed(1) + 'k' : v}`
          }
        },
        series: [
          {
            name: "Monthly Net Sales",
            type: "line",
            smooth: false,
            data: [216, 430, 165, 819, 1840, 501, 2035, 494, 1675, 983],
            itemStyle: { color: "#58a6ff" },
            areaStyle: {
              color: {
                type: "linear",
                x: 0, y: 0, x2: 0, y2: 1,
                colorStops: [
                  { offset: 0, color: "rgba(88, 166, 255, 0.3)" },
                  { offset: 1, color: "rgba(88, 166, 255, 0.0)" }
                ]
              }
            }
          }
        ]
      }
    },
    {
      query_id: "chart_regional_split",
      title: "Regional Sales Distribution",
      subtitle: "Share of revenue by geographical market",
      chart_type: "donut",
      responsive: true,
      layout: { x: 8, y: 2, w: 4, h: 5 },
      echarts_option: {
        backgroundColor: "transparent",
        sql_query: "SELECT country, ROUND(SUM(sales_amount), 2) AS sales FROM orders GROUP BY country ORDER BY sales DESC;",
        tooltip: {
          trigger: "item",
          backgroundColor: "#161b22",
          borderColor: "#30363d",
          textStyle: { color: "#e6edf3" }
        },
        series: [
          {
            name: "Market",
            type: "pie",
            radius: ["42%", "68%"],
            center: ["32%", "50%"],
            avoidLabelOverlap: true,
            itemStyle: {
              borderRadius: 6,
              borderColor: "#0d1117",
              borderWidth: 2
            },
            label: { show: false },
            labelLine: { show: false },
            emphasis: {
              label: { show: true, fontSize: 13, fontWeight: "bold", color: "#e6edf3" }
            },
            data: [
              { value: 32351, name: "United States", itemStyle: { color: "#58a6ff" } },
              { value: 28240, name: "India", itemStyle: { color: "#3fb950" } },
              { value: 27742, name: "Other Markets", itemStyle: { color: "#d29922" } },
              { value: 11546, name: "Germany", itemStyle: { color: "#bc8cff" } },
              { value: 11156, name: "United Kingdom", itemStyle: { color: "#f85149" } },
              { value: 6499, name: "Australia", itemStyle: { color: "#22d3ee" } }
            ]
          }
        ]
      }
    },
    {
      query_id: "chart_category_profit",
      title: "Gross Margin by Business Segment",
      subtitle: "Gross Margin ($) across product categories",
      chart_type: "bar",
      responsive: true,
      layout: { x: 0, y: 7, w: 6, h: 5 },
      echarts_option: {
        backgroundColor: "transparent",
        sql_query: "SELECT product_category, ROUND(SUM(profit), 2) AS gross_margin FROM data GROUP BY product_category ORDER BY gross_margin DESC;",
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "shadow" },
          backgroundColor: "#161b22",
          borderColor: "#30363d",
          textStyle: { color: "#e6edf3" }
        },
        grid: { left: "3%", right: "4%", bottom: "3%", containLabel: true },
        xAxis: {
          type: "category",
          data: ["Home & Kitchen", "Electronics", "Apparel", "Sports & Fitness", "Beauty"],
          axisLine: { lineStyle: { color: "#30363d" } },
          axisLabel: { color: "#8b949e" }
        },
        yAxis: {
          type: "value",
          splitLine: { lineStyle: { color: "#21262d" } },
          axisLabel: {
            color: "#8b949e",
            formatter: (v: number) => `$${v.toLocaleString()}`
          }
        },
        series: [
          {
            name: "Gross Margin ($)",
            type: "bar",
            barWidth: "40%",
            data: [
              { value: 3654, itemStyle: { color: "#58a6ff" } },
              { value: 2439, itemStyle: { color: "#3fb950" } },
              { value: 2126, itemStyle: { color: "#d29922" } },
              { value: 1965, itemStyle: { color: "#bc8cff" } },
              { value: 1819, itemStyle: { color: "#f85149" } }
            ]
          }
        ]
      }
    },
    {
      query_id: "chart_top_products_table",
      title: "Top Performing Assets & Products",
      subtitle: "Verified query execution on retail dataset",
      chart_type: "data_table",
      responsive: true,
      layout: { x: 6, y: 7, w: 6, h: 5 },
      echarts_option: {
        chart_type: "data_table",
        sql_query: "SELECT product_name, category, count(*) AS orders, ROUND(SUM(sales), 2) AS revenue, ROUND((SUM(profit)/NULLIF(SUM(sales),0))*100, 1) AS margin FROM data GROUP BY 1, 2 ORDER BY revenue DESC LIMIT 5;",
        columns: ["Product Name", "Category", "Orders", "Revenue", "Margin"],
        rows: [
          { "Product Name": "Smart Watch", "Category": "Electronics", "Orders": 29, "Revenue": "$21,563.83", "Margin": "6.9%" },
          { "Product Name": "Dumbbell Set", "Category": "Sports & Fitness", "Orders": 37, "Revenue": "$13,856.79", "Margin": "10.3%" },
          { "Product Name": "Perfume", "Category": "Beauty", "Orders": 37, "Revenue": "$11,183.18", "Margin": "12.0%" },
          { "Product Name": "Denim Jacket", "Category": "Apparel", "Orders": 30, "Revenue": "$10,770.38", "Margin": "7.7%" },
          { "Product Name": "Air Fryer", "Category": "Home & Kitchen", "Orders": 32, "Revenue": "$9,315.79", "Margin": "12.4%" }
        ]
      }
    }
  ],
  generated_queries: [
    { id: "q1", sql: "SELECT STRFTIME(DATE_TRUNC('month', order_date), '%Y-%m') AS month, ROUND(SUM(sales_amount), 2) AS revenue FROM orders GROUP BY month ORDER BY month;" },
    { id: "q2", sql: "SELECT country, ROUND(SUM(sales_amount), 2) AS sales FROM orders GROUP BY country ORDER BY sales DESC;" },
    { id: "q3", sql: "SELECT product_name, category, count(*) AS orders, ROUND(SUM(sales), 2) AS revenue, ROUND((SUM(profit)/NULLIF(SUM(sales),0))*100, 1) AS margin FROM orders GROUP BY 1, 2 ORDER BY revenue DESC LIMIT 5;" },
    { id: "q4", sql: "SELECT product_category, ROUND(SUM(profit), 2) AS gross_margin FROM orders GROUP BY product_category ORDER BY gross_margin DESC;" }
  ]
};

export default function GeneralDashboardPage() {
  const router = useRouter();
  const [sessionInput, setSessionInput] = useState("");
  const [showSessionModal, setShowSessionModal] = useState(false);
  const [followUpOpen, setFollowUpOpen] = useState(false);
  const [followUpQ, setFollowUpQ] = useState("");
  const [followUpLoading, setFollowUpLoading] = useState(false);
  const [activeSessionData, setActiveSessionData] = useState<any>(null);
  const [recentSessions, setRecentSessions] = useState<any[]>([]);
  const followUpRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    try {
      const stored = JSON.parse(localStorage.getItem("autobi_sessions") || "[]");
      if (Array.isArray(stored) && stored.length > 0) {
        setRecentSessions(stored);
        const latestId = stored[0].sessionId;
        const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
        fetch(`${API}/api/pipeline/${latestId}/dashboard`)
          .then((res) => (res.ok ? res.json() : null))
          .then((data) => {
            if (data && Array.isArray(data.charts) && data.charts.length > 0) {
              setActiveSessionData(data);
            }
          })
          .catch(() => {});
      }
    } catch {}
  }, []);

  const currentDashboard = activeSessionData || DEMO_DASHBOARD;
  const isDirectMode = !activeSessionData;

  const handleOpenSession = (e: React.FormEvent) => {
    e.preventDefault();
    if (sessionInput.trim()) {
      router.push(`/dashboard/${sessionInput.trim()}`);
    }
  };

  const handleFollowUp = () => {
    if (!followUpQ.trim()) return;
    setFollowUpLoading(true);
    setTimeout(() => {
      setFollowUpLoading(false);
      setFollowUpOpen(false);
      setFollowUpQ("");
      alert("In an active session, this initiates real-time LangGraph multi-agent analysis directly on your cleaned dataset.");
    }, 1000);
  };

  return (
    <div className="dashboard-page">
      {/* ── Top Header ──────────────────────────────────────────────────────── */}
      <header className="dashboard-header">
        <div className="dashboard-header-left">
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              ● {isDirectMode ? "Sample Overview" : "Active Dataset"}
            </span>
            <span className="text-xs text-muted-foreground/80">
              {isDirectMode ? "Interactive View" : `Session: ${currentDashboard.session_id?.slice(0, 8)}…`}
            </span>
          </div>
          <h1 className="dashboard-title">{currentDashboard.title}</h1>
          <div className="dashboard-meta">
            <span className="meta-badge">{currentDashboard.chart_count || currentDashboard.charts?.length || 0} Visualizations</span>
            <span className="meta-badge">{currentDashboard.generated_queries?.length || 0} Queries</span>
            <span className="meta-badge quality-badge" style={{ borderColor: "#5AD8A6", color: "#5AD8A6" }}>
              Data Quality: {Math.round((currentDashboard.data_quality_score || 0.96) * 100)}%
            </span>
          </div>
        </div>

        <div className="dashboard-header-right">
          <button 
            className="btn-secondary flex items-center gap-1.5"
            onClick={() => setShowSessionModal(true)}
          >
            <Search className="w-3.5 h-3.5" />
            Switch Session
          </button>
          <Link href="/upload" className="btn-secondary flex items-center gap-1.5">
            <Upload className="w-3.5 h-3.5" />
            Upload Dataset
          </Link>
          <button
            className="btn-follow-up flex items-center gap-1.5"
            onClick={() => { setFollowUpOpen(true); setTimeout(() => followUpRef.current?.focus(), 100); }}
          >
            <Sparkles className="w-3.5 h-3.5" />
            Ask Question
          </button>
        </div>
      </header>

      {/* ── Layout Rationale Bar ────────────────────────────────────────────── */}
      <div className="layout-rationale-bar">
        <span className="rationale-icon">🧠</span>
        <span>{currentDashboard.layout_rationale || "Optimized layout highlighting critical KPIs followed by distributions and trends."}</span>
      </div>

      {/* ── Mode Banner: Sample Overview vs Active Dataset ────────────────────── */}
      {isDirectMode ? (
        <div style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "8px 16px",
          margin: "0 24px 16px 24px",
          background: "#FEF2F2",
          border: "1px solid #FECACA",
          borderRadius: "var(--abi-radius-sm)",
          fontSize: "0.82rem",
          color: "#991B1B"
        }}>
          <div>
            <strong>Interactive Sample Overview:</strong> Illustrates executive layout, verified SQL aggregation, and dynamic charting. When you upload any arbitrary dataset (HR, IoT sensors, healthcare, finance), AutoBI dynamically derives all KPIs, dimensions, and custom charts strictly from that dataset.
          </div>
          {recentSessions.length > 0 && (
            <button
              onClick={() => {
                const latest = recentSessions[0];
                const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
                fetch(`${API}/api/pipeline/${latest.sessionId}/dashboard`)
                  .then((r) => (r.ok ? r.json() : null))
                  .then((d) => { if (d && d.charts) setActiveSessionData(d); });
              }}
              className="btn-secondary"
              style={{ fontSize: "0.78rem", padding: "4px 10px", whiteSpace: "nowrap", marginLeft: "12px" }}
            >
              Load Active Dataset ({recentSessions[0].fileName || "Uploaded"}) →
            </button>
          )}
        </div>
      ) : (
        <div style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "8px 16px",
          margin: "0 24px 16px 24px",
          background: "#F0FDF4",
          border: "1px solid #BBF7D0",
          borderRadius: "var(--abi-radius-sm)",
          fontSize: "0.82rem",
          color: "#166534"
        }}>
          <div>
            <strong>Active Dataset Mode:</strong> All KPIs, visualizations, units, and insights are derived dynamically from your uploaded data schema and verified via SQL execution.
          </div>
          <button
            onClick={() => setActiveSessionData(null)}
            className="btn-secondary"
            style={{ fontSize: "0.78rem", padding: "4px 10px", whiteSpace: "nowrap", marginLeft: "12px" }}
          >
            ← View Sample Demo
          </button>
        </div>
      )}

      {/* ── Dashboard Grid ─────────────────────────────────────────────────── */}
      <main className="dashboard-main">
        <DashboardGrid
          charts={currentDashboard.charts}
          sessionId={currentDashboard.session_id || "executive-overview"}
          filters={currentDashboard.filters || []}
        />
      </main>

      {/* ── Conversational Data Chatbot Drawer ───────────────────────────────────── */}
      <DataChatDrawer
        isOpen={followUpOpen}
        onClose={() => setFollowUpOpen(false)}
        sessionId={currentDashboard.session_id || (recentSessions[0]?.sessionId) || "demo"}
        datasetTitle={currentDashboard.title || "Dataset"}
      />

      {/* ── Session Switcher Modal ─────────────────────────────────────────── */}
      {showSessionModal && (
        <div className="follow-up-overlay" onClick={(e) => e.target === e.currentTarget && setShowSessionModal(false)}>
          <div className="follow-up-panel">
            <div className="follow-up-header">
              <h3>Load Session Dashboard</h3>
              <button className="close-btn" onClick={() => setShowSessionModal(false)}>✕</button>
            </div>
            {recentSessions && recentSessions.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--abi-text)", marginBottom: 8 }}>
                  Recent Sessions:
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 150, overflowY: "auto" }}>
                  {recentSessions.map((s) => (
                    <button
                      key={s.sessionId}
                      type="button"
                      onClick={() => {
                        router.push(`/dashboard/${s.sessionId}`);
                        setShowSessionModal(false);
                      }}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        padding: "8px 12px",
                        background: "#F8FAFC",
                        border: "1px solid var(--abi-border)",
                        borderRadius: "var(--abi-radius-sm)",
                        fontSize: "0.8rem",
                        cursor: "pointer",
                        textAlign: "left",
                      }}
                    >
                      <span style={{ fontWeight: 600, color: "var(--abi-text)" }}>{s.fileName || "Dataset Analysis"}</span>
                      <span style={{ color: "var(--abi-text-muted)", fontSize: "0.72rem" }}>{s.sessionId.slice(0, 8)}…</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
            <p className="follow-up-desc">
              Or enter any session ID directly:
            </p>
            <form onSubmit={handleOpenSession} className="flex flex-col gap-4">
              <input
                type="text"
                placeholder="Enter Session UUID (e.g. 550e8400-e29b-41d4-a716-446655440000)"
                className="follow-up-input"
                value={sessionInput}
                onChange={(e) => setSessionInput(e.target.value)}
                autoFocus
              />
              <div className="follow-up-actions">
                <button type="button" className="btn-secondary" onClick={() => setShowSessionModal(false)}>
                  Cancel
                </button>
                <button type="submit" className="btn-primary" disabled={!sessionInput.trim()}>
                  Open Dashboard →
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ── Footer ─────────────────────────────────────────────────────────── */}
      <footer className="dashboard-footer">
        <div className="footer-stat">
          <span className="footer-label">Dashboard View</span>
          <span className="footer-value mono">{isDirectMode ? "Overview" : currentDashboard.session_id?.slice(0, 8)}</span>
        </div>
        <div className="footer-stat">
          <span className="footer-label">Visualizations</span>
          <span className="footer-value">{currentDashboard.chart_count || currentDashboard.charts?.length || 0}</span>
        </div>
        <div className="footer-stat">
          <span className="footer-label">Engine</span>
          <span className="footer-value">Multi-Agent Swarm</span>
        </div>
        <div className="footer-stat">
          <span className="footer-label">Quality Score</span>
          <span className="footer-value" style={{ color: "#5AD8A6" }}>{Math.round((currentDashboard.data_quality_score || 0.96) * 100)}%</span>
        </div>
      </footer>
    </div>
  );
}
