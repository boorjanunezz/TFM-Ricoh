"""
SmartReg Monitor - Data Layer para historial de conversaciones
===============================================================
Implementa la persistencia de hilos y mensajes en Supabase
para que Chainlit muestre el historial en la barra lateral.
"""

import datetime
from typing import Optional
import chainlit as cl
import chainlit.data as cl_data
from chainlit.data import BaseDataLayer, queue_until_user_message
from chainlit.types import (
    Feedback,
    Pagination,
    PaginatedResponse,
    ThreadDict,
    ThreadFilter,
)
from chainlit.element import ElementDict
from chainlit.step import StepDict
from chainlit.user import PersistedUser, User
from supabase import create_client

def get_iso_now():
    """Genera un timestamp seguro y compatible con React y Supabase"""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def es_perfil_consultar() -> bool:
    """
    Devuelve True solo si el perfil activo es 'Consultar'.
    Se usa para filtrar la persistencia: solo guardamos el historial
    de conversaciones de consulta, no de ingesta ni gestión.
    """
    try:
        return cl.context.session.chat_profile == "Consultar"
    except Exception:
        # Si no hay contexto (p.ej. al reanudar) dejamos pasar
        return True

class SupabaseDataLayer(BaseDataLayer):
    """Data layer mínimo para persistir conversaciones en Supabase."""

    def __init__(self, supabase_url: str, supabase_key: str):
        self.sb = create_client(supabase_url, supabase_key)

    async def close(self) -> None:
        pass

    async def build_debug_url(self) -> str:
        return ""

    # ── Usuarios ──

    async def get_user(self, identifier: str) -> Optional[PersistedUser]:
        # Evitamos crasheos en React inyectando un timestamp válido en lugar de ""
        return PersistedUser(id=identifier, identifier=identifier, metadata={}, createdAt=get_iso_now())

    async def create_user(self, user: User) -> Optional[PersistedUser]:
        return PersistedUser(id=user.identifier, identifier=user.identifier, metadata={}, createdAt=get_iso_now())

    # ── Feedback y Elementos (stub) ──

    async def upsert_feedback(self, feedback: Feedback) -> str: return ""
    async def delete_feedback(self, feedback_id: str) -> bool: return True
    async def get_favorite_steps(self, user_id: str) -> list: return []
    async def create_element(self, element: ElementDict) -> Optional[ElementDict]:
        """Guarda los paneles laterales (fuentes) en Supabase."""
        try:
            self.sb.table("elements").upsert({
                "id": element.get("id"),
                "thread_id": element.get("threadId"),
                "for_id": element.get("forId"),  # 👈 AÑADE ESTA LÍNEA
                "type": element.get("type", "text"),
                "name": element.get("name"),
                "display": element.get("display", "side"),
                "content": element.get("content", "")
            }, on_conflict="id").execute()
        except Exception as e:
            print(f"Error guardando elemento: {e}")
        return element
    async def get_element(self, thread_id: str, element_id: str) -> Optional[ElementDict]: return None
    async def delete_element(self, element_id: str) -> bool: return True

    # ── Steps / Mensajes ──

    async def create_step(self, step_dict: StepDict) -> Optional[StepDict]:
        """Guarda un mensaje (step) en Supabase. Solo persiste en el perfil 'Consultar'."""
        if not es_perfil_consultar():
            return step_dict

        thread_id = step_dict.get("threadId")
        step_type = step_dict.get("type")
        step_id = step_dict.get("id", "")
        
        # SOLUCIÓN CRÍTICA: En la versión 2.x, TODOS los textos están en "output"
        content = step_dict.get("output", "")

        if not thread_id or step_type not in ("user_message", "assistant_message"):
            return step_dict

        role = "user" if step_type == "user_message" else "assistant"

        # 👇 HEMOS QUITADO EL "if content:" DE AQUÍ 👇

        # 1. Crear el hilo preventivamente para evitar "Foreign Key Violation" en Supabase
        try:
            author = cl.context.session.user.identifier
        except:
            author = "default"
        
        self.sb.table("threads").upsert({
            "id": thread_id,
            "author": author,
            "updated_at": get_iso_now()
        }, on_conflict="id").execute()

        self.sb.table("thread_messages").upsert({
            "id": step_id,
            "thread_id": thread_id,
            "role": role,
            "content": content,
        }, on_conflict="id").execute()

        return step_dict

    async def update_step(self, step_dict: StepDict) -> Optional[StepDict]:
        if not es_perfil_consultar():
            return step_dict

        step_id = step_dict.get("id", "")
        output = step_dict.get("output", "")
        step_type = step_dict.get("type")

        # Quitamos "updated_at" para evitar el error PGRST204 de Supabase
        self.sb.table("thread_messages").update({
            "content": output
        }).eq("id", step_id).execute()
        
        return step_dict

    async def delete_step(self, step_id: str) -> bool:
        self.sb.table("thread_messages").delete().eq("id", step_id).execute()
        return True

    # ── Threads / Hilos ──

    async def get_thread_author(self, thread_id: str) -> str:
        result = self.sb.table("threads").select("author").eq("id", thread_id).execute()
        if result.data:
            author = result.data[0].get("author")
            if author and author != "default":
                return author
        try:
            current_user = cl.context.session.user.identifier
            self.sb.table("threads").update({"author": current_user}).eq("id", thread_id).execute()
            return current_user
        except Exception:
            return "default"

    async def update_thread(
        self, thread_id: str, name: Optional[str] = None, user_id: Optional[str] = None,
        metadata: Optional[dict] = None, tags: Optional[list[str]] = None,
    ) -> None:
        if not es_perfil_consultar():
            return
        
        data = {"updated_at": get_iso_now()}
        if name:
            data["name"] = name

        author = user_id
        if not author:
            try:
                author = cl.context.session.user.identifier
            except Exception:
                pass
        if author:
            data["author"] = author

        res = self.sb.table("threads").select("id").eq("id", thread_id).execute()
        if res.data:
            self.sb.table("threads").update(data).eq("id", thread_id).execute()
        else:
            data["id"] = thread_id
            if "name" not in data: data["name"] = "Nueva conversación"
            if "author" not in data: data["author"] = "default"
            self.sb.table("threads").insert(data).execute()

    async def get_thread(self, thread_id: str) -> Optional[ThreadDict]:
        thread_result = self.sb.table("threads").select("*").eq("id", thread_id).execute()
        if not thread_result.data:
            return None

        thread = thread_result.data[0]
        msgs_result = self.sb.table("thread_messages").select("*").eq("thread_id", thread_id).order("created_at").execute()

        steps = []
        for msg in msgs_result.data:
            step_type = "user_message" if msg["role"] == "user" else "assistant_message"
            steps.append({
                "id": msg["id"],
                "threadId": thread_id,
                "type": step_type,
                "name": msg["role"],
                "output": msg["content"], 
                "createdAt": msg["created_at"] or get_iso_now(),
            })

        elements_result = self.sb.table("elements").select("*").eq("thread_id", thread_id).execute()
        elements_list = []
        for el in elements_result.data:
            elements_list.append({
                "id": el["id"],
                "threadId": el["thread_id"],
                "forId": el.get("for_id"),
                "type": el["type"],
                "name": el["name"],
                "display": el["display"],
                "content": el["content"],
            })

        return {
            "id": thread["id"],
            "name": thread.get("name", "Conversación"),
            "createdAt": thread.get("created_at") or get_iso_now(), 
            "userIdentifier": thread.get("author", "default"),
            "steps": steps,
            "elements": elements_list,
            "metadata": {},
            "tags": [],
        }
    
    async def delete_thread(self, thread_id: str) -> bool:
        self.sb.table("thread_messages").delete().eq("thread_id", thread_id).execute()
        self.sb.table("threads").delete().eq("id", thread_id).execute()
        return True

    async def list_threads(
        self, pagination: Pagination, filters: ThreadFilter,
    ) -> PaginatedResponse[ThreadDict]:
        
        current_user = getattr(filters, "userId", None)
        if not current_user:
            current_user = getattr(filters, "userIdentifier", "default")

        result = self.sb.table("threads").select("*").eq("author", current_user).order("updated_at", desc=True).limit(20).execute()

        threads = []
        for t in result.data:
            threads.append({
                "id": t["id"],
                "name": t.get("name", "Conversación"),
                "createdAt": t.get("created_at") or get_iso_now(), 
                "userIdentifier": t.get("author", "default"), # 👈 AÑADE ESTA LÍNEA
                "steps": [],
                "elements": [],
                "metadata": {},
                "tags": [],
            })

        return PaginatedResponse(
            data=threads,
            pageInfo={"hasNextPage": False, "startCursor": None, "endCursor": None}
        )