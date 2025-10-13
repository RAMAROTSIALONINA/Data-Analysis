import threading
import time
import webbrowser
import uvicorn
from app import app  # Assure-toi que app.py est dans le même dossier

def start_server():
    """Lancer directement le serveur Uvicorn dans le même process"""
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="error")

def open_browser_delayed():
    time.sleep(2)
    print("🌐 Ouverture du navigateur...")
    webbrowser.open("http://127.0.0.1:8000")

def main():
    print("🚀 SWIS Madagascar - Démarrage rapide...")

    # Démarrage du serveur dans un thread
    threading.Thread(target=start_server, daemon=True).start()

    # Ouvre le navigateur automatiquement
    threading.Thread(target=open_browser_delayed, daemon=True).start()

    # Boucle principale pour garder la fenêtre ouverte
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n👋 Arrêt...")

if __name__ == "__main__":
    main()
