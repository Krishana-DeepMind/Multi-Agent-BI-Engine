"use client";

import React, { useState, useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useDropzone, FileRejection } from "react-dropzone";
import axios, { AxiosProgressEvent } from "axios";
import {
  UploadCloud,
  FileText,
  AlertTriangle,
  X,
  Loader2,
  Table,
  Sparkles,
  ArrowRight,
  CheckCircle2,
} from "lucide-react";

interface UploadResponse {
  session_id: string;
  file_path: string;
  file_type: string;
  file_size_mb: number;
  row_count_estimate: number;
}

interface SchemaPreview {
  columns: string[];
  sampleRows: string[][];
  totalEstimatedRows: number;
}

interface UploadDatasetModalProps {
  isOpen: boolean;
  onClose: () => void;
  currentSessionId?: string;
  onUploadSuccess?: (newSessionId: string, fileName: string) => void;
}

export default function UploadDatasetModal({
  isOpen,
  onClose,
  currentSessionId,
  onUploadSuccess,
}: UploadDatasetModalProps) {
  const router = useRouter();

  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [businessQuery, setBusinessQuery] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadStage, setUploadStage] = useState<"idle" | "uploading" | "validating" | "starting" | "success" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<SchemaPreview | null>(null);

  // Reset state on open/close
  useEffect(() => {
    if (!isOpen) {
      setSelectedFile(null);
      setBusinessQuery("");
      setIsUploading(false);
      setUploadProgress(0);
      setUploadStage("idle");
      setError(null);
      setPreview(null);
    }
  }, [isOpen]);

  // Handle ESC key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isOpen && !isUploading) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, isUploading, onClose]);

  // Simple CSV parser for client-side schema preview
  const parseCsvPreview = useCallback((file: File) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const text = e.target?.result as string;
        if (!text) return;
        const lines = text.split(/\r?\n/).filter((l) => l.trim().length > 0);
        if (lines.length === 0) return;

        // Parse header
        const header = lines[0].split(",").map((c) => c.replace(/^["']|["']$/g, "").trim());
        const sampleRows: string[][] = [];

        for (let i = 1; i < Math.min(lines.length, 6); i++) {
          const row = lines[i].split(",").map((c) => c.replace(/^["']|["']$/g, "").trim());
          sampleRows.push(row);
        }

        setPreview({
          columns: header,
          sampleRows,
          totalEstimatedRows: Math.max(0, lines.length - 1),
        });
      } catch (err) {
        console.warn("Could not generate local CSV preview:", err);
      }
    };
    // Read only first 64KB for speed
    const slice = file.slice(0, 65536);
    reader.readAsText(slice);
  }, []);

  const onDrop = useCallback(
    (acceptedFiles: File[], fileRejections: FileRejection[]) => {
      setError(null);
      if (fileRejections.length > 0) {
        const rej = fileRejections[0];
        if (rej.errors.some((e) => e.code === "file-too-large")) {
          setError("File exceeds maximum allowed limit of 100MB.");
        } else {
          setError(rej.errors[0]?.message || "Invalid file. Please select a valid CSV dataset.");
        }
        return;
      }

      if (acceptedFiles.length > 0) {
        const f = acceptedFiles[0];
        if (f.size === 0) {
          setError("The selected file is empty (0 bytes). Please upload a valid dataset.");
          return;
        }
        setSelectedFile(f);
        if (f.name.toLowerCase().endsWith(".csv")) {
          parseCsvPreview(f);
        } else {
          setPreview(null);
        }
      }
    },
    [parseCsvPreview]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      "text/csv": [".csv"],
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
      "application/json": [".json"],
      "application/octet-stream": [".parquet"],
      "application/x-parquet": [".parquet"],
    },
    maxSize: 100 * 1024 * 1024,
    multiple: false,
    disabled: isUploading,
  });

  const handleUploadAndStart = async () => {
    if (!selectedFile) return;

    setIsUploading(true);
    setUploadProgress(0);
    setUploadStage("uploading");
    setError(null);

    const formData = new FormData();
    formData.append("file", selectedFile);
    formData.append("user_id", "00000000-0000-0000-0000-000000000000");

    const API = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

    try {
      const response = await axios.post<UploadResponse>(`${API}/api/upload`, formData, {
        headers: { "Content-Type": "multipart/form-data" },
        timeout: 45000,
        onUploadProgress: (progressEvent: AxiosProgressEvent) => {
          if (progressEvent.total) {
            const percent = Math.round((progressEvent.loaded * 100) / progressEvent.total);
            setUploadProgress(percent);
            if (percent >= 100) {
              setUploadStage("validating");
            }
          }
        },
      });

      const newSessionId = response.data.session_id;

      // Register new session in localStorage for multi-session support
      try {
        const existing = JSON.parse(localStorage.getItem("autobi_sessions") || "[]");
        const updated = [
          {
            sessionId: newSessionId,
            fileName: selectedFile.name,
            date: new Date().toISOString(),
            rowCount: response.data.row_count_estimate,
          },
          ...existing.filter((s: { sessionId: string }) => s.sessionId !== newSessionId),
        ];
        localStorage.setItem("autobi_sessions", JSON.stringify(updated.slice(0, 15)));
      } catch (lsErr) {
        console.warn("Could not save to localStorage:", lsErr);
      }

      setUploadStage("starting");

      // Kick off the pipeline
      const queryParam = businessQuery.trim()
        ? `?raw_query=${encodeURIComponent(businessQuery.trim())}`
        : "";
      await axios.post(`${API}/api/pipeline/${newSessionId}/start${queryParam}`).catch((e) => {
        console.warn("Pipeline start triggered:", e);
      });

      setUploadStage("success");

      if (onUploadSuccess) {
        onUploadSuccess(newSessionId, selectedFile.name);
      }

      // Transition smoothly into configure/dashboard
      setTimeout(() => {
        onClose();
        router.push(`/session/${newSessionId}/configure`);
      }, 800);
    } catch (err: unknown) {
      setUploadStage("error");
      setIsUploading(false);
      let detail = "Failed to upload dataset. Please try again.";
      if (axios.isAxiosError(err)) {
        detail = err.response?.data?.detail || err.message || detail;
      } else if (err instanceof Error) {
        detail = err.message;
      }
      setError(detail);
    }
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${(bytes / Math.pow(k, i)).toFixed(2)} ${sizes[i]}`;
  };

  if (!isOpen) return null;

  return (
    <div className="modal-backdrop" onClick={(e) => { if (e.target === e.currentTarget && !isUploading) onClose(); }}>
      <div className="modal-dialog" role="dialog" aria-modal="true">
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "18px 24px", borderBottom: "1px solid #E2E8F0" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <div style={{ width: 36, height: 36, borderRadius: 8, background: "rgba(153, 27, 27, 0.08)", display: "flex", alignItems: "center", justifyContent: "center", color: "#991B1B" }}>
              <UploadCloud size={20} />
            </div>
            <div>
              <h2 style={{ fontSize: "1.1rem", fontWeight: 700, color: "#1E293B", margin: 0 }}>Upload New Dataset</h2>
              <p style={{ fontSize: "0.78rem", color: "#64748B", margin: 0 }}>Start a fresh analysis pipeline in a new session</p>
            </div>
          </div>
          <button
            onClick={onClose}
            disabled={isUploading}
            style={{ border: "none", background: "transparent", color: "#94A3B8", cursor: isUploading ? "not-allowed" : "pointer", padding: 4, borderRadius: 6 }}
            title="Close"
          >
            <X size={20} />
          </button>
        </div>

        {/* Content */}
        <div style={{ padding: "20px 24px" }}>
          {/* Error Alert */}
          {error && (
            <div style={{ background: "#FEF2F2", border: "1px solid #FCA5A5", borderRadius: 8, padding: "12px 14px", marginBottom: 16, display: "flex", alignItems: "flex-start", gap: 10, color: "#991B1B" }}>
              <AlertTriangle size={18} style={{ flexShrink: 0, marginTop: 2 }} />
              <div style={{ fontSize: "0.82rem", lineHeight: 1.4 }}>
                <strong>Upload Error: </strong>{error}
              </div>
            </div>
          )}

          {/* Dropzone */}
          {!selectedFile ? (
            <div
              {...getRootProps()}
              style={{
                border: isDragActive ? "2px dashed #991B1B" : "2px dashed #CBD5E1",
                background: isDragActive ? "rgba(153, 27, 27, 0.04)" : "#F8FAFC",
                borderRadius: 10,
                padding: "36px 20px",
                textAlign: "center",
                cursor: "pointer",
                transition: "all 0.2s ease",
              }}
            >
              <input {...getInputProps()} />
              <div style={{ width: 44, height: 44, borderRadius: "50%", background: "#FEF2F2", color: "#991B1B", display: "flex", alignItems: "center", justifyContent: "center", margin: "0 auto 12px" }}>
                <UploadCloud size={24} />
              </div>
              <p style={{ fontSize: "0.92rem", fontWeight: 600, color: "#1E293B", margin: "0 0 4px" }}>
                {isDragActive ? "Drop CSV file here..." : "Choose a CSV file or drag & drop"}
              </p>
              <p style={{ fontSize: "0.78rem", color: "#64748B", margin: 0 }}>
                Supports CSV, Excel (XLSX), JSON, Parquet up to 100MB
              </p>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              {/* Selected File Card */}
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 16px", background: "#F1F5F9", borderRadius: 8, border: "1px solid #E2E8F0" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <FileText size={20} color="#991B1B" />
                  <div>
                    <div style={{ fontSize: "0.88rem", fontWeight: 600, color: "#1E293B" }}>{selectedFile.name}</div>
                    <div style={{ fontSize: "0.75rem", color: "#64748B" }}>
                      {formatFileSize(selectedFile.size)} {preview ? `• ~${preview.totalEstimatedRows} rows` : ""}
                    </div>
                  </div>
                </div>
                {!isUploading && (
                  <button
                    onClick={() => { setSelectedFile(null); setPreview(null); }}
                    style={{ border: "none", background: "transparent", color: "#64748B", cursor: "pointer", fontSize: "0.78rem", textDecoration: "underline" }}
                  >
                    Change file
                  </button>
                )}
              </div>

              {/* Schema Preview */}
              {preview && preview.columns.length > 0 && (
                <div style={{ marginTop: 14 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
                    <Table size={15} color="#64748B" />
                    <span style={{ fontSize: "0.8rem", fontWeight: 600, color: "#475569" }}>
                      Detected Schema Preview ({preview.columns.length} columns)
                    </span>
                  </div>
                  <div style={{ overflowX: "auto", border: "1px solid #E2E8F0", borderRadius: 6, maxHeight: 160 }}>
                    <table style={{ width: "100%", fontSize: "0.72rem", borderCollapse: "collapse", textAlign: "left" }}>
                      <thead style={{ background: "#F8FAFC", borderBottom: "1px solid #E2E8F0" }}>
                        <tr>
                          {preview.columns.slice(0, 7).map((col, idx) => (
                            <th key={idx} style={{ padding: "6px 10px", color: "#475569", fontWeight: 600, whiteSpace: "nowrap" }}>
                              {col}
                            </th>
                          ))}
                          {preview.columns.length > 7 && (
                            <th style={{ padding: "6px 10px", color: "#94A3B8" }}>+{preview.columns.length - 7} more</th>
                          )}
                        </tr>
                      </thead>
                      <tbody>
                        {preview.sampleRows.slice(0, 3).map((row, rIdx) => (
                          <tr key={rIdx} style={{ borderBottom: "1px solid #F1F5F9" }}>
                            {row.slice(0, 7).map((cell, cIdx) => (
                              <td key={cIdx} style={{ padding: "6px 10px", color: "#334155", whiteSpace: "nowrap" }}>
                                {cell || "—"}
                              </td>
                            ))}
                            {preview.columns.length > 7 && <td style={{ padding: "6px 10px", color: "#94A3B8" }}>...</td>}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Business Question Input */}
              <div style={{ marginTop: 14 }}>
                <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.8rem", fontWeight: 600, color: "#475569", marginBottom: 6 }}>
                  <Sparkles size={14} color="#991B1B" /> Business Focus / Objective (Optional)
                </label>
                <input
                  type="text"
                  placeholder="e.g. Analyze sales performance, revenue drivers, and regional trends"
                  value={businessQuery}
                  onChange={(e) => setBusinessQuery(e.target.value)}
                  disabled={isUploading}
                  style={{
                    width: "100%",
                    padding: "9px 12px",
                    borderRadius: 6,
                    border: "1px solid #CBD5E1",
                    fontSize: "0.82rem",
                    outline: "none",
                    boxSizing: "border-box",
                  }}
                />
              </div>

              {/* Progress bar during upload */}
              {isUploading && (
                <div style={{ marginTop: 16 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.78rem", color: "#475569", marginBottom: 6 }}>
                    <span>
                      {uploadStage === "uploading" && `Uploading dataset (${uploadProgress}%)...`}
                      {uploadStage === "validating" && "Validating schema & magic bytes..."}
                      {uploadStage === "starting" && "Initializing Agent Swarm..."}
                      {uploadStage === "success" && "Session ready! Transitioning..."}
                    </span>
                    <span style={{ fontWeight: 600 }}>{uploadProgress}%</span>
                  </div>
                  <div style={{ height: 6, background: "#E2E8F0", borderRadius: 3, overflow: "hidden" }}>
                    <div
                      style={{
                        height: "100%",
                        width: `${uploadProgress}%`,
                        background: "#991B1B",
                        transition: "width 0.2s ease",
                      }}
                    />
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer actions */}
        <div style={{ padding: "14px 24px", background: "#F8FAFC", borderTop: "1px solid #E2E8F0", display: "flex", justifyContent: "flex-end", gap: 10 }}>
          <button
            onClick={onClose}
            disabled={isUploading}
            style={{
              padding: "8px 16px",
              borderRadius: 6,
              border: "1px solid #CBD5E1",
              background: "#FFFFFF",
              color: "#475569",
              fontSize: "0.82rem",
              fontWeight: 600,
              cursor: isUploading ? "not-allowed" : "pointer",
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleUploadAndStart}
            disabled={!selectedFile || isUploading}
            style={{
              padding: "8px 20px",
              borderRadius: 6,
              border: "1px solid rgba(153, 27, 27, 0.4)",
              background: !selectedFile || isUploading ? "#A1A1AA" : "#991B1B",
              color: "#FFFFFF",
              fontSize: "0.82rem",
              fontWeight: 600,
              cursor: !selectedFile || isUploading ? "not-allowed" : "pointer",
              display: "flex",
              alignItems: "center",
              gap: 6,
              boxShadow: "0 1px 2px rgba(0, 0, 0, 0.05)",
            }}
          >
            {isUploading ? (
              <>
                <Loader2 size={16} className="animate-spin" />
                {uploadStage === "uploading" && `Uploading (${uploadProgress}%)...`}
                {uploadStage === "validating" && "Validating..."}
                {uploadStage === "starting" && "Starting Pipeline..."}
                {uploadStage !== "uploading" && uploadStage !== "validating" && uploadStage !== "starting" && "Processing..."}
              </>
            ) : uploadStage === "success" ? (
              <>
                <CheckCircle2 size={16} /> Ready
              </>
            ) : (
              <>
                Start Analysis <ArrowRight size={16} />
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
