-- Migration: Add outbox/inbox tables for exactly-once delivery semantics
-- Version: 002
-- Date: 2025-08-10

-- Outbox table for pending side effects
CREATE TABLE IF NOT EXISTS orchestrator_outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL,
    effect_type VARCHAR(50) NOT NULL, -- 'webhook', 'api_call', 'notification', etc.
    payload JSONB NOT NULL,
    target_url TEXT,
    status VARCHAR(20) DEFAULT 'pending', -- 'pending', 'delivered', 'failed'
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    delivered_at TIMESTAMPTZ,
    last_error TEXT,
    metadata JSONB DEFAULT '{}',
    
    INDEX idx_outbox_status (status),
    INDEX idx_outbox_task (task_id),
    INDEX idx_outbox_created (created_at)
);

-- Inbox table for processed webhook IDs (deduplication)
CREATE TABLE IF NOT EXISTS orchestrator_inbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id VARCHAR(255) NOT NULL, -- GitHub event ID, ClickUp webhook ID, etc.
    source VARCHAR(50) NOT NULL, -- 'github', 'clickup', 'make', etc.
    signature_hash VARCHAR(64), -- HMAC-SHA256 hash for verification
    received_at TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    payload JSONB NOT NULL,
    task_id UUID, -- Reference to created task if any
    status VARCHAR(20) DEFAULT 'received', -- 'received', 'processing', 'processed', 'rejected'
    rejection_reason TEXT,
    
    UNIQUE(source, external_id), -- Prevent duplicate processing
    INDEX idx_inbox_source (source),
    INDEX idx_inbox_received (received_at),
    INDEX idx_inbox_status (status)
);

-- API keys table for RBAC
CREATE TABLE IF NOT EXISTS orchestrator_api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash VARCHAR(64) NOT NULL UNIQUE, -- SHA256 hash of the key
    name VARCHAR(100) NOT NULL,
    role VARCHAR(20) NOT NULL, -- 'admin', 'service', 'readonly'
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT true,
    created_by VARCHAR(100),
    metadata JSONB DEFAULT '{}',
    
    INDEX idx_api_keys_hash (key_hash),
    INDEX idx_api_keys_active (is_active),
    INDEX idx_api_keys_expires (expires_at)
);

-- Budget tracking table for AI model usage
CREATE TABLE IF NOT EXISTS orchestrator_budgets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider VARCHAR(50) NOT NULL, -- 'anthropic', 'openai', 'google'
    date DATE NOT NULL,
    budget_usd DECIMAL(10, 2) NOT NULL,
    spent_usd DECIMAL(10, 2) DEFAULT 0,
    token_count INTEGER DEFAULT 0,
    request_count INTEGER DEFAULT 0,
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    
    UNIQUE(provider, date),
    INDEX idx_budgets_provider_date (provider, date)
);

-- Circuit breaker state table
CREATE TABLE IF NOT EXISTS orchestrator_circuit_breakers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    service VARCHAR(50) NOT NULL UNIQUE, -- 'anthropic', 'github', 'supabase', etc.
    state VARCHAR(20) DEFAULT 'closed', -- 'closed', 'open', 'half_open'
    failure_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    last_failure_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    opened_at TIMESTAMPTZ,
    next_retry_at TIMESTAMPTZ,
    error_rate DECIMAL(5, 4) DEFAULT 0, -- 0.0000 to 1.0000
    metadata JSONB DEFAULT '{}',
    
    INDEX idx_circuit_state (state),
    INDEX idx_circuit_service (service)
);

-- Add columns to existing tables if they don't exist
ALTER TABLE orchestrator_runs 
ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(255),
ADD COLUMN IF NOT EXISTS model_used VARCHAR(50),
ADD COLUMN IF NOT EXISTS model_tokens_used INTEGER,
ADD COLUMN IF NOT EXISTS model_cost_usd DECIMAL(10, 6);

CREATE INDEX IF NOT EXISTS idx_runs_idempotency ON orchestrator_runs(idempotency_key);

-- Function to clean up old outbox messages
CREATE OR REPLACE FUNCTION cleanup_old_outbox()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM orchestrator_outbox
    WHERE status = 'delivered' 
    AND delivered_at < NOW() - INTERVAL '7 days';
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Function to check replay attacks
CREATE OR REPLACE FUNCTION check_webhook_replay(
    p_source VARCHAR,
    p_external_id VARCHAR,
    p_signature VARCHAR,
    p_timestamp TIMESTAMPTZ,
    p_tolerance_minutes INTEGER DEFAULT 5
) RETURNS BOOLEAN AS $$
BEGIN
    -- Check if already processed
    IF EXISTS (
        SELECT 1 FROM orchestrator_inbox
        WHERE source = p_source
        AND external_id = p_external_id
    ) THEN
        RETURN FALSE; -- Replay detected
    END IF;
    
    -- Check timestamp tolerance
    IF ABS(EXTRACT(EPOCH FROM (NOW() - p_timestamp))) > (p_tolerance_minutes * 60) THEN
        RETURN FALSE; -- Too old or too far in future
    END IF;
    
    RETURN TRUE; -- Valid webhook
END;
$$ LANGUAGE plpgsql;

-- Permissions
GRANT SELECT, INSERT, UPDATE ON orchestrator_outbox TO service_role;
GRANT SELECT, INSERT ON orchestrator_inbox TO service_role;
GRANT SELECT ON orchestrator_api_keys TO service_role;
GRANT SELECT, INSERT, UPDATE ON orchestrator_budgets TO service_role;
GRANT SELECT, INSERT, UPDATE ON orchestrator_circuit_breakers TO service_role;