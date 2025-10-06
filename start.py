#!/usr/bin/env python3
"""
Script de démarrage optimisé pour Render
"""

import os
import uvicorn
from config import Config

def main():
    """Point d'entrée principal optimisé pour le cloud"""
    try:
        # Configuration spécifique Render
        port = int(os.environ.get("PORT", 8000))
        host = os.environ.get("HOST", "0.0.0.0")
        
        # Valider la configuration
        Config.validate()
        
        print("=" * 50)
        print("🚀 Swis Madagascar - Analyse Intelligente")
        print("📊 Système de détection d'anomalies")
        print("🌐 Démarrage en mode production")
        print("=" * 50)
        
        Config.print_config()
        
        print(f"\n📍 Serveur accessible sur: http://{host}:{port}")
        print("⏹️  Appuyez sur Ctrl+C pour arrêter")
        print("-" * 50)
        
        # Démarrer le serveur Uvicorn
        uvicorn.run(
            "app:app",
            host=host,
            port=port,
            workers=1,  # 1 worker pour le plan gratuit
            log_level="info",
            access_log=True
        )
        
    except KeyboardInterrupt:
        print("\n🛑 Arrêt du serveur demandé...")
    except Exception as e:
        print(f"❌ Erreur critique: {e}")
        raise

if __name__ == "__main__":
    main()