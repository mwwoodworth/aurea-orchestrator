-- AUREA Orchestrator Database Schema
-- Run this migration in Supabase SQL Editor

-- Create orchestrator_tasks table
CREATE TABLE IF NOT EXISTS orchestrator_tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  type TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  priority INT DEFAULT 100,
  enqueued_at TIMESTAMPTZ DEFAULT NOW(),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  status TEXT CHECK (status IN ('queued','running','done','failed','canceled')) DEFAULT 'queued',
  last_error TEXT,
  trace_id TEXT,
  idempotency_key TEXT,
  retry_count INT DEFAULT 0,
  metadata JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create orchestrator_runs table
CREATE TABLE IF NOT EXISTS orchestrator_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id UUID REFERENCES orchestrator_tasks(id) ON DELETE CASCADE,
  started_at TIMESTAMPTZ DEFAULT NOW(),
  ended_at TIMESTAMPTZ,
  status TEXT CHECK (status IN ('started','success','failed','timeout','canceled')) DEFAULT 'started',
  attempts INT DEFAULT 1,
  metrics JSONB DEFAULT '{}'::jsonb,
  logs_url TEXT,
  error_details JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_tasks_status_priority 
  ON orchestrator_tasks(status, priority DESC, enqueued_at ASC);

CREATE INDEX IF NOT EXISTS idx_tasks_type 
  ON orchestrator_tasks(type);

CREATE INDEX IF NOT EXISTS idx_tasks_idempotency 
  ON orchestrator_tasks(idempotency_key) 
  WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_tasks_trace 
  ON orchestrator_tasks(trace_id) 
  WHERE trace_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_runs_task_id 
  ON orchestrator_runs(task_id);

CREATE INDEX IF NOT EXISTS idx_runs_started_at 
  ON orchestrator_runs(started_at DESC);

-- Create updated_at trigger function
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create triggers for updated_at
CREATE TRIGGER update_orchestrator_tasks_updated_at
  BEFORE UPDATE ON orchestrator_tasks
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER update_orchestrator_runs_updated_at
  BEFORE UPDATE ON orchestrator_runs
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at();

-- Create views for common queries
CREATE OR REPLACE VIEW v_task_summary AS
SELECT 
  type,
  status,
  COUNT(*) as count,
  AVG(EXTRACT(EPOCH FROM (completed_at - enqueued_at))) as avg_duration_seconds,
  MIN(enqueued_at) as first_task,
  MAX(completed_at) as last_completed
FROM orchestrator_tasks
WHERE enqueued_at > NOW() - INTERVAL '24 hours'
GROUP BY type, status;

CREATE OR REPLACE VIEW v_recent_runs AS
SELECT 
  r.*,
  t.type as task_type,
  t.status as task_status,
  EXTRACT(EPOCH FROM (r.ended_at - r.started_at)) as duration_seconds
FROM orchestrator_runs r
JOIN orchestrator_tasks t ON r.task_id = t.id
WHERE r.started_at > NOW() - INTERVAL '24 hours'
ORDER BY r.started_at DESC;

-- Row Level Security (RLS) policies
ALTER TABLE orchestrator_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE orchestrator_runs ENABLE ROW LEVEL SECURITY;

-- Create policies for service role (full access)
CREATE POLICY "Service role has full access to tasks" 
  ON orchestrator_tasks 
  FOR ALL 
  USING (auth.role() = 'service_role');

CREATE POLICY "Service role has full access to runs" 
  ON orchestrator_runs 
  FOR ALL 
  USING (auth.role() = 'service_role');

-- Create storage bucket for logs and content
INSERT INTO storage.buckets (id, name, public)
VALUES ('orchestrator', 'orchestrator', false)
ON CONFLICT (id) DO NOTHING;

INSERT INTO storage.buckets (id, name, public)
VALUES ('content', 'content', true)
ON CONFLICT (id) DO NOTHING;

-- Grant permissions
GRANT ALL ON orchestrator_tasks TO service_role;
GRANT ALL ON orchestrator_runs TO service_role;
GRANT SELECT ON v_task_summary TO service_role;
GRANT SELECT ON v_recent_runs TO service_role;