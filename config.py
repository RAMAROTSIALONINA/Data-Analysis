import os
import sys
from dotenv import load_dotenv

# Charger les variables d'environnement
load_dotenv()

class Config:
    """Configuration optimisée pour Render"""
    
    # Application
    APP_NAME = os.getenv("APP_NAME", "Swis Madagascar - Analyse Intelligente")
    APP_VERSION = os.getenv("APP_VERSION", "3.0.0")
    APP_ENV = os.getenv("APP_ENV", "production")
    
    # API Gemini
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyCCnrruOeLHd5V4gKoDnhoKdXQThHqWKHs")
    GEMINI_MODEL = "gemini-2.0-flash-exp"
    
    # Base de données (SQLite inclus dans Python)
    DATABASE_NAME = os.getenv("DATABASE_NAME", "analyse_db_thread.sqlite")
    
    # Serveur Render
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", "8000"))
    
    # Limites
    MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", "10485760"))  # 10MB pour Render
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
    RETRY_DELAY = int(os.getenv("RETRY_DELAY", "2"))
    
    # Chemins adaptés pour Render
    UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/tmp/uploads")
    TEMP_DIR = os.getenv("TEMP_DIR", "/tmp")
    LOG_DIR = os.getenv("LOG_DIR", "/tmp/logs")
    
    @classmethod
    def validate(cls):
        """Validation optimisée pour Render"""
        errors = []
        
        # Vérifier la clé API
        if not cls.GEMINI_API_KEY or cls.GEMINI_API_KEY == "votre_cle_api_ici":
            errors.append("GEMINI_API_KEY n'est pas configurée")
        
        # Créer les dossiers nécessaires
        try:
            for directory in [cls.UPLOAD_DIR, cls.TEMP_DIR, cls.LOG_DIR]:
                os.makedirs(directory, exist_ok=True)
                print(f"✓ Dossier créé: {directory}")
        except Exception as e:
            errors.append(f"Erreur création dossiers: {e}")
        
        if errors:
            error_msg = " | ".join(errors)
            print(f"❌ Erreurs de configuration: {error_msg}")
            sys.exit(1)
    
    @classmethod
    def print_config(cls):
        """Affiche la configuration (sans secrets)"""
        config_info = {
            "Application": cls.APP_NAME,
            "Version": cls.APP_VERSION,
            "Environnement": cls.APP_ENV,
            "Hôte": cls.HOST,
            "Port": cls.PORT,
            "Base de données": cls.DATABASE_NAME,
            "Taille max fichier": f"{cls.MAX_FILE_SIZE / 1024 / 1024} MB",
            "Tentatives max": cls.MAX_RETRIES,
            "Délai réessai": f"{cls.RETRY_DELAY}s",
        }
        
        print("⚙️ Configuration:")
        for key, value in config_info.items():
            print(f"  {key}: {value}")