"""
SmartReg Monitor - Registro de usuarios
=========================================
Ejecutar con:  python registrar_usuario.py

Registra un nuevo usuario en Supabase para que pueda
acceder al chatbot de SmartReg Monitor.
"""

import os
import hashlib
import getpass
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

sb = create_client(SUPABASE_URL, SUPABASE_KEY)


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def registrar():
    print("=" * 40)
    print("SmartReg Monitor - Registro de usuario")
    print("=" * 40)

    username = input("\nNombre de usuario: ").strip()
    if not username:
        print("❌ El nombre de usuario no puede estar vacío.")
        return

    # Comprobar si ya existe
    result = sb.table("app_users").select("username").eq("username", username).execute()
    if result.data:
        print(f"❌ El usuario '{username}' ya existe.")
        return

    password = getpass.getpass("Contraseña: ")
    password_confirm = getpass.getpass("Confirmar contraseña: ")

    if password != password_confirm:
        print("❌ Las contraseñas no coinciden.")
        return

    if len(password) < 4:
        print("❌ La contraseña debe tener al menos 4 caracteres.")
        return

    # Registrar
    sb.table("app_users").insert({
        "username": username,
        "password_hash": hash_password(password),
    }).execute()

    print(f"\n✅ Usuario '{username}' registrado correctamente.")


if __name__ == "__main__":
    registrar()