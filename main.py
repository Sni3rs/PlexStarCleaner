import os
import requests
import schedule
import time
import logging
from datetime import datetime, timedelta, timezone
from plexapi.server import PlexServer
from plexapi.exceptions import NotFound
from typing import List, Dict, Optional, Any

# --- Configuration du Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# --- Variables d'Environnement ---
# Services
TAUTULLI_URL = os.getenv('TAUTULLI_URL')
TAUTULLI_API_KEY = os.getenv('TAUTULLI_API_KEY')
RADARR_URL = os.getenv('RADARR_URL')
RADARR_API_KEY = os.getenv('RADARR_API_KEY')
SONARR_URL = os.getenv('SONARR_URL')
SONARR_API_KEY = os.getenv('SONARR_API_KEY')
PLEX_URL = os.getenv('PLEX_URL')
PLEX_TOKEN = os.getenv('PLEX_TOKEN')

# Paramètres du script
DRY_RUN = os.getenv('DRY_RUN', 'true').lower() == 'true'
DAYS_DELAY = int(os.getenv('DAYS_DELAY', 30))
RATING_THRESHOLD = float(os.getenv('RATING_THRESHOLD', 6.5))
CRON_SCHEDULE = os.getenv('CRON_SCHEDULE', '02:00')
EXCLUDED_LIBRARIES_STR = os.getenv('EXCLUDED_LIBRARIES', '')
EXCLUDED_LIBRARIES = [lib.strip().lower() for lib in EXCLUDED_LIBRARIES_STR.split(',') if lib.strip()]

# Logique de traitement
# NOTE: Le mode 'full' pour les séries est simplifié. Le script agira sur une série si
# un de ses épisodes vus est plus ancien que DAYS_DELAY. Vérifier que la série
# est *entièrement* vue est beaucoup plus complexe et coûteux en appels API.
SERIES_WATCH_MODE = os.getenv('SERIES_WATCH_MODE', 'full').lower()


def get_plex_item_details(plex_server: PlexServer, rating_key: str) -> (Optional[float], Optional[str]):
    """
    Récupère les détails d'un média depuis Plex via son rating_key.
    Retourne la note personnelle et l'ID TMDB/TVDB.
    """
    try:
        item = plex_server.fetchItem(int(rating_key))
        rating = item.userRating if hasattr(item, 'userRating') else None
        
        db_id = None
        if hasattr(item, 'guids') and item.guids:
            for guid_obj in item.guids:
                if 'tmdb' in guid_obj.id:
                    db_id = guid_obj.id.split('//')[1]
                    break
                elif 'tvdb' in guid_obj.id:
                    db_id = guid_obj.id.split('//')[1]
                    break
        
        return rating, db_id
    except NotFound:
        logging.warning(f"Item avec rating_key {rating_key} non trouvé sur Plex. Il a peut-être déjà été supprimé.")
        return None, None
    except Exception as e:
        logging.error(f"Erreur lors de la récupération des détails pour rating_key {rating_key} depuis Plex: {e}")
        return None, None


def delete_radarr_movie(tmdb_id: str) -> bool:
    """
    Ordonne à Radarr de supprimer un film via son ID TMDB.
    """
    if not RADARR_URL or not RADARR_API_KEY:
        logging.warning("Radarr n'est pas configuré. Suppression de film ignorée.")
        return False
        
    try:
        # 1. Trouver le film dans Radarr par son TMDB ID
        lookup_response = requests.get(
            f"{RADARR_URL}/api/v3/movie",
            params={'tmdbId': tmdb_id},
            headers={'X-Api-Key': RADARR_API_KEY},
            timeout=15
        )
        lookup_response.raise_for_status()
        movies = lookup_response.json()
        if not movies or 'id' not in movies[0]:
            logging.info(f"Film avec TMDB ID {tmdb_id} non trouvé dans Radarr.")
            return False

        radarr_id = movies[0]['id']
        
        # 2. Envoyer la commande de suppression
        logging.info(f"Commande de suppression à Radarr pour le film (Radarr ID: {radarr_id}, TMDB ID: {tmdb_id}).")
        if not DRY_RUN:
            delete_response = requests.delete(
                f"{RADARR_URL}/api/v3/movie/{radarr_id}",
                params={'deleteFiles': 'true', 'addImportExclusion': 'false'},
                headers={'X-Api-Key': RADARR_API_KEY},
                timeout=30
            )
            delete_response.raise_for_status()
            logging.info("Commande de suppression envoyée avec succès à Radarr.")
        return True

    except requests.exceptions.RequestException as e:
        logging.error(f"Erreur de communication avec Radarr pour TMDB ID {tmdb_id}: {e}")
        return False


def delete_sonarr_series(tvdb_id: str) -> bool:
    """
    Ordonne à Sonarr de supprimer une série via son ID TVDB.
    """
    if not SONARR_URL or not SONARR_API_KEY:
        logging.warning("Sonarr n'est pas configuré. Suppression de série ignorée.")
        return False

    try:
        # 1. Trouver la série dans Sonarr par son TVDB ID
        lookup_response = requests.get(
            f"{SONARR_URL}/api/v3/series",
            params={'tvdbId': tvdb_id},
            headers={'X-Api-Key': SONARR_API_KEY},
            timeout=15
        )
        lookup_response.raise_for_status()
        series_list = lookup_response.json()
        if not series_list or 'id' not in series_list[0]:
            logging.info(f"Série avec TVDB ID {tvdb_id} non trouvée dans Sonarr.")
            return False

        sonarr_id = series_list[0]['id']
        
        # 2. Envoyer la commande de suppression
        logging.info(f"Commande de suppression à Sonarr pour la série (Sonarr ID: {sonarr_id}, TVDB ID: {tvdb_id}).")
        if not DRY_RUN:
            delete_response = requests.delete(
                f"{SONARR_URL}/api/v3/series/{sonarr_id}",
                params={'deleteFiles': 'true'},
                headers={'X-Api-Key': SONARR_API_KEY},
                timeout=30
            )
            delete_response.raise_for_status()
            logging.info("Commande de suppression envoyée avec succès à Sonarr.")
        return True
            
    except requests.exceptions.RequestException as e:
        logging.error(f"Erreur de communication avec Sonarr pour TVDB ID {tvdb_id}: {e}")
        return False


def run_cleanup_job():
    """
    Fonction principale du job.
    """
    logging.info("="*80)
    logging.info(f"Démarrage du job PlexStarCleaner")
    logging.info(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE DELETION'}")
    if EXCLUDED_LIBRARIES:
        logging.info(f"Bibliothèques exclues: {', '.join(EXCLUDED_LIBRARIES)}")
    logging.info("="*80)

    # --- Vérification des variables d'environnement ---
    required_vars = ['TAUTULLI_URL', 'TAUTULLI_API_KEY', 'PLEX_URL', 'PLEX_TOKEN']
    if any(not globals().get(var) for var in required_vars):
        logging.critical(f"Variables d'environnement manquantes: {[var for var in required_vars if not globals().get(var)]}. Arrêt.")
        return

    # --- Connexion à Plex ---
    try:
        logging.info("Connexion au serveur Plex...")
        plex_server = PlexServer(PLEX_URL, PLEX_TOKEN)
        logging.info("Connexion au serveur Plex réussie.")
    except Exception as e:
        logging.critical(f"Erreur de connexion au serveur Plex ({PLEX_URL}): {e}")
        return

    # --- Récupération de l'historique Tautulli ---
    try:
        logging.info("Récupération de l'historique depuis Tautulli...")
        params = {'apikey': TAUTULLI_API_KEY, 'cmd': 'get_history', 'length': 5000}
        response = requests.get(f"{TAUTULLI_URL}/api/v2", params=params, timeout=60)
        response.raise_for_status()
        history_data = response.json()['response']['data']['data']
        logging.info(f"{len(history_data)} éléments d'historique récupérés.")
    except requests.exceptions.RequestException as e:
        logging.critical(f"Erreur lors de la récupération de l'historique Tautulli: {e}")
        return
    except (KeyError, TypeError):
        logging.critical("Erreur de parsing de la réponse Tautulli. Vérifiez la clé API.")
        return

    # --- Traitement de l'historique ---
    media_to_process: Dict[str, Dict[str, Any]] = {}
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=DAYS_DELAY)

    for item in history_data:
        if item.get('library_name', '').lower() in EXCLUDED_LIBRARIES:
            continue
        
        media_type = item.get('media_type')
        if media_type not in ['movie', 'episode']:
            continue

        last_watched_date = datetime.fromtimestamp(item.get('date', 0), timezone.utc)
        if last_watched_date > cutoff_date:
            continue
        
        unique_id, title, rating_key = None, None, None

        if media_type == 'movie' and item.get('watched_status') == 1:
            rating_key = item.get('rating_key')
            title = item.get('full_title')
            unique_id = rating_key
        elif media_type == 'episode':
            # Pour une série, on utilise la clé du show (grandparent)
            rating_key = item.get('grandparent_rating_key')
            title = item.get('grandparent_title')
            unique_id = rating_key

        if not all([unique_id, title, rating_key]):
            continue
        
        # On ne garde que la dernière date de visionnage pour chaque média unique
        if unique_id not in media_to_process or last_watched_date > media_to_process[unique_id]['last_watched']:
            media_to_process[unique_id] = {
                'title': title, 
                'media_type': 'series' if media_type == 'episode' else 'movie',
                'rating_key': rating_key, 
                'last_watched': last_watched_date
            }
    
    # --- Évaluation et suppression ---
    if not media_to_process:
        logging.info("Aucun média éligible à traiter.")
    else:
        logging.info(f"{len(media_to_process)} médias uniques éligibles pour évaluation.")
        sorted_media = sorted(media_to_process.values(), key=lambda x: x['last_watched'])
        
        results = {'deleted': [], 'kept': [], 'failed': []}

        for media in sorted_media:
            title = media['title']
            logging.info(f"--- Traitement de: {title} ({media['media_type']}) ---")
            
            rating, db_id = get_plex_item_details(plex_server, media['rating_key'])

            if rating is None:
                logging.info("Pas de note personnelle trouvée. Conservation.")
                results['kept'].append(title)
                continue

            logging.info(f"Note personnelle trouvée: {rating}. Seuil: {RATING_THRESHOLD}.")

            if rating >= RATING_THRESHOLD:
                logging.info(f"DÉCISION: CONSERVER (note {rating} >= {RATING_THRESHOLD}).")
                results['kept'].append(title)
                continue

            logging.info(f"DÉCISION: SUPPRIMER (note {rating} < {RATING_THRESHOLD}).")
            
            if not db_id:
                logging.warning(f"Impossible de trouver un ID TMDB/TVDB pour '{title}'. Suppression annulée.")
                results['failed'].append(title)
                continue

            delete_successful = False
            if media['media_type'] == 'movie':
                delete_successful = delete_radarr_movie(db_id)
            elif media['media_type'] == 'series':
                delete_successful = delete_sonarr_series(db_id)
            
            if delete_successful:
                results['deleted'].append(title)
            else:
                results['failed'].append(title)

    # --- Résumé Final ---
    logging.info("="*80)
    logging.info("Job PlexStarCleaner Terminé")
    action = "seraient supprimés" if DRY_RUN else "ont été supprimés"
    
    logging.info(f"\n--- RÉSUMÉ ({'DRY RUN' if DRY_RUN else 'LIVE'}) ---\n")
    logging.info(f"Total de médias uniques traités: {len(media_to_process)}")
    logging.info(f"Médias conservés (note suffisante ou pas de note): {len(results['kept'])}")
    logging.info(f"Échecs de suppression (ID non trouvé, erreur API): {len(results['failed'])}")
    logging.info(f"Total de médias qui {action}: {len(results['deleted'])}")
    
    if results['deleted']:
        logging.info("\nListe des médias supprimés :")
        for title in results['deleted']:
            logging.info(f"  - {title}")
            
    if results['failed']:
        logging.info("\nListe des échecs :")
        for title in results['failed']:
            logging.info(f"  - {title}")

    logging.info("="*80)


if __name__ == "__main__":
    run_cleanup_job()
    schedule.every().day.at(CRON_SCHEDULE).do(run_cleanup_job)
    logging.info(f"Planification du job chaque jour à {CRON_SCHEDULE}. En attente...")
    while True:
        schedule.run_pending()
        time.sleep(60)