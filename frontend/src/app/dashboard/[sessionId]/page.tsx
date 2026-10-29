"use client";

import React, { useEffect, useState, useCallback, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import DashboardGrid from "@/components/DashboardGrid";
import { usePipelineStore } from "@/store/pipeline-store";

import UploadDatasetModal from "@/components/UploadDatasetModal";
import DataChatDrawer from "@/components/DataChatDrawer";

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
  quality_sub_scores?: {
    completeness?: number;
    uniqueness?: number;
    type_consistency?: number;
    business_integrity?: number;
    remaining_issues?: string[];
  };
  review_notes?: Array<{ column?: string; action?: string; note?: string }>;
  chart_count: number;
  charts: ChartConfig[];
  generated_queries: Record<string, unknown>[];
  filters: FilterConfig[];
}

interface SessionEntry {
  sessionId: string;
  fileName: string;
  date: string;
  rowCount: number;
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
  onNewDataset,
  currentSessionId,
  recentSessions = [],
  onSelectSession,
}: {
  activeStep: number;
  isComplete: boolean;
  error: string | null;
  sessionMeta: { fileName: string; rowCount: number; colCount: number; fileType: string };
  onNewDataset: () => void;
  currentSessionId?: string;
  recentSessions?: SessionEntry[];
  onSelectSession?: (id: string) => void;
}) {
  return (
    <aside className="autobi-sidebar">
      <div className="sidebar-brand">
        <span className="sidebar-logo">Auto<span className="logo-accent">BI</span></span>
        <span className="sidebar-version">v4</span>
      </div>

      {/* Prominent + New Dataset Button */}
      <button
        id="btn-new-dataset-sidebar"
        className="sidebar-upload-btn"
        onClick={onNewDataset}
        title="Upload a new dataset to start a fresh analysis"
      >
        <span style={{ fontSize: "1.1rem", fontWeight: 700, lineHeight: 1 }}>+</span>
        <span>New Dataset</span>
      </button>

      {/* Multiple Sessions Switcher if recent sessions exist */}
      {recentSessions && recentSessions.length > 1 && (
        <div style={{ marginBottom: 14, padding: "0 16px 10px", borderBottom: "1px solid var(--abi-border)" }}>
          <div className="sidebar-section-title" style={{ padding: "0 0 6px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span>Datasets / Sessions</span>
            <span style={{ fontSize: "0.68rem", opacity: 0.6 }}>{recentSessions.length}</span>
          </div>
          <select
            value={currentSessionId}
            onChange={(e) => onSelectSession?.(e.target.value)}
            style={{
              width: "100%",
              padding: "6px 8px",
              borderRadius: "var(--abi-radius-sm)",
              background: "var(--abi-surface)",
              border: "1px solid var(--abi-border)",
              fontSize: "0.75rem",
              color: "var(--abi-text)",
              cursor: "pointer",
              outline: "none",
            }}
          >
            {recentSessions.map((s) => (
              <option key={s.sessionId} value={s.sessionId}>
                {s.fileName} ({s.rowCount ? `${s.rowCount} rows` : "Dashboard"})
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="sidebar-section-title">Pipeline Status</div>
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
                 <span>{String(idx + 1).padStart(2, "0")}</span>}
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
  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const [recentSessions, setRecentSessions] = useState<SessionEntry[]>([]);
  const [followUpOpen, setFollowUpOpen]     = useState(false);
  const [followUpQ, setFollowUpQ]           = useState("");
  const [followUpLoading, setFollowUpLoading] = useState(false);
  const [followUpSessionId, setFollowUpSessionId] = useState<string | null>(null);
  const [qualityModalOpen, setQualityModalOpen] = useState(false);
  const followUpRef = useRef<HTMLTextAreaElement>(null);

  // Reuse pipeline store for sidebar state
  const { activeStep, isComplete, error: pipelineError, sessionMeta } = usePipelineStore();

  const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

  // Load recent sessions from localStorage
  useEffect(() => {
    try {
      const stored = JSON.parse(localStorage.getItem("autobi_sessions") || "[]");
      setRecentSessions(stored);
    } catch {}
  }, [sessionId]);

  // Register current session in recent sessions list
  useEffect(() => {
    if (dashboard && sessionId) {
      try {
        const stored: SessionEntry[] = JSON.parse(localStorage.getItem("autobi_sessions") || "[]");
        const idx = stored.findIndex((s) => s.sessionId === sessionId);
        const currentMeta: SessionEntry = {
          sessionId,
          fileName: dashboard.title || "Dataset",
          date: new Date().toISOString(),
          rowCount: dashboard.chart_count,
        };
        if (idx === -1) {
          const updated = [currentMeta, ...stored].slice(0, 15);
          localStorage.setItem("autobi_sessions", JSON.stringify(updated));
          setRecentSessions(updated);
        }
      } catch {}
    }
  }, [dashboard, sessionId]);

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
    if (score >= 0.8) return "#15803D"; // Forest emerald
    if (score >= 0.6) return "#B45309"; // Warm bronze
    return "#991B1B"; // Enterprise crimson
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
          onNewDataset={() => setUploadModalOpen(true)}
          currentSessionId={sessionId}
          recentSessions={recentSessions}
          onSelectSession={(id) => router.push(`/dashboard/${id}`)}
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
        <UploadDatasetModal
          isOpen={uploadModalOpen}
          onClose={() => setUploadModalOpen(false)}
          currentSessionId={sessionId}
        />
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
          onNewDataset={() => setUploadModalOpen(true)}
          currentSessionId={sessionId}
          recentSessions={recentSessions}
          onSelectSession={(id) => router.push(`/dashboard/${id}`)}
        />
        <div className="autobi-main">
          <div className="dashboard-error">
            <div className="error-icon">⚠️</div>
            <h2>Could not load dashboard</h2>
            <p>{error}</p>
            <div className="error-actions">
              <button onClick={fetchDashboard} className="btn-primary">↻ Retry</button>
              <button onClick={() => setUploadModalOpen(true)} className="btn-secondary">+ Upload Dataset</button>
            </div>
          </div>
        </div>
        <UploadDatasetModal
          isOpen={uploadModalOpen}
          onClose={() => setUploadModalOpen(false)}
          currentSessionId={sessionId}
        />
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
        onNewDataset={() => setUploadModalOpen(true)}
        currentSessionId={sessionId}
        recentSessions={recentSessions}
        onSelectSession={(id) => router.push(`/dashboard/${id}`)}
      />

      <div className="autobi-main">

        {/* ── Sticky Header ──────────────────────────────────────────── */}
        <header className="dashboard-header">
          <div className="dashboard-header-left">
            <h1 className="dashboard-title">{dashboard.title}</h1>
            <div className="dashboard-meta">
              <span className="meta-badge">{dashboard.chart_count} charts</span>
              <span className="meta-badge">{dashboard.generated_queries.length} queries</span>
              <button
                className="meta-badge quality-badge"
                style={{
                  color: qualityColor(dashboard.data_quality_score),
                  borderColor: qualityColor(dashboard.data_quality_score),
                  cursor: "pointer",
                  background: "transparent",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "4px"
                }}
                onClick={() => setQualityModalOpen(true)}
                title="Click to view detailed Data Quality Audit & Calculation"
              >
                Quality: {(dashboard.data_quality_score * 100).toFixed(0)}% <span style={{ fontSize: "0.75rem", opacity: 0.8 }}>ⓘ</span>
              </button>
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
            <span className="rationale-badge">RATIONALE</span>
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

        {/* ── Data Quality Audit Drawer ─────────────────────────────────── */}
        {qualityModalOpen && (
          <div
            className="follow-up-overlay"
            onClick={(e) => e.target === e.currentTarget && setQualityModalOpen(false)}
          >
            <div className="follow-up-panel" style={{ width: "460px", overflowY: "auto" }}>
              <div className="follow-up-header">
                <div>
                  <h3 style={{ fontSize: "1.05rem" }}>Data Quality Audit</h3>
                  <span style={{ fontSize: "0.78rem", color: "var(--abi-text-muted)" }}>
                    Reproducible Multi-Factor Assessment
                  </span>
                </div>
                <button className="close-btn" onClick={() => setQualityModalOpen(false)}>✕</button>
              </div>

              {/* Overall Score Banner */}
              <div style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "12px 16px",
                borderRadius: "var(--abi-radius-sm)",
                background: "#FEF2F2",
                border: "1px solid #FECACA"
              }}>
                <div>
                  <span style={{ fontSize: "0.8rem", color: "var(--abi-text-secondary)", display: "block" }}>
                    Composite Score
                  </span>
                  <span style={{ fontSize: "1.4rem", fontWeight: 800, color: qualityColor(dashboard.data_quality_score) }}>
                    {(dashboard.data_quality_score * 100).toFixed(1)}%
                  </span>
                </div>
                <div style={{ fontSize: "0.75rem", textAlign: "right", color: "var(--abi-text-muted)", maxWidth: "200px" }}>
                  Formula: 35% Completeness + 25% Type Consistency + 20% Integrity + 20% Uniqueness
                </div>
              </div>

              {/* 4 Measurable Sub-Scores */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
                <div style={{ padding: "10px", background: "#F8FAFC", border: "1px solid var(--abi-border)", borderRadius: "var(--abi-radius-sm)" }}>
                  <span style={{ fontSize: "0.72rem", color: "var(--abi-text-muted)", display: "block" }}>Completeness (35%)</span>
                  <span style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--abi-text)" }}>
                    {dashboard.quality_sub_scores?.completeness != null ? `${(dashboard.quality_sub_scores.completeness * 100).toFixed(1)}%` : "—"}
                  </span>
                  <span style={{ fontSize: "0.68rem", color: "var(--abi-text-muted)", display: "block" }}>Non-null cell ratio</span>
                </div>

                <div style={{ padding: "10px", background: "#F8FAFC", border: "1px solid var(--abi-border)", borderRadius: "var(--abi-radius-sm)" }}>
                  <span style={{ fontSize: "0.72rem", color: "var(--abi-text-muted)", display: "block" }}>Uniqueness (20%)</span>
                  <span style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--abi-text)" }}>
                    {dashboard.quality_sub_scores?.uniqueness != null ? `${(dashboard.quality_sub_scores.uniqueness * 100).toFixed(1)}%` : "—"}
                  </span>
                  <span style={{ fontSize: "0.68rem", color: "var(--abi-text-muted)", display: "block" }}>Row deduplication ratio</span>
                </div>

                <div style={{ padding: "10px", background: "#F8FAFC", border: "1px solid var(--abi-border)", borderRadius: "var(--abi-radius-sm)" }}>
                  <span style={{ fontSize: "0.72rem", color: "var(--abi-text-muted)", display: "block" }}>Type Consistency (25%)</span>
                  <span style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--abi-text)" }}>
                    {dashboard.quality_sub_scores?.type_consistency != null ? `${(dashboard.quality_sub_scores.type_consistency * 100).toFixed(1)}%` : "—"}
                  </span>
                  <span style={{ fontSize: "0.68rem", color: "var(--abi-text-muted)", display: "block" }}>Physical & semantic cast</span>
                </div>

                <div style={{ padding: "10px", background: "#F8FAFC", border: "1px solid var(--abi-border)", borderRadius: "var(--abi-radius-sm)" }}>
                  <span style={{ fontSize: "0.72rem", color: "var(--abi-text-muted)", display: "block" }}>Business Integrity (20%)</span>
                  <span style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--abi-text)" }}>
                    {dashboard.quality_sub_scores?.business_integrity != null ? `${(dashboard.quality_sub_scores.business_integrity * 100).toFixed(1)}%` : "—"}
                  </span>
                  <span style={{ fontSize: "0.68rem", color: "var(--abi-text-muted)", display: "block" }}>Identifiers & bounds</span>
                </div>
              </div>

              {/* Remaining Issues & Review Notes */}
              <div>
                <h4 style={{ fontSize: "0.82rem", fontWeight: 700, margin: "10px 0 6px 0", color: "var(--abi-text)" }}>
                  Remaining Issues & Audit Notes
                </h4>
                {dashboard.quality_sub_scores?.remaining_issues && dashboard.quality_sub_scores.remaining_issues.length > 0 && (
                  <ul style={{ margin: "0 0 10px 0", paddingLeft: "18px", fontSize: "0.75rem", color: "var(--abi-text-secondary)" }}>
                    {dashboard.quality_sub_scores.remaining_issues.map((issue, idx) => (
                      <li key={idx} style={{ marginBottom: "4px" }}>{issue}</li>
                    ))}
                  </ul>
                )}
                {dashboard.review_notes && dashboard.review_notes.length > 0 && (
                  <div style={{ display: "flex", flexDirection: "column", gap: "6px", maxHeight: "220px", overflowY: "auto" }}>
                    {dashboard.review_notes.slice(0, 10).map((rn, idx) => (
                      <div key={idx} style={{ padding: "6px 8px", background: "#F8FAFC", borderRadius: "4px", fontSize: "0.73rem", borderLeft: "3px solid #991B1B" }}>
                        <strong>{rn.column}:</strong> {rn.note}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* ── Conversational Data Chatbot Drawer ──────────────────────────────────────── */}
        <DataChatDrawer
          isOpen={followUpOpen}
          onClose={() => setFollowUpOpen(false)}
          sessionId={sessionId}
          datasetTitle={dashboard.title || sessionMeta.fileName || "Dataset"}
        />

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

      {/* Upload Modal (accessible without losing active dashboard) */}
      <UploadDatasetModal
        isOpen={uploadModalOpen}
        onClose={() => setUploadModalOpen(false)}
        currentSessionId={sessionId}
      />
    </div>/* end autobi-shell */
  );
}