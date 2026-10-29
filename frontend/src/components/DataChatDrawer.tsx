"use client";

import React, { useState, useRef, useEffect, useMemo } from "react";
import {
  Sparkles,
  Send,
  X,
  User,
  RotateCcw,
  Database,
} from "lucide-react";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sql?: string;
  columns?: string[];
  data?: Record<string, any>[];
  rowCount?: number;
  timestamp: string;
  error?: string;
}

interface DataChatDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  sessionId: string;
  datasetTitle?: string;
}

export default function DataChatDrawer({
  isOpen,
  onClose,
  sessionId,
  datasetTitle = "Dataset",
}: DataChatDrawerProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputMessage, setInputMessage] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

  // Dynamic suggestion chips based on dataset context
  const suggestions = useMemo(() => {
    const titleLower = datasetTitle.toLowerCase();
    if (titleLower.includes("hr") || titleLower.includes("employee")) {
      return [
        "Which department has the highest average salary?",
        "How many employees are in Sales?",
        "What is the overall attrition rate?",
        "Who has the highest overtime hours?",
        "Show average salary by department",
      ];
    }
    if (titleLower.includes("retail") || titleLower.includes("order") || titleLower.includes("sales")) {
      return [
        "What is the total revenue?",
        "Which product or SKU has the highest sales volume?",
        "What is the average order value?",
        "How many unique orders are in the dataset?",
        "Show top 5 customers by total spending",
      ];
    }
    if (titleLower.includes("patient") || titleLower.includes("health") || titleLower.includes("hospital") || titleLower.includes("medical")) {
      return [
        "Which diagnosis category has the highest net revenue?",
        "Which diagnosis category has the highest patient volume?",
        "What is the overall 30-day readmission rate?",
        "How many total admissions are recorded?",
      ];
    }
    return [
      "How many total records are in this dataset?",
      "What are the key summary statistics?",
      "Which category has the highest volume?",
      "Show top 5 records by highest values",
    ];
  }, [datasetTitle]);

  // Initial greeting
  useEffect(() => {
    if (messages.length === 0) {
      setMessages([
        {
          id: "welcome",
          role: "assistant",
          content: `👋 Hi! I'm your **AutoBI Data Assistant**. Ask me any question about **${datasetTitle}** and I'll give you a direct, accurate answer:`,
          timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        },
      ]);
    }
  }, [datasetTitle, messages.length]);

  // Focus input when opened
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 150);
    }
  }, [isOpen]);

  // Auto-scroll on new messages
  useEffect(() => {
    if (isOpen) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, isLoading, isOpen]);

  const handleSendMessage = async (textToSend?: string) => {
    const text = (textToSend ?? inputMessage).trim();
    if (!text || isLoading) return;

    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      content: text,
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInputMessage("");
    setIsLoading(true);

    try {
      const res = await fetch(`${API}/api/pipeline/${sessionId}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });

      if (!res.ok) {
        throw new Error(`Server returned ${res.status}`);
      }

      const data = await res.json();

      const assistantMsg: ChatMessage = {
        id: `assistant-${Date.now()}`,
        role: "assistant",
        content: data.answer || "I processed your request, but received no specific answer summary.",
        sql: data.sql,
        columns: data.columns,
        data: data.data,
        rowCount: data.row_count,
        timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      };

      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err: any) {
      const errorMsg: ChatMessage = {
        id: `assistant-err-${Date.now()}`,
        role: "assistant",
        content: `⚠️ Sorry, I encountered an error answering that question: ${err.message || "Unknown error"}. Please try again or rephrase.`,
        timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setIsLoading(false);
    }
  };

  const clearChat = () => {
    setMessages([
      {
        id: "welcome-reset",
        role: "assistant",
        content: `Conversation cleared. Ready for your questions about **${datasetTitle}**!`,
        timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      },
    ]);
  };

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/40 backdrop-blur-xs transition-opacity animate-in fade-in duration-200"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        className="w-full sm:w-[480px] md:w-[520px] h-full bg-white border-l border-slate-200 shadow-2xl flex flex-col animate-in slide-in-from-right duration-200"
        style={{ fontFamily: "'Inter', sans-serif" }}
      >
        {/* Header */}
        <div className="px-5 py-4 border-b border-slate-200 bg-slate-50/70 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-red-800 text-white flex items-center justify-center shadow-xs">
              <Sparkles className="w-4 h-4" />
            </div>
            <h3 className="text-sm font-bold text-slate-900 leading-tight">Data Assistant</h3>
          </div>
          <div className="flex items-center gap-1.5">
            <button
              onClick={clearChat}
              title="Clear conversation"
              className="p-1.5 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-md transition-colors"
            >
              <RotateCcw className="w-4 h-4" />
            </button>
            <button
              onClick={onClose}
              title="Close panel"
              className="p-1.5 text-slate-400 hover:text-red-700 hover:bg-red-50 rounded-md transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Suggestion Chips */}
        <div className="px-5 py-2.5 bg-slate-100/60 border-b border-slate-200 overflow-x-auto no-scrollbar flex items-center gap-2">
          <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider shrink-0 flex items-center gap-1">
            <Database className="w-3 h-3 text-red-700" /> Suggestions:
          </span>
          {suggestions.map((sug, idx) => (
            <button
              key={idx}
              onClick={() => handleSendMessage(sug)}
              disabled={isLoading}
              className="text-xs bg-white text-slate-700 hover:text-red-700 hover:border-red-300 hover:bg-red-50/40 px-2.5 py-1 rounded-full border border-slate-200 transition-all shrink-0 whitespace-nowrap shadow-2xs font-medium cursor-pointer"
            >
              {sug}
            </button>
          ))}
        </div>

        {/* Message Thread */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4 bg-slate-50/30">
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex flex-col ${msg.role === "user" ? "items-end" : "items-start"}`}
            >
              {/* Message Header */}
              <div className="flex items-center gap-1.5 mb-1 px-1">
                {msg.role === "assistant" ? (
                  <>
                    <div className="w-4 h-4 rounded-full bg-red-700 text-white flex items-center justify-center text-[10px] font-bold">
                      A
                    </div>
                    <span className="text-[11px] font-semibold text-slate-700">AutoBI Assistant</span>
                  </>
                ) : (
                  <>
                    <span className="text-[11px] font-medium text-slate-500">You</span>
                    <div className="w-4 h-4 rounded-full bg-slate-300 text-slate-700 flex items-center justify-center text-[10px]">
                      <User className="w-2.5 h-2.5" />
                    </div>
                  </>
                )}
                <span className="text-[10px] text-slate-400 ml-1">{msg.timestamp}</span>
              </div>

              {/* Message Bubble: Pure answer only */}
              <div
                className={`rounded-xl px-4 py-3 max-w-[92%] shadow-xs text-sm leading-relaxed ${
                  msg.role === "user"
                    ? "bg-[#991B1B] text-white rounded-tr-xs"
                    : "bg-white border border-slate-200 text-slate-800 rounded-tl-xs"
                }`}
              >
                {/* Render formatted answer text */}
                <div className="space-y-1.5 whitespace-pre-wrap">
                  {renderFormattedText(msg.content, msg.role === "user")}
                </div>
              </div>
            </div>
          ))}

          {/* Thinking / Loading Indicator */}
          {isLoading && (
            <div className="flex flex-col items-start animate-in fade-in duration-150">
              <div className="flex items-center gap-1.5 mb-1 px-1">
                <div className="w-4 h-4 rounded-full bg-red-700 text-white flex items-center justify-center text-[10px] font-bold">
                  A
                </div>
                <span className="text-[11px] font-semibold text-slate-700">AutoBI Assistant</span>
              </div>
              <div className="rounded-xl rounded-tl-xs px-4 py-3 bg-white border border-slate-200 shadow-xs flex items-center gap-3">
                <div className="flex items-center gap-1">
                  <div className="w-2 h-2 rounded-full bg-red-700 animate-bounce [animation-delay:-0.3s]" />
                  <div className="w-2 h-2 rounded-full bg-red-700 animate-bounce [animation-delay:-0.15s]" />
                  <div className="w-2 h-2 rounded-full bg-red-700 animate-bounce" />
                </div>
                <span className="text-xs text-slate-500 font-medium">
                  Querying DuckDB & synthesizing answer...
                </span>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input Bar */}
        <div className="p-4 border-t border-slate-200 bg-white">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSendMessage();
            }}
            className="flex items-end gap-2"
          >
            <div className="relative flex-1">
              <textarea
                ref={inputRef}
                rows={2}
                value={inputMessage}
                onChange={(e) => setInputMessage(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSendMessage();
                  }
                }}
                placeholder="Ask any question about your data (e.g. Which department has highest salary?)..."
                disabled={isLoading}
                className="w-full resize-none rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:border-red-700 focus:ring-1 focus:ring-red-700/20 leading-relaxed disabled:opacity-50"
              />
              <span className="absolute bottom-2 right-2 text-[10px] text-slate-400 pointer-events-none">
                Press Enter ↵
              </span>
            </div>

            <button
              type="submit"
              disabled={isLoading || !inputMessage.trim()}
              className="h-9 px-3.5 rounded-lg bg-red-800 hover:bg-red-900 disabled:opacity-40 disabled:hover:bg-red-800 text-white text-xs font-semibold flex items-center justify-center gap-1.5 transition-all shadow-xs shrink-0 cursor-pointer"
            >
              <Send className="w-3.5 h-3.5" />
              <span>Ask</span>
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}

/** Helper to render simple markdown bolding and inline code tokens */
function renderFormattedText(text: string, isUser: boolean) {
  if (!text) return null;
  const parts = text.split(/(\*\*.*?\*\*|`.*?`|\n)/g);

  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong
          key={i}
          className={`font-semibold ${isUser ? "text-white underline decoration-white/40" : "text-slate-950 font-bold"}`}
        >
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code
          key={i}
          className={`px-1 py-0.5 rounded font-mono text-[11px] ${
            isUser ? "bg-red-900 text-white" : "bg-slate-100 text-red-800 border border-slate-200"
          }`}
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part === "\n") {
      return <br key={i} />;
    }
    return part;
  });
}
