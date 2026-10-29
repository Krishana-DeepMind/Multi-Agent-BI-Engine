import { create } from 'zustand';

interface PipelineLog {
  status: string;
  message: string;
  agent: string;
  timestamp: number;
  [key: string]: unknown;
}

interface SessionMeta {
  fileName: string;
  rowCount: number;
  colCount: number;
  fileType: string;
}

interface DashboardSummary {
  chartCount: number;
  queryCount: number;
  dashboardTitle: string;
  dataQualityScore: number;
  sessionId: string;
}

interface PipelineState {
  activeStep: number;
  logs: PipelineLog[];
  isComplete: boolean;
  error: string | null;
  sessionMeta: SessionMeta;
  dashboardSummary: DashboardSummary | null;
  connectSSE: (sessionId: string) => void;
  reset: () => void;
}

const statusToStepMap: Record<string, number> = {
  initiated: 0,
  routing: 0,
  ingesting: 1,
  cleaning: 2,
  featuring: 3,
  querying: 4,
  layouting: 5,
  verifying: 5,
  complete: 6,
};

export const usePipelineStore = create<PipelineState>((set, get) => ({
  activeStep: 0,
  logs: [],
  isComplete: false,
  error: null,
  dashboardSummary: null,
  sessionMeta: { fileName: '—', rowCount: 0, colCount: 0, fileType: '—' },
  connectSSE: (sessionId: string) => {
    // Prevent multiple connections
    if (get().logs.length > 0 && !get().isComplete) return;

    set({ error: null, isComplete: false, activeStep: 0, logs: [], dashboardSummary: null, sessionMeta: { fileName: '—', rowCount: 0, colCount: 0, fileType: '—' } });

    const API = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

    // Kick off pipeline execution in background if not already running
    fetch(`${API}/api/pipeline/${sessionId}/start`, { method: 'POST' }).catch((err) => {
      console.warn('Pipeline start call completed or handled:', err);
    });

    const eventSource = new EventSource(`${API}/api/pipeline/${sessionId}/stream`);

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        const step = statusToStepMap[data.status];

        set((state) => {
          const newMeta = { ...state.sessionMeta };
          if (data.file_name) newMeta.fileName = data.file_name;
          if (data.row_count !== undefined) newMeta.rowCount = data.row_count;
          if (data.col_count !== undefined) newMeta.colCount = data.col_count;
          if (data.file_type) newMeta.fileType = data.file_type;

          let dashboardSummary = state.dashboardSummary;
          if (data.status === 'complete' && data.chart_count !== undefined) {
            dashboardSummary = {
              chartCount: data.chart_count ?? 0,
              queryCount: data.query_count ?? 0,
              dashboardTitle: data.dashboard_title ?? '',
              dataQualityScore: data.data_quality_score ?? 0,
              sessionId: data.session_id ?? sessionId,
            };
          }

          return {
            logs: [...state.logs, { ...data, timestamp: Date.now() }],
            activeStep: step !== undefined ? step : state.activeStep,
            isComplete: data.status === 'complete',
            error: data.status === 'failed' ? data.message : state.error,
            sessionMeta: newMeta,
            dashboardSummary,
          };
        });

        if (data.status === 'complete' || data.status === 'failed') {
          eventSource.close();
        }
      } catch (e) {
        console.error('Error parsing SSE data', e);
      }
    };

    eventSource.onerror = (error) => {
      console.error('SSE Error:', error);
      if (!get().isComplete) {
        set({ error: 'Connection lost to pipeline stream.' });
      }
      eventSource.close();
    };
  },
  reset: () => set({ activeStep: 0, logs: [], isComplete: false, error: null, dashboardSummary: null, sessionMeta: { fileName: '—', rowCount: 0, colCount: 0, fileType: '—' } }),
}));

