"use client"

import React, { useState, useCallback } from "react"
import { useRouter } from "next/navigation"
import { useDropzone, FileRejection } from "react-dropzone"
import axios, { AxiosProgressEvent } from "axios"
import {
  UploadCloud,
  FileSpreadsheet,
  FileCode,
  FileText,
  Database,
  CheckCircle2,
  AlertTriangle,
  ArrowRight,
  Terminal,
  Loader2,
  X,
  Sparkles
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert"

interface UploadResponse {
  session_id: string
  file_path: string
  file_type: string
  file_size_mb: number
  row_count_estimate: number
}

const ACCEPTED_FORMATS = {
  "text/csv": [".csv"],
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
  "application/json": [".json"],
  "application/octet-stream": [".parquet"],
  "application/x-parquet": [".parquet"],
}

const FORMAT_BADGES = [
  { ext: "CSV", label: "Comma Separated", icon: FileText, color: "text-emerald-400 border-emerald-500/30 bg-emerald-500/10" },
  { ext: "XLSX", label: "Excel Sheets", icon: FileSpreadsheet, color: "text-green-400 border-green-500/30 bg-green-500/10" },
  { ext: "JSON", label: "JSON / NDJSON", icon: FileCode, color: "text-amber-400 border-amber-500/30 bg-amber-500/10" },
  { ext: "PARQUET", label: "Apache Parquet", icon: Database, color: "text-cyan-400 border-cyan-500/30 bg-cyan-500/10" },
]

export default function UploadPage() {
  const router = useRouter()
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [isUploading, setIsUploading] = useState<boolean>(false)
  const [uploadProgress, setUploadProgress] = useState<number>(0)
  const [uploadedBytes, setUploadedBytes] = useState<number>(0)
  const [totalBytes, setTotalBytes] = useState<number>(0)
  const [uploadStage, setUploadStage] = useState<string>("idle")
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<UploadResponse | null>(null)

  const onDrop = useCallback((acceptedFiles: File[], fileRejections: FileRejection[]) => {
    setError(null)
    if (fileRejections.length > 0) {
      const rej = fileRejections[0]
      if (rej.errors.some(e => e.code === "file-too-large")) {
        setError("File exceeds maximum allowed limit of 100MB.")
      } else {
        setError(rej.errors[0]?.message || "Invalid file format. Please upload CSV, XLSX, JSON, or Parquet.")
      }
      return
    }

    if (acceptedFiles.length > 0) {
      setSelectedFile(acceptedFiles[0])
      setTotalBytes(acceptedFiles[0].size)
    }
  }, [])

  const { getRootProps, getInputProps, isDragActive, isDragReject } = useDropzone({
    onDrop,
    accept: ACCEPTED_FORMATS,
    maxSize: 100 * 1024 * 1024, // 100MB
    multiple: false,
    disabled: isUploading,
  })

  const handleUpload = async () => {
    if (!selectedFile) return

    setIsUploading(true)
    setUploadProgress(0)
    setUploadStage("uploading")
    setError(null)

    const formData = new FormData()
    formData.append("file", selectedFile)
    formData.append("user_id", "00000000-0000-0000-0000-000000000000")

    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"

    try {
      const response = await axios.post<UploadResponse>(`${apiUrl}/api/upload`, formData, {
        headers: {
          "Content-Type": "multipart/form-data",
        },
        onUploadProgress: (progressEvent: AxiosProgressEvent) => {
          if (progressEvent.total) {
            const percent = Math.round((progressEvent.loaded * 100) / progressEvent.total)
            setUploadProgress(percent)
            setUploadedBytes(progressEvent.loaded)
            setTotalBytes(progressEvent.total)
            if (percent >= 100) {
              setUploadStage("validating")
            }
          }
        },
      })

      setUploadStage("success")
      setResult(response.data)

      // Automatically redirect after short pause
      setTimeout(() => {
        router.push(`/session/${response.data.session_id}/configure`)
      }, 1200)
    } catch (err: any) {
      setUploadStage("error")
      setIsUploading(false)
      const detail = err.response?.data?.detail || err.message || "Failed to upload file. Please try again."
      setError(detail)
    }
  }

  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return "0 B"
    const k = 1024
    const sizes = ["B", "KB", "MB", "GB"]
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return `${(bytes / Math.pow(k, i)).toFixed(2)} ${sizes[i]}`
  }

  return (
    <main className="min-h-screen bg-[#080B11] text-slate-100 font-mono flex flex-col items-center justify-center p-4 sm:p-8 relative overflow-hidden selection:bg-cyan-500/30 selection:text-cyan-200">
      {/* Background Cyber Grid */}
      <div className="absolute inset-0 bg-[linear-gradient(to_right,#1e293b15_1px,transparent_1px),linear-gradient(to_bottom,#1e293b15_1px,transparent_1px)] bg-[size:4rem_4rem] [mask-image:radial-gradient(ellipse_60%_50%_at_50%_50%,#000_70%,transparent_100%)] pointer-events-none" />

      {/* Subtle Glows */}
      <div className="absolute -top-40 left-1/2 -translate-x-1/2 w-[600px] h-[350px] bg-cyan-500/10 blur-[120px] rounded-full pointer-events-none" />
      <div className="absolute -bottom-40 left-1/2 -translate-x-1/2 w-[600px] h-[350px] bg-emerald-500/10 blur-[120px] rounded-full pointer-events-none" />

      <div className="w-full max-w-4xl z-10 space-y-6">
        {/* Engineering Header */}
        <div className="border border-slate-800/80 bg-slate-900/60 backdrop-blur-md rounded-xl p-6 shadow-2xl relative overflow-hidden">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-800 pb-4">
            <div className="flex items-center gap-3">
              <div className="h-10 w-10 rounded-lg bg-gradient-to-br from-cyan-500/20 to-emerald-500/20 border border-cyan-500/40 flex items-center justify-center text-cyan-400 shadow-[0_0_15px_rgba(6,182,212,0.25)]">
                <Terminal className="h-5 w-5" />
              </div>
              <div>
                <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
                  Multiagent BI Engine
                  <span className="text-xs px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-400 border border-cyan-500/30">
                    INGESTION NODE
                  </span>
                </h1>
                <p className="text-xs text-slate-400 mt-0.5">
                  Stream tabular datasets directly to the autonomous swarm pipeline
                </p>
              </div>
            </div>

            {/* Status indicators */}
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-slate-800/80 border border-slate-700/60">
                <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                <span>STORAGE READY</span>
              </div>
              <div className="px-2.5 py-1 rounded-md bg-slate-800/80 border border-slate-700/60 text-slate-300">
                MAX 100MB
              </div>
            </div>
          </div>

          {/* Format Badges */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-4">
            {FORMAT_BADGES.map((badge) => {
              const Icon = badge.icon
              return (
                <div
                  key={badge.ext}
                  className={`flex items-center gap-2.5 p-2.5 rounded-lg border text-xs transition-all ${badge.color}`}
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  <div>
                    <div className="font-bold tracking-wider">{badge.ext}</div>
                    <div className="text-[10px] opacity-80">{badge.label}</div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        {/* Error Alert */}
        {error && (
          <Alert variant="destructive" className="bg-red-950/40 border-red-500/40 text-red-300 backdrop-blur-md">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Upload Failure</AlertTitle>
            <AlertDescription className="text-red-200 mt-1 flex flex-col gap-2">
              <span>{error}</span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => { setError(null); setIsUploading(false); }}
                className="w-fit h-7 text-xs border-red-500/40 bg-red-500/10 hover:bg-red-500/20 text-red-200"
              >
                Dismiss & Try Again
              </Button>
            </AlertDescription>
          </Alert>
        )}

        {/* Main Drag-and-Drop Area */}
        <div className="border border-slate-800/80 bg-slate-900/40 backdrop-blur-md rounded-xl p-6 shadow-2xl space-y-6">
          {!selectedFile ? (
            <div
              {...getRootProps()}
              className={`border-2 border-dashed rounded-xl p-12 text-center cursor-pointer transition-all duration-200 flex flex-col items-center justify-center min-h-[300px] group ${
                isDragActive
                  ? "border-cyan-400 bg-cyan-950/20 shadow-[0_0_30px_rgba(6,182,212,0.2)]"
                  : isDragReject
                  ? "border-red-500 bg-red-950/20"
                  : "border-slate-700/70 hover:border-cyan-500/60 hover:bg-slate-800/30"
              }`}
            >
              <input {...getInputProps()} />
              <div className="h-20 w-20 rounded-2xl bg-gradient-to-tr from-slate-800 to-slate-800/50 border border-slate-700 flex items-center justify-center mb-5 group-hover:scale-105 group-hover:border-cyan-500/40 group-hover:text-cyan-400 transition-all shadow-inner">
                <UploadCloud className="h-10 w-10 text-slate-400 group-hover:text-cyan-400 transition-colors" />
              </div>
              <h2 className="text-base sm:text-lg font-semibold text-slate-200 mb-1">
                {isDragActive ? "Drop dataset to upload" : "Drag and drop your dataset here"}
              </h2>
              <p className="text-xs text-slate-400 max-w-sm mb-4">
                or click anywhere inside the container to browse from local filesystem
              </p>
              <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-slate-800 text-[11px] text-slate-400 border border-slate-700">
                <Sparkles className="h-3 w-3 text-cyan-400" />
                <span>Strict magic bytes verification enforced</span>
              </div>
            </div>
          ) : (
            <div className="space-y-6">
              {/* Selected File Card */}
              <div className="border border-slate-800 bg-slate-950/60 rounded-xl p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div className="flex items-center gap-4">
                  <div className="h-12 w-12 rounded-lg bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center text-cyan-400 shrink-0">
                    <Database className="h-6 w-6" />
                  </div>
                  <div className="overflow-hidden">
                    <div className="font-semibold text-white truncate max-w-md" title={selectedFile.name}>
                      {selectedFile.name}
                    </div>
                    <div className="flex items-center gap-3 text-xs text-slate-400 mt-1">
                      <span>Size: <strong className="text-slate-200">{formatFileSize(selectedFile.size)}</strong></span>
                      <span>Type: <strong className="text-cyan-400 uppercase">{selectedFile.name.split('.').pop() || 'RAW'}</strong></span>
                    </div>
                  </div>
                </div>

                {!isUploading && !result && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setSelectedFile(null)}
                    className="h-8 px-3 text-xs text-slate-400 hover:text-red-400 hover:bg-red-500/10 w-fit"
                  >
                    <X className="h-4 w-4 mr-1" />
                    Remove
                  </Button>
                )}
              </div>

              {/* Upload Progress Bar */}
              {isUploading && (
                <div className="space-y-3 border border-slate-800 bg-slate-950/40 p-4 rounded-xl">
                  <div className="flex items-center justify-between text-xs">
                    <div className="flex items-center gap-2 text-cyan-300">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      <span>
                        {uploadStage === "uploading" && `Streaming data to Supabase Storage (${uploadProgress}%)...`}
                        {uploadStage === "validating" && "Verifying magic byte signatures and generating row estimate..."}
                        {uploadStage === "success" && "Upload complete! Redirecting to configuration..."}
                      </span>
                    </div>
                    <div className="text-slate-400">
                      {formatFileSize(uploadedBytes)} / {formatFileSize(totalBytes)}
                    </div>
                  </div>

                  {/* Progress track */}
                  <div className="w-full h-2.5 bg-slate-800 rounded-full overflow-hidden p-0.5 border border-slate-700/50">
                    <div
                      className="h-full bg-gradient-to-r from-cyan-500 to-emerald-400 rounded-full transition-all duration-300 shadow-[0_0_12px_rgba(6,182,212,0.5)]"
                      style={{ width: `${uploadProgress}%` }}
                    />
                  </div>
                </div>
              )}

              {/* Upload Success Details */}
              {result && (
                <div className="border border-emerald-500/30 bg-emerald-950/20 p-5 rounded-xl space-y-3">
                  <div className="flex items-center gap-2 text-emerald-400 font-semibold text-sm">
                    <CheckCircle2 className="h-5 w-5" />
                    <span>File Ingestion Successful</span>
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs font-mono pt-2 border-t border-emerald-500/20">
                    <div>
                      <span className="text-slate-400 block">Session ID:</span>
                      <span className="text-slate-200 truncate block" title={result.session_id}>
                        {result.session_id.slice(0, 12)}...
                      </span>
                    </div>
                    <div>
                      <span className="text-slate-400 block">Detected Type:</span>
                      <span className="text-emerald-400 uppercase font-bold">{result.file_type}</span>
                    </div>
                    <div>
                      <span className="text-slate-400 block">File Size:</span>
                      <span className="text-slate-200">{result.file_size_mb} MB</span>
                    </div>
                    <div>
                      <span className="text-slate-400 block">Est. Rows:</span>
                      <span className="text-cyan-400 font-bold">{result.row_count_estimate.toLocaleString()}</span>
                    </div>
                  </div>
                </div>
              )}

              {/* Action Buttons */}
              <div className="flex items-center justify-end gap-3 pt-2">
                {!isUploading && !result && (
                  <Button
                    onClick={handleUpload}
                    className="w-full sm:w-auto px-6 py-5 bg-gradient-to-r from-cyan-500 to-emerald-500 hover:from-cyan-400 hover:to-emerald-400 text-slate-950 font-bold tracking-wide shadow-[0_0_20px_rgba(6,182,212,0.3)] transition-all"
                  >
                    <span>Upload & Launch Swarm</span>
                    <ArrowRight className="h-4 w-4 ml-2" />
                  </Button>
                )}

                {result && (
                  <Button
                    onClick={() => router.push(`/session/${result.session_id}/configure`)}
                    className="w-full sm:w-auto px-6 py-5 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold tracking-wide transition-all shadow-[0_0_20px_rgba(16,185,129,0.3)]"
                  >
                    <span>Proceed to Swarm Config</span>
                    <ArrowRight className="h-4 w-4 ml-2" />
                  </Button>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Footer info */}
        <div className="text-center text-[11px] text-slate-500">
          Multiagent BI Engine • Day 3 File Upload Infrastructure • Magic Byte Security
        </div>
      </div>
    </main>
  )
}
