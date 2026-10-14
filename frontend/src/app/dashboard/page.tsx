"use client";

import React, { useState, useRef } from "react";
import { useRouter } from "next/navigation";
import DashboardGrid from "@/components/DashboardGrid";
import Link from "next/link";
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
const DEMO_DASHBOARD = {
  title: "Executive BI Performance Overview",
  layout_rationale: "Optimized layout highlighting critical revenue KPIs at top, followed by regional sales distribution and category profitability trends.",
  data_quality_score: 0.96,
  chart_count: 6,
  charts: [
    {
      query_id: "kpi_revenue",
      title: "Total Revenue",
      subtitle: "+18.4% vs last period",
      responsive: true,
      layout: { x: 0, y: 0, w: 3, h: 2 },
      echarts_option: {
        chart_type: "kpi_card",
        label: "Total Gross Revenue",
        value: "$4,829,150",
        raw_value: 4829150,
      }
    },
    {
      query_id: "kpi_customers",
      title: "Active Customers",
      subtitle: "+12.1% new acquisitions",
      responsive: true,
      layout: { x: 3, y: 0, w: 3, h: 2 },
      echarts_option: {
        chart_type: "kpi_card",
        label: "Active Customers",
        value: "48,290",
        raw_value: 48290,
      }
    },
    {
      query_id: "kpi_orders",
      title: "Order Volume",
      subtitle: "Avg 3,420 orders/day",
      responsive: true,
      layout: { x: 6, y: 0, w: 3, h: 2 },
      echarts_option: {
        chart_type: "kpi_card",
        label: "Completed Orders",
        value: "128,450",
        raw_value: 128450,
      }
    },
    {
      query_id: "kpi_aov",
      title: "Avg Order Value",
      subtitle: "+5.3% margin expansion",
      responsive: true,
      layout: { x: 9, y: 0, w: 3, h: 2 },
      echarts_option: {
        chart_type: "kpi_card",
        label: "Average Order Value (AOV)",
        value: "$142.80",
        raw_value: 142.80,
      }
    },
    {
      query_id: "chart_revenue_trend",
      title: "Monthly Revenue & Growth Trajectory",
      subtitle: "Trailing 12-month performance in USD",
      responsive: true,
      layout: { x: 0, y: 2, w: 8, h: 5 },
      echarts_option: {
        backgroundColor: "transparent",
        tooltip: {
          trigger: "axis",
          backgroundColor: "#161b22",
          borderColor: "#30363d",
          textStyle: { color: "#e6edf3" }
        },
        grid: { left: "3%", right: "4%", bottom: "3%", containLabel: true },
        xAxis: {
          type: "category",
          data: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
          axisLine: { lineStyle: { color: "#30363d" } },
          axisLabel: { color: "#8b949e" }
        },
        yAxis: {
          type: "value",
          splitLine: { lineStyle: { color: "#21262d" } },
          axisLabel: {
            color: "#8b949e",
            formatter: (v: number) => `$${(v / 1000).toFixed(0)}k`
          }
        },
        series: [
          {
            name: "Revenue",
            type: "line",
            smooth: true,
            data: [280000, 310000, 340000, 390000, 420000, 460000, 490000, 520000, 540000, 610000, 680000, 750000],
            itemStyle: { color: "#58a6ff" },
            areaStyle: {
              color: {
                type: "linear",
                x: 0, y: 0, x2: 0, y2: 1,
                colorStops: [
                  { offset: 0, color: "rgba(88, 166, 255, 0.4)" },
                  { offset: 1, color: "rgba(88, 166, 255, 0.0)" }
                ]
              }
            }
          },
          {
            name: "Target",
            type: "line",
            smooth: true,
            lineStyle: { type: "dashed", color: "#3fb950" },
            data: [260000, 290000, 320000, 360000, 400000, 440000, 480000, 500000, 530000, 580000, 640000, 710000],
            itemStyle: { color: "#3fb950" }
          }
        ]
      }
    },
    {
      query_id: "chart_regional_split",
      title: "Regional Sales Distribution",
      subtitle: "Share of global revenue by market",
      responsive: true,
      layout: { x: 8, y: 2, w: 4, h: 5 },
      echarts_option: {
        backgroundColor: "transparent",
        tooltip: {
          trigger: "item",
          backgroundColor: "#161b22",
          borderColor: "#30363d",
          textStyle: { color: "#e6edf3" }
        },
        legend: {
          bottom: "2%",
          left: "center",
          textStyle: { color: "#8b949e", fontSize: 11 }
        },
        series: [
          {
            name: "Market",
            type: "pie",
            radius: ["45%", "70%"],
            avoidLabelOverlap: false,
            itemStyle: {
              borderRadius: 6,
              borderColor: "#0d1117",
              borderWidth: 2
            },
            label: { show: false },
            emphasis: {
              label: { show: true, fontSize: 13, fontWeight: "bold", color: "#e6edf3" }
            },
            data: [
              { value: 1930000, name: "North America", itemStyle: { color: "#58a6ff" } },
              { value: 1320000, name: "Europe", itemStyle: { color: "#3fb950" } },
              { value: 1040000, name: "Asia Pacific", itemStyle: { color: "#d29922" } },
              { value: 539150, name: "Latin America", itemStyle: { color: "#bc8cff" } }
            ]
          }
        ]
      }
    },
    {
      query_id: "chart_category_profit",
      title: "Category Profitability & Volume",
      subtitle: "Margin % vs units sold",
      responsive: true,
      layout: { x: 0, y: 7, w: 6, h: 5 },
      echarts_option: {
        backgroundColor: "transparent",
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
          data: ["Cloud Services", "Hardware", "Consulting", "Sub-Software", "Support"],
          axisLine: { lineStyle: { color: "#30363d" } },
          axisLabel: { color: "#8b949e" }
        },
        yAxis: {
          type: "value",
          splitLine: { lineStyle: { color: "#21262d" } },
          axisLabel: { color: "#8b949e" }
        },
        series: [
          {
            name: "Gross Margin ($k)",
            type: "bar",
            barWidth: "40%",
            data: [
              { value: 890, itemStyle: { color: "#58a6ff" } },
              { value: 540, itemStyle: { color: "#3fb950" } },
              { value: 420, itemStyle: { color: "#d29922" } },
              { value: 680, itemStyle: { color: "#bc8cff" } },
              { value: 290, itemStyle: { color: "#f85149" } }
            ]
          }
        ]
      }
    },
    {
      query_id: "chart_top_products_table",
      title: "Top Performing Assets & Products",
      subtitle: "Real-time query execution output",
      responsive: true,
      layout: { x: 6, y: 7, w: 6, h: 5 },
      echarts_option: {
        chart_type: "data_table",
        columns: ["Product Name", "Category", "Orders", "Revenue", "Margin"],
        rows: [
          { "Product Name": "Enterprise Cloud Suite", "Category": "Cloud Services", "Orders": 1240, "Revenue": "$1,488,000", "Margin": "74.2%" },
          { "Product Name": "AI Analytics Pro", "Category": "Sub-Software", "Orders": 3490, "Revenue": "$872,500", "Margin": "88.5%" },
          { "Product Name": "Managed Infrastructure", "Category": "Hardware", "Orders": 820, "Revenue": "$656,000", "Margin": "45.0%" },
          { "Product Name": "Strategic Architecture Audit", "Category": "Consulting", "Orders": 240, "Revenue": "$480,000", "Margin": "62.1%" },
          { "Product Name": "24/7 Premium SLA", "Category": "Support", "Orders": 1560, "Revenue": "$312,000", "Margin": "81.4%" }
        ]
      }
    }
  ],
  generated_queries: [
    { id: "q1", sql: "SELECT sum(amount) as revenue, month FROM sales GROUP BY month;" },
    { id: "q2", sql: "SELECT region, sum(amount) FROM sales GROUP BY region;" },
    { id: "q3", sql: "SELECT product_name, category, count(*), sum(amount) FROM orders GROUP BY 1, 2;" }
  ]
};

export default function GeneralDashboardPage() {
  const router = useRouter();
  const [sessionInput, setSessionInput] = useState("");
  const [showSessionModal, setShowSessionModal] = useState(false);
  const [followUpOpen, setFollowUpOpen] = useState(false);
  const [followUpQ, setFollowUpQ] = useState("");
  const [followUpLoading, setFollowUpLoading] = useState(false);
  const followUpRef = useRef<HTMLTextAreaElement>(null);

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
              ● Dashboard Live
            </span>
            <span className="text-xs text-muted-foreground/80">Direct View Mode</span>
          </div>
          <h1 className="dashboard-title">{DEMO_DASHBOARD.title}</h1>
          <div className="dashboard-meta">
            <span className="meta-badge">{DEMO_DASHBOARD.chart_count} Visualizations</span>
            <span className="meta-badge">{DEMO_DASHBOARD.generated_queries.length} AI Queries</span>
            <span className="meta-badge quality-badge" style={{ borderColor: "#5AD8A6", color: "#5AD8A6" }}>
              Data Quality: 96%
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
        <span>{DEMO_DASHBOARD.layout_rationale}</span>
      </div>

      {/* ── Dashboard Grid ─────────────────────────────────────────────────── */}
      <main className="dashboard-main">
        <DashboardGrid
          charts={DEMO_DASHBOARD.charts}
          sessionId="executive-overview"
        />
      </main>

      {/* ── Follow-up Modal / Drawer ───────────────────────────────────────── */}
      {followUpOpen && (
        <div className="follow-up-overlay" onClick={(e) => e.target === e.currentTarget && setFollowUpOpen(false)}>
          <div className="follow-up-panel">
            <div className="follow-up-header">
              <h3>Ask Question with AI Agents</h3>
              <button className="close-btn" onClick={() => setFollowUpOpen(false)}>✕</button>
            </div>
            <p className="follow-up-desc">
              Type any ad-hoc BI question. The multi-agent swarm will query the data lakehouse and append interactive visualizations.
            </p>
            <textarea
              ref={followUpRef}
              className="follow-up-input"
              rows={4}
              placeholder="e.g. Which region had highest quarterly revenue growth?"
              value={followUpQ}
              onChange={(e) => setFollowUpQ(e.target.value)}
            />
            <div className="follow-up-actions">
              <button className="btn-secondary" onClick={() => setFollowUpOpen(false)}>Cancel</button>
              <button
                className="btn-primary"
                onClick={handleFollowUp}
                disabled={followUpLoading || !followUpQ.trim()}
              >
                {followUpLoading ? "Synthesizing..." : "Analyze →"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Session Switcher Modal ─────────────────────────────────────────── */}
      {showSessionModal && (
        <div className="follow-up-overlay" onClick={(e) => e.target === e.currentTarget && setShowSessionModal(false)}>
          <div className="follow-up-panel">
            <div className="follow-up-header">
              <h3>Load Session Dashboard</h3>
              <button className="close-btn" onClick={() => setShowSessionModal(false)}>✕</button>
            </div>
            <p className="follow-up-desc">
              Enter any session ID generated from your pipeline runs to view its dedicated dashboard directly.
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
          <span className="footer-value mono">Direct Mode</span>
        </div>
        <div className="footer-stat">
          <span className="footer-label">Visualizations</span>
          <span className="footer-value">{DEMO_DASHBOARD.chart_count}</span>
        </div>
        <div className="footer-stat">
          <span className="footer-label">Engine</span>
          <span className="footer-value">Multi-Agent Swarm</span>
        </div>
        <div className="footer-stat">
          <span className="footer-label">Quality Score</span>
          <span className="footer-value" style={{ color: "#5AD8A6" }}>96%</span>
        </div>
      </footer>
    </div>
  );
}
