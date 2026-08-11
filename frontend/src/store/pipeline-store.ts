import { create } from 'zustand';

interface PipelineLog {
  status: string;
  message: string;
  agent: string;
  timestamp: number;
}

interface SessionMeta {
  fileName: string;
  rowCount: number;
  colCount: number;
  fileType: string;
}

interface PipelineState {
  activeStep: number;
  logs: PipelineLog[];
  isComplete: boolean;
  error: string | null;
  sessionMeta: SessionMeta;
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
  sessionMeta: { fileName: '—', rowCount: 0, colCount: 0, fileType: '—' },
  connectSSE: (sessionId: string) => {
    // Prevent multiple connections
    if (get().logs.length > 0 && !get().isComplete) return;

    set({ error: null, isComplete: false, activeStep: 0, logs: [], sessionMeta: { fileName: '—', rowCount: 0, colCount: 0, fileType: '—' } });

    const eventSource = new EventSource(`/api/pipeline/${sessionId}/stream`);

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        const step = statusToStepMap[data.status];
        
        set((state) => {
          // Update session meta if present in the SSE payload
          const newMeta = { ...state.sessionMeta };
          if (data.file_name) newMeta.fileName = data.file_name;
          if (data.row_count !== undefined) newMeta.rowCount = data.row_count;
          if (data.col_count !== undefined) newMeta.colCount = data.col_count;
          if (data.file_type) newMeta.fileType = data.file_type;

          return {
            logs: [...state.logs, { ...data, timestamp: Date.now() }],
            activeStep: step !== undefined ? step : state.activeStep,
            isComplete: data.status === 'complete',
            error: data.status === 'failed' ? data.message : state.error,
            sessionMeta: newMeta,
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
      set({ error: 'Connection lost to pipeline stream.' });
      eventSource.close();
    };
  },
  reset: () => set({ activeStep: 0, logs: [], isComplete: false, error: null, sessionMeta: { fileName: '—', rowCount: 0, colCount: 0, fileType: '—' } }),
}));
