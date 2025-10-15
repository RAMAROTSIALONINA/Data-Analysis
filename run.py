import threading
import time
import webbrowser
import uvicorn
import sys
import os
import logging

from app import app

# Production-safe logging
logging.basicConfig(
    level=logging.WARNING,  # Seule warnings et errors
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("swis_app.log", encoding="utf-8")]
)

def start_server():
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="warning",  # INFO logs non affichés
        access_log=False
    )

def open_browser_delayed():
    time.sleep(2)
    try:
        webbrowser.open("http://127.0.0.1:8000")
    except:
        pass

def main():
    print("L’analyse de données fonctionne maintenant !")
    print("✅ Analyse terminée ! Cliquez sur X ou appuyez sur Ctrl+C pour quitter.")


    # Server thread
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    # Browser thread
    browser_thread = threading.Thread(target=open_browser_delayed, daemon=True)
    browser_thread.start()

    # Boucle principale pour garder la fenêtre ouverte
    try:
        while True:
            time.sleep(1)
            if not server_thread.is_alive():
                print("❌ Le serveur s'est arrêté")
                break
    except KeyboardInterrupt:
        pass
    finally:
        print("📴 Fermeture de l'application")

if __name__ == "__main__":
    main()
