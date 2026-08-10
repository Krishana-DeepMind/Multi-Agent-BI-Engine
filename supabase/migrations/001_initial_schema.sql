-- sessions: one row per user pipeline run
CREATE TABLE sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES auth.users(id),
  status TEXT NOT NULL DEFAULT 'initiated',
  raw_file_path TEXT,
  file_type TEXT,
  raw_query TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- pipeline_states: checkpoint per agent completion
CREATE TABLE pipeline_states (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
  agent_name TEXT NOT NULL,
  state_json JSONB NOT NULL,
  tokens_used INT DEFAULT 0,
  checkpoint_at TIMESTAMPTZ DEFAULT NOW()
);

-- dashboards: final output stored for sharing/re-loading
CREATE TABLE dashboards (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID UNIQUE REFERENCES sessions(id),
  config_json JSONB NOT NULL,
  title TEXT,
  published BOOL DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE schema_embeddings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  fingerprint TEXT UNIQUE NOT NULL,
  embedding vector(768),
  column_metadata JSONB,
  domain TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Row-Level Security
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users see own sessions" ON sessions FOR ALL USING (auth.uid() = user_id);
ALTER TABLE dashboards ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users see own dashboards" ON dashboards FOR ALL USING (
  EXISTS (SELECT 1 FROM sessions s WHERE s.id = session_id AND s.user_id = auth.uid())
);
