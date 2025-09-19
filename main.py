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
RATING_MODE = os.getenv('RATING_MODE', 'average').lower()
SERIES_WATCH_MODE = os.getenv('SERIES_WATCH_MODE', 'full').lower()
CRON_SCHEDULE = os.getenv('CRON_SCHEDULE', '02:00')
EXCLUDED_LIBRARIES_STR = os.getenv('EXCLUDED_LIBRARIES', '')
EXCLUDED_LIBRARIES = [lib.strip().lower() for lib in EXCLUDED_LIBRARIES_STR.split(',') if lib.strip()]


def send_tautulli_notification(subject, body):
    """Sends a notification through a configured Tautulli agent, respecting DRY_RUN."""
    if not TAUTULLI_NOTIFIER_ID:
        return
    if DRY_RUN:
        print("DRY RUN: Skipping Tautulli notification.")
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
    """Fetches the full media item from Plex and its external database IDs."""
    try:
        item = plex_server.fetchItem(int(rating_key))
        db_id, db_type = None, None
        if hasattr(item, 'guids') and item.guids:
            for guid_obj in item.guids:
                if 'tmdb' in guid_obj.id:
                    db_id, db_type = guid_obj.id.split('//')[1], 'movie'
                    break
                elif 'tvdb' in guid_obj.id:
                    db_id, db_type = guid_obj.id.split('//')[1], 'series'
                    break
        return item, db_id, db_type
    except NotFound:
        return None, None, None
    except Exception as e:
        print(f"Error fetching Plex item {rating_key}: {e}")
        return None, None, None

def evaluate_ratings(ratings):
    """Evaluates a list of ratings based on the configured RATING_MODE."""
    if not ratings:
        return False, "No user ratings found.", 0.0
    
    average_rating = sum(ratings) / len(ratings)
    
    if RATING_MODE == 'any_high':
        if any(r >= RATING_THRESHOLD for r in ratings):
            return False, f"Kept because at least one rating is >= {RATING_THRESHOLD}", average_rating
    
    if average_rating < RATING_THRESHOLD:
        return True, f"Eligible for deletion (Average Rating: {average_rating:.1f})", average_rating
    
    return False, f"Kept (Average Rating: {average_rating:.1f})", average_rating

def is_series_fully_watched(series_item):
    """Checks if a series has been fully watched."""
    return hasattr(series_item, 'viewCount') and hasattr(series_item, 'leafCount') and series_item.viewCount >= series_item.leafCount

def delete_radarr_movie(tmdb_id):
    """Instructs Radarr to delete a movie."""
    if not RADARR_URL or not RADARR_API_KEY: return False
    try:
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error communicating with Radarr: {e}")
        return False

def delete_sonarr_series(tvdb_id):
    """Instructs Sonarr to delete a series."""
    if not SONARR_URL or not SONARR_API_KEY: return False
    try:
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error communicating with Sonarr: {e}")
        return False

def run_cleanup_job():
    """Main job function to handle the entire warning and deletion process."""
    print(f"\n{'='*80}\nStarting PlexStarCleaner Job at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE DELETION'}")

    # --- Service Connection ---
    try:
        plex_server = PlexServer(PLEX_URL, PLEX_TOKEN)
        print("Successfully connected to Plex server.")
    except Exception as e:
        print(f"Fatal: Could not connect to Plex server. Aborting. Error: {e}")
        return

    try:
        params = {'apikey': TAUTULLI_API_KEY, 'cmd': 'get_history', 'length': 10000}
        response = requests.get(f"{TAUTULLI_URL}/api/v2", params=params, timeout=60)
        response.raise_for_status()
        history_data = response.json()['response']['data']['data']
    except Exception as e:
        print(f"Fatal: Could not fetch Tautulli history. Aborting. Error: {e}")
        return

    # --- Data Aggregation from Tautulli ---
    media_data = {}
    for item in history_data:
        if item.get('library_name', '').lower() in EXCLUDED_LIBRARIES: continue
        
        rating_key = item.get('grandparent_rating_key') if item.get('media_type') == 'episode' else item.get('rating_key')
        title = item.get('grandparent_title') if item.get('media_type') == 'episode' else item.get('full_title')
        user = item.get('friendly_name', 'Unknown')
        user_rating = item.get('user_rating')
        
        if not rating_key or not title: continue
        
        last_watched_date = datetime.fromtimestamp(item.get('date', 0), timezone.utc)
        
        if rating_key not in media_data:
            media_data[rating_key] = {'ratings': [], 'users': set(), 'last_watched': last_watched_date, 'title': title, 'type': 'series' if item.get('media_type') == 'episode' else 'movie'}
        
        if user_rating is not None:
            media_data[rating_key]['ratings'].append(float(user_rating))
        
        media_data[rating_key]['users'].add(user)
        if last_watched_date > media_data[rating_key]['last_watched']:
            media_data[rating_key]['last_watched'] = last_watched_date
    
    # --- Processing Logic ---
    items_for_warning, items_for_deletion = [], []
    now = datetime.now(timezone.utc)
    warning_start = now - timedelta(days=DAYS_DELAY_DELETION)
    warning_end = now - timedelta(days=DAYS_DELAY_WARNING)
    deletion_cutoff = now - timedelta(days=DAYS_DELAY_DELETION)

    for key, data in media_data.items():
        plex_item, db_id, db_type = get_plex_item_details(plex_server, key)
        if not plex_item: continue

        should_delete, reason, avg_rating = evaluate_ratings(data['ratings'])
        if not should_delete: continue

        if db_type == 'series' and SERIES_WATCH_MODE == 'full' and not is_series_fully_watched(plex_item):
            print(f"Skipping '{data['title']}': Not fully watched (mode: full).")
            continue

        if warning_start < data['last_watched'] <= warning_end:
            items_for_warning.append({'title': data['title'], 'avg_rating': avg_rating, 'users': data['users']})
        elif data['last_watched'] < deletion_cutoff:
            items_for_deletion.append({'db_id': db_id, 'db_type': db_type, 'title': data['title'], 'avg_rating': avg_rating})
    
    # --- Actions and Notifications ---
    if items_for_warning:
        subject = f"Plex Deletion Warning: {len(items_for_warning)} Item(s)"
        body = f"<b>The following media have a low rating and are scheduled for deletion in ~7 days:</b>\n"
        for item_data in items_for_warning:
            user_list = ", ".join(sorted(list(item_data['users'])))
            body += f"- <b>{item_data['title']}</b> (Avg Rating: {item_data['avg_rating']:.1f}, Watched by: {user_list})\n"
        print("\n--- WARNING SUMMARY ---")
        print(body.replace('<b>', '').replace('</b>', ''))
        send_tautulli_notification(subject, body)

    actions_taken = []
    if items_for_deletion:
        print("\n--- PROCESSING DELETIONS ---")
        for item_data in items_for_deletion:
            deleted = False
            if item_data['db_type'] == 'movie':
                deleted = delete_radarr_movie(item_data['db_id'])
            elif item_data['db_type'] == 'series':
                deleted = delete_sonarr_series(item_data['db_id'])
            if deleted:
                actions_taken.append(item_data)

    if actions_taken:
        action_verb = "Flagged for Deletion" if DRY_RUN else "Deleted"
        subject = f"PlexStarCleaner Report: {len(actions_taken)} Item(s) {action_verb}"
        body = f"<b>A total of {len(actions_taken)} media item(s) were {'flagged for deletion' if DRY_RUN else 'deleted'}:</b>\n"
        for action in actions_taken:
            body += f"- <b>{action['title']}</b> ({action['db_type'].capitalize()}) - Avg Rating: {action['avg_rating']:.1f}\n"
        print("\n--- DELETION SUMMARY ---")
        print(body.replace('<b>', '').replace('</b>', ''))
        send_tautulli_notification(subject, body)

    print(f"\nJob finished at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'='*80}")

if __name__ == "__main__":
    if DAYS_DELAY_WARNING >= DAYS_DELAY_DELETION:
        raise ValueError("FATAL: DAYS_DELAY_DELETION must be greater than DAYS_DELAY_WARNING.")
    
    run_cleanup_job()
    schedule.every().day.at(CRON_SCHEDULE).do(run_cleanup_job)
    print(f"Scheduling job to run every day at {CRON_SCHEDULE}. Waiting...")
    while True:
        schedule.run_pending()
        time.sleep(60)