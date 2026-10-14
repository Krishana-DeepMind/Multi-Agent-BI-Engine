"use client";

import React, { useEffect, useState, useCallback, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import DashboardGrid from "@/components/DashboardGrid";
import { usePipelineStore } from "@/store/pipeline-store";

// ── Types ─────────────────────────────────────────────────────────────────────

interface ChartConfig {
  query_id: string;
  echarts_option: Record<string, unknown>;
  layout: { x: number; y: number; w: number; h: number };
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

interface DashboardData {
  session_id: string;
  title: string;
  layout_rationale: string;
  data_quality_score: number;
  chart_count: number;
  charts: ChartConfig[];
  generated_queries: Record<string, unknown>[];
  filters: FilterConfig[];
}

// ── Pipeline sidebar config ───────────────────────────────────────────────────

const PIPELINE_STEPS = [
  { key: "ingesting",  label: "Ingestion",  icon: "📥", desc: "Loading & profiling schema" },
  { key: "cleaning",   label: "Cleaning",   icon: "🧹", desc: "Quality fixes & imputation" },
  { key: "featuring",  label: "Feature Eng", icon: "⚙️",  desc: "Derived business metrics" },
  { key: "querying",   label: "Analytics",  icon: "🔍", desc: "DuckDB SQL queries" },
  { key: "layouting",  label: "Layout",     icon: "📊", desc: "Chart config generation" },
  { key: "complete",   label: "Dashboard",  icon: "✨", desc: "Ready to explore" },
];

const STATUS_TO_STEP: Record<string, number> = {
  initiated: -1, routing: 0,
  ingesting: 0, cleaning: 1, featuring: 2,
  querying: 3, layouting: 4, verifying: 4, complete: 5,
};

function stepState(idx: number, activeStep: number, isComplete: boolean, hasError: boolean) {
  if (isComplete) return "complete";
  if (hasError && idx === activeStep) return "error";
  if (idx < activeStep) return "complete";
  if (idx === activeStep) return "active";
  return "pending";
}

// ── Sidebar ───────────────────────────────────────────────────────────────────

function PipelineSidebar({
  activeStep,
  isComplete,
  error,
  sessionMeta,
}: {
  activeStep: number;
  isComplete: boolean;
  error: string | null;
  sessionMeta: { fileName: string; rowCount: number; colCount: number; fileType: string };
}) {
  return (
    <aside className="autobi-sidebar">
      <div className="sidebar-brand">
        <span className="sidebar-logo">AutoBI</span>
        <span className="sidebar-version">v4</span>
      </div>

      <div className="sidebar-section-title">Pipeline</div>
      <nav className="sidebar-pipeline">
        {PIPELINE_STEPS.map((step, idx) => {
          const state = stepState(idx, activeStep, isComplete, !!error);
          return (
            <div
              key={step.key}
              className={`sidebar-step step-${state}`}
              title={step.desc}
            >
              <div className="sidebar-step-icon">
                {state === "complete" ? "✓" :
                 state === "error"    ? "✕" :
                 state === "active"   ? <span className="sidebar-spinner" /> :
                 <span style={{ fontSize: "0.9rem" }}>{step.icon}</span>}
              </div>
              <div className="sidebar-step-info">
                <span className="sidebar-step-label">{step.label}</span>
                <span className="sidebar-step-desc">{step.desc}</span>
              </div>
              {idx < PIPELINE_STEPS.length - 1 && (
                <div className="sidebar-step-connector" />
              )}
            </div>
          );
        })}
      </nav>

      {sessionMeta.fileName && sessionMeta.fileName !== "—" && (
        <div className="sidebar-file-info">
          <div className="sidebar-file-label">Dataset</div>
          <div className="sidebar-file-name">{sessionMeta.fileName}</div>
          <div className="sidebar-file-meta">
            {sessionMeta.rowCount > 0 && (
              <span className="sidebar-badge">{sessionMeta.rowCount.toLocaleString()} rows</span>
            )}
            {sessionMeta.colCount > 0 && (
              <span className="sidebar-badge">{sessionMeta.colCount} cols</span>
            )}
            {sessionMeta.fileType && sessionMeta.fileType !== "—" && (
              <span className="sidebar-badge">{sessionMeta.fileType.toUpperCase()}</span>
            )}
          </div>
        </div>
      )}
    </aside>
  );
}

// ── Dashboard Page ────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const params    = useParams();
  const router    = useRouter();
  const sessionId = params.sessionId as string;

  const [dashboard, setDashboard]           = useState<DashboardData | null>(null);
  const [loading, setLoading]               = useState(true);
  const [error, setError]                   = useState<string | null>(null);
  const [followUpOpen, setFollowUpOpen]     = useState(false);
  const [followUpQ, setFollowUpQ]           = useState("");
  const [followUpLoading, setFollowUpLoading] = useState(false);
  const [followUpSessionId, setFollowUpSessionId] = useState<string | null>(null);
  const followUpRef = useRef<HTMLTextAreaElement>(null);

  // Reuse pipeline store for sidebar state
  const { activeStep, isComplete, error: pipelineError, sessionMeta } = usePipelineStore();

  const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

  const fetchDashboard = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await fetch(`${API}/api/pipeline/${sessionId}/dashboard`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
      }
      const data = await res.json();
      setDashboard(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load dashboard");
    } finally {
      setLoading(false);
    }
  }, [sessionId, API]);

  useEffect(() => {
    if (sessionId) fetchDashboard();
  }, [sessionId, fetchDashboard]);

  const handleFollowUp = async () => {
    if (!followUpQ.trim()) return;
    setFollowUpLoading(true);
    try {
      const res = await fetch(
        `${API}/api/pipeline/${sessionId}/follow-up?new_question=${encodeURIComponent(followUpQ)}`,
        { method: "POST" }
      );
      if (!res.ok) throw new Error("Follow-up request failed");
      const data = await res.json();
      setFollowUpSessionId(data.new_session_id);
      setTimeout(() => router.push(`/session/${data.new_session_id}/processing`), 1200);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Follow-up failed");
    } finally {
      setFollowUpLoading(false);
    }
  };

  const qualityColor = (score: number) => {
    if (score >= 0.8) return "#22C997";
    if (score >= 0.6) return "#F59E0B";
    return "#EF4444";
  };

  // ── Loading skeleton ─────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="autobi-shell">
        <PipelineSidebar
          activeStep={activeStep}
          isComplete={isComplete}
          error={pipelineError}
          sessionMeta={sessionMeta}
        />
        <div className="autobi-main">
          <div className="dashboard-header skeleton-header">
            <div className="skeleton skeleton-title" />
            <div className="skeleton skeleton-pill" />
          </div>
          <div className="dashboard-skeleton-grid">
            {[0, 1, 2, 3, 4].map((i) => (
              <div
                key={i}
                className={`skeleton skeleton-chart ${i < 4 ? "col-span-3" : "col-span-12"}`}
              />
            ))}
          </div>
        </div>
      </div>
    );
  }

  // ── Error state ──────────────────────────────────────────────────────────
  if (error) {
    return (
      <div className="autobi-shell">
        <PipelineSidebar
          activeStep={activeStep}
          isComplete={isComplete}
          error={pipelineError}
          sessionMeta={sessionMeta}
        />
        <div className="autobi-main">
          <div className="dashboard-error">
            <div className="error-icon">⚠️</div>
            <h2>Could not load dashboard</h2>
            <p>{error}</p>
            <div className="error-actions">
              <button onClick={fetchDashboard} className="btn-primary">↻ Retry</button>
              <button onClick={() => router.push("/upload")} className="btn-secondary">Upload Dataset</button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (!dashboard) return null;

  const effectiveMeta = {
    fileName: sessionMeta.fileName !== "—" ? sessionMeta.fileName : (dashboard.title || "Dataset"),
    rowCount: sessionMeta.rowCount > 0 ? sessionMeta.rowCount : (dashboard.charts.length > 0 ? dashboard.charts.length : 0),
    colCount: sessionMeta.colCount > 0 ? sessionMeta.colCount : 0,
    fileType: sessionMeta.fileType !== "—" ? sessionMeta.fileType : "BI",
  };

  // ── Main Dashboard ────────────────────────────────────────────────────────
  return (
    <div className="autobi-shell">
      {/* Left pipeline sidebar */}
      <PipelineSidebar
        activeStep={5}
        isComplete={true}
        error={pipelineError}
        sessionMeta={effectiveMeta}
      />

      <div className="autobi-main">

        {/* ── Sticky Header ──────────────────────────────────────────── */}
        <header className="dashboard-header">
          <div className="dashboard-header-left">
            <h1 className="dashboard-title">{dashboard.title}</h1>
            <div className="dashboard-meta">
              <span className="meta-badge">{dashboard.chart_count} charts</span>
              <span className="meta-badge">{dashboard.generated_queries.length} queries</span>
              <span
                className="meta-badge quality-badge"
                style={{ color: qualityColor(dashboard.data_quality_score), borderColor: qualityColor(dashboard.data_quality_score) }}
              >
                Quality: {(dashboard.data_quality_score * 100).toFixed(0)}%
              </span>
            </div>
          </div>

          <div className="dashboard-header-right">
            <button
              className="btn-follow-up"
              onClick={() => { setFollowUpOpen(true); setTimeout(() => followUpRef.current?.focus(), 100); }}
            >
              + Ask Another Question
            </button>
            <button className="btn-secondary" onClick={fetchDashboard}>
              ↻ Refresh
            </button>
          </div>
        </header>

        {/* ── Layout Rationale bar ────────────────────────────────────── */}
        {dashboard.layout_rationale && (
          <div className="layout-rationale-bar">
            <span className="rationale-icon">🧠</span>
            <span>{dashboard.layout_rationale}</span>
          </div>
        )}

        {/* ── Dashboard Grid ──────────────────────────────────────────── */}
        <main className="dashboard-main">
          <DashboardGrid
            charts={dashboard.charts}
            sessionId={sessionId}
            filters={dashboard.filters ?? []}
          />
        </main>

        {/* ── Follow-up Sidebar ──────────────────────────────────────── */}
        {followUpOpen && (
          <div
            className="follow-up-overlay"
            onClick={(e) => e.target === e.currentTarget && setFollowUpOpen(false)}
          >
            <div className="follow-up-panel">
              <div className="follow-up-header">
                <h3>Ask Another Question</h3>
                <button className="close-btn" onClick={() => setFollowUpOpen(false)}>✕</button>
              </div>
              <p className="follow-up-desc">
                Ask a new question about your data. The pipeline reuses your cleaned dataset and jumps directly to analytics.
              </p>
              <textarea
                ref={followUpRef}
                className="follow-up-input"
                rows={4}
                placeholder="e.g. Which department has the highest average salary?"
                value={followUpQ}
                onChange={(e) => setFollowUpQ(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && e.ctrlKey) handleFollowUp(); }}
              />
              {followUpSessionId && (
                <div className="follow-up-success">✓ New analysis started! Redirecting...</div>
              )}
              <div className="follow-up-actions">
                <button className="btn-secondary" onClick={() => setFollowUpOpen(false)}>Cancel</button>
                <button
                  className="btn-primary"
                  onClick={handleFollowUp}
                  disabled={followUpLoading || !followUpQ.trim()}
                >
                  {followUpLoading ? "Starting..." : "Analyze →"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ── Footer ─────────────────────────────────────────────────── */}
        <footer className="dashboard-footer">
          <div className="footer-stat">
            <span className="footer-label">Session</span>
            <span className="footer-value mono">{sessionId.slice(0, 8)}…</span>
          </div>
          <div className="footer-stat">
            <span className="footer-label">Charts</span>
            <span className="footer-value">{dashboard.chart_count}</span>
          </div>
          <div className="footer-stat">
            <span className="footer-label">Queries</span>
            <span className="footer-value">{dashboard.generated_queries.length}</span>
          </div>
          <div className="footer-stat">
            <span className="footer-label">Data Quality</span>
            <span className="footer-value" style={{ color: qualityColor(dashboard.data_quality_score) }}>
              {(dashboard.data_quality_score * 100).toFixed(0)}%
            </span>
          </div>
        </footer>

      </div>{/* end autobi-main */}
    </div>/* end autobi-shell */
  );
}