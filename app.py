import os
import sqlite3 
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from google.genai.errors import APIError
from typing import Annotated, Optional
import shutil  
import tempfile  
import traceback 
import json
from datetime import datetime
import logging

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuration et Initialisation de l'API Gemini ---

# Utilisation d'une variable d'environnement pour plus de sécurité
API_KEY_NAO = os.getenv("GEMINI_API_KEY", "AIzaSyCCnrruOeLHd5V4gKoDnhoKdXQThHqWKHs")

try:
    client = genai.Client(api_key=API_KEY_NAO) 
    logger.info("Client Gemini initialisé avec succès")
except Exception as e:
    logger.error(f"Erreur lors de l'initialisation de l'API: {e}")
    raise RuntimeError(f"Erreur lors de l'initialisation de l'API: {e}")

app = FastAPI(
    title="Swis Madagascar - Système d'Analyse Intelligente",
    description="Application de détection automatique des anomalies financières et de stock",
    version="3.0.0"
)

# Configuration CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration des dossiers statiques
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
            FOREIGN KEY (message_id) REFERENCES MESSAGES(message_id) ON DELETE CASCADE
        )
    """)
    
    # Création d'index pour améliorer les performances
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_thread_id ON MESSAGES(thread_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_date ON MESSAGES(date_message)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_threads_date_modification ON THREADS(date_modification)")
    
    conn.commit()
    conn.close()
    logger.info(f"Base de données '{DB_NAME}' initialisée.")

# Initialisation de la base de données
init_db()

# --- Fonctions utilitaires améliorées ---

def get_db_connection():
    """Retourne une connexion à la base de données avec gestion d'erreurs."""
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row  # Pour accéder aux colonnes par nom
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
               (message_id, nom_fichier, taille_ko, type_mime, gemini_file_name) 
               VALUES (?, ?, ?, ?, ?)""",
            (message_id, f_info['display_name'], f_info['size_ko'], f_info['mime_type'], f_info['gemini_file_name'])
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

# --- Endpoints ---

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
        "version": "3.0.0"
    }

@app.post("/api/process_query")
async def process_multimodal_query(
    prompt: Annotated[str, Form(description="La question à poser")] = "",
    thread_id: Annotated[Optional[int], Form(description="ID du thread pour la conversation continue")] = None,
    files: Annotated[list[UploadFile] | None, File(description="Liste de fichiers optionnels")] = None,
):
    """Traite la requête multimodale (texte et/ou fichiers) et enregistre les résultats."""
    
    # Validation des entrées
    if not files or len(files) == 0:
        raise HTTPException(
            status_code=400,
            detail="Veuillez fournir des fichiers à analyser."
        )

    # Variables de traitement
    gemini_uploaded_files = []
    files_info_for_db = []
    temp_file_paths = []
    response_text = ""
    model_to_use = 'gemini-2.0-flash-exp'
    final_status = "Succès"
    
    conn = None
    user_message_id = None
    
    try:
        contents = []
        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. Gestion du Thread - titre basé sur les fichiers
        file_names = ", ".join([file.filename for file in files if file.filename])
        prompt_for_title = f"Analyse: {file_names}" if file_names else "Analyse automatique de fichiers"
        
        if thread_id is None or thread_id == 0:
            thread_id = _create_new_thread(cursor, prompt_for_title)
        else:
            _update_thread_date(cursor, thread_id)
            
        # 2. Enregistrement du message utilisateur
        user_message_id = _save_message(cursor, thread_id, 'user', "Analyse automatique des fichiers déposés", 'En cours')
        
        # 3. Traitement des fichiers
        is_complex_file = False
        total_file_size = 0
        max_file_size = 20 * 1024 * 1024  # 20MB limite
        
        file_details = []
        
        for file in files:
            if not file.filename:
                continue
                
            # Vérification de la taille des fichiers
            file_size = file.size or 0
            total_file_size += file_size
            
            if total_file_size > max_file_size:
                raise HTTPException(
                    status_code=400,
                    detail=f"Taille totale des fichiers trop importante ({total_file_size/1024/1024:.1f}MB). Maximum: 20MB"
                )
            
            # Détection de fichiers complexes
            ext = file.filename.lower().split('.')[-1]
            if ext in ['xlsx', 'xls', 'csv', 'pdf', 'docx', 'pptx']:
                is_complex_file = True

            # Upload vers Gemini
            temp_file_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as temp:
                    content = await file.read()
                    temp.write(content)
                    temp_file_path = temp.name
                    temp_file_paths.append(temp_file_path)
                
                await file.seek(0)  # Reset pour réutilisation
                    
                uploaded_file_gemini = client.files.upload(file=temp_file_path)
                
                if uploaded_file_gemini:
                    gemini_uploaded_files.append(uploaded_file_gemini)
                    contents.append(uploaded_file_gemini)
                    
                    files_info_for_db.append({
                        'display_name': file.filename,
                        'size_ko': file_size / 1024,
                        'mime_type': uploaded_file_gemini.mime_type,
                        'gemini_file_name': uploaded_file_gemini.name
                    })
                    
                    file_details.append(f"📄 {file.filename} ({(file_size/1024/1024):.2f} MB)")
                    
            except Exception as e:
                logger.error(f"Erreur upload fichier {file.filename}: {e}")
                final_status = "Erreur Fichier(s)"
                continue
        
        # Sauvegarde des infos fichiers
        if files_info_for_db:
            _save_files_to_message(cursor, user_message_id, files_info_for_db)
        
        # Choix du modèle basé sur la complexité
        if is_complex_file:
            model_to_use = 'gemini-2.0-flash-exp'
    
        # 4. Préparation du contenu pour Gemini - analyse automatique DÉTAILLÉE
        file_list_text = "\n".join(file_details)
        
        analysis_prompt = f"""
        SWIS MADAGASCAR - RAPPORT D'ANALYSE AUTOMATIQUE

        FICHIERS ANALYSÉS :
        {file_list_text}

        **INSTRUCTIONS D'ANALYSE DÉTAILLÉE :**

        **PREMIÈREMENT : ANALYSE GLOBALE**
        Commencez par fournir une analyse globale avec les indicateurs clés :
        - Chiffre d'affaire total
        - Taux de vente moyen
        - Nombre total de transactions
        - Stock moyen disponible
        - Valeur totale du stock
        - Performance globale

        **ENSUITE : DÉTECTION DES ANOMALIES**

        Pour CHAQUE fichier, identifiez PRÉCISÉMENT :
        1. **Localisation exacte des anomalies** (ligne, colonne, cellule si possible)
        2. **Nature de l'erreur** avec explication claire
        3. **Impact financier** ou opérationnel
        4. **Recommandation corrective**

        **ANALYSE PAR CATÉGORIE :**

        🔍 **ANOMALIES FINANCIÈRES :**
        - Incohérences entre montants encaissés et ventes
        - Écarts de caisse identifiables
        - Transactions dupliquées ou manquantes
        - Problèmes de rapprochement

        📊 **ERREURS DE STOCK :**
        - Différences stock théorique vs physique
        - Ruptures de stock critiques
        - Mouvements anormaux
        - Données manquantes ou incohérentes

        💰 **ANOMALIES DE TARIFICATION :**
        - Prix incohérents ou aberrants
        - Remises anormales
        - Variations de prix suspectes

        📈 **PERFORMANCE COMMERCIALE :**
        - Tendances anormales
        - Points de performance exceptionnels
        - Opportunités d'optimisation

        **FORMAT DE RÉPONSE EXIGÉ :**

        **ANALYSE GLOBALE**
        [Fournir ici les indicateurs clés globaux]

        Pour CHAQUE anomalie détectée, utilisez cette structure :

        🚨 **ANOMALIE DÉTECTÉE**
        📁 Fichier : [Nom du fichier]
        📍 Localisation : [Ligne X, Colonne Y, Feuille Z]
        🔎 Description : [Description détaillée de l'anomalie]
        💰 Impact : [Impact financier ou quantitatif]
        ✅ Recommandation : [Action corrective spécifique]

        **EXEMPLES CONCRETS :**

        🚨 **ANOMALIE DÉTECTÉE**
        📁 Fichier : ventes_mars.xlsx
        📍 Localisation : Ligne 45, Colonne D, Feuille "Ventes"
        🔎 Description : Montant de vente (1 500 000 MGA) ne correspond pas au total des articles
        💰 Impact : Écart de 250 000 MGA détecté
        ✅ Recommandation : Vérifier la saisie ligne 45 et corriger le montant

        🚨 **ANOMALIE DÉTECTÉE**  
        📁 Fichier : stock_physique.csv
        📍 Localisation : Lignes 23-25, Produit "RX-456"
        🔎 Description : Stock physique (150 unités) différent du stock théorique (180 unités)
        💰 Impact : 30 unités manquantes (valeur : 450 000 MGA)
        ✅ Recommandation : Audit immédiat du produit RX-456

        Présentez les anomalies par ordre de criticité.
        Soyez exhaustif et précis dans vos détections.
        """
        
        contents.append(analysis_prompt)
        
        if not contents:
            raise HTTPException(status_code=400, detail="Aucun contenu valide pour l'analyse.")

        # 5. Appel à l'API Gemini
        try:
            logger.info(f"Appel API avec modèle: {model_to_use}")
            api_response = client.models.generate_content(
                model=model_to_use,
                contents=contents,
                config={
                    'temperature': 0.1,
                    'top_p': 0.8,
                    'top_k': 40
                }
            )
            response_text = api_response.text
            
        except APIError as e:
            final_status = "Erreur API"
            response_text = f"Erreur lors de l'analyse: {str(e)}"
            logger.error(f"Erreur API: {e}")
        except Exception as e:
            final_status = "Erreur API"
            response_text = f"Erreur lors du traitement: {str(e)}"
            logger.error(f"Erreur inattendue: {e}")

    except HTTPException:
        raise
    except Exception as e:
        full_traceback = traceback.format_exc()
        logger.error(f"Erreur interne: {full_traceback}")
        final_status = "Erreur Interne"
        response_text = f"Erreur interne du serveur: {str(e)}"
        
    finally:
        # 6. Sauvegarde finale et nettoyage
        try:
            if conn:
                # Sauvegarde réponse assistant
                _save_message(cursor, thread_id, 'assistant', response_text, final_status)
                
                # Mise à jour statut message utilisateur
                if user_message_id:
                    cursor.execute(
                        "UPDATE MESSAGES SET statut = ? WHERE message_id = ?",
                        (final_status, user_message_id)
                    )
                
                conn.commit()
                logger.info(f"Traitement terminé pour thread {thread_id}, statut: {final_status}")

        except Exception as db_e:
            logger.error(f"Erreur BD lors de la sauvegarde finale: {db_e}")
            
        finally:
            # Nettoyage des fichiers
            _cleanup_files(gemini_uploaded_files, temp_file_paths)
            
            if conn:
                conn.close()

    if final_status != "Succès":
        raise HTTPException(status_code=500, detail=response_text)

    return {
        "thread_id": thread_id, 
        "response": response_text,
        "status": final_status,
        "model_used": model_to_use
    }

@app.get("/api/history")
async def get_history(limit: int = 50, offset: int = 0):
    """Récupère la liste des threads avec pagination."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Récupération des threads avec pagination
        cursor.execute("""
            SELECT 
                thread_id, 
                title, 
                date_modification,
                (SELECT COUNT(*) FROM MESSAGES WHERE thread_id = THREADS.thread_id) as message_count
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
                "message_count": row["message_count"]
            })
            
        # Nombre total de threads
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
        
        # Vérification existence thread
        cursor.execute("SELECT title FROM THREADS WHERE thread_id = ?", (thread_id,))
        thread_title = cursor.fetchone()
        
        if not thread_title:
            raise HTTPException(status_code=404, detail="Thread non trouvé.")
            
        # Récupération messages avec infos fichiers
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
            # Nettoyage des noms de fichiers
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
        raise HTTPException(status_code=500, detail="Erreur lors de la récupération du détail.")
        
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
            raise HTTPException(status_code=404, detail="Thread non trouvé.")
        
        conn.commit()
        logger.info(f"Thread {thread_id} supprimé")
        return {"message": f"Thread {thread_id} supprimé avec succès."}
        
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
    uvicorn.run(app, host="0.0.0.0", port=8000)