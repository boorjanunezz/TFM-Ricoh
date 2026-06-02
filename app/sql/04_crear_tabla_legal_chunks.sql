CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS legal_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_id TEXT UNIQUE NOT NULL,
    documento TEXT,
    referencia_legal TEXT,
    capitulo TEXT,
    articulo TEXT,
    titulo_articulo TEXT,
    contenido TEXT,
    contenido_sin_header TEXT,
    num_tokens_estimados INT,
    jurisdiccion TEXT DEFAULT 'UE',
    embedding vector(1536)
);

-- Índice HNSW para búsqueda vectorial eficiente por distancia coseno
CREATE INDEX IF NOT EXISTS idx_legal_chunks_embedding
    ON legal_chunks
    USING hnsw (embedding vector_cosine_ops);