#!/usr/bin/env python3
import os
import uvicorn
from config import Config

def main():
    # Utiliser le port de Render
    port = int(os.environ.get("PORT", 8000))
    
    Config.validate()
    Config.print_config()
    
    print(f"🚀 Démarrage sur le port: {port}")
    
    uvicorn.run(
        "app:app",
        host="0.0.0.0",  # Important pour Render
        port=port,
        log_level="info"
    )

if __name__ == "__main__":
    main()