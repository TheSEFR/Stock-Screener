"""
Escucha el boton "Generar informe ahora" (o el comando /informe) en Telegram
y dispara el screener bajo demanda. No hay servidor propio: esto se ejecuta
periodicamente via GitHub Actions (ver .github/workflows/bot_listener.yml),
asi que la respuesta tarda hasta el siguiente ciclo programado, no es
instantanea.

Uso: python bot_listener.py
"""
import os

import requests
from dotenv import load_dotenv

from screener import generate_and_send_report

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
# "".join(...split()) quita cualquier espacio o salto de linea que se haya
# colado al copiar el secret (frecuente al pegar desde el movil): un token
# de Telegram nunca lleva espacios de verdad.
TOKEN = "".join(os.environ["TELEGRAM_BOT_TOKEN"].split())
API = f"https://api.telegram.org/bot{TOKEN}"


def ensure_bot_command() -> None:
    requests.post(
        f"{API}/setMyCommands",
        json={"commands": [{"command": "informe", "description": "Generar el informe del screener ahora"}]},
        timeout=15,
    )


def answer_callback(callback_id: str) -> None:
    requests.post(f"{API}/answerCallbackQuery", data={"callback_query_id": callback_id}, timeout=15)


def main() -> None:
    # Diagnostico temporal: getMe solo devuelve datos publicos del bot
    # (nombre/usuario), pero confirma sin ambiguedad si el token es valido.
    me = requests.get(f"{API}/getMe", timeout=15).json()
    print(f"getMe ok={me.get('ok')} bot={me.get('result', {}).get('username')}")

    ensure_bot_command()

    updates = requests.get(f"{API}/getUpdates", timeout=15).json().get("result", [])
    if not updates:
        print("Sin mensajes nuevos.")
        return

    triggered = False
    for update in updates:
        message = update.get("message", {})
        chat = message.get("chat", {})
        if chat:
            # Ayuda a encontrar el TELEGRAM_CHAT_ID: aparece aqui en el log
            # de esta ejecucion la primera vez que alguien le escribe al bot.
            print(f"Mensaje recibido de chat_id={chat.get('id')} (tipo={chat.get('type')}, nombre={chat.get('first_name') or chat.get('title')})")
        text = message.get("text", "")
        if text.strip().lower() == "/informe":
            triggered = True
        callback = update.get("callback_query")
        if callback and callback.get("data") == "informe":
            triggered = True
            answer_callback(callback["id"])

    # Confirma (ack) todos los updates para que Telegram no los reenvie
    # en el siguiente ciclo, se hayan procesado o no.
    last_id = max(u["update_id"] for u in updates)
    requests.get(f"{API}/getUpdates", params={"offset": last_id + 1}, timeout=15)

    if triggered:
        generate_and_send_report()


if __name__ == "__main__":
    main()
