"""
SmartReg Monitor - Colector Diario de Noticias de IA
=====================================================
Este script se ejecuta automáticamente de manera programada (cron) 
para recopilar las últimas noticias de Inteligencia Artificial,
generar sus embeddings e indexarlas en la tabla 'legal_chunks' de Supabase.

Autores: Alonso Gómez, Ethan Macías, Borja Núñez
Proyecto: SmartReg Monitor - Ricoh Edition
"""

import os
import urllib.request
import xml.etree.ElementTree as ET
import hashlib
import re
from datetime import datetime
from dotenv import load_dotenv
from openai import AzureOpenAI
from supabase import create_client

# Cargar variables de entorno si estamos en local
load_dotenv()

# ══════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════
AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-06-01")
AZURE_EMBEDDING_DEPLOYMENT = os.environ.get("AZURE_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# URL del RSS de Google News para "Inteligencia Artificial" en Español
RSS_URL = "https://news.google.com/rss/search?q=Inteligencia+Artificial&hl=es&gl=ES&ceid=ES:es"

def limpiar_html(texto: str) -> str:
    """Elimina etiquetas HTML residuales de la descripción de la noticia."""
    if not texto:
        return ""
    texto_limpio = re.sub(r'<[^>]*>', '', texto)
    return html_unescape(texto_limpio).strip()

def html_unescape(s: str) -> str:
    """Decodifica entidades HTML básicas."""
    s = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    s = s.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    return s

def obtener_noticias() -> list[dict]:
    """Descarga y parsea el RSS de Google News."""
    print("📰 Descargando noticias desde Google News RSS...")
    try:
        req = urllib.request.Request(
            RSS_URL, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req) as response:
            xml_data = response.read()
        
        root = ET.fromstring(xml_data)
        items = root.findall(".//item")
        
        noticias = []
        for item in items:
            title = item.find("title").text if item.find("title") is not None else ""
            link = item.find("link").text if item.find("link") is not None else ""
            pub_date_str = item.find("pubDate").text if item.find("pubDate") is not None else ""
            desc = item.find("description").text if item.find("description") is not None else ""
            source = item.find("source").text if item.find("source") is not None else "Google News"
            
            # Limpiar el título (Google News suele añadir " - Medio" al final)
            title_clean = title.rsplit(" - ", 1)[0] if " - " in title else title
            desc_clean = limpiar_html(desc)
            
            noticias.append({
                "titulo": title_clean,
                "link": link,
                "fecha": pub_date_str,
                "descripcion": desc_clean if desc_clean else title_clean,
                "fuente": source
            })
            
        print(f"✅ Se han recopilado {len(noticias)} noticias.")
        return noticias
    except Exception as e:
        print(f"❌ Error al descargar o parsear el RSS: {e}")
        return []

def main():
    if not all([AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, SUPABASE_URL, SUPABASE_KEY]):
        print("❌ Error: Faltan variables de entorno requeridas en la configuración.")
        return

    # Inicializar clientes
    openai_client = AzureOpenAI(
        api_key=AZURE_OPENAI_API_KEY,
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_version=AZURE_OPENAI_API_VERSION,
    )
    supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    
    # Obtener noticias de la web
    noticias = obtener_noticias()
    if not noticias:
        return
        
    # Procesar las 15 noticias más recientes para evitar saturar y sobreescribir la BD
    noticias_a_procesar = noticias[:15]
    print(f"🔄 Procesando las {len(noticias_a_procesar)} noticias más recientes...")
    
    ingestadas = 0
    for idx, noticia in enumerate(noticias_a_procesar, 1):
        # Crear un chunk_id único basado en el enlace de la noticia
        chunk_id = hashlib.md5(noticia["link"].encode()).hexdigest()[:12]
        
        # Formatear el contenido de la noticia
        header = f"Noticias IA > Actualidad > {noticia['titulo']}"
        contenido = (
            f"{header}\n\n"
            f"Título: {noticia['titulo']}\n"
            f"Medio: {noticia['fuente']}\n"
            f"Fecha de publicación: {noticia['fecha']}\n\n"
            f"Resumen de la noticia:\n{noticia['descripcion']}\n\n"
            f"Enlace de origen: {noticia['link']}"
        )
        contenido_sin_header = (
            f"📰 {noticia['titulo']}\n"
            f"📅 Fecha: {noticia['fecha']} | Fuente: {noticia['fuente']}\n\n"
            f"{noticia['descripcion']}\n\n"
            f"🔗 Leer noticia completa: {noticia['link']}"
        )
        
        try:
            # Generar embedding del resumen
            response = openai_client.embeddings.create(
                model=AZURE_EMBEDDING_DEPLOYMENT,
                input=noticia['descripcion'],
            )
            embedding = response.data[0].embedding
            
            # Preparar fila
            row = {
                "chunk_id": chunk_id,
                "documento": "Noticias IA",
                "referencia_legal": noticia["fuente"],
                "capitulo": "Actualidad",
                "articulo": noticia["titulo"],
                "titulo_articulo": noticia["fuente"],
                "contenido": contenido,
                "contenido_sin_header": contenido_sin_header,
                "num_tokens_estimados": len(contenido) // 4,
                "jurisdiccion": "Global",
                "embedding": embedding,
            }
            
            # Upsert en Supabase
            supabase_client.table("legal_chunks").upsert(row, on_conflict="chunk_id").execute()
            ingestadas += 1
            print(f"   [{idx}/{len(noticias_a_procesar)}] ✅ Guardada: {noticia['titulo'][:50]}...")
        
        except Exception as e:
            print(f"   [{idx}/{len(noticias_a_procesar)}] ❌ Error en '{noticia['titulo'][:30]}': {e}")
            
    print(f"\n🚀 PROCESO COMPLETADO: {ingestadas}/{len(noticias_a_procesar)} noticias indexadas en Supabase.")

if __name__ == "__main__":
    main()
