import os
os.environ["PYTHONASYNCIODEBUG"] = "0"
os.environ["UVICORN_LOOP"] = "asyncio"  

import sqlite3
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from google.genai import Client
from google.genai.errors import APIError
import sys
from typing import Annotated, Optional
import shutil
import tempfile
import traceback
import json
from datetime import datetime
import logging
import base64
import re
import time
import pandas as pd
import io
import uuid

# ⚡ Production-safe logging
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI()

def resource_path(relative_path):
    """Retourne le chemin correct même dans un .exe"""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

# Mount static folder correctement
static_dir = resource_path("static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# ---------------------
# Configuration du logging
# ---------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------
# Configuration et Initialisation de l'API Gemini (GenAI)
# ---------------------
API_KEY_NAO = os.getenv("GEMINI_API_KEY", "AIzaSyCCnrruOeLHd5V4gKoDnhoKdXQThHqWKHs")

try:
    client = Client(api_key=API_KEY_NAO)
    logger.info("Client Gemini initialisé avec succès")
except Exception as e:
    logger.error(f"Erreur lors de l'initialisation de l'API: {e}")
    raise RuntimeError(f"Erreur lors de l'initialisation de l'API: {e}")

# ---------------------
# Création de l'application FastAPI
# ---------------------
app = FastAPI(
    title="Swis Madagascar - Système d'Analyse Intelligente",
    description="Application de détection automatique des anomalies financières et de stock",
    version="5.0.0"
)

# ---------------------
# Configuration CORS
# ---------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration des dossiers
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- Configuration SQLite (Threads & Messages) ---
DB_NAME = "analyse_db_thread.sqlite" 

def init_db():
    """Crée la base de données et les tables nécessaires avec des index pour les performances."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # TABLE 1: THREADS (la conversation/analyse)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS THREADS (
            thread_id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            date_creation DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            date_modification DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # TABLE 2: MESSAGES (les étapes de l'analyse)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS MESSAGES (
            message_id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER NOT NULL,
            sender VARCHAR(50) NOT NULL, -- 'user' ou 'assistant'
            content TEXT NOT NULL,
            date_message DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            statut VARCHAR(20) NOT NULL DEFAULT 'Succès',
            FOREIGN KEY (thread_id) REFERENCES THREADS(thread_id) ON DELETE CASCADE
        )
    """)
    
    # TABLE 3: FICHIERS_MESSAGES 
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS FICHIERS_MESSAGES (
            fichier_message_id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            nom_fichier VARCHAR(255) NOT NULL,
            taille_ko INTEGER,
            type_mime VARCHAR(100),
            gemini_file_name VARCHAR(255) NOT NULL,
            file_content TEXT,
            FOREIGN KEY (message_id) REFERENCES MESSAGES(message_id) ON DELETE CASCADE
        )
    """)
    
    # TABLE 4: ANOMALIES_DETAILLEES (pour stocker les anomalies détectées)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ANOMALIES_DETAILLEES (
            anomalie_id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER NOT NULL,
            fichier_source VARCHAR(255) NOT NULL,
            type_anomalie VARCHAR(100) NOT NULL,
            description TEXT NOT NULL,
            localisation VARCHAR(255),
            impact_estime DECIMAL(15,2),
            criticite VARCHAR(20),
            recommandation TEXT,
            date_detection DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (thread_id) REFERENCES THREADS(thread_id) ON DELETE CASCADE
        )
    """)
    
    # TABLE 5: STATISTIQUES_FICHIERS (pour stocker les stats globales)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS STATISTIQUES_FICHIERS (
            stat_id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER NOT NULL,
            nom_fichier VARCHAR(255) NOT NULL,
            type_fichier VARCHAR(100),
            nombre_lignes INTEGER,
            nombre_colonnes INTEGER,
            chiffre_affaires DECIMAL(15,2),
            nombre_transactions INTEGER,
            montant_reductions DECIMAL(15,2),
            donnees_manquantes INTEGER,
            FOREIGN KEY (thread_id) REFERENCES THREADS(thread_id) ON DELETE CASCADE
        )
    """)
    
    # Création d'index pour améliorer les performances
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_thread_id ON MESSAGES(thread_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_date ON MESSAGES(date_message)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_threads_date_modification ON THREADS(date_modification)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_anomalies_thread_id ON ANOMALIES_DETAILLEES(thread_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_stats_thread_id ON STATISTIQUES_FICHIERS(thread_id)")
    
    conn.commit()
    conn.close()
    logger.info(f"Base de données '{DB_NAME}' initialisée.")

def update_db_schema():
    """Met à jour le schéma de la base de données si nécessaire."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    try:
        # Vérifier si la colonne file_content existe
        cursor.execute("PRAGMA table_info(FICHIERS_MESSAGES)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'file_content' not in columns:
            logger.info("Ajout de la colonne file_content...")
            cursor.execute("ALTER TABLE FICHIERS_MESSAGES ADD COLUMN file_content TEXT")
            conn.commit()
            logger.info("Colonne file_content ajoutée avec succès.")
        else:
            logger.info("La colonne file_content existe déjà.")
            
        # Vérifier si la table ANOMALIES_DETAILLEES existe
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ANOMALIES_DETAILLEES'")
        if not cursor.fetchone():
            logger.info("Création de la table ANOMALIES_DETAILLEES...")
            cursor.execute("""
                CREATE TABLE ANOMALIES_DETAILLEES (
                    anomalie_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id INTEGER NOT NULL,
                    fichier_source VARCHAR(255) NOT NULL,
                    type_anomalie VARCHAR(100) NOT NULL,
                    description TEXT NOT NULL,
                    localisation VARCHAR(255),
                    impact_estime DECIMAL(15,2),
                    criticite VARCHAR(20),
                    recommandation TEXT,
                    date_detection DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (thread_id) REFERENCES THREADS(thread_id) ON DELETE CASCADE
                )
            """)
            cursor.execute("CREATE INDEX idx_anomalies_thread_id ON ANOMALIES_DETAILLEES(thread_id)")
            conn.commit()
            logger.info("Table ANOMALIES_DETAILLEES créée avec succès.")
            
        # Vérifier si la table STATISTIQUES_FICHIERS existe
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='STATISTIQUES_FICHIERS'")
        if not cursor.fetchone():
            logger.info("Création de la table STATISTIQUES_FICHIERS...")
            cursor.execute("""
                CREATE TABLE STATISTIQUES_FICHIERS (
                    stat_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id INTEGER NOT NULL,
                    nom_fichier VARCHAR(255) NOT NULL,
                    type_fichier VARCHAR(100),
                    nombre_lignes INTEGER,
                    nombre_colonnes INTEGER,
                    chiffre_affaires DECIMAL(15,2),
                    nombre_transactions INTEGER,
                    montant_reductions DECIMAL(15,2),
                    donnees_manquantes INTEGER,
                    FOREIGN KEY (thread_id) REFERENCES THREADS(thread_id) ON DELETE CASCADE
                )
            """)
            cursor.execute("CREATE INDEX idx_stats_thread_id ON STATISTIQUES_FICHIERS(thread_id)")
            conn.commit()
            logger.info("Table STATISTIQUES_FICHIERS créée avec succès.")
            
    except Exception as e:
        logger.error(f"Erreur lors de la mise à jour du schéma: {e}")
    finally:
        conn.close()

# Initialisation de la base de données
init_db()
update_db_schema()

# --- Fonctions utilitaires améliorées ---

def get_db_connection():
    """Retourne une connexion à la base de données avec gestion d'erreurs."""
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        logger.error(f"Erreur de connexion à la base de données: {e}")
        raise

def _create_new_thread(cursor, initial_prompt):
    """Crée un nouveau thread avec un titre basé sur le prompt."""
    title = (initial_prompt[:50] + '...') if len(initial_prompt) > 50 else initial_prompt
    cursor.execute(
        "INSERT INTO THREADS (title) VALUES (?)", 
        (title,)
    )
    thread_id = cursor.lastrowid
    logger.info(f"Nouveau thread créé: {thread_id}")
    return thread_id

def _update_thread_date(cursor, thread_id):
    """Met à jour la date de modification du thread."""
    cursor.execute(
        "UPDATE THREADS SET date_modification = CURRENT_TIMESTAMP WHERE thread_id = ?", 
        (thread_id,)
    )

def _save_message(cursor, thread_id, sender, content, status='Succès'):
    """Enregistre un message (user ou assistant) dans le thread."""
    cursor.execute(
        "INSERT INTO MESSAGES (thread_id, sender, content, statut) VALUES (?, ?, ?, ?)",
        (thread_id, sender, content, status)
    )
    message_id = cursor.lastrowid
    logger.debug(f"Message sauvegardé: {message_id} pour le thread {thread_id}")
    return message_id

def _save_files_to_message(cursor, message_id, file_infos_for_db):
    """Enregistre les fichiers liés à un message."""
    for f_info in file_infos_for_db:
        cursor.execute(
            """INSERT INTO FICHIERS_MESSAGES 
               (message_id, nom_fichier, taille_ko, type_mime, gemini_file_name, file_content) 
               VALUES (?, ?, ?, ?, ?, ?)""",
            (message_id, f_info['display_name'], f_info['size_ko'], 
             f_info['mime_type'], f_info['gemini_file_name'], f_info.get('file_content', ''))
        )

def _save_detailed_anomalies(cursor, thread_id, anomalies_detailed):
    """Enregistre les anomalies détaillées dans la base de données."""
    for anomaly in anomalies_detailed:
        cursor.execute(
            """INSERT INTO ANOMALIES_DETAILLEES 
               (thread_id, fichier_source, type_anomalie, description, localisation, impact_estime, criticite, recommandation)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (thread_id, anomaly['fichier_source'], anomaly['type_anomalie'], 
             anomaly['description'], anomaly.get('localisation'), anomaly.get('impact_estime'),
             anomaly.get('criticite'), anomaly.get('recommandation'))
        )

def _save_file_statistics(cursor, thread_id, file_stats):
    """Enregistre les statistiques des fichiers analysés."""
    for stat in file_stats:
        cursor.execute(
            """INSERT INTO STATISTIQUES_FICHIERS 
               (thread_id, nom_fichier, type_fichier, nombre_lignes, nombre_colonnes, 
                chiffre_affaires, nombre_transactions, montant_reductions, donnees_manquantes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (thread_id, stat['nom_fichier'], stat['type_fichier'], stat['nombre_lignes'], 
             stat['nombre_colonnes'], stat.get('chiffre_affaires'), stat.get('nombre_transactions'),
             stat.get('montant_reductions'), stat.get('donnees_manquantes'))
        )

def _cleanup_files(gemini_uploaded_files, temp_file_paths):
    """Nettoie les fichiers temporaires et les fichiers Gemini."""
    for f in gemini_uploaded_files:
        try:
            client.files.delete(name=f.name)
            logger.debug(f"Fichier Gemini supprimé: {f.name}")
        except Exception as e:
            logger.warning(f"Erreur lors de la suppression du fichier Gemini {f.name}: {e}")
    
    for temp_file_path in temp_file_paths:
        if os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.debug(f"Fichier temporaire supprimé: {temp_file_path}")
            except Exception as e:
                logger.warning(f"Erreur lors de la suppression du fichier temporaire {temp_file_path}: {e}")

def extract_anomaly_stats(response_text):
    """Extrait les statistiques d'anomalies de la réponse."""
    try:
        total_anomalies = len(re.findall(r'🚨\s*\*\*ANOMALIE DÉTECTÉE\*\*', response_text, re.IGNORECASE))
        financial_anomalies = len(re.findall(r'ANOMALIES FINANCIÈRES', response_text, re.IGNORECASE))
        stock_anomalies = len(re.findall(r'ERREURS DE STOCK', response_text, re.IGNORECASE))
        pricing_anomalies = len(re.findall(r'ANOMALIES DE TARIFICATION', response_text, re.IGNORECASE))
        
        impact_amounts = re.findall(r'💰 Impact :.*?(\d+[\d\s,]*\.?\d*)\s*(MGA|€|euros?|ariary)', response_text, re.IGNORECASE)
        total_impact = 0
        for amount, currency in impact_amounts:
            try:
                clean_amount = amount.replace(' ', '').replace(',', '.')
                total_impact += float(clean_amount)
            except ValueError:
                continue
        
        has_critical_issues = total_anomalies > 0
        
        return {
            "total_anomalies": total_anomalies,
            "financial_anomalies": financial_anomalies,
            "stock_anomalies": stock_anomalies,
            "pricing_anomalies": pricing_anomalies,
            "total_impact": round(total_impact, 2),
            "has_critical_issues": has_critical_issues,
            "impact_currency": "MGA" if impact_amounts else "N/A"
        }
    except Exception as e:
        logger.error(f"Erreur lors de l'extraction des stats d'anomalies: {e}")
        return {
            "total_anomalies": 0,
            "financial_anomalies": 0,
            "stock_anomalies": 0,
            "pricing_anomalies": 0,
            "total_impact": 0,
            "has_critical_issues": False,
            "impact_currency": "N/A"
        }

def extract_detailed_anomalies(response_text, file_names):
    """Extrait les anomalies détaillées de la réponse pour un stockage structuré."""
    try:
        anomalies = []
        
        # Pattern pour détecter chaque anomalie
        anomaly_pattern = r'🚨\s*\*\*ANOMALIE DÉTECTÉE\*\*(.*?)(?=🚨\s*\*\*ANOMALIE DÉTECTÉE\*\*|🔄\s*\*\*ÉTAPE|📊\s*\*\*RÉSUMÉ|$)'
        matches = re.findall(anomaly_pattern, response_text, re.DOTALL | re.IGNORECASE)
        
        for match in matches:
            anomaly_text = match.strip()
            
            # Extraire les informations structurées
            fichier_match = re.search(r'📁\s*Fichier:\s*(.*?)(?=\n|$)', anomaly_text, re.IGNORECASE)
            localisation_match = re.search(r'📍\s*Localisation:\s*(.*?)(?=\n|$)', anomaly_text, re.IGNORECASE)
            description_match = re.search(r'🔎\s*Description:\s*(.*?)(?=\n|$)', anomaly_text, re.IGNORECASE)
            impact_match = re.search(r'💰\s*Impact:\s*(.*?)(?=\n|$)', anomaly_text, re.IGNORECASE)
            recommandation_match = re.search(r'✅\s*Recommandation:\s*(.*?)(?=\n|$)', anomaly_text, re.IGNORECASE)
            
            # Déterminer le type d'anomalie
            type_anomalie = "Autre"
            if re.search(r'financier|argent|coût|prix|euro|ariary|mga', anomaly_text, re.IGNORECASE):
                type_anomalie = "Financière"
            elif re.search(r'stock|inventaire|quantité|produit', anomaly_text, re.IGNORECASE):
                type_anomalie = "Stock"
            elif re.search(r'tarification|prix|coût', anomaly_text, re.IGNORECASE):
                type_anomalie = "Tarification"
            
            # Déterminer la criticité
            criticite = "Moyenne"
            if re.search(r'critique|urgent|grave|important', anomaly_text, re.IGNORECASE):
                criticite = "Élevée"
            elif re.search(r'mineur|faible|petit', anomaly_text, re.IGNORECASE):
                criticite = "Faible"
            
            # Extraire l'impact numérique
            impact_estime = 0
            if impact_match:
                impact_text = impact_match.group(1)
                montant_match = re.search(r'(\d+[\d\s,]*\.?\d*)', impact_text)
                if montant_match:
                    try:
                        montant = montant_match.group(1).replace(' ', '').replace(',', '.')
                        impact_estime = float(montant)
                    except ValueError:
                        pass
            
            anomalies.append({
                "fichier_source": fichier_match.group(1).strip() if fichier_match else file_names[0] if file_names else "Fichier inconnu",
                "type_anomalie": type_anomalie,
                "description": description_match.group(1).strip() if description_match else "Description non spécifiée",
                "localisation": localisation_match.group(1).strip() if localisation_match else "Localisation non spécifiée",
                "impact_estime": impact_estime,
                "criticite": criticite,
                "recommandation": recommandation_match.group(1).strip() if recommandation_match else "Recommandation non spécifiée"
            })
        
        return anomalies
    except Exception as e:
        logger.error(f"Erreur lors de l'extraction des anomalies détaillées: {e}")
        return []

def analyze_file_statistics(file_path, mime_type, filename):
    """Analyse les statistiques de base d'un fichier."""
    try:
        stats = {
            "nom_fichier": filename,
            "type_fichier": mime_type,
            "nombre_lignes": 0,
            "nombre_colonnes": 0,
            "chiffre_affaires": 0,
            "nombre_transactions": 0,
            "montant_reductions": 0,
            "donnees_manquantes": 0
        }
        
        if mime_type in ['application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet']:
            df = pd.read_excel(file_path)
            stats["nombre_lignes"] = len(df)
            stats["nombre_colonnes"] = len(df.columns)
            stats["donnees_manquantes"] = df.isnull().sum().sum()
            
            # Tentative d'identification des colonnes financières
            for col in df.columns:
                if any(keyword in str(col).lower() for keyword in ['montant', 'prix', 'total', 'chiffre', 'affaires', 'ca']):
                    if pd.api.types.is_numeric_dtype(df[col]):
                        stats["chiffre_affaires"] = df[col].sum()
                elif any(keyword in str(col).lower() for keyword in ['quantité', 'qte', 'nombre']):
                    if pd.api.types.is_numeric_dtype(df[col]):
                        stats["nombre_transactions"] = df[col].sum()
                elif any(keyword in str(col).lower() for keyword in ['réduction', 'remise', 'discount']):
                    if pd.api.types.is_numeric_dtype(df[col]):
                        stats["montant_reductions"] = df[col].sum()
                        
        elif mime_type == 'text/csv':
            df = pd.read_csv(file_path, encoding='utf-8', errors='ignore')
            stats["nombre_lignes"] = len(df)
            stats["nombre_colonnes"] = len(df.columns)
            stats["donnees_manquantes"] = df.isnull().sum().sum()
            
            # Tentative d'identification des colonnes financières
            for col in df.columns:
                if any(keyword in str(col).lower() for keyword in ['montant', 'prix', 'total', 'chiffre', 'affaires', 'ca']):
                    try:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                        stats["chiffre_affaires"] = df[col].sum()
                    except:
                        pass
                elif any(keyword in str(col).lower() for keyword in ['quantité', 'qte', 'nombre']):
                    try:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                        stats["nombre_transactions"] = df[col].sum()
                    except:
                        pass
                elif any(keyword in str(col).lower() for keyword in ['réduction', 'remise', 'discount']):
                    try:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                        stats["montant_reductions"] = df[col].sum()
                    except:
                        pass
        
        return stats
        
    except Exception as e:
        logger.error(f"Erreur lors de l'analyse des statistiques du fichier {filename}: {e}")
        return {
            "nom_fichier": filename,
            "type_fichier": mime_type,
            "nombre_lignes": 0,
            "nombre_colonnes": 0,
            "chiffre_affaires": 0,
            "nombre_transactions": 0,
            "montant_reductions": 0,
            "donnees_manquantes": 0
        }

async def call_gemini_api_with_retry(contents, max_retries=3):
    """Effectue l'appel API avec système de retry et gestion d'erreurs."""
    
    models_to_try = [
        'gemini-2.0-flash-exp',
        'gemini-1.5-flash', 
        'gemini-1.5-pro'
    ]
    
    for attempt in range(max_retries):
        for model in models_to_try:
            try:
                logger.info(f"Tentative {attempt + 1} avec modèle: {model}")
                
                api_response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config={
                        'temperature': 0.1,
                        'top_p': 0.8,
                        'top_k': 40,
                        'max_output_tokens': 4000
                    }
                )
                
                logger.info(f"✅ Succès avec le modèle: {model}")
                return api_response.text, "Succès"
                
            except APIError as e:
                error_msg = str(e).lower()
                if 'overload' in error_msg or 'unavailable' in error_msg or '503' in str(e):
                    logger.warning(f"⚠️ Modèle {model} surchargé, tentative suivante...")
                    continue
                else:
                    logger.error(f"❌ Erreur API avec {model}: {e}")
                    return f"Erreur technique: {str(e)}", "Erreur Technique"
                    
            except Exception as e:
                logger.error(f"❌ Erreur inattendue avec {model}: {e}")
                continue
        
        if attempt < max_retries - 1:
            wait_time = (attempt + 1) * 3
            logger.info(f"⏳ Attente de {wait_time}s avant nouvelle tentative...")
            time.sleep(wait_time)
    
    error_msg = """🔧 **Service Temporairement Indisponible**

Nous rencontrons actuellement une forte demande sur notre service d'analyse.

💡 **Que faire ?**
• Réessayez dans 2-3 minutes
• Vérifiez votre connexion internet
• Réduisez le nombre de fichiers si possible

📞 **Assistance**
Si le problème persiste, contactez notre support technique.

Nous vous remercions de votre patience."""
    
    return error_msg, "Service Indisponible"

def read_file_content(file_path, mime_type):
    """Lit le contenu d'un fichier selon son type avec un meilleur formatage."""
    try:
        if mime_type in ['application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet']:
            try:
                # Lire le fichier Excel
                df = pd.read_excel(file_path, nrows=100)
                
                # Obtenir les informations de base
                file_info = f"📊 FICHIER EXCEL: {os.path.basename(file_path)}\n"
                file_info += f"📏 Dimensions: {df.shape[0]} lignes × {df.shape[1]} colonnes\n"
                file_info += f"📋 Colonnes: {', '.join(df.columns.astype(str))}\n\n"
                
                # Informations sur les types de données
                file_info += "🔍 TYPES DE DONNÉES PAR COLONNE:\n"
                for col in df.columns:
                    dtype = str(df[col].dtype)
                    non_null = df[col].count()
                    total = len(df[col])
                    file_info += f"   • {col}: {dtype} ({non_null}/{total} non-null)\n"
                
                file_info += f"\n📋 APERÇU DES DONNÉES (premières 20 lignes):\n"
                file_info += "─" * 80 + "\n"
                
                # Formater l'aperçu des données de manière plus lisible
                preview_df = df.head(20)
                
                # Créer une représentation textuelle formatée
                with pd.option_context('display.max_rows', 20, 'display.max_columns', 10, 'display.width', 1000):
                    file_info += preview_df.to_string(index=False)
                
                file_info += f"\n\n📈 STATISTIQUES NUMÉRIQUES:\n"
                numeric_cols = df.select_dtypes(include=['number']).columns
                if len(numeric_cols) > 0:
                    file_info += df[numeric_cols].describe().to_string()
                else:
                    file_info += "   Aucune colonne numérique trouvée"
                
                return file_info
                
            except Exception as e:
                return f"📊 FICHIER EXCEL: {os.path.basename(file_path)}\n❌ Erreur de lecture: {str(e)}"
                
        elif mime_type == 'text/csv':
            try:
                # Lire le fichier CSV
                df = pd.read_csv(file_path, nrows=100, encoding='utf-8', errors='ignore')
                
                # Obtenir les informations de base
                file_info = f"📄 FICHIER CSV: {os.path.basename(file_path)}\n"
                file_info += f"📏 Dimensions: {df.shape[0]} lignes × {df.shape[1]} colonnes\n"
                file_info += f"📋 Colonnes: {', '.join(df.columns.astype(str))}\n\n"
                
                # Informations sur les types de données
                file_info += "🔍 TYPES DE DONNÉES PAR COLONNE:\n"
                for col in df.columns:
                    dtype = str(df[col].dtype)
                    non_null = df[col].count()
                    total = len(df[col])
                    file_info += f"   • {col}: {dtype} ({non_null}/{total} non-null)\n"
                
                file_info += f"\n📋 APERÇU DES DONNÉES (premières 20 lignes):\n"
                file_info += "─" * 80 + "\n"
                
                # Formater l'aperçu des données
                preview_df = df.head(20)
                
                with pd.option_context('display.max_rows', 20, 'display.max_columns', 10, 'display.width', 1000):
                    file_info += preview_df.to_string(index=False)
                
                file_info += f"\n\n📈 STATISTIQUES NUMÉRIQUES:\n"
                numeric_cols = df.select_dtypes(include=['number']).columns
                if len(numeric_cols) > 0:
                    file_info += df[numeric_cols].describe().to_string()
                else:
                    file_info += "   Aucune colonne numérique trouvée"
                
                return file_info
                
            except Exception as e:
                return f"📄 FICHIER CSV: {os.path.basename(file_path)}\n❌ Erreur de lecture: {str(e)}"
                
        elif mime_type == 'application/pdf':
            return f"📑 FICHIER PDF: {os.path.basename(file_path)}\n📏 Taille: {os.path.getsize(file_path)/1024:.2f} KB\n💡 Contenu: Document PDF (analyse textuelle limitée)"
        
        elif 'text' in mime_type:
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    
                file_info = f"📝 FICHIER TEXTE: {os.path.basename(file_path)}\n"
                file_info += f"📏 Taille: {len(content)} caractères\n"
                file_info += f"📊 Lignes: {len(content.splitlines())}\n\n"
                
                # Aperçu du contenu
                preview_lines = content.splitlines()[:30]
                file_info += "📋 APERÇU DU CONTENU:\n"
                file_info += "─" * 80 + "\n"
                file_info += "\n".join(preview_lines)
                
                if len(content.splitlines()) > 30:
                    file_info += f"\n[...] {len(content.splitlines()) - 30} lignes supplémentaires"
                
                return file_info
                
            except Exception as e:
                return f"📝 FICHIER TEXTE: {os.path.basename(file_path)}\n❌ Erreur de lecture: {str(e)}"
                
        else:
            return f"📁 FICHIER: {os.path.basename(file_path)}\n🔤 Type: {mime_type}\n📏 Taille: {os.path.getsize(file_path)/1024:.2f} KB\n💡 Type non supporté pour l'aperçu détaillé"
            
    except Exception as e:
        logger.error(f"Erreur lecture fichier {file_path}: {e}")
        return f"❌ Erreur lors de la lecture du fichier: {str(e)}"

# --- Endpoints principaux ---

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """Sert le fichier HTML principal."""
    try:
        with open("static/index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.error("Fichier static/index.html introuvable")
        return HTMLResponse(
            "<h1>Erreur: Fichier 'static/index.html' introuvable.</h1>", 
            status_code=404
        )

@app.get("/api/health")
async def health_check():
    """Endpoint de santé de l'application."""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "5.0.0"
    }

@app.post("/api/process_query")
async def process_multimodal_query(
    prompt: Annotated[str, Form(description="La question à poser")] = "",
    thread_id: Annotated[Optional[int], Form(description="ID du thread pour la conversation continue")] = None,
    files: Annotated[list[UploadFile] | None, File(description="Liste de fichiers optionnels")] = None,
):
    """Traite la requête multimodale avec gestion robuste des erreurs et analyse structurée."""
    
    if not files or len(files) == 0:
        raise HTTPException(
            status_code=400,
            detail="Veuillez sélectionner au moins un fichier à analyser."
        )

    # Variables de traitement
    gemini_uploaded_files = []
    files_info_for_db = []
    temp_file_paths = []
    response_text = ""
    final_status = "Succès"
    anomaly_stats = {}
    anomalies_detailed = []
    file_statistics = []
    
    conn = None
    user_message_id = None
    
    try:
        contents = []
        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. Gestion du Thread
        file_names = ", ".join([file.filename for file in files if file.filename])
        prompt_for_title = f"Analyse: {file_names}" if file_names else "Analyse automatique"
        
        if thread_id is None or thread_id == 0:
            thread_id = _create_new_thread(cursor, prompt_for_title)
        else:
            _update_thread_date(cursor, thread_id)
            
        # 2. Enregistrement du message utilisateur
        user_message_id = _save_message(cursor, thread_id, 'user', "Analyse automatique des fichiers déposés", 'En cours')
        
        # 3. Traitement des fichiers
        total_file_size = 0
        max_file_size = 20 * 1024 * 1024
        
        file_details = []
        file_names_list = []
        
        for file in files:
            if not file.filename:
                continue
                
            file_size = file.size or 0
            total_file_size += file_size
            
            if total_file_size > max_file_size:
                raise HTTPException(
                    status_code=400,
                    detail=f"Taille totale des fichiers trop importante ({total_file_size/1024/1024:.1f}MB). Maximum: 20MB"
                )

            # Upload vers Gemini
            temp_file_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as temp:
                    content = await file.read()
                    temp.write(content)
                    temp_file_path = temp.name
                    temp_file_paths.append(temp_file_path)
                
                await file.seek(0)
                    
                uploaded_file_gemini = client.files.upload(file=temp_file_path)
                
                if uploaded_file_gemini:
                    gemini_uploaded_files.append(uploaded_file_gemini)
                    contents.append(uploaded_file_gemini)
                    
                    # Sauvegarder le contenu du fichier pour l'aperçu
                    file_content = read_file_content(temp_file_path, uploaded_file_gemini.mime_type)
                    
                    files_info_for_db.append({
                        'display_name': file.filename,
                        'size_ko': file_size / 1024,
                        'mime_type': uploaded_file_gemini.mime_type,
                        'gemini_file_name': uploaded_file_gemini.name,
                        'file_content': file_content
                    })
                    
                    file_details.append(f"📄 {file.filename} ({(file_size/1024/1024):.2f} MB)")
                    file_names_list.append(file.filename)
                    
                    # Analyser les statistiques du fichier
                    file_stats = analyze_file_statistics(temp_file_path, uploaded_file_gemini.mime_type, file.filename)
                    file_statistics.append(file_stats)
                    
            except Exception as e:
                logger.error(f"Erreur upload fichier {file.filename}: {e}")
                final_status = "Erreur Fichier"
                continue
        
        # Sauvegarde des infos fichiers
        if files_info_for_db:
            _save_files_to_message(cursor, user_message_id, files_info_for_db)
            
        # Sauvegarde des statistiques
        if file_statistics:
            _save_file_statistics(cursor, thread_id, file_statistics)
    
        # 4. Préparation du contenu avec la nouvelle structure d'analyse simplifiée
        file_list_text = "\n".join(file_details)
        
        # Préparer le résumé des statistiques
        stats_summary = "📊 RÉSUMÉ GLOBAL DES FICHIERS ANALYSÉS:\n\n"
        total_ca = 0
        total_transactions = 0
        total_reductions = 0
        
        for stat in file_statistics:
            stats_summary += f"📁 {stat['nom_fichier']}:\n"
            stats_summary += f"   • Lignes: {stat['nombre_lignes']:,}\n"
            stats_summary += f"   • Colonnes: {stat['nombre_colonnes']}\n"
            if stat['chiffre_affaires'] > 0:
                stats_summary += f"   • Chiffre d'affaires: {stat['chiffre_affaires']:,.2f} MGA\n"
                total_ca += stat['chiffre_affaires']
            if stat['nombre_transactions'] > 0:
                stats_summary += f"   • Transactions: {stat['nombre_transactions']:,}\n"
                total_transactions += stat['nombre_transactions']
            if stat['montant_reductions'] > 0:
                stats_summary += f"   • Réductions: {stat['montant_reductions']:,.2f} MGA\n"
                total_reductions += stat['montant_reductions']
            if stat['donnees_manquantes'] > 0:
                stats_summary += f"   • Données manquantes: {stat['donnees_manquantes']}\n"
            stats_summary += "\n"
        
        stats_summary += f"📈 TOTAUX GLOBAUX:\n"
        stats_summary += f"   • Chiffre d'affaires total: {total_ca:,.2f} MGA\n"
        stats_summary += f"   • Nombre total de transactions: {total_transactions:,}\n"
        stats_summary += f"   • Montant total des réductions: {total_reductions:,.2f} MGA\n"
        
        analysis_prompt = f"""
ANALYSE SWIS MADAGASCAR - RAPPORT COMPLET

{stats_summary}

🔍 STRUCTURE LOGIQUE DE L'ANALYSE SIMPLIFIÉE :

🎯 ÉTAPE 1 - COLLECTE DES FICHIERS
• Recherche, chargement et préparation de tous les fichiers nécessaires
• Vérification de l'intégrité et de la complétude des données

🎯 ÉTAPE 2 - VÉRIFICATION INTERNE  
• Analyse individuelle de chaque fichier
• Détection des erreurs internes : valeurs manquantes, doublons, incohérences locales
• Identification des anomalies structurelles

🎯 ÉTAPE 3 - VÉRIFICATION CROISÉE
• Comparaison des fichiers entre eux
• Repérage des différences et contradictions dans les données communes
• Identification des écarts inter-fichiers

🎯 ÉTAPE 4 - INTERPRÉTATION
• Évaluation de la gravité des anomalies détectées
• Analyse des causes racines possibles
• Estimation de l'impact sur la fiabilité des données

🎯 ÉTAPE 5 - RECOMMANDATIONS
• Formulation de propositions concrètes pour corriger les anomalies
• Suggestions d'amélioration pour la cohérence future
• Hiérarchisation des actions par criticité

🎯 ÉTAPE 6 - RAPPORT FINAL
• Génération d'un résumé clair et structuré
• Présentation des résultats et du taux de conformité global
• Identification des axes d'amélioration prioritaires

📊 FORMAT DE RAPPORT OBLIGATOIRE :

🚨 **ANOMALIE DÉTECTÉE**
📁 Fichier: [Nom du fichier concerné]
📍 Localisation: [Ligne/Colonne/Zone précise]
🔎 Description: [Description détaillée du problème]
💰 Impact: [Montant estimé en MGA ou quantité]
🎯 Cause: [Analyse de la cause racine]
✅ Recommandation: [Solution corrective concrète]

CRITÈRES DE CRITICITÉ:
• 🔴 CRITIQUE: Impact financier > 1,000,000 MGA ou risque opérationnel grave
• 🟡 MOYEN: Impact entre 100,000 et 1,000,000 MGA
• 🟢 FAIBLE: Impact < 100,000 MGA ou anomalie mineure

PRÉSENTEZ LES RÉSULTATS PAR ORDRE DE CRITICITÉ DÉCROISSANTE.
"""
        
        contents.append(analysis_prompt)
        
        if not contents:
            raise HTTPException(status_code=400, detail="Aucun contenu valide pour l'analyse.")

        # 5. Appel API avec gestion robuste
        response_text, api_status = await call_gemini_api_with_retry(contents)
        
        if api_status != "Succès":
            final_status = api_status
        else:
            # Extraction des statistiques d'anomalies
            anomaly_stats = extract_anomaly_stats(response_text)
            
            # Extraction des anomalies détaillées pour le stockage structuré
            anomalies_detailed = extract_detailed_anomalies(response_text, file_names_list)
            
            # Sauvegarde des anomalies détaillées dans la base de données
            if anomalies_detailed:
                _save_detailed_anomalies(cursor, thread_id, anomalies_detailed)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur interne: {traceback.format_exc()}")
        final_status = "Erreur Interne"
        response_text = f"Une erreur inattendue s'est produite. Veuillez réessayer."
        
    finally:
        # 6. Sauvegarde finale
        try:
            if conn:
                # Sauvegarde réponse
                _save_message(cursor, thread_id, 'assistant', response_text, final_status)
                
                # Mise à jour statut message utilisateur
                if user_message_id:
                    cursor.execute(
                        "UPDATE MESSAGES SET statut = ? WHERE message_id = ?",
                        (final_status, user_message_id)
                    )
                
                conn.commit()
                logger.info(f"Traitement terminé - Thread: {thread_id}, Statut: {final_status}")

        except Exception as db_e:
            logger.error(f"Erreur BD finale: {db_e}")
            
        finally:
            # Nettoyage
            _cleanup_files(gemini_uploaded_files, temp_file_paths)
            if conn:
                conn.close()

    if final_status != "Succès":
        raise HTTPException(
            status_code=500, 
            detail=response_text if "Erreur technique" not in response_text else "Problème de connexion au service d'analyse"
        )

    return {
        "thread_id": thread_id, 
        "response": response_text,
        "status": final_status,
        "anomaly_stats": anomaly_stats,
        "anomalies_detailed": anomalies_detailed,
        "file_statistics": file_statistics
    }

# --- Nouveaux endpoints pour les données enrichies ---

@app.get("/api/thread/{thread_id}/anomalies")
async def get_thread_anomalies(thread_id: int):
    """Récupère les anomalies détaillées pour un thread spécifique."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                anomalie_id,
                fichier_source,
                type_anomalie,
                description,
                localisation,
                impact_estime,
                criticite,
                recommandation,
                date_detection
            FROM ANOMALIES_DETAILLEES
            WHERE thread_id = ?
            ORDER BY 
                CASE criticite
                    WHEN 'Élevée' THEN 1
                    WHEN 'Moyenne' THEN 2
                    WHEN 'Faible' THEN 3
                    ELSE 4
                END,
                impact_estime DESC
        """, (thread_id,))
        
        anomalies = []
        for row in cursor.fetchall():
            anomalies.append({
                "id": row["anomalie_id"],
                "fichier_source": row["fichier_source"],
                "type_anomalie": row["type_anomalie"],
                "description": row["description"],
                "localisation": row["localisation"],
                "impact_estime": float(row["impact_estime"]) if row["impact_estime"] else 0,
                "criticite": row["criticite"],
                "recommandation": row["recommandation"],
                "date_detection": row["date_detection"]
            })
        
        # Calcul des statistiques
        total_anomalies = len(anomalies)
        anomalies_critiques = len([a for a in anomalies if a["criticite"] == "Élevée"])
        anomalies_moyennes = len([a for a in anomalies if a["criticite"] == "Moyenne"])
        anomalies_faibles = len([a for a in anomalies if a["criticite"] == "Faible"])
        impact_total = sum(a["impact_estime"] for a in anomalies)
        
        return {
            "thread_id": thread_id,
            "anomalies": anomalies,
            "statistiques": {
                "total_anomalies": total_anomalies,
                "anomalies_critiques": anomalies_critiques,
                "anomalies_moyennes": anomalies_moyennes,
                "anomalies_faibles": anomalies_faibles,
                "impact_total": round(impact_total, 2),
                "impact_moyen": round(impact_total / total_anomalies, 2) if total_anomalies > 0 else 0
            }
        }
        
    except Exception as e:
        logger.error(f"Erreur récupération anomalies thread {thread_id}: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la récupération des anomalies.")
    finally:
        if conn:
            conn.close()

@app.get("/api/thread/{thread_id}/statistics")
async def get_thread_statistics(thread_id: int):
    """Récupère les statistiques des fichiers pour un thread spécifique."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                nom_fichier,
                type_fichier,
                nombre_lignes,
                nombre_colonnes,
                chiffre_affaires,
                nombre_transactions,
                montant_reductions,
                donnees_manquantes
            FROM STATISTIQUES_FICHIERS
            WHERE thread_id = ?
            ORDER BY nom_fichier
        """, (thread_id,))
        
        statistics = []
        for row in cursor.fetchall():
            statistics.append({
                "nom_fichier": row["nom_fichier"],
                "type_fichier": row["type_fichier"],
                "nombre_lignes": row["nombre_lignes"],
                "nombre_colonnes": row["nombre_colonnes"],
                "chiffre_affaires": float(row["chiffre_affaires"]) if row["chiffre_affaires"] else 0,
                "nombre_transactions": row["nombre_transactions"] or 0,
                "montant_reductions": float(row["montant_reductions"]) if row["montant_reductions"] else 0,
                "donnees_manquantes": row["donnees_manquantes"] or 0
            })
        
        # Calcul des totaux
        total_files = len(statistics)
        total_lignes = sum(s["nombre_lignes"] for s in statistics)
        total_ca = sum(s["chiffre_affaires"] for s in statistics)
        total_transactions = sum(s["nombre_transactions"] for s in statistics)
        total_reductions = sum(s["montant_reductions"] for s in statistics)
        total_manquantes = sum(s["donnees_manquantes"] for s in statistics)
        
        return {
            "thread_id": thread_id,
            "statistics": statistics,
            "totals": {
                "total_files": total_files,
                "total_lignes": total_lignes,
                "total_ca": round(total_ca, 2),
                "total_transactions": total_transactions,
                "total_reductions": round(total_reductions, 2),
                "total_manquantes": total_manquantes
            }
        }
        
    except Exception as e:
        logger.error(f"Erreur récupération statistiques thread {thread_id}: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la récupération des statistiques.")
    finally:
        if conn:
            conn.close()

# --- Endpoints historiques ---

@app.get("/api/history")
async def get_history(limit: int = 50, offset: int = 0):
    """Récupère la liste des threads avec pagination."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                thread_id, 
                title, 
                date_modification,
                (SELECT COUNT(*) FROM MESSAGES WHERE thread_id = THREADS.thread_id) as message_count,
                (SELECT statut FROM MESSAGES WHERE thread_id = THREADS.thread_id ORDER BY date_message DESC LIMIT 1) as last_status,
                (SELECT COUNT(*) FROM ANOMALIES_DETAILLEES WHERE thread_id = THREADS.thread_id) as anomaly_count
            FROM THREADS
            ORDER BY date_modification DESC
            LIMIT ? OFFSET ?
        """, (limit, offset))
        
        history_list = []
        for row in cursor.fetchall():
            history_list.append({
                "id": row["thread_id"],
                "title": row["title"],
                "date": row["date_modification"],
                "message_count": row["message_count"],
                "last_status": row["last_status"] or "Succès",
                "anomaly_count": row["anomaly_count"] or 0
            })
            
        cursor.execute("SELECT COUNT(*) as total FROM THREADS")
        total = cursor.fetchone()["total"]
            
        return {
            "history": history_list,
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset
            }
        }
    
    except Exception as e:
        logger.error(f"Erreur récupération historique: {e}")
        raise HTTPException(status_code=500, detail="Impossible de charger l'historique.")
    finally:
        if conn:
            conn.close()

@app.get("/api/thread/{thread_id}")
async def get_thread_detail(thread_id: int):
    """Récupère le détail complet d'un thread."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT title FROM THREADS WHERE thread_id = ?", (thread_id,))
        thread_title = cursor.fetchone()
        
        if not thread_title:
            raise HTTPException(status_code=404, detail="Analyse non trouvée.")
            
        cursor.execute("""
            SELECT 
                m.message_id, m.sender, m.content, m.date_message, m.statut,
                GROUP_CONCAT(fm.nom_fichier) as fichiers
            FROM MESSAGES m
            LEFT JOIN FICHIERS_MESSAGES fm ON m.message_id = fm.message_id
            WHERE m.thread_id = ?
            GROUP BY m.message_id
            ORDER BY m.date_message ASC
        """, (thread_id,))
        
        messages = []
        for row in cursor.fetchall():
            files = row["fichiers"].split(",") if row["fichiers"] else []
            files = [f.strip() for f in files if f.strip()]
            
            messages.append({
                "id": row["message_id"],
                "sender": row["sender"],
                "content": row["content"],
                "date": row["date_message"],
                "status": row["statut"],
                "files": files
            })
        
        return {
            "id": thread_id,
            "title": thread_title["title"],
            "messages": messages
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur détail thread {thread_id}: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la récupération des détails.")
    finally:
        if conn:
            conn.close()

@app.get("/api/thread/{thread_id}/files")
async def get_thread_files(thread_id: int):
    """Récupère les fichiers et leur contenu pour un thread."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                fm.nom_fichier,
                fm.file_content,
                fm.type_mime,
                fm.taille_ko
            FROM FICHIERS_MESSAGES fm
            JOIN MESSAGES m ON fm.message_id = m.message_id
            WHERE m.thread_id = ?
            ORDER BY fm.fichier_message_id
        """, (thread_id,))
        
        files_data = []
        for row in cursor.fetchall():
            files_data.append({
                "filename": row["nom_fichier"],
                "content": row["file_content"],
                "mime_type": row["type_mime"],
                "size_kb": row["taille_ko"]
            })
        
        return {
            "thread_id": thread_id,
            "files": files_data
        }
        
    except Exception as e:
        logger.error(f"Erreur récupération fichiers thread {thread_id}: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la récupération des fichiers.")
    finally:
        if conn:
            conn.close()

@app.delete("/api/thread/{thread_id}")
async def delete_thread(thread_id: int):
    """Supprime un thread et tous ses messages associés."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM THREADS WHERE thread_id = ?", (thread_id,))
        
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Analyse non trouvée.")
        
        conn.commit()
        logger.info(f"Thread {thread_id} supprimé")
        return {"message": f"Analyse #{thread_id} supprimée avec succès."}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur suppression thread {thread_id}: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la suppression.")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)