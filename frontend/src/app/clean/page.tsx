"use client"

import React, { useState, useCallback, useRef, useEffect } from "react"
import { useDropzone } from "react-dropzone"

// ─── Types ────────────────────────────────────────────────────────────────────
type OperationIcon = "wrench" | "scissors" | "wand" | "trash" | "copy" | "filter" | "calendar"

interface QualityBefore {
  score: number
  score_pct: number
  rows: number
  cols: number
  null_cells: number
}

interface QualityAfter {
  score: number
  score_pct: number
  rows_after: number
  cols_after: number
  null_cells: number
  improvement: number
  columns_dropped: string[]
}

interface OperationEvent {
  index: number
  column: string
  operation: string
  strategy: string
  rows_affected: number
  before_nulls?: number
  after_nulls?: number
  rationale: string
  icon: OperationIcon
  polars_code: string
}

interface SkippedOperation {
  index: number
  column: string
  operation: string
  reason: string
}

type Phase = "idle" | "uploading" | "running" | "done" | "error"

// ─── SVG Circular Gauge ──────────────────────────────────────────────────────
function CircularGauge({
  pct,
  label,
  color,
  size = 140,
}: {
  pct: number
  label: string
  color: string
  size?: number
}) {
  const radius = (size - 20) / 2
  const circ = 2 * Math.PI * radius
  const filled = ((pct / 100) * circ)
  const cx = size / 2
  const cy = size / 2

  return (
    <div className="flex flex-col items-center gap-1">
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
        <circle
          cx={cx} cy={cy} r={radius}
          fill="none" stroke="#1e293b" strokeWidth={12}
        />
        <circle
          cx={cx} cy={cy} r={radius}
          fill="none"
          stroke={color}
          strokeWidth={12}
          strokeDasharray={`${circ}`}
          strokeDashoffset={circ - filled}
          strokeLinecap="round"
          style={{ transition: "stroke-dashoffset 1s ease" }}
        />
      </svg>
      <div style={{ marginTop: -size / 2 - 8 }} className="flex flex-col items-center pointer-events-none select-none">
        <span className="text-2xl font-bold text-white">{pct.toFixed(1)}%</span>
        <span className="text-xs text-slate-400">{label}</span>
      </div>
      <div style={{ marginTop: size / 2 - 20 }} />
    </div>
  )
}

// ─── Icon map ────────────────────────────────────────────────────────────────
function OpIcon({ icon }: { icon: OperationIcon }) {
  const base = "h-4 w-4"
  const icons: Record<OperationIcon, React.ReactNode> = {
    wrench: <svg className={base} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>,
    scissors: <svg className={base} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.121 14.121L19 19m-7-7l7-7m-7 7l-2.879 2.879M12 12L9.121 9.121m0 5.758a3 3 0 10-4.243 4.243 3 3 0 004.243-4.243zm0-5.758a3 3 0 10-4.243-4.243 3 3 0 004.243 4.243z" /></svg>,
    wand: <svg className={base} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 3l4 4-4 4 4 4-4 4M19 3l-4 4 4 4-4 4 4 4M12 3v18" /></svg>,
    trash: <svg className={base} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>,
    copy: <svg className={base} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" /></svg>,
    filter: <svg className={base} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z" /></svg>,
    calendar: <svg className={base} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>,
  }
  return <>{icons[icon] ?? icons.wrench}</>
}

const OP_COLORS: Record<string, string> = {
  cast_type: "#06b6d4",
  trim_whitespace: "#a78bfa",
  fill_null: "#34d399",
  normalize: "#34d399",
  drop_column: "#f87171",
  deduplicate: "#fbbf24",
  remove_outlier: "#fb923c",
  remove_outliers: "#fb923c",
  parse_date: "#60a5fa",
}

// ─── Render Cell Value Helper ────────────────────────────────────────────────
function renderCellValue(val: string | null) {
  if (val === null || val === undefined || val === "" || val === "None" || val === "null" || val === "NaN") {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono bg-slate-800/80 text-slate-500 italic border border-slate-700/50">
        N/A
      </span>
    )
  }
  if (val === "Unknown Customer") {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono bg-violet-950/40 text-violet-300 border border-violet-800/40">
        Unknown Customer
      </span>
    )
  }
  if (val === "Unspecified") {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono bg-amber-950/40 text-amber-300/80 border border-amber-800/40">
        Unspecified
      </span>
    )
  }
  if (val === "N/A") {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono bg-slate-800/80 text-slate-400 italic border border-slate-700/50">
        N/A
      </span>
    )
  }
  if (["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"].includes(val)) {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold bg-rose-950/40 text-rose-300 border border-rose-800/40">
        {val}
      </span>
    )
  }
  if (val === "+") {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-950/40 text-emerald-300 border border-emerald-800/40">
        +
      </span>
    )
  }
  if (val === "-") {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-slate-800 text-slate-300 border border-slate-700">
        -
      </span>
    )
  }
  if (val === "Active") {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-emerald-950/50 text-emerald-300 border border-emerald-800/50">
        Active
      </span>
    )
  }
  if (val === "Terminated") {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-rose-950/50 text-rose-300 border border-rose-800/50">
        Terminated
      </span>
    )
  }
  if (val === "On Leave") {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-950/50 text-amber-300 border border-amber-800/50">
        On Leave
      </span>
    )
  }
  if (val === "Resigned") {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-800 text-slate-300 border border-slate-700">
        Resigned
      </span>
    )
  }
  if (val === "Full-Time") {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-sky-950/50 text-sky-300 border border-sky-800/50">
        Full-Time
      </span>
    )
  }
  if (val === "Part-Time") {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-cyan-950/50 text-cyan-300 border border-cyan-800/50">
        Part-Time
      </span>
    )
  }
  if (val === "Contract") {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-purple-950/50 text-purple-300 border border-purple-800/50">
        Contract
      </span>
    )
  }
  if (val === "Intern") {
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-indigo-950/50 text-indigo-300 border border-indigo-800/50">
        Intern
      </span>
    )
  }
  return <span className="text-slate-300">{val}</span>
}

// ─── Main Page ───────────────────────────────────────────────────────────────
export default function CleanDemoPage() {
  const API = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"

  const [phase, setPhase] = useState<Phase>("idle")
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [statusMsg, setStatusMsg] = useState<string>("")
  const [errorMsg, setErrorMsg] = useState<string>("")

  const [qualityBefore, setQualityBefore] = useState<QualityBefore | null>(null)
  const [qualityAfter, setQualityAfter] = useState<QualityAfter | null>(null)
  const [operations, setOperations] = useState<OperationEvent[]>([])
  const [skipped, setSkipped] = useState<SkippedOperation[]>([])
  const [previewCols, setPreviewCols] = useState<string[]>([])
  const [previewRows, setPreviewRows] = useState<Record<string, string | null>[]>([])

  const feedRef = useRef<HTMLDivElement>(null)
  const esRef = useRef<EventSource | null>(null)

  // Auto-scroll operation feed
  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight
    }
  }, [operations, statusMsg])

  // ── Dropzone ──
  const onDrop = useCallback((accepted: File[]) => {
    if (accepted.length) {
      setSelectedFile(accepted[0])
      setPhase("idle")
      setErrorMsg("")
      setQualityBefore(null)
      setQualityAfter(null)
      setOperations([])
      setSkipped([])
      setPreviewCols([])
      setPreviewRows([])
      setJobId(null)
    }
  }, [])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      "text/csv": [".csv"],
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
      "application/json": [".json"],
      "application/octet-stream": [".parquet"],
    },
    maxSize: 100 * 1024 * 1024,
    multiple: false,
    disabled: phase === "running" || phase === "uploading",
  })

  // ── Start Pipeline ──
  const handleRun = async () => {
    if (!selectedFile) return
    setPhase("uploading")
    setStatusMsg("Uploading file…")
    setErrorMsg("")
    setOperations([])
    setSkipped([])
    setQualityBefore(null)
    setQualityAfter(null)
    setPreviewCols([])
    setPreviewRows([])

    const fd = new FormData()
    fd.append("file", selectedFile)

    try {
      const res = await fetch(`${API}/api/cleaning/run`, { method: "POST", body: fd })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Upload failed" }))
        throw new Error(err.detail || "Upload failed")
      }
      const data = await res.json()
      const id: string = data.job_id
      setJobId(id)
      setPhase("running")
      setStatusMsg("Connecting to agent stream…")
      openSSE(id)
    } catch (e: any) {
      setPhase("error")
      setErrorMsg(e.message || "Unknown error")
    }
  }

  // ── SSE Connection ──
  const openSSE = (id: string) => {
    if (esRef.current) esRef.current.close()
    const es = new EventSource(`${API}/api/cleaning/${id}/stream`)
    esRef.current = es

    es.onmessage = (ev) => {
      try {
        const event = JSON.parse(ev.data)
        handleEvent(event)
      } catch {}
    }
    es.onerror = () => {
      setPhase("error")
      setErrorMsg("SSE connection lost.")
      es.close()
    }
  }

  const handleEvent = (event: any) => {
    switch (event.type) {
      case "status":
        setStatusMsg(event.message)
        break
      case "quality_before":
        setQualityBefore(event)
        setStatusMsg("Schema profiled — quality score captured.")
        break
      case "operation":
        setOperations(prev => [...prev, event])
        setStatusMsg(`Applied: ${event.operation} on "${event.column}"`)
        break
      case "operation_skipped":
        setSkipped(prev => [...prev, event])
        break
      case "quality_after":
        setQualityAfter(event)
        setStatusMsg("Calculating final data quality score…")
        break
      case "preview":
        setPreviewCols(event.columns)
        setPreviewRows(event.rows)
        setStatusMsg("Cleaned data preview ready.")
        break
      case "done":
        setPhase("done")
        setStatusMsg("✅ Cleaning pipeline complete.")
        esRef.current?.close()
        break
      case "error":
        setPhase("error")
        setErrorMsg(event.message || "Pipeline error.")
        esRef.current?.close()
        break
      case "heartbeat":
        break
    }
  }

  const handleDownload = () => {
    if (!jobId) return
    window.location.href = `${API}/api/cleaning/${jobId}/download`
  }

  const handleResume = async () => {
    if (!jobId) return
    setPhase("running")
    setStatusMsg("Resuming pipeline from last checkpoint…")
    setErrorMsg("")
    try {
      const res = await fetch(`${API}/api/cleaning/${jobId}/resume`, { method: "POST" })
      if (!res.ok) {
        throw new Error("Failed to resume job")
      }
      openSSE(jobId)
    } catch (e: any) {
      setPhase("error")
      setErrorMsg(e.message || "Failed to resume pipeline")
    }
  }

  const handleStartOver = () => {
    setPhase("idle")
    setSelectedFile(null)
    setJobId(null)
    setErrorMsg("")
    setStatusMsg("")
    setQualityBefore(null)
    setQualityAfter(null)
    setOperations([])
    setSkipped([])
    setPreviewCols([])
    setPreviewRows([])
  }

  const formatSize = (b: number) => {
    if (b < 1024) return `${b} B`
    if (b < 1048576) return `${(b / 1024).toFixed(1)} KB`
    return `${(b / 1048576).toFixed(2)} MB`
  }

  const isRunning = phase === "running" || phase === "uploading"

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <main className="min-h-screen bg-[#080B11] text-slate-100 font-mono selection:bg-cyan-500/30">
      {/* Background grid */}
      <div className="fixed inset-0 bg-[linear-gradient(to_right,#1e293b15_1px,transparent_1px),linear-gradient(to_bottom,#1e293b15_1px,transparent_1px)] bg-[size:4rem_4rem] [mask-image:radial-gradient(ellipse_80%_60%_at_50%_40%,#000_70%,transparent_100%)] pointer-events-none" />
      <div className="fixed -top-32 left-1/2 -translate-x-1/2 w-[700px] h-[300px] bg-cyan-500/8 blur-[140px] rounded-full pointer-events-none" />
      <div className="fixed -bottom-32 left-1/2 -translate-x-1/2 w-[700px] h-[300px] bg-violet-500/8 blur-[140px] rounded-full pointer-events-none" />

      <div className="relative z-10 max-w-7xl mx-auto px-4 py-8 space-y-6">

        {/* ── Header ── */}
        <div className="border border-slate-800/80 bg-slate-900/60 backdrop-blur-md rounded-2xl p-6">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="h-10 w-10 rounded-xl bg-gradient-to-br from-cyan-500/20 to-violet-500/20 border border-cyan-500/40 flex items-center justify-center shadow-[0_0_20px_rgba(6,182,212,0.2)]">
                <svg className="h-5 w-5 text-cyan-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18" />
                </svg>
              </div>
              <div>
                <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
                  Cleaning Agent
                  <span className="text-xs px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-400 border border-cyan-500/30">LIVE DEMO</span>
                </h1>
                <p className="text-xs text-slate-400 mt-0.5">Upload messy data → watch the AI clean it in real-time</p>
              </div>
            </div>
            <div className="flex items-center gap-2 text-xs">
              <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md border ${isRunning ? "bg-amber-500/10 border-amber-500/30 text-amber-400" : phase === "done" ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-400" : "bg-slate-800 border-slate-700 text-slate-400"}`}>
                <span className={`h-2 w-2 rounded-full ${isRunning ? "bg-amber-400 animate-pulse" : phase === "done" ? "bg-emerald-400" : "bg-slate-500"}`} />
                {isRunning ? "AGENT RUNNING" : phase === "done" ? "COMPLETE" : "READY"}
              </div>
              <div className="px-2.5 py-1 rounded-md bg-slate-800 border border-slate-700 text-slate-300">
                Ollama + Gemini
              </div>
            </div>
          </div>
        </div>

        {/* ── Main Grid ── */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

          {/* ── LEFT COLUMN: Upload + Quality Gauges ── */}
          <div className="flex flex-col gap-6">

            {/* Upload Card */}
            <div className="border border-slate-800/80 bg-slate-900/40 backdrop-blur-md rounded-2xl p-5 space-y-4">
              <h2 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
                <svg className="h-4 w-4 text-cyan-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                </svg>
                Upload Dataset
              </h2>

              {/* Dropzone */}
              <div
                {...getRootProps()}
                className={`border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-all duration-200 flex flex-col items-center gap-3 ${
                  isDragActive
                    ? "border-cyan-400 bg-cyan-950/20"
                    : isRunning
                    ? "border-slate-700 opacity-50 cursor-not-allowed"
                    : "border-slate-700/70 hover:border-cyan-500/60 hover:bg-slate-800/20"
                }`}
              >
                <input {...getInputProps()} />
                <div className="h-12 w-12 rounded-xl bg-slate-800 border border-slate-700 flex items-center justify-center text-slate-400">
                  <svg className="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                  </svg>
                </div>
                <p className="text-xs text-slate-400">
                  {isDragActive ? "Drop it!" : "Drag & drop or click to browse"}
                </p>
                <div className="flex flex-wrap gap-1 justify-center">
                  {["CSV", "XLSX", "JSON", "Parquet"].map(f => (
                    <span key={f} className="text-[10px] px-2 py-0.5 rounded bg-slate-800 border border-slate-700 text-slate-300">{f}</span>
                  ))}
                </div>
              </div>

              {/* Selected file */}
              {selectedFile && (
                <div className="flex items-center gap-3 p-3 rounded-xl bg-slate-950/60 border border-slate-800">
                  <div className="h-9 w-9 rounded-lg bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center text-cyan-400 shrink-0">
                    <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                    </svg>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-semibold text-white truncate">{selectedFile.name}</div>
                    <div className="text-[10px] text-slate-400">{formatSize(selectedFile.size)}</div>
                  </div>
                </div>
              )}

              {/* Error & Recovery Banner */}
              {phase === "error" && (
                <div className="p-4 rounded-xl bg-red-950/40 border border-red-500/40 text-red-200 text-xs space-y-3">
                  <div className="flex items-start gap-2">
                    <svg className="h-5 w-5 shrink-0 text-red-400 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                    </svg>
                    <div>
                      <div className="font-bold text-red-300">Pipeline Execution Interrupted</div>
                      <div className="text-[11px] text-red-300/80 mt-1">{errorMsg || "A transient error occurred during execution."}</div>
                    </div>
                  </div>
                  <div className="flex gap-2 pt-1">
                    {jobId && (
                      <button
                        onClick={handleResume}
                        className="flex-1 py-2 px-3 rounded-lg bg-amber-500/20 hover:bg-amber-500/30 border border-amber-500/40 text-amber-300 font-bold text-xs transition-colors flex items-center justify-center gap-1.5"
                      >
                        <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                        </svg>
                        Retry Pipeline
                      </button>
                    )}
                    <button
                      onClick={handleStartOver}
                      className="flex-1 py-2 px-3 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 font-semibold text-xs transition-colors flex items-center justify-center gap-1.5"
                    >
                      <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                      Start Over
                    </button>
                  </div>
                </div>
              )}

              {/* Run button */}
              <button
                onClick={handleRun}
                disabled={!selectedFile || isRunning}
                className="w-full py-3 rounded-xl font-bold text-sm tracking-wide transition-all duration-200 bg-gradient-to-r from-cyan-500 to-violet-500 text-slate-950 hover:from-cyan-400 hover:to-violet-400 shadow-[0_0_20px_rgba(6,182,212,0.25)] disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-2"
              >
                {isRunning ? (
                  <>
                    <svg className="h-4 w-4 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                    </svg>
                    Agent Running…
                  </>
                ) : (
                  <>
                    <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                    </svg>
                    Run Cleaning Agent
                  </>
                )}
              </button>
            </div>

            {/* Quality Score Panel */}
            <div className="border border-slate-800/80 bg-slate-900/40 backdrop-blur-md rounded-2xl p-5 space-y-4">
              <h2 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
                <svg className="h-4 w-4 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                </svg>
                Data Quality Score
              </h2>

              {!qualityBefore && !qualityAfter && (
                <div className="flex flex-col items-center justify-center py-8 text-slate-500 text-xs gap-2">
                  <svg className="h-10 w-10 text-slate-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                  </svg>
                  Run the agent to see quality scores
                </div>
              )}

              {qualityBefore && (
                <div className="flex justify-around items-center">
                  <div className="text-center">
                    <CircularGauge pct={qualityBefore.score_pct} label="BEFORE" color="#f87171" size={130} />
                    <div className="text-[10px] text-slate-400 mt-1">{qualityBefore.rows.toLocaleString()} rows · {qualityBefore.cols} cols</div>
                  </div>
                  {qualityAfter && (
                    <>
                      <div className="flex flex-col items-center gap-1">
                        <svg className="h-6 w-6 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14 5l7 7m0 0l-7 7m7-7H3" />
                        </svg>
                        {qualityAfter.improvement >= 0 ? (
                          <span className="text-xs text-emerald-400 font-bold">+{qualityAfter.improvement}%</span>
                        ) : (
                          <span className="text-xs text-amber-400 font-bold">{qualityAfter.improvement}%</span>
                        )}
                      </div>
                      <div className="text-center">
                        <CircularGauge pct={qualityAfter.score_pct} label="AFTER" color="#34d399" size={130} />
                        <div className="text-[10px] text-slate-400 mt-1">{qualityAfter.rows_after.toLocaleString()} rows · {qualityAfter.cols_after} cols</div>
                      </div>
                    </>
                  )}
                </div>
              )}

              {/* Stat rows */}
              {qualityBefore && qualityAfter && (
                <div className="border-t border-slate-800 pt-3 space-y-2 text-xs">
                  <div className="flex justify-between"><span className="text-slate-400">Rows removed</span><span className="text-white">{(qualityBefore.rows - qualityAfter.rows_after).toLocaleString()}</span></div>
                  <div className="flex justify-between"><span className="text-slate-400">Null cells fixed</span><span className="text-emerald-400">{(qualityBefore.null_cells - qualityAfter.null_cells).toLocaleString()}</span></div>
                  <div className="flex justify-between"><span className="text-slate-400">Columns dropped</span><span className="text-red-400">{qualityAfter.columns_dropped.length}</span></div>
                  <div className="flex justify-between"><span className="text-slate-400">Operations applied</span><span className="text-cyan-400">{operations.length}</span></div>
                </div>
              )}

              {/* Download button */}
              {phase === "done" && (
                <button
                  onClick={handleDownload}
                  className="w-full py-2.5 rounded-xl text-sm font-semibold tracking-wide transition-all border border-emerald-500/40 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 flex items-center justify-center gap-2"
                >
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                  </svg>
                  Download Cleaned CSV
                </button>
              )}
            </div>
          </div>

          {/* ── RIGHT 2 COLUMNS: Operations Feed + Preview ── */}
          <div className="lg:col-span-2 flex flex-col gap-6">

            {/* Status bar */}
            {(isRunning || phase === "done") && statusMsg && (
              <div className={`flex items-center gap-2 px-4 py-2 rounded-xl border text-xs ${isRunning ? "bg-cyan-950/30 border-cyan-500/30 text-cyan-300" : "bg-emerald-950/30 border-emerald-500/30 text-emerald-300"}`}>
                {isRunning && <svg className="h-3.5 w-3.5 animate-spin shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>}
                {phase === "done" && <svg className="h-3.5 w-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>}
                {statusMsg}
              </div>
            )}

            {/* Operations Feed */}
            <div className="border border-slate-800/80 bg-slate-900/40 backdrop-blur-md rounded-2xl overflow-hidden flex flex-col" style={{ minHeight: "400px" }}>
              <div className="flex items-center justify-between px-5 py-3 bg-slate-900/80 border-b border-slate-800">
                <h2 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
                  <svg className="h-4 w-4 text-violet-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 10h16M4 14h16M4 18h16" />
                  </svg>
                  Live Operations Feed
                </h2>
                <span className="text-xs text-slate-500">{operations.length} applied{skipped.length > 0 ? ` · ${skipped.length} skipped` : ""}</span>
              </div>

              <div ref={feedRef} className="flex-1 overflow-y-auto p-4 space-y-2 max-h-[420px]">
                {operations.length === 0 && !isRunning && phase === "idle" && (
                  <div className="flex flex-col items-center justify-center h-full text-slate-500 text-xs gap-3 py-16">
                    <svg className="h-12 w-12 text-slate-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z" />
                    </svg>
                    Operations will appear here as the agent runs
                  </div>
                )}
                {isRunning && operations.length === 0 && (
                  <div className="flex items-center gap-2 text-slate-500 text-xs py-8 justify-center">
                    <svg className="h-4 w-4 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                    </svg>
                    Agent is analysing dataset…
                  </div>
                )}
                {operations.map((op, i) => (
                  <div
                    key={i}
                    className="flex items-start gap-3 p-3 rounded-xl border bg-slate-950/50 border-slate-800/60 hover:border-slate-700 transition-all animate-in fade-in slide-in-from-left-2 duration-300"
                  >
                    {/* Icon */}
                    <div
                      className="h-8 w-8 rounded-lg flex items-center justify-center shrink-0 mt-0.5"
                      style={{
                        background: `${OP_COLORS[op.operation] || "#94a3b8"}18`,
                        border: `1px solid ${OP_COLORS[op.operation] || "#94a3b8"}40`,
                        color: OP_COLORS[op.operation] || "#94a3b8",
                      }}
                    >
                      <OpIcon icon={op.icon as OperationIcon} />
                    </div>

                    {/* Body */}
                    <div className="flex-1 min-w-0">
                      <div className="flex flex-wrap items-center gap-2 mb-1">
                        <span className="font-bold text-xs text-white">{op.column}</span>
                        <span
                          className="text-[10px] px-1.5 py-0.5 rounded font-mono"
                          style={{
                            background: `${OP_COLORS[op.operation] || "#94a3b8"}18`,
                            color: OP_COLORS[op.operation] || "#94a3b8",
                            border: `1px solid ${OP_COLORS[op.operation] || "#94a3b8"}30`,
                          }}
                        >
                          {op.operation}
                        </span>
                        <span className="text-[10px] text-slate-500 px-1.5 py-0.5 rounded bg-slate-800 border border-slate-700">{op.strategy}</span>
                        {op.rows_affected > 0 && (
                          <span className="text-[10px] text-amber-400">{op.rows_affected} rows</span>
                        )}
                        {op.before_nulls !== undefined && op.after_nulls !== undefined && (
                          <span className="text-[10px] text-emerald-400/90 bg-emerald-950/40 border border-emerald-800/40 px-1.5 py-0.5 rounded font-mono">
                            nulls: {op.before_nulls} → {op.after_nulls}
                          </span>
                        )}
                      </div>
                      <p className="text-[11px] text-slate-400 leading-relaxed">{op.rationale}</p>
                      {op.polars_code && (
                        <code className="text-[10px] text-cyan-300/70 mt-1 block truncate">{op.polars_code}</code>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Preview Table */}
            {previewCols.length > 0 && (
              <div className="border border-slate-800/80 bg-slate-900/40 backdrop-blur-md rounded-2xl overflow-hidden">
                <div className="px-5 py-3 bg-slate-900/80 border-b border-slate-800 flex items-center justify-between">
                  <h2 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
                    <svg className="h-4 w-4 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 10h18M3 14h18M10 6H3m7 12H3m10-6h7" />
                    </svg>
                    Cleaned Data Preview
                  </h2>
                  <span className="text-xs text-slate-500">First 20 rows</span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-[11px] font-mono">
                    <thead>
                      <tr className="border-b border-slate-800 bg-slate-950/60">
                        {previewCols.map(col => (
                          <th key={col} className="px-3 py-2 text-left text-slate-400 whitespace-nowrap font-semibold">{col}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {previewRows.map((row, i) => (
                        <tr key={i} className={`border-b border-slate-800/50 hover:bg-slate-800/20 transition-colors ${i % 2 === 0 ? "" : "bg-slate-950/20"}`}>
                          {previewCols.map(col => (
                            <td key={col} className="px-3 py-1.5 whitespace-nowrap max-w-[160px] overflow-hidden text-ellipsis">
                              {renderCellValue(row[col])}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="text-center text-[11px] text-slate-600">
          Multiagent BI Engine · Cleaning Agent Live Demo · Powered by Ollama qwen2.5-coder + Gemini Flash
        </div>
      </div>
    </main>
  )
}
