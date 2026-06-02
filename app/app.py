"""
SmartReg Monitor - Chatbot RAG de Compliance (Chainlit)
========================================================
Sprint 5 - Opción 2: Pipeline de ingesta integrado en app.py

Ejecutar con:  chainlit run app.py

Autores: Alonso Gómez, Ethan Macías, Borja Núñez
Proyecto: SmartReg Monitor - Ricoh Edition

Pipeline Medallion integrado:
  Usuario sube PDF → Bronze (Blob) → Silver (Doc Intelligence)
  → Gold (Chunking) → Supabase (Embeddings) → Notificación en chat
"""
import os
import re
import json
import random
import hashlib
import logging
import asyncio
from typing import Optional
from datetime import datetime, timezone

import chainlit as cl
import chainlit.data as cl_data
from openai import AzureOpenAI
from supabase import create_client
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from azure.storage.blob import BlobServiceClient
from data_layer import SupabaseDataLayer


# ══════════════════════════════════════════════
# CONFIGURACIÓN (desde .env)
# ══════════════════════════════════════════════
AZURE_OPENAI_API_KEY = os.environ["AZURE_OPENAI_API_KEY"]
AZURE_OPENAI_ENDPOINT = os.environ["AZURE_OPENAI_ENDPOINT"]
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-06-01")
AZURE_EMBEDDING_DEPLOYMENT = os.environ.get("AZURE_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
AZURE_CHAT_DEPLOYMENT = os.environ.get("AZURE_CHAT_DEPLOYMENT", "gpt-4o")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

DOC_INTELLIGENCE_ENDPOINT = os.environ["DOC_INTELLIGENCE_ENDPOINT"]
DOC_INTELLIGENCE_KEY = os.environ["DOC_INTELLIGENCE_KEY"]

BLOB_CONNECTION_STRING = os.environ["AZURE_STORAGE_CONNECTION_STRING"]

TOP_K = 4
UMBRAL_SIMILITUD = 0.4
MAX_CHUNK_CHARS = 3200
OVERLAP_CHARS = 400


# ══════════════════════════════════════════════
# SYSTEM PROMPT LEGAL (PBI 4.2)
# ══════════════════════════════════════════════
SYSTEM_PROMPT = """Eres un asistente legal especializado en normativa europea para Ricoh.
Tu función es ayudar al equipo de Compliance a entender regulaciones como el RGPD y el AI Act.

REGLAS ESTRICTAS:
1. Responde ÚNICAMENTE con la información proporcionada en el CONTEXTO. No inventes ni añadas información externa.
2. Si el contexto no contiene información suficiente para responder, di exactamente: "No dispongo de información suficiente en los documentos cargados para responder a esta pregunta."
3. SIEMPRE cita la fuente de cada afirmación usando el formato: [Documento - Artículo]. Por ejemplo: [RGPD - Artículo 2] o [AI Act - Artículo 1].
4. Si varios artículos son relevantes, cítalos todos.
5. Responde en español, de forma clara y concisa.
6. Estructura las respuestas largas con párrafos, no con listas.
7. Al final de cada respuesta, incluye una sección "📎 Fuentes consultadas:" listando todos los artículos citados.

Recuerda: eres una herramienta de APOYO. Tus respuestas no constituyen asesoramiento legal vinculante."""

# Frase exacta que el modelo debe devolver cuando no tiene información suficiente.
# Se usa para suprimir el bloque de fuentes en ese caso.
RESPUESTA_SIN_INFO = "No dispongo de información suficiente en los documentos cargados para responder a esta pregunta."


# ══════════════════════════════════════════════
# CLIENTES
# ══════════════════════════════════════════════
openai_client = AzureOpenAI(
    api_key=AZURE_OPENAI_API_KEY,
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_version=AZURE_OPENAI_API_VERSION,
)

supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)

doc_client = DocumentIntelligenceClient(
    endpoint=DOC_INTELLIGENCE_ENDPOINT,
    credential=AzureKeyCredential(DOC_INTELLIGENCE_KEY),
)

blob_service = BlobServiceClient.from_connection_string(BLOB_CONNECTION_STRING)

# Data layer para historial
cl_data._data_layer = SupabaseDataLayer(SUPABASE_URL, SUPABASE_KEY)

# Logger del módulo
logger = logging.getLogger("smartreg.pipeline")


# ══════════════════════════════════════════════
# AUTENTICACIÓN con Supabase
# ══════════════════════════════════════════════
def hash_password(password: str) -> str:
    """Hashea la contraseña con SHA-256."""
    return hashlib.sha256(password.encode()).hexdigest()


@cl.password_auth_callback
def auth_callback(username: str, password: str):
    """
    Login estricto:
    - Si el usuario existe y la contraseña coincide → login permitido
    - Si el usuario no existe o la contraseña es incorrecta → acceso denegado
    """
    password_hashed = hash_password(password)

    result = supabase_client.table("app_users") \
        .select("*") \
        .eq("username", username) \
        .execute()

    if result.data:
        user = result.data[0]
        if user["password_hash"] == password_hashed:
            return cl.User(identifier=username)

    print(f"🔒 Intento de acceso fallido para el usuario: '{username}'")
    return None


# ══════════════════════════════════════════════
# FUNCIONES RAG (PBI 4.1)
# ══════════════════════════════════════════════
def vectorizar_pregunta(pregunta: str) -> list[float]:
    """Genera el embedding de la pregunta del usuario."""
    response = openai_client.embeddings.create(
        model=AZURE_EMBEDDING_DEPLOYMENT,
        input=pregunta,
    )
    return response.data[0].embedding


def buscar_contexto(query_embedding: list[float]) -> list[dict]:
    """Busca los chunks más similares en Supabase."""
    result = supabase_client.rpc("buscar_chunks", {
        "query_embedding": query_embedding,
        "top_k": TOP_K,
        "umbral_similitud": UMBRAL_SIMILITUD,
    }).execute()
    return result.data


def construir_contexto(chunks: list[dict]) -> str:
    """Formatea los chunks recuperados como contexto para el LLM."""
    if not chunks:
        return "No se encontraron documentos relevantes."

    partes = []
    for i, chunk in enumerate(chunks, 1):
        ref = f"{chunk['documento']} - {chunk['articulo']}"
        if chunk.get("titulo_articulo"):
            ref += f" ({chunk['titulo_articulo']})"
        if chunk.get("capitulo"):
            ref = f"{chunk['capitulo']} > {ref}"

        similitud = chunk.get("similitud", 0)
        partes.append(
            f"--- Fuente {i}: [{ref}] (relevancia: {similitud:.0%}) ---\n"
            f"{chunk['contenido_sin_header']}"
        )

    return "\n\n".join(partes)


# ══════════════════════════════════════════════
# PIPELINE MEDALLION — Funciones auxiliares
# ══════════════════════════════════════════════

def subir_blob(container: str, blob_name: str, data: bytes | str) -> None:
    """Sube contenido a un contenedor de Azure Blob Storage."""
    container_client = blob_service.get_container_client(container)
    try:
        container_client.create_container()
    except Exception:
        pass  # Ya existe

    blob_client = container_client.get_blob_client(blob_name)
    content = data if isinstance(data, bytes) else data.encode("utf-8")
    blob_client.upload_blob(content, overwrite=True)
    logger.info(f"   📦 Subido: {container}/{blob_name}")


def nombre_doc_desde_filename(filename: str) -> str:
    """Convierte 'mi_documento.pdf' → 'Mi Documento'."""
    return (
        filename
        .replace(".pdf", "")
        .replace(".json", "")
        .replace("_", " ")
        .title()
    )


def encontrar_inicio_contenido(paragraphs: list[dict]) -> int:
    """
    Detecta la página donde comienza el contenido normativo.
    Busca patrones como 'Artículo', 'CAPÍTULO', 'Disposición'.
    Salta automáticamente portadas, índices e información inicial.
    """
    patrones_inicio = {
        "Artículo", "ARTÍCULO",
        "Capítulo", "CAPÍTULO",
        "Disposición", "DISPOSICIÓN",
        "Sección", "SECCIÓN",
    }

    for para in paragraphs:
        contenido = para.get("content", "").strip()
        for patron in patrones_inicio:
            if patron in contenido and 10 < len(contenido) < 200:
                page = para.get("page", 1)
                logger.info(f"   🔍 Inicio detectado en página {page}")
                return page

    logger.info("   ⚠️  No se detectó patrón. Procesando desde página 1.")
    return 1


# ══════════════════════════════════════════════
# ETAPA 1 — BRONZE → SILVER
# Document Intelligence → JSON de párrafos
# ══════════════════════════════════════════════

def extraer_texto_document_intelligence(pdf_bytes: bytes) -> list[dict]:
    """
    Envía el PDF a Azure Document Intelligence (prebuilt-layout)
    y devuelve párrafos desde donde comienza el contenido normativo.
    """
    poller = doc_client.begin_analyze_document(
        model_id="prebuilt-layout",
        body=pdf_bytes,
        content_type="application/pdf",
    )
    result = poller.result()

    paragraphs = []
    skip_roles = {"pageHeader", "pageFooter", "pageNumber"}

    for p in result.paragraphs:
        role = p.role if hasattr(p, "role") and p.role else "none"
        if role in skip_roles:
            continue

        page = None
        if p.bounding_regions:
            page = p.bounding_regions[0].page_number

        paragraphs.append({
            "role": role,
            "content": p.content.strip(),
            "page": page,
        })

    # Detectar dónde comienza el contenido normativo
    pagina_inicio = encontrar_inicio_contenido(paragraphs)

    # Filtrar: solo párrafos desde esa página en adelante
    paragraphs_filtrados = [
        p for p in paragraphs
        if p.get("page") is None or p.get("page") >= pagina_inicio
    ]

    logger.info(f"   🔄 Párrafos antes de filtrar: {len(paragraphs)}")
    logger.info(f"   📄 Párrafos después de filtrar: {len(paragraphs_filtrados)}")

    return paragraphs_filtrados


# ══════════════════════════════════════════════
# ETAPA 2 — SILVER → GOLD
# Chunking semántico por artículos
# ══════════════════════════════════════════════

def agrupar_por_articulos(paragraphs: list[dict]) -> list[dict]:
    """Agrupa párrafos consecutivos bajo su artículo correspondiente."""
    groups: list[dict] = []
    current_chapter: Optional[str] = None
    current_article: Optional[str] = None
    current_title: Optional[str] = None
    current_parts: list[str] = []
    current_pages: set[int] = set()

    def flush() -> None:
        nonlocal current_parts, current_pages
        if current_parts:
            groups.append({
                "capitulo": current_chapter,
                "articulo": current_article or "Preámbulo",
                "titulo_articulo": current_title,
                "contenido": "\n\n".join(current_parts),
                "paginas": sorted(current_pages),
            })
        current_parts = []
        current_pages = set()

    for para in paragraphs:
        content = para["content"]

        # Detectar capítulo
        cap_match = re.match(
            r'^CAPÍTULO\s+([IVXLCDM]+)\s*(.*)', content, re.IGNORECASE
        )
        if cap_match:
            current_chapter = f"Capítulo {cap_match.group(1)}"
            if cap_match.group(2).strip():
                current_chapter += f" - {cap_match.group(2).strip()}"
            continue

        # Detectar artículo
        art_match = re.match(
            r'^Artículo\s+(\d+)\s*(.*)', content, re.IGNORECASE
        )
        if art_match:
            flush()
            current_article = f"Artículo {art_match.group(1)}"
            current_title = art_match.group(2).strip() if art_match.group(2) else None
            if para.get("page"):
                current_pages.add(para["page"])
            continue

        # Subtítulo tras artículo
        if para["role"] == "sectionHeading":
            if current_article and not current_title and not current_parts:
                current_title = content
                continue
            elif not current_article and current_chapter:
                if " - " not in current_chapter:
                    current_chapter += f" - {content}"
                continue

        # Contenido normal
        current_parts.append(content)
        if para.get("page"):
            current_pages.add(para["page"])

    flush()
    return groups


def dividir_chunks_largos(
    groups: list[dict], max_chars: int, overlap_chars: int
) -> list[dict]:
    """Divide grupos demasiado largos en sub-chunks con solapamiento."""
    chunks_finales: list[dict] = []
    for group in groups:
        contenido = group["contenido"]
        if len(contenido) <= max_chars:
            chunks_finales.append(group)
        else:
            parrafos = contenido.split("\n\n")
            current: list[str] = []
            current_len = 0

            for p in parrafos:
                if current_len + len(p) > max_chars and current:
                    chunks_finales.append({
                        **group,
                        "articulo": f"{group['articulo']} (parte {len(chunks_finales) + 1})",
                        "contenido": "\n\n".join(current),
                    })
                    overlap: list[str] = []
                    ol = 0
                    for prev in reversed(current):
                        if ol + len(prev) <= overlap_chars:
                            overlap.insert(0, prev)
                            ol += len(prev)
                        else:
                            break
                    current = overlap
                    current_len = ol

                current.append(p)
                current_len += len(p)

            if current:
                label = group["articulo"]
                if len(contenido) > max_chars:
                    label = f"{group['articulo']} (parte {len(chunks_finales) + 1})"
                chunks_finales.append({
                    **group,
                    "articulo": label,
                    "contenido": "\n\n".join(current),
                })

    return chunks_finales


def generar_chunks_con_metadatos(
    groups: list[dict], nombre_doc: str
) -> list[dict]:
    """Añade IDs, headers y metadatos a cada chunk."""
    chunks: list[dict] = []
    for i, g in enumerate(groups):
        chunk_id = hashlib.md5(
            f"{nombre_doc}|{g['articulo']}|{i}".encode()
        ).hexdigest()[:12]

        header_parts = [nombre_doc]
        if g.get("capitulo"):
            header_parts.append(g["capitulo"])
        header_parts.append(g["articulo"])
        if g.get("titulo_articulo"):
            header_parts.append(g["titulo_articulo"])
        header = " > ".join(header_parts)

        chunks.append({
            "chunk_id": chunk_id,
            "documento": nombre_doc,
            "referencia_legal": "",
            "capitulo": g.get("capitulo"),
            "articulo": g["articulo"],
            "titulo_articulo": g.get("titulo_articulo"),
            "contenido": f"{header}\n\n{g['contenido']}",
            "contenido_sin_header": g["contenido"],
            "num_tokens_estimados": len(g["contenido"]) // 4,
            "jurisdiccion": "UE",
            "paginas": g.get("paginas", []),
        })

    return chunks


# ══════════════════════════════════════════════
# ETAPA 3 — GOLD → SUPABASE
# Embeddings + upsert en legal_chunks
# ══════════════════════════════════════════════

def generar_embedding(texto: str) -> list[float]:
    """Genera embedding con Azure OpenAI."""
    response = openai_client.embeddings.create(
        model=AZURE_EMBEDDING_DEPLOYMENT,
        input=texto,
    )
    return response.data[0].embedding


# ══════════════════════════════════════════════
# PIPELINE COMPLETO (Orquestador)
# ══════════════════════════════════════════════

async def ejecutar_pipeline_medallion(
    filename: str,
    pdf_bytes: bytes,
    msg: cl.Message,
) -> None:
    """
    Pipeline Medallion completo ejecutado en background.
    Bronze → Silver → Gold → Supabase, con notificación en el chat.

    Args:
        filename:  Nombre del archivo PDF (ej. 'rgpd.pdf').
        pdf_bytes: Contenido binario del PDF.
        msg:       Mensaje de Chainlit para actualizar el progreso.
    """
    nombre_doc = nombre_doc_desde_filename(filename)
    json_filename = filename.replace(".pdf", ".json")
    gold_json_filename = f"chunks_{json_filename}"

    try:
        # ── BRONZE: Subir PDF al Blob Storage ──
        msg.content += f"🥉 **[Bronze]** Subiendo PDF a Azure Blob Storage...\n"
        await msg.update()

        await asyncio.to_thread(subir_blob, "bronze", filename, pdf_bytes)
        msg.content += f"   ✅ PDF almacenado en `bronze/{filename}`\n\n"
        await msg.update()

        # ── SILVER: Extracción con Document Intelligence ──
        msg.content += f"🥈 **[Silver]** Extrayendo texto con Document Intelligence...\n"
        await msg.update()

        paragraphs = await asyncio.to_thread(
            extraer_texto_document_intelligence, pdf_bytes
        )

        if not paragraphs:
            msg.content += "   ⚠️ No se extrajeron párrafos del PDF.\n"
            await msg.update()
            return

        # Construir y subir JSON de Silver
        silver_output = {
            "metadata": {
                "source_filename": filename,
                "document_name": nombre_doc,
                "extraction_date": datetime.now(timezone.utc).isoformat(),
                "total_paragraphs": len(paragraphs),
                "extraction_model": "prebuilt-layout",
            },
            "paragraphs": paragraphs,
        }
        silver_json = json.dumps(silver_output, ensure_ascii=False, indent=2)
        await asyncio.to_thread(subir_blob, "silver", json_filename, silver_json)

        msg.content += (
            f"   ✅ {len(paragraphs)} párrafos extraídos → `silver/{json_filename}`\n\n"
        )
        await msg.update()

        # ── GOLD: Chunking semántico por artículos ──
        msg.content += f"🥇 **[Gold]** Chunking semántico por artículos...\n"
        await msg.update()

        groups = agrupar_por_articulos(paragraphs)
        groups = dividir_chunks_largos(groups, MAX_CHUNK_CHARS, OVERLAP_CHARS)
        chunks = generar_chunks_con_metadatos(groups, nombre_doc)

        if not chunks:
            msg.content += "   ⚠️ No se generaron chunks del documento.\n"
            await msg.update()
            return

        # Construir y subir JSON de Gold
        gold_output = {
            "metadata": {
                **silver_output["metadata"],
                "chunking_date": datetime.now(timezone.utc).isoformat(),
                "total_chunks": len(chunks),
                "max_chunk_chars": MAX_CHUNK_CHARS,
                "overlap_chars": OVERLAP_CHARS,
            },
            "chunks": chunks,
        }
        gold_json = json.dumps(gold_output, ensure_ascii=False, indent=2)
        await asyncio.to_thread(subir_blob, "gold", gold_json_filename, gold_json)

        msg.content += (
            f"   ✅ {len(chunks)} chunks generados → `gold/{gold_json_filename}`\n\n"
        )
        await msg.update()

        # ── SUPABASE: Embeddings + upsert ──
        msg.content += (
            f"💾 **[Supabase]** Generando embeddings e indexando "
            f"({len(chunks)} chunks)...\n"
        )
        await msg.update()

        ok = 0
        errores = 0

        for i, chunk in enumerate(chunks, 1):
            try:
                embedding = await asyncio.to_thread(
                    generar_embedding, chunk["contenido_sin_header"]
                )

                row = {
                    "chunk_id": chunk["chunk_id"],
                    "documento": chunk["documento"],
                    "referencia_legal": chunk.get("referencia_legal", ""),
                    "capitulo": chunk.get("capitulo"),
                    "articulo": chunk["articulo"],
                    "titulo_articulo": chunk.get("titulo_articulo"),
                    "contenido": chunk["contenido"],
                    "contenido_sin_header": chunk["contenido_sin_header"],
                    "num_tokens_estimados": chunk.get("num_tokens_estimados", 0),
                    "jurisdiccion": chunk.get("jurisdiccion", "UE"),
                    "embedding": embedding,
                }

                await asyncio.to_thread(
                    lambda r: supabase_client.table("legal_chunks")
                    .upsert(r, on_conflict="chunk_id")
                    .execute(),
                    row,
                )
                ok += 1

                # Actualizar progreso cada 5 chunks
                if i % 5 == 0 or i == len(chunks):
                    msg.content = msg.content.rsplit("💾", 1)[0] + (
                        f"💾 **[Supabase]** Generando embeddings e indexando "
                        f"({i}/{len(chunks)} chunks procesados)...\n"
                    )
                    await msg.update()

            except Exception as e:
                errores += 1
                logger.error(f"   ❌ Error en chunk {chunk.get('chunk_id', '?')}: {e}")

        # ── RESUMEN FINAL ──
        msg.content += (
            f"\n---\n\n"
            f"✅ **Documento procesado correctamente: {nombre_doc}**\n\n"
            f"📊 **Resumen del pipeline:**\n\n"
            f"| Etapa | Resultado |\n"
            f"|---|---|\n"
            f"| 🥉 Bronze | PDF almacenado |\n"
            f"| 🥈 Silver | {len(paragraphs)} párrafos extraídos |\n"
            f"| 🥇 Gold | {len(chunks)} chunks generados |\n"
            f"| 💾 Supabase | {ok}/{len(chunks)} chunks indexados |\n\n"
        )

        if errores:
            msg.content += f"⚠️ {errores} chunk(s) con error durante la indexación.\n\n"

        msg.content += (
            "🔍 El documento ya está disponible para consultas en el "
            "perfil **Consultar**."
        )
        await msg.update()

    except Exception as e:
        logger.error(f"❌ Error en pipeline para {filename}: {e}")
        msg.content += (
            f"\n\n❌ **Error durante el procesamiento de {filename}:**\n"
            f"```\n{e}\n```\n\n"
            f"Por favor, inténtalo de nuevo o contacta con el administrador."
        )
        await msg.update()


# ══════════════════════════════════════════════
# GESTIÓN DE DOCUMENTOS — Listar y borrar
# ══════════════════════════════════════════════

def listar_documentos_indexados() -> list[dict]:
    """
    Consulta Supabase para obtener la lista de documentos indexados
    con el número de chunks de cada uno.
    Usa una consulta directa a la tabla legal_chunks y agrupa en Python.
    """
    result = supabase_client.table("legal_chunks") \
        .select("documento") \
        .execute()

    if not result.data:
        return []

    # Agrupar por documento y contar chunks en Python
    conteo: dict[str, int] = {}
    for row in result.data:
        doc = row["documento"]
        conteo[doc] = conteo.get(doc, 0) + 1

    return sorted(
        [{"documento": doc, "chunks": count} for doc, count in conteo.items()],
        key=lambda x: x["documento"],
    )


def borrar_blob_seguro(container: str, blob_name: str) -> bool:
    """
    Intenta borrar un blob de Azure Blob Storage.
    Retorna True si se borró correctamente, False si no existía o hubo error.
    """
    try:
        blob_service \
            .get_container_client(container) \
            .get_blob_client(blob_name) \
            .delete_blob()
        logger.info(f"   ✅ Borrado: {container}/{blob_name}")
        return True
    except Exception as e:
        logger.warning(f"   ⚠️ No se pudo borrar {container}/{blob_name}: {e}")
        return False


async def ejecutar_borrado_documento(
    nombre_doc: str,
    num_chunks: int,
    msg: cl.Message,
) -> None:
    """
    Borrado en cascada ejecutado en background:
    Bronze (PDF) → Silver (JSON) → Gold (JSON) → Supabase (chunks).

    Args:
        nombre_doc: Nombre del documento tal como aparece en Supabase (ej. 'Nis2').
        num_chunks: Número de chunks que se van a eliminar.
        msg:        Mensaje de Chainlit para actualizar el progreso.
    """
    # Reconstruir los nombres de archivo a partir del nombre del documento
    # nombre_doc_desde_filename hace: "mi_documento" → "Mi Documento"
    # Aquí invertimos: "Mi Documento" → "mi_documento.pdf" / "mi_documento.json"
    base_filename = nombre_doc.lower().replace(" ", "_")
    pdf_filename = f"{base_filename}.pdf"
    json_filename = f"{base_filename}.json"
    gold_json_filename = f"chunks_{json_filename}"

    resultados: dict[str, str] = {}

    try:
        # ── Bronze: borrar PDF ──
        msg.content += "🥉 Borrando PDF de `bronze/`...\n"
        await msg.update()

        ok = await asyncio.to_thread(borrar_blob_seguro, "bronze", pdf_filename)
        resultados["Bronze"] = "✅ Borrado" if ok else "⚠️ No encontrado"

        # ── Silver: borrar JSON ──
        msg.content += "🥈 Borrando JSON de `silver/`...\n"
        await msg.update()

        ok = await asyncio.to_thread(borrar_blob_seguro, "silver", json_filename)
        resultados["Silver"] = "✅ Borrado" if ok else "⚠️ No encontrado"

        # ── Gold: borrar JSON ──
        msg.content += "🥇 Borrando JSON de `gold/`...\n"
        await msg.update()

        ok = await asyncio.to_thread(borrar_blob_seguro, "gold", gold_json_filename)
        resultados["Gold"] = "✅ Borrado" if ok else "⚠️ No encontrado"

        # ── Supabase: borrar chunks ──
        msg.content += f"💾 Borrando {num_chunks} chunks de Supabase...\n"
        await msg.update()

        try:
            await asyncio.to_thread(
                lambda: supabase_client
                .table("legal_chunks")
                .delete()
                .eq("documento", nombre_doc)
                .execute()
            )
            resultados["Supabase"] = f"✅ {num_chunks} chunks eliminados"
        except Exception as e:
            logger.error(f"   ❌ Error borrando chunks de Supabase: {e}")
            resultados["Supabase"] = f"❌ Error: {e}"

        # ── Resumen final ──
        msg.content += (
            f"\n---\n\n"
            f"🗑️ **Documento '{nombre_doc}' eliminado**\n\n"
            f"| Capa | Resultado |\n"
            f"|---|---|\n"
            f"| 🥉 Bronze | {resultados.get('Bronze', '—')} |\n"
            f"| 🥈 Silver | {resultados.get('Silver', '—')} |\n"
            f"| 🥇 Gold | {resultados.get('Gold', '—')} |\n"
            f"| 💾 Supabase | {resultados.get('Supabase', '—')} |\n\n"
            f"El documento ya no está disponible para consultas."
        )
        await msg.update()

    except Exception as e:
        logger.error(f"❌ Error en borrado de '{nombre_doc}': {e}")
        msg.content += (
            f"\n\n❌ **Error durante el borrado de '{nombre_doc}':**\n"
            f"```\n{e}\n```\n\n"
            f"Por favor, inténtalo de nuevo o contacta con el administrador."
        )
        await msg.update()


async def mostrar_lista_documentos() -> None:
    """Consulta Supabase y muestra la lista de documentos indexados con botones de borrado."""
    try:
        documentos = await asyncio.to_thread(listar_documentos_indexados)
    except Exception as e:
        await cl.Message(
            content=f"❌ Error al consultar los documentos indexados: {e}"
        ).send()
        return

    if not documentos:
        await cl.Message(
            content=(
                "📂 **No hay documentos indexados** en la base de conocimiento.\n\n"
                "Sube un PDF desde el perfil **Subir documentos** para empezar."
            )
        ).send()
        return

    tabla = "| # | Documento | Chunks |\n|---|---|---|\n"
    for i, doc in enumerate(documentos, 1):
        tabla += f"| {i} | {doc['documento']} | {doc['chunks']} |\n"

    total_chunks = sum(d["chunks"] for d in documentos)

    # Un botón de borrado por documento
    acciones = [
        cl.Action(
            name="borrar_documento",
            label=f"🗑️ {doc['documento']}",
            payload={
                "nombre_doc": doc["documento"],
                "num_chunks": doc["chunks"],
            },
        )
        for doc in documentos
    ]

    await cl.Message(
        content=(
            f"📂 **Documentos indexados** ({len(documentos)} documentos, "
            f"{total_chunks:,} chunks totales):\n\n"
            f"{tabla}\n"
            f"Pulsa el botón del documento que quieras eliminar:"
        ),
        actions=acciones,
    ).send()


# ══════════════════════════════════════════════
# POOL DE PREGUNTAS PARA STARTERS
# ══════════════════════════════════════════════
QUESTION_POOL = [
    {"label": "Objeto del RGPD", "message": "¿Cuál es el objeto y la finalidad del RGPD?"},
    {"label": "Derechos fundamentales", "message": "¿Qué derechos fundamentales protege el RGPD?"},
    {"label": "Ámbito material", "message": "¿A qué tipo de tratamiento de datos se aplica el RGPD?"},
    {"label": "Exclusiones RGPD", "message": "¿Qué actividades quedan excluidas del ámbito de aplicación del RGPD?"},
    {"label": "Seguridad y RGPD", "message": "¿Se aplica el RGPD a actividades relacionadas con la seguridad nacional?"},
    {"label": "Ámbito territorial", "message": "¿A qué países o territorios se aplica el RGPD?"},
    {"label": "Empresas fuera de UE", "message": "¿Se aplica el RGPD a empresas que no están establecidas en la UE?"},
    {"label": "Datos personales", "message": "¿Qué se considera dato personal según el RGPD?"},
    {"label": "Responsable del tratamiento", "message": "¿Qué es un responsable del tratamiento según el RGPD?"},
    {"label": "Definición de tratamiento", "message": "¿Cómo define el RGPD el concepto de tratamiento de datos?"},
    {"label": "Propósito del AI Act", "message": "¿Por qué se ha creado el Reglamento de Inteligencia Artificial europeo?"},
    {"label": "Contexto del AI Act", "message": "¿Cuál es el contexto regulatorio que motiva el AI Act?"},
    {"label": "Objeto del AI Act", "message": "¿Cuál es el objetivo principal del Reglamento de Inteligencia Artificial?"},
    {"label": "Alcance del AI Act", "message": "¿Qué aspectos de la inteligencia artificial regula el AI Act?"},
    {"label": "Ámbito del AI Act", "message": "¿A quién se aplica el Reglamento de Inteligencia Artificial?"},
    {"label": "Exclusiones del AI Act", "message": "¿Qué usos de la IA quedan fuera del ámbito del AI Act?"},
    {"label": "IA militar y AI Act", "message": "¿Se aplica el AI Act a sistemas de inteligencia artificial con fines militares?"},
    {"label": "Comparativa normativa", "message": "¿Qué tienen en común el RGPD y el AI Act en cuanto a su ámbito de aplicación?"},
    {"label": "RGPD vs AI Act", "message": "¿En qué se diferencian los objetivos del RGPD y del AI Act?"},
    {"label": "Protección de derechos", "message": "¿Cómo protegen los derechos de los ciudadanos tanto el RGPD como el AI Act?"},
]


# ══════════════════════════════════════════════
# CALLBACKS DE ACCIONES — Botones de confirmación
# ══════════════════════════════════════════════

@cl.action_callback("borrar_documento")
async def on_borrar_documento(action: cl.Action) -> None:
    """Se dispara al pulsar el botón '🗑️ [Documento]' de la lista."""
    nombre_doc: str = action.payload["nombre_doc"]
    num_chunks: int = action.payload["num_chunks"]

    # Retirar los botones de la lista para evitar doble clic
    await action.remove()

    # Mostrar confirmación con los botones confirmar/cancelar
    await cl.Message(
        content=(
            f"⚠️ **¿Eliminar '{nombre_doc}'?**\n\n"
            f"Se borrarán **{num_chunks} chunks** de todas las "
            f"capas (Bronze, Silver, Gold y Supabase).\n\n"
            f"Esta acción **no se puede deshacer**."
        ),
        actions=[
            cl.Action(
                name="confirmar_borrado",
                label="🗑️ Sí, eliminar",
                payload={"nombre_doc": nombre_doc, "num_chunks": num_chunks},
            ),
            cl.Action(
                name="cancelar_borrado",
                label="↩️ Cancelar",
                payload={"nombre_doc": nombre_doc},
            ),
        ],
    ).send()


@cl.action_callback("confirmar_borrado")
async def on_confirmar_borrado(action: cl.Action) -> None:
    """Se dispara al pulsar el botón '🗑️ Sí, eliminar'."""
    # action.payload ya es un dict directamente
    nombre_doc: str = action.payload["nombre_doc"]
    num_chunks: int = action.payload["num_chunks"]

    # Eliminar los botones del mensaje de confirmación
    await action.remove()

    msg = cl.Message(
        content=(
            f"🗑️ **Eliminando '{nombre_doc}'** "
            f"({num_chunks} chunks)...\n\n"
        )
    )
    await msg.send()

    asyncio.create_task(ejecutar_borrado_documento(nombre_doc, num_chunks, msg))


@cl.action_callback("cancelar_borrado")
async def on_cancelar_borrado(action: cl.Action) -> None:
    """Se dispara al pulsar el botón '↩️ Cancelar'."""
    nombre_doc: str = action.payload["nombre_doc"]

    await action.remove()
    await cl.Message(
        content=f"↩️ Borrado de **{nombre_doc}** cancelado."
    ).send()


# ══════════════════════════════════════════════
# EVENTOS CHAINLIT
# ══════════════════════════════════════════════

@cl.set_chat_profiles
async def set_chat_profiles():
    """Define los perfiles de chat disponibles."""
    return [
        cl.ChatProfile(
            name="Consultar",
            markdown_description="Haz preguntas sobre normativa europea",
            default=True,
        ),
        cl.ChatProfile(
            name="Subir documentos",
            markdown_description="Sube un PDF para añadirlo a la base de conocimiento",
        ),
        cl.ChatProfile(
            name="Gestionar documentos",
            markdown_description="Consulta y elimina documentos de la base de conocimiento",
        ),
    ]


@cl.set_starters
async def set_starters():
    """Muestra 4 preguntas aleatorias como sugerencias iniciales."""
    seleccion = random.sample(QUESTION_POOL, min(4, len(QUESTION_POOL)))
    return [
        cl.Starter(label=q["label"], message=q["message"])
        for q in seleccion
    ]


@cl.on_chat_start
async def on_chat_start():
    """Inicializa la sesión del usuario."""
    cl.user_session.set("historial", [])
    perfil = cl.user_session.get("chat_profile", "Consultar")

    if perfil == "Subir documentos":
        await cl.Message(
            content=(
                "📁 **Modo de ingesta de documentos**\n\n"
                "Arrastra un PDF al chat o usa el icono 📎 para subirlo.\n\n"
                "El sistema ejecutará el pipeline completo en background:\n"
                "**Bronze** (almacenamiento) → **Silver** (extracción con Document Intelligence) "
                "→ **Gold** (chunking semántico) → **Supabase** (embeddings + indexación).\n\n"
            )
        ).send()

    elif perfil == "Gestionar documentos":
        await cl.Message(
            content=(
                "📂 **Gestión de documentos**\n\n"
                "| Comando | Acción |\n"
                "|---|---|\n"
                "| `listar` | Refresca la lista de documentos indexados |\n"
                "| `borrar [nombre]` | Elimina un documento (pide confirmación) |\n\n"
                "También puedes pulsar directamente el botón **🗑️** de cada documento."
            )
        ).send()
        await mostrar_lista_documentos()


@cl.on_chat_resume
async def on_chat_resume(thread):
    """Restaura la memoria del LLM al hacer clic en un chat del historial."""
    historial = []

    for step in thread["steps"]:
        if step["type"] in ["user_message", "assistant_message"]:
            role = "user" if step["type"] == "user_message" else "assistant"
            historial.append({"role": role, "content": step["output"]})

    cl.user_session.set("historial", historial)
    print(f"🔄 Chat reanudado con {len(historial)} mensajes en memoria.")


@cl.on_message
async def on_message(message: cl.Message):
    """Maneja los mensajes del usuario según el perfil activo."""
    perfil = cl.user_session.get("chat_profile", "Consultar")

    # ══════════════════════════════════════════
    # MODO SUBIR DOCUMENTOS
    # ══════════════════════════════════════════
    if perfil == "Subir documentos":
        if not message.elements:
            await cl.Message(
                content="📎 Arrastra uno o varios archivos PDF aquí o usa el icono del clip para subirlos."
            ).send()
            return

        pdf_files = [f for f in message.elements if f.mime and "pdf" in f.mime.lower()]
        otros = len(message.elements) - len(pdf_files)

        if not pdf_files:
            await cl.Message(content="⚠️ Solo se aceptan archivos PDF.").send()
            return

        if otros > 0:
            await cl.Message(
                content=f"⚠️ {otros} archivo(s) ignorado(s) (solo se aceptan PDFs)."
            ).send()

        # Procesar cada PDF en background
        for pdf_file in pdf_files:
            # Leer el contenido del archivo
            try:
                file_content = (
                    pdf_file.content
                    if isinstance(getattr(pdf_file, "content", None), bytes)
                    else open(pdf_file.path, "rb").read()
                )
            except Exception as e:
                await cl.Message(
                    content=f"❌ No se pudo leer **{pdf_file.name}**: {e}"
                ).send()
                continue

            # Crear mensaje de progreso para este archivo
            msg = cl.Message(
                content=(
                    f"📄 **Procesando: {pdf_file.name}** "
                    f"({len(file_content):,} bytes)\n\n"
                    f"⏳ Iniciando pipeline Medallion...\n\n"
                )
            )
            await msg.send()

            # Pipeline bloqueante: el usuario espera a que termine antes de continuar
            await ejecutar_pipeline_medallion(pdf_file.name, file_content, msg)

        return

    # ══════════════════════════════════════════
    # MODO GESTIONAR DOCUMENTOS
    # ══════════════════════════════════════════
    if perfil == "Gestionar documentos":
        texto = message.content.strip()

        # Comando: listar documentos
        if texto.lower() in ("listar", "lista", "documentos", "docs"):
            await mostrar_lista_documentos()
            return

        # Comando: borrar [nombre] — fallback de texto, misma lógica que el botón
        match_borrar = re.match(
            r'^borrar\s+(.+)$', texto, re.IGNORECASE
        )
        if match_borrar:
            nombre_buscado = match_borrar.group(1).strip()

            try:
                documentos = await asyncio.to_thread(listar_documentos_indexados)
            except Exception as e:
                await cl.Message(
                    content=f"❌ Error al consultar documentos: {e}"
                ).send()
                return

            # Búsqueda flexible: exacta primero, luego parcial
            doc_encontrado: Optional[dict] = None
            for doc in documentos:
                if doc["documento"].lower() == nombre_buscado.lower():
                    doc_encontrado = doc
                    break

            if not doc_encontrado:
                candidatos = [
                    d for d in documentos
                    if nombre_buscado.lower() in d["documento"].lower()
                ]
                if len(candidatos) == 1:
                    doc_encontrado = candidatos[0]
                elif len(candidatos) > 1:
                    nombres = ", ".join(f"**{c['documento']}**" for c in candidatos)
                    await cl.Message(
                        content=(
                            f"⚠️ Varios documentos coinciden con '{nombre_buscado}': "
                            f"{nombres}.\n\nEspecifica el nombre completo."
                        )
                    ).send()
                    return

            if not doc_encontrado:
                await cl.Message(
                    content=(
                        f"⚠️ No se encontró **{nombre_buscado}** en la base de conocimiento.\n\n"
                        f"Escribe `listar` para ver los documentos disponibles."
                    )
                ).send()
                return

            # Reutilizar el mismo mensaje de confirmación con botones
            await cl.Message(
                content=(
                    f"⚠️ **¿Eliminar '{doc_encontrado['documento']}'?**\n\n"
                    f"Se borrarán **{doc_encontrado['chunks']} chunks** de todas las "
                    f"capas (Bronze, Silver, Gold y Supabase).\n\n"
                    f"Esta acción **no se puede deshacer**."
                ),
                actions=[
                    cl.Action(
                        name="confirmar_borrado",
                        label="🗑️ Sí, eliminar",
                        payload={
                            "nombre_doc": doc_encontrado["documento"],
                            "num_chunks": doc_encontrado["chunks"],
                        },
                    ),
                    cl.Action(
                        name="cancelar_borrado",
                        label="↩️ Cancelar",
                        payload={"nombre_doc": doc_encontrado["documento"]},
                    ),
                ],
            ).send()
            return

        # Comando no reconocido
        await cl.Message(
            content=(
                "ℹ️ **Comandos disponibles:**\n\n"
                "| Comando | Acción |\n"
                "|---|---|\n"
                "| `listar` | Ver documentos indexados |\n"
                "| `borrar [nombre]` | Eliminar un documento |\n"
            )
        ).send()
        return

    # ══════════════════════════════════════════
    # MODO CONSULTAR (RAG)
    # ══════════════════════════════════════════
    pregunta = message.content
    historial = cl.user_session.get("historial", [])

    # Mensaje de respuesta (se irá llenando con streaming)
    msg = cl.Message(content="")
    await msg.send()

    # 1. Vectorizar la pregunta
    query_embedding = vectorizar_pregunta(pregunta)

    # 2. Buscar chunks relevantes en Supabase
    chunks = buscar_contexto(query_embedding)

    # 3. Construir contexto para el LLM
    contexto = construir_contexto(chunks)

    # 4. Preparar mensajes para el LLM
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in historial:
        messages.append({"role": h["role"], "content": h["content"]})

    user_message = (
        f"CONTEXTO (fragmentos de normativa recuperados):\n{contexto}\n\n"
        f"PREGUNTA DEL USUARIO:\n{pregunta}"
    )
    messages.append({"role": "user", "content": user_message})

    # 5. Llamada al LLM con streaming
    stream = openai_client.chat.completions.create(
        model=AZURE_CHAT_DEPLOYMENT,
        messages=messages,
        temperature=0.1,
        max_tokens=2000,
        stream=True,
    )

    respuesta_completa = ""
    for part in stream:
        if part.choices and part.choices[0].delta.content:
            token = part.choices[0].delta.content
            respuesta_completa += token
            await msg.stream_token(token)

    # 6. Añadir fuentes consultadas al final (solo si el modelo encontró información útil)
    sin_informacion = RESPUESTA_SIN_INFO in respuesta_completa
    if chunks and not sin_informacion:
        respuesta_completa += "\n\n---\n\n📎 **Fuentes consultadas:**\n\n"
        for chunk in chunks:
            nombre = f"{chunk['documento']} — {chunk['articulo']}"
            if chunk.get("titulo_articulo"):
                nombre += f" ({chunk['titulo_articulo']})"
            similitud = chunk.get("similitud", 0)

            respuesta_completa += (
                f"<details><summary style='cursor:pointer; font-weight:bold; color:#F51462;'>"
                f"📄 {nombre} · {similitud:.0%}</summary>\n"
                f"<p><strong>Capítulo:</strong> {chunk.get('capitulo', 'N/A')}\n\n"
                f"<strong>Referencia:</strong> {chunk.get('referencia_legal', 'N/A')}\n\n"
                f"{chunk['contenido_sin_header']}\n\n"
                f"</p></details>\n\n"
            )

    msg.content = respuesta_completa
    await msg.update()

    # 7. Actualizar historial de conversación
    historial.append({"role": "user", "content": pregunta})
    historial.append({"role": "assistant", "content": respuesta_completa})
    cl.user_session.set("historial", historial)