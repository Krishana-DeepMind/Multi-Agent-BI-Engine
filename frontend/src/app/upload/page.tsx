"use client"

import React, { useState, useCallback } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
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
  ArrowLeft,
  Terminal,
  Loader2,
  X,
  Sparkles,
  ShieldCheck
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
  {
    ext: "CSV",
    label: "Comma Separated",
    icon: FileText,
    iconBg: "bg-red-50 text-red-700 border border-red-200/70",
    iconColor: "text-red-700",
  },
  {
    ext: "XLSX",
    label: "Excel Sheets",
    icon: FileSpreadsheet,
    iconBg: "bg-emerald-50 text-emerald-700 border border-emerald-200/70",
    iconColor: "text-emerald-700",
  },
  {
    ext: "JSON",
    label: "JSON / NDJSON",
    icon: FileCode,
    iconBg: "bg-amber-50 text-amber-700 border border-amber-200/70",
    iconColor: "text-amber-700",
  },
  {
    ext: "PARQUET",
    label: "Apache Parquet",
    icon: Database,
    iconBg: "bg-slate-100 text-slate-700 border border-slate-200/70",
    iconColor: "text-slate-700",
  },
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
    <main className="min-h-screen bg-[#F8F9FA] text-[#18181B] font-sans flex flex-col items-center justify-center p-4 sm:p-8 relative selection:bg-red-500/10 selection:text-[#991B1B]">
      {/* Subtle Dot Grid Background */}
      <div className="absolute inset-0 bg-[radial-gradient(#E2E8F0_1px,transparent_1px)] [background-size:20px_20px] pointer-events-none opacity-70" />

      {/* Subtle Ambient Red Glow */}
      <div className="absolute -top-32 left-1/2 -translate-x-1/2 w-[720px] h-[320px] bg-red-500/5 blur-[120px] rounded-full pointer-events-none" />

      <div className="w-full max-w-4xl z-10 space-y-4">
        {/* Top Navigation Bar */}
        <div className="flex items-center justify-between pb-1">
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-1.5 text-xs font-semibold text-slate-600 hover:text-[#991B1B] bg-white hover:bg-red-50/50 border border-slate-200/90 rounded-lg px-3 py-1.5 shadow-sm transition-all"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            <span>Back to Dashboard</span>
          </Link>
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-red-50 text-red-700 border border-red-200/80 text-xs font-semibold">
              <span className="h-1.5 w-1.5 rounded-full bg-[#991B1B] animate-pulse" />
              AutoBI Ingestion Swarm
            </span>
          </div>
        </div>

        {/* Engineering Header Card */}
        <div className="border border-slate-200/90 bg-white rounded-xl p-6 shadow-sm relative overflow-hidden">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-100 pb-5">
            <div className="flex items-center gap-3.5">
              <div className="h-11 w-11 rounded-xl bg-red-50 border border-red-200 flex items-center justify-center text-[#991B1B] shadow-sm shrink-0">
                <Terminal className="h-5 w-5" />
              </div>
              <div>
                <h1 className="text-xl font-bold tracking-tight text-slate-900 flex items-center gap-2.5">
                  Multiagent BI Engine
                  <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-red-50 text-red-700 border border-red-200/80 tracking-wide uppercase">
                    Ingestion Node
                  </span>
                </h1>
                <p className="text-xs text-slate-500 mt-0.5">
                  Stream tabular datasets directly to the autonomous swarm pipeline
                </p>
              </div>
            </div>

            {/* Status indicators */}
            <div className="flex items-center gap-2 text-xs">
              <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-50 border border-slate-200 text-slate-700 font-medium">
                <span className="h-2 w-2 rounded-full bg-emerald-600 animate-pulse" />
                <span className="font-semibold text-slate-800">STORAGE READY</span>
              </div>
              <div className="px-3 py-1.5 rounded-lg bg-slate-50 border border-slate-200 text-slate-600 font-medium">
                MAX 100MB
              </div>
            </div>
          </div>

          {/* Format Badges */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-5">
            {FORMAT_BADGES.map((badge) => {
              const Icon = badge.icon
              return (
                <div
                  key={badge.ext}
                  className="flex items-center gap-3 p-3 rounded-lg border border-slate-200/80 bg-slate-50/40 hover:bg-white hover:border-slate-300 hover:shadow-xs transition-all text-xs"
                >
                  <div className={`p-2 rounded-lg ${badge.iconBg}`}>
                    <Icon className={`h-4 w-4 shrink-0 ${badge.iconColor}`} />
                  </div>
                  <div>
                    <div className="font-bold text-slate-900 tracking-wider">{badge.ext}</div>
                    <div className="text-[11px] text-slate-500">{badge.label}</div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        {/* Error Alert */}
        {error && (
          <Alert variant="destructive" className="bg-red-50 border-red-200 text-red-900 rounded-xl shadow-sm">
            <AlertTriangle className="h-4 w-4 text-red-600" />
            <AlertTitle className="font-bold text-red-900">Upload Failure</AlertTitle>
            <AlertDescription className="text-red-700 mt-1 flex flex-col gap-2 text-xs">
              <span>{error}</span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => { setError(null); setIsUploading(false); }}
                className="w-fit h-7 text-xs border-red-300 bg-white hover:bg-red-50 text-red-800"
              >
                Dismiss & Try Again
              </Button>
            </AlertDescription>
          </Alert>
        )}

        {/* Main Drag-and-Drop Area */}
        <div className="border border-slate-200/90 bg-white rounded-xl p-6 shadow-sm space-y-6">
          {!selectedFile ? (
            <div
              {...getRootProps()}
              className={`border-2 border-dashed rounded-xl p-10 sm:p-14 text-center cursor-pointer transition-all duration-200 flex flex-col items-center justify-center min-h-[300px] group ${
                isDragActive
                  ? "border-[#991B1B] bg-red-50/40 shadow-[0_0_24px_rgba(153,27,27,0.08)]"
                  : isDragReject
                  ? "border-red-500 bg-red-50/50"
                  : "border-slate-300/80 hover:border-red-400 hover:bg-red-50/15"
              }`}
            >
              <input {...getInputProps()} />
              <div className="h-20 w-20 rounded-2xl bg-slate-50 border border-slate-200 shadow-sm flex items-center justify-center mb-5 group-hover:scale-105 group-hover:border-red-300 group-hover:bg-red-50/50 transition-all">
                <UploadCloud className="h-10 w-10 text-slate-400 group-hover:text-[#991B1B] transition-colors" />
              </div>
              <h2 className="text-base sm:text-lg font-semibold text-slate-900 mb-1">
                {isDragActive ? "Drop dataset to upload" : "Drag and drop your dataset here"}
              </h2>
              <p className="text-xs text-slate-500 max-w-sm mb-4">
                or click anywhere inside the container to browse from local filesystem
              </p>
              <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-slate-100 text-[11px] text-slate-600 border border-slate-200">
                <Sparkles className="h-3 w-3 text-[#991B1B]" />
                <span>Strict magic bytes verification enforced</span>
              </div>
            </div>
          ) : (
            <div className="space-y-6">
              {/* Selected File Card */}
              <div className="border border-slate-200 bg-slate-50/80 rounded-xl p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div className="flex items-center gap-4">
                  <div className="h-12 w-12 rounded-xl bg-red-50 border border-red-200 flex items-center justify-center text-[#991B1B] shrink-0 shadow-sm">
                    <Database className="h-6 w-6" />
                  </div>
                  <div className="overflow-hidden">
                    <div className="font-semibold text-slate-900 truncate max-w-md text-sm sm:text-base" title={selectedFile.name}>
                      {selectedFile.name}
                    </div>
                    <div className="flex items-center gap-3 text-xs text-slate-500 mt-1 font-mono">
                      <span>Size: <strong className="text-slate-800 font-semibold">{formatFileSize(selectedFile.size)}</strong></span>
                      <span>Type: <strong className="text-[#991B1B] font-bold uppercase">{selectedFile.name.split('.').pop() || 'RAW'}</strong></span>
                    </div>
                  </div>
                </div>

                {!isUploading && !result && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setSelectedFile(null)}
                    className="h-8 px-3 text-xs text-slate-500 hover:text-red-700 hover:bg-red-50 rounded-lg w-fit transition-colors"
                  >
                    <X className="h-4 w-4 mr-1" />
                    Remove
                  </Button>
                )}
              </div>

              {/* Upload Progress Bar */}
              {isUploading && (
                <div className="space-y-3 border border-slate-200 bg-slate-50/60 p-4 rounded-xl">
                  <div className="flex items-center justify-between text-xs">
                    <div className="flex items-center gap-2 text-slate-800 font-medium">
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-[#991B1B]" />
                      <span>
                        {uploadStage === "uploading" && `Streaming data to storage pipeline (${uploadProgress}%)...`}
                        {uploadStage === "validating" && "Verifying magic byte signatures and generating row estimate..."}
                        {uploadStage === "success" && "Upload complete! Redirecting to configuration..."}
                      </span>
                    </div>
                    <div className="text-slate-500 font-mono text-[11px]">
                      {formatFileSize(uploadedBytes)} / {formatFileSize(totalBytes)}
                    </div>
                  </div>

                  {/* Progress track */}
                  <div className="w-full h-2.5 bg-slate-200 rounded-full overflow-hidden p-0.5 border border-slate-200">
                    <div
                      className="h-full bg-gradient-to-r from-[#991B1B] to-[#DC2626] rounded-full transition-all duration-300 shadow-[0_0_8px_rgba(153,27,27,0.3)]"
                      style={{ width: `${uploadProgress}%` }}
                    />
                  </div>
                </div>
              )}

              {/* Upload Success Details */}
              {result && (
                <div className="border border-emerald-200 bg-emerald-50/70 p-5 rounded-xl space-y-3">
                  <div className="flex items-center gap-2 text-emerald-800 font-semibold text-sm">
                    <CheckCircle2 className="h-5 w-5 text-emerald-600" />
                    <span>File Ingestion Successful</span>
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs font-mono pt-2 border-t border-emerald-200/60">
                    <div>
                      <span className="text-slate-500 block">Session ID:</span>
                      <span className="text-slate-800 font-semibold truncate block" title={result.session_id}>
                        {result.session_id.slice(0, 12)}...
                      </span>
                    </div>
                    <div>
                      <span className="text-slate-500 block">Detected Type:</span>
                      <span className="text-emerald-700 uppercase font-bold">{result.file_type}</span>
                    </div>
                    <div>
                      <span className="text-slate-500 block">File Size:</span>
                      <span className="text-slate-800 font-semibold">{result.file_size_mb} MB</span>
                    </div>
                    <div>
                      <span className="text-slate-500 block">Est. Rows:</span>
                      <span className="text-[#991B1B] font-bold">{result.row_count_estimate.toLocaleString()}</span>
                    </div>
                  </div>
                </div>
              )}

              {/* Action Buttons */}
              <div className="flex items-center justify-end gap-3 pt-2">
                {!isUploading && !result && (
                  <Button
                    onClick={handleUpload}
                    className="w-full sm:w-auto px-6 py-5 bg-[#991B1B] hover:bg-[#7F1D1D] text-white font-semibold text-sm rounded-lg shadow-[0_2px_8px_rgba(153,27,27,0.25)] hover:shadow-[0_4px_14px_rgba(153,27,27,0.35)] transition-all cursor-pointer"
                  >
                    <span>Upload & Launch Swarm</span>
                    <ArrowRight className="h-4 w-4 ml-2" />
                  </Button>
                )}

                {result && (
                  <Button
                    onClick={() => router.push(`/session/${result.session_id}/configure`)}
                    className="w-full sm:w-auto px-6 py-5 bg-[#991B1B] hover:bg-[#7F1D1D] text-white font-semibold text-sm rounded-lg transition-all shadow-[0_2px_8px_rgba(153,27,27,0.25)] hover:shadow-[0_4px_14px_rgba(153,27,27,0.35)] cursor-pointer"
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
        <div className="text-center text-xs text-slate-500 flex items-center justify-center gap-2 pt-2">
          <ShieldCheck className="w-4 h-4 text-[#991B1B]" />
          <span>Multiagent BI Engine • Enterprise Ingestion Node • Magic Byte Security</span>
        </div>
      </div>
    </main>
  )
}
