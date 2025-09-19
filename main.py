import os
import requests
import schedule
import time
from datetime import datetime, timedelta, timezone
from plexapi.server import PlexServer
from plexapi.exceptions import NotFound

# --- Environment Variables ---
# Service Configurations
TAUTULLI_URL = os.getenv('TAUTULLI_URL')
TAUTULLI_API_KEY = os.getenv('TAUTULLI_API_KEY')
TAUTULLI_NOTIFIER_ID = os.getenv('TAUTULLI_NOTIFIER_ID')
RADARR_URL = os.getenv('RADARR_URL')
RADARR_API_KEY = os.getenv('RADARR_API_KEY')
SONARR_URL = os.getenv('SONARR_URL')
SONARR_API_KEY = os.getenv('SONARR_API_KEY')
PLEX_URL = os.getenv('PLEX_URL')
PLEX_TOKEN = os.getenv('PLEX_TOKEN')

# Script Settings
DRY_RUN = os.getenv('DRY_RUN', 'true').lower() == 'true'
DAYS_DELAY_WARNING = int(os.getenv('DAYS_DELAY_WARNING', 30))
DAYS_DELAY_DELETION = int(os.getenv('DAYS_DELAY_DELETION', 37))
RATING_THRESHOLD = float(os.getenv('RATING_THRESHOLD', 6.5))
CRON_SCHEDULE = os.getenv('CRON_SCHEDULE', '02:00')
EXCLUDED_LIBRARIES_STR = os.getenv('EXCLUDED_LIBRARIES', '')
EXCLUDED_LIBRARIES = [lib.strip().lower() for lib in EXCLUDED_LIBRARIES_STR.split(',') if lib.strip()]


def send_tautulli_notification(subject, body):
    """
    Sends a notification through a configured Tautulli agent.
    Does not send notifications if DRY_RUN is true or if no notifier ID is set.
    """
    if not TAUTULLI_NOTIFIER_ID or DRY_RUN:
        return
        
    print("Sending Tautulli notification...")
    try:
        params = {
            'apikey': TAUTULLI_API_KEY,
            'cmd': 'notify',
            'notifier_id': TAUTULLI_NOTIFIER_ID,
            'subject': subject,
            'body': body
        }
        response = requests.post(f"{TAUTULLI_URL}/api/v2", params=params, timeout=20)
        response.raise_for_status()
        print("Tautulli notification sent successfully.")
    except requests.exceptions.RequestException as e:
        print(f"Error sending Tautulli notification: {e}")

def get_plex_item_details(plex_server, rating_key):
    """
    Fetches the full media item from Plex to get its rating and all associated GUIDs.
    Handles cases where the item is not found on the Plex server.
    """
    try:
        item = plex_server.fetchItem(int(rating_key))
        rating = item.userRating if hasattr(item, 'userRating') else None
        
        db_id = None
        db_type = None
        if hasattr(item, 'guids') and item.guids:
            for guid_obj in item.guids:
                if 'tmdb' in guid_obj.id:
                    db_id = guid_obj.id.split('//')[1]
                    db_type = 'movie'
                    break
                elif 'tvdb' in guid_obj.id:
                    db_id = guid_obj.id.split('//')[1]
                    db_type = 'series'
                    break
        
        return item, rating, db_id, db_type
    except NotFound:
        print(f"Item with rating_key {rating_key} not found on Plex server. It may have been previously deleted.")
        return None, None, None, None
    except Exception as e:
        print(f"Error fetching details for rating_key {rating_key} from Plex: {e}")
        return None, None, None, None

def delete_radarr_movie(tmdb_id):
    """
    Instructs Radarr to delete a movie and all its files by its TMDB ID.
    Returns True on success, False on failure.
    """
    if not RADARR_URL or not RADARR_API_KEY:
        print("Radarr URL or API Key is not configured. Cannot delete movie.")
        return False
        
    try:
        lookup_response = requests.get(
            f"{RADARR_URL}/api/v3/movie",
            params={'tmdbId': tmdb_id},
            headers={'X-Api-Key': RADARR_API_KEY},
            timeout=15
        )
        lookup_response.raise_for_status()
        movies = lookup_response.json()
        if not movies:
            print(f"Movie with TMDB ID {tmdb_id} not found in Radarr.")
            return False

        radarr_id = movies[0]['id']
        
        print(f"Instructing Radarr to delete movie (Radarr ID: {radarr_id}, TMDB ID: {tmdb_id})...")
        if not DRY_RUN:
            delete_response = requests.delete(
                f"{RADARR_URL}/api/v3/movie/{radarr_id}",
                params={'deleteFiles': 'true', 'addImportExclusion': 'false'},
                headers={'X-Api-Key': RADARR_API_KEY},
                timeout=30
            )
            delete_response.raise_for_status()
            print("Deletion command sent to Radarr successfully.")
        return True

    except requests.exceptions.RequestException as e:
        print(f"Error communicating with Radarr for TMDB ID {tmdb_id}: {e}")
        return False

def delete_sonarr_series(tvdb_id):
    """
    Instructs Sonarr to delete a series and all its files by its TVDB ID.
    Returns True on success, False on failure.
    """
    if not SONARR_URL or not SONARR_API_KEY:
        print("Sonarr URL or API Key is not configured. Cannot delete series.")
        return False

    try:
        lookup_response = requests.get(
            f"{SONARR_URL}/api/v3/series",
            params={'tvdbId': tvdb_id},
            headers={'X-Api-Key': SONARR_API_KEY},
            timeout=15
        )
        lookup_response.raise_for_status()
        series = lookup_response.json()
        if not series:
            print(f"Series with TVDB ID {tvdb_id} not found in Sonarr.")
            return False

        sonarr_id = series[0]['id']

        print(f"Instructing Sonarr to delete series (Sonarr ID: {sonarr_id}, TVDB ID: {tvdb_id})...")
        if not DRY_RUN:
            delete_response = requests.delete(
                f"{SONARR_URL}/api/v3/series/{sonarr_id}",
                params={'deleteFiles': 'true'},
                headers={'X-Api-Key': SONARR_API_KEY},
                timeout=30
            )
            delete_response.raise_for_status()
            print("Deletion command sent to Sonarr successfully.")
        return True
            
    except requests.exceptions.RequestException as e:
        print(f"Error communicating with Sonarr for TVDB ID {tvdb_id}: {e}")
        return False

def run_cleanup_job():
    """
    Main job function that connects to services, fetches history, and processes media
    for warnings and deletions in two separate phases.
    """
    print(f"\n{'='*80}\nStarting PlexStarCleaner Job at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE DELETION'}")
    if EXCLUDED_LIBRARIES:
        print(f"Excluding libraries: {', '.join(EXCLUDED_LIBRARIES)}")
    print(f"Warning delay: {DAYS_DELAY_WARNING} days, Deletion delay: {DAYS_DELAY_DELETION} days")

    # --- Service Connection ---
    required_vars = ['TAUTULLI_URL', 'TAUTULLI_API_KEY', 'PLEX_URL', 'PLEX_TOKEN']
    if any(not os.getenv(var) for var in required_vars):
        print(f"Error: Missing one or more required environment variables: {required_vars}. Aborting.")
        return

    try:
        plex_server = PlexServer(PLEX_URL, PLEX_TOKEN)
        print("Successfully connected to Plex server.")
    except Exception as e:
        print(f"Fatal: Could not connect to Plex server at {PLEX_URL}. Error: {e}")
        return

    try:
        params = {'apikey': TAUTULLI_API_KEY, 'cmd': 'get_history', 'length': 10000}
        response = requests.get(f"{TAUTULLI_URL}/api/v2", params=params, timeout=60)
        response.raise_for_status()
        history_data = response.json()['response']['data']['data']
        print(f"Successfully fetched {len(history_data)} items from Tautulli history.")
    except (requests.exceptions.RequestException, KeyError, TypeError) as e:
        print(f"Fatal: Could not fetch or parse Tautulli history. Error: {e}")
        return

    # --- Data Aggregation ---
    media_history = {}  # Key: rating_key, Value: {last_watched, users}
    for item in history_data:
        if item.get('library_name', '').lower() in EXCLUDED_LIBRARIES:
            continue
        
        rating_key = item.get('grandparent_rating_key') if item.get('media_type') == 'episode' else item.get('rating_key')
        user = item.get('friendly_name', 'Unknown')
        
        if not rating_key:
            continue
            
        last_watched_date = datetime.fromtimestamp(item.get('date', 0), timezone.utc)
        
        if rating_key not in media_history:
            media_history[rating_key] = {'last_watched': last_watched_date, 'users': {user}}
        else:
            if last_watched_date > media_history[rating_key]['last_watched']:
                media_history[rating_key]['last_watched'] = last_watched_date
            media_history[rating_key]['users'].add(user)

    # --- Processing Logic ---
    now = datetime.now(timezone.utc)
    warning_start_date = now - timedelta(days=DAYS_DELAY_DELETION)
    warning_end_date = now - timedelta(days=DAYS_DELAY_WARNING)
    deletion_cutoff_date = now - timedelta(days=DAYS_DELAY_DELETION)

    items_for_warning = []
    items_for_deletion = []

    for key, data in media_history.items():
        # Phase 1: Check for items that fall within the warning window
        if warning_start_date < data['last_watched'] <= warning_end_date:
            item, rating, _, _ = get_plex_item_details(plex_server, key)
            if item and rating is not None and rating < RATING_THRESHOLD:
                items_for_warning.append({'title': item.title, 'rating': rating, 'users': data['users']})
        
        # Phase 2: Check for items that are past the deletion date
        elif data['last_watched'] < deletion_cutoff_date:
            item, rating, db_id, db_type = get_plex_item_details(plex_server, key)
            if item and rating is not None and rating < RATING_THRESHOLD and db_id and db_type:
                items_for_deletion.append({'item': item, 'rating': rating, 'db_id': db_id, 'type': db_type})
    
    # --- Execute Actions ---
    if items_for_warning:
        print(f"\n--- Found {len(items_for_warning)} Items for WARNING ---")
        subject = f"Plex Deletion Warning: {len(items_for_warning)} Item(s)"
        body = "**The following media have a low rating and are scheduled for deletion in ~7 days:**\n"
        for item_data in items_for_warning:
            user_list = ", ".join(sorted(list(item_data['users'])))
            body += f"- **{item_data['title']}** (Your Rating: {item_data['rating']:.1f}, Watched by: {user_list})\n"
        print(body)
        send_tautulli_notification(subject, body)

    actions_taken = []
    if items_for_deletion:
        print(f"\n--- Found {len(items_for_deletion)} Items for DELETION ---")
        for item_data in items_for_deletion:
            deleted = False
            print(f"Processing for deletion: {item_data['item'].title}")
            if item_data['type'] == 'movie':
                deleted = delete_radarr_movie(item_data['db_id'])
            elif item_data['type'] == 'series':
                deleted = delete_sonarr_series(item_data['db_id'])
            
            if deleted:
                actions_taken.append({'title': item_data['item'].title, 'rating': item_data['rating'], 'type': item_data['type']})

    # --- Final Summary and Report ---
    if actions_taken:
        action_verb = "Flagged for Deletion" if DRY_RUN else "Deleted"
        subject = f"PlexStarCleaner Report: {len(actions_taken)} Item(s) {action_verb}"
        body = f"**A total of {len(actions_taken)} media item(s) were {'flagged for deletion' if DRY_RUN else 'deleted'} based on your ratings:**\n"
        for action in actions_taken:
            body += f"- **{action['title']}** ({action['type'].capitalize()}) - Your Rating: {action['rating']:.1f}\n"
        
        print("\n--- DELETION SUMMARY ---")
        print(body)
        send_tautulli_notification(subject, body)
    
    elif not items_for_warning:
        print("\nNo items found for warning or deletion. All clean!")

    print(f"\nJob finished at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'='*80}")

if __name__ == "__main__":
    if DAYS_DELAY_WARNING >= DAYS_DELAY_DELETION:
        raise ValueError("FATAL: DAYS_DELAY_DELETION must be greater than DAYS_DELAY_WARNING for the script to function correctly.")
    
    print("Starting initial run of the job...")
    run_cleanup_job()
    
    schedule.every().day.at(CRON_SCHEDULE).do(run_cleanup_job)
    print(f"Scheduling job to run every day at {CRON_SCHEDULE}. Waiting for the next scheduled run...")
    
    while True:
        schedule.run_pending()
        time.sleep(60)