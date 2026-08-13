"use client";

import React, { useEffect, useRef } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { Stepper } from '@/components/ui/stepper';
import { usePipelineStore } from '@/store/pipeline-store';
import { Button } from '@/components/ui/button';
import { AlertTriangle, Terminal, PlayCircle, FileText, Rows3, Columns3, FileType } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';

export default function ConfigurePage() {
  const params = useParams();
  const router = useRouter();
  const sessionId = params.sessionId as string;
  const logsEndRef = useRef<HTMLDivElement>(null);

  const { activeStep, logs, isComplete, error, sessionMeta, connectSSE, reset } = usePipelineStore();

  useEffect(() => {
    if (sessionId) {
      connectSSE(sessionId);
    }
    return () => {
      // Optional: don't auto-reset on unmount to keep state if they navigate back
    };
  }, [sessionId, connectSSE]);

  useEffect(() => {
    // Auto-scroll logs
    if (logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs]);

  const steps = [
    'Upload',
    'Schema Analysis',
    'Cleaning',
    'Features',
    'Queries',
    'Dashboard',
  ];

  const metaCards = [
    { label: 'File Name', value: sessionMeta.fileName, icon: FileText },
    { label: 'Rows', value: sessionMeta.rowCount.toLocaleString(), icon: Rows3 },
    { label: 'Columns', value: sessionMeta.colCount.toLocaleString(), icon: Columns3 },
    { label: 'File Type', value: sessionMeta.fileType.toUpperCase(), icon: FileType },
  ];

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col items-center pt-24 pb-12 px-4 sm:px-6 lg:px-8">
      <div className="w-full max-w-4xl space-y-10 animate-in fade-in slide-in-from-bottom-4 duration-700">
        
        {/* Header */}
        <div className="text-center space-y-2">
          <h1 className="text-3xl font-bold tracking-tight">Pipeline Execution</h1>
          <p className="text-muted-foreground text-sm">
            Session ID: <span className="font-mono bg-muted px-2 py-0.5 rounded">{sessionId}</span>
          </p>
        </div>

        {error && (
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Pipeline Error</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {/* File Metadata Cards */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          {metaCards.map((card) => (
            <div
              key={card.label}
              className="bg-card border rounded-xl p-4 flex flex-col items-center justify-center space-y-2 shadow-sm transition-all duration-300 hover:shadow-md"
            >
              <card.icon className="w-5 h-5 text-muted-foreground" />
              <span className="text-xs text-muted-foreground font-medium">{card.label}</span>
              <span className="text-sm font-semibold truncate max-w-full">{card.value}</span>
            </div>
          ))}
        </div>

        {/* Stepper */}
        <div className="p-8 bg-card rounded-xl border shadow-sm">
          <Stepper steps={steps} activeStep={activeStep} />
        </div>

        {/* Live Logs Terminal */}
        <div className="bg-[#0D1117] border rounded-xl shadow-lg overflow-hidden flex flex-col h-[400px]">
          <div className="flex items-center px-4 py-3 bg-[#161B22] border-b border-border/10">
            <Terminal className="w-4 h-4 text-muted-foreground mr-2" />
            <span className="text-sm font-medium text-muted-foreground">Agent Swarm Activity Log</span>
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-3 font-mono text-sm">
            {logs.length === 0 && !error && (
              <div className="text-muted-foreground/50 animate-pulse">Waiting for agents to initialize...</div>
            )}
            {logs.map((log, i) => (
              <div key={i} className="flex space-x-3">
                <span className="text-muted-foreground/60 shrink-0">
                  {new Date(log.timestamp).toISOString().split('T')[1].replace('Z', '')}
                </span>
                <span className="text-blue-400 shrink-0 w-24">[{log.agent || 'system'}]</span>
                <span className="text-foreground/90">{log.message}</span>
              </div>
            ))}
            <div ref={logsEndRef} />
          </div>
        </div>

        {/* Action Button */}
        <div className="flex justify-center pt-4">
          <Button
            size="lg"
            className="w-full sm:w-auto h-12 px-8 text-base shadow-xl transition-all duration-300"
            disabled={!isComplete}
            onClick={() => router.push(`/dashboard/${sessionId}`)}
          >
            {isComplete ? (
              <>
                <PlayCircle className="w-5 h-5 mr-2" />
                View Dashboard
              </>
            ) : (
              'Pipeline Running...'
            )}
          </Button>
        </div>

      </div>
    </div>
  );
}
