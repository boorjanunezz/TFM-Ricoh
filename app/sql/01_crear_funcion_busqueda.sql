CREATE OR REPLACE FUNCTION buscar_chunks(
    query_embedding vector(1536),
    top_k int DEFAULT 5,
    umbral_similitud float DEFAULT 0.3
)
RETURNS TABLE (
    chunk_id text,
    documento text,
    referencia_legal text,
    capitulo text,
    articulo text,
    titulo_articulo text,
    contenido text,
    contenido_sin_header text,
    num_tokens_estimados int,
    jurisdiccion text,
    similitud float
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        lc.chunk_id,
        lc.documento,
        lc.referencia_legal,
        lc.capitulo,
        lc.articulo,
        lc.titulo_articulo,
        lc.contenido,
        lc.contenido_sin_header,
        lc.num_tokens_estimados,
        lc.jurisdiccion,
        1 - (lc.embedding <=> query_embedding)::float AS similitud
    FROM legal_chunks lc
    WHERE 1 - (lc.embedding <=> query_embedding) > umbral_similitud
    ORDER BY lc.embedding <=> query_embedding
    LIMIT top_k;
END;
$$;
