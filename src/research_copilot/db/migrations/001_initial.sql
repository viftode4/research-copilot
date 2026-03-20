-- Research Copilot: Initial schema
-- Run with: psql -d research_copilot -f 001_initial.sql

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Experiments table
CREATE TABLE IF NOT EXISTS experiments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    hypothesis TEXT,
    description TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'planned',
    config JSONB,
    results JSONB,
    wandb_run_id VARCHAR(255),
    wandb_run_url TEXT,
    slurm_job_id VARCHAR(100),
    script_path TEXT,
    dataset VARCHAR(255),
    model_type VARCHAR(255),
    tags JSONB DEFAULT '[]'::jsonb,
    parent_experiment_id UUID REFERENCES experiments(id),
    created_by VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_experiments_status ON experiments(status);
CREATE INDEX IF NOT EXISTS ix_experiments_dataset ON experiments(dataset);
CREATE INDEX IF NOT EXISTS ix_experiments_model_type ON experiments(model_type);
CREATE INDEX IF NOT EXISTS ix_experiments_created_at ON experiments(created_at);
CREATE INDEX IF NOT EXISTS ix_experiments_tags ON experiments USING GIN(tags);

-- Insights table
CREATE TABLE IF NOT EXISTS insights (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    experiment_id UUID REFERENCES experiments(id),
    category VARCHAR(100) NOT NULL,
    title VARCHAR(500) NOT NULL,
    content TEXT NOT NULL,
    confidence FLOAT,
    tags JSONB DEFAULT '[]'::jsonb,
    metadata JSONB,
    created_by VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_insights_category ON insights(category);
CREATE INDEX IF NOT EXISTS ix_insights_tags ON insights USING GIN(tags);
CREATE INDEX IF NOT EXISTS ix_insights_created_at ON insights(created_at);

-- Papers table
CREATE TABLE IF NOT EXISTS papers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title TEXT NOT NULL,
    authors JSONB NOT NULL DEFAULT '[]'::jsonb,
    abstract TEXT,
    arxiv_id VARCHAR(50) UNIQUE,
    semantic_scholar_id VARCHAR(100) UNIQUE,
    doi VARCHAR(255),
    url TEXT,
    venue VARCHAR(500),
    year INTEGER,
    citation_count INTEGER,
    relevance_notes TEXT,
    tags JSONB DEFAULT '[]'::jsonb,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_papers_arxiv_id ON papers(arxiv_id);
CREATE INDEX IF NOT EXISTS ix_papers_year ON papers(year);
CREATE INDEX IF NOT EXISTS ix_papers_tags ON papers USING GIN(tags);

-- Research context table
CREATE TABLE IF NOT EXISTS research_context (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    key VARCHAR(255) NOT NULL UNIQUE,
    value TEXT NOT NULL,
    context_type VARCHAR(100) NOT NULL,
    metadata JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by VARCHAR(255)
);

CREATE INDEX IF NOT EXISTS ix_research_context_type ON research_context(context_type);

-- Updated_at trigger
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER experiments_updated_at
    BEFORE UPDATE ON experiments
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER research_context_updated_at
    BEFORE UPDATE ON research_context
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
