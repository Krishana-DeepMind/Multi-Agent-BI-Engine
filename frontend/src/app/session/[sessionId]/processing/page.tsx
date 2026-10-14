"use client";

import React, { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { usePipelineStore } from "@/store/pipeline-store";

const STEPS = [
  { key: "routing",   label: "Intent Router",        icon: "🧭", desc: "Classifying business intent and domain" },
  { key: "ingesting", label: "Ingestion & Schema",    icon: "📥", desc: "Loading file into DuckDB, profiling schema" },
  { key: "cleaning",  label: "Data Cleaning",         icon: "🧹", desc: "Fixing nulls, outliers, type mismatches" },
  { key: "featuring", label: "Feature Architect",     icon: "⚙️",  desc: "Engineering derived business metrics" },
  { key: "querying",  label: "Analytics Engine",      icon: "🔍", desc: "Generating and executing DuckDB SQL queries" },
  { key: "layouting", label: "Layout Agent",          icon: "📊", desc: "Selecting chart types and building ECharts configs" },
];

const statusToStep: Record<string, number> = {
  initiated: -1, routing: 0, ingesting: 1, cleaning: 2,
  featuring: 3, querying: 4, layouting: 5, verifying: 5, complete: 6,
};

export default function ProcessingPage() {
  const params = useParams();
  const router = useRouter();
  const sessionId = params.sessionId as string;

  const { activeStep, logs, isComplete, error, sessionMeta, connectSSE, reset } = usePipelineStore();

  useEffect(() => {
    if (!sessionId) return;
    reset();
    connectSSE(sessionId);
  }, [sessionId]);

  useEffect(() => {
    if (isComplete) {
      const timer = setTimeout(() => {
        router.push(`/dashboard/${sessionId}`);
      }, 2000);
      return () => clearTimeout(timer);
    }
  }, [isComplete, sessionId, router]);

  const lastLog = logs[logs.length - 1];
  const chartCount = (lastLog as Record<string, unknown>)?.chart_count as number | undefined;
  const queryCount = (lastLog as Record<string, unknown>)?.query_count as number | undefined;

  return (
    <div className="processing-page">

      {/* ── Header ───────────────────────────────────────────────────────── */}
      <div className="processing-header">
        <div className="processing-logo">AutoBI</div>
        <h1 className="processing-title">
          {isComplete ? "Analysis Complete!" : error ? "Pipeline Failed" : "Analyzing your data..."}
        </h1>
        {sessionMeta.fileName && sessionMeta.fileName !== "—" && (
          <div className="processing-file-info">
            <span className="file-badge">{sessionMeta.fileName}</span>
            {sessionMeta.rowCount > 0 && (
              <span className="file-badge">{sessionMeta.rowCount.toLocaleString()} rows</span>
            )}
            {sessionMeta.colCount > 0 && (
              <span className="file-badge">{sessionMeta.colCount} columns</span>
            )}
          </div>
        )}
      </div>

      {/* ── Pipeline Steps ────────────────────────────────────────────────── */}
      <div className="pipeline-steps">
        {STEPS.map((step, idx) => {
          const stepNum = statusToStep[step.key] ?? idx;
          let state: "pending" | "active" | "complete" | "error" = "pending";
          if (error && idx === activeStep) state = "error";
          else if (idx < activeStep || (isComplete && idx <= activeStep)) state = "complete";
          else if (idx === activeStep) state = "active";

          return (
            <div key={step.key} className={`pipeline-step step-${state}`}>
              <div className="step-indicator">
                <div className="step-icon-wrap">
                  {state === "complete" ? (
                    <span className="step-check">✓</span>
                  ) : state === "active" ? (
                    <span className="step-spinner"></span>
                  ) : state === "error" ? (
                    <span className="step-err">✕</span>
                  ) : (
                    <span className="step-num">{idx + 1}</span>
                  )}
                </div>
                {idx < STEPS.length - 1 && (
                  <div className={`step-connector ${idx < activeStep ? "connector-done" : ""}`}></div>
                )}
              </div>
              <div className="step-body">
                <div className="step-header-row">
                  <span className="step-emoji">{step.icon}</span>
                  <span className="step-label">{step.label}</span>
                </div>
                <div className="step-desc">{step.desc}</div>
              </div>
            </div>
          );
        })}
      </div>

      {/* ── Live Log Feed ─────────────────────────────────────────────────── */}
      <div className="log-feed">
        <div className="log-feed-title">Live Pipeline Log</div>
        <div className="log-entries">
          {logs.slice(-12).map((log, i) => (
            <div key={i} className={`log-entry log-${log.status}`}>
              <span className="log-agent">[{log.agent}]</span>
              <span className="log-message">{log.message}</span>
            </div>
          ))}
          {logs.length === 0 && (
            <div className="log-entry log-info">
              <span className="log-agent">[system]</span>
              <span className="log-message">Connecting to pipeline...</span>
            </div>
          )}
        </div>
      </div>

      {/* ── Complete / Error states ───────────────────────────────────────── */}
      {isComplete && (
        <div className="processing-complete">
          <div className="complete-confetti">🎉</div>
          <div className="complete-msg">
            Dashboard ready!{" "}
            {queryCount && <span>{queryCount} queries executed, </span>}
            {chartCount && <span>{chartCount} charts generated.</span>}
          </div>
          <div className="complete-redirect">Redirecting to dashboard...</div>
          <button
            className="btn-primary"
            onClick={() => router.push(`/dashboard/${sessionId}`)}
          >
            Open Dashboard →
          </button>
        </div>
      )}

      {error && (
        <div className="processing-error">
          <div className="error-icon">⚠️</div>
          <div className="error-msg">{error}</div>
          <button className="btn-secondary" onClick={() => router.push("/")}>
            Start Over
          </button>
        </div>
      )}
    </div>
  );
}