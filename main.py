import os
import requests
import schedule
import time
from datetime import datetime, timedelta, timezone
from plexapi.server import PlexServer

# --- Environment Variables ---
# Service Configurations
TAUTULLI_URL = os.getenv('TAUTULLI_URL')
TAUTULLI_API_KEY = os.getenv('TAUTULLI_API_KEY')
RADARR_URL = os.getenv('RADARR_URL')
RADARR_API_KEY = os.getenv('RADARR_API_KEY')
SONARR_URL = os.getenv('SONARR_URL')
SONARR_API_KEY = os.getenv('SONARR_API_KEY')
PLEX_URL = os.getenv('PLEX_URL')
PLEX_TOKEN = os.getenv('PLEX_TOKEN')

# Script Settings
DRY_RUN = os.getenv('DRY_RUN', 'true').lower() == 'true'
DAYS_DELAY = int(os.getenv('DAYS_DELAY', 30))
RATING_THRESHOLD = float(os.getenv('RATING_THRESHOLD', 6.5))
CRON_SCHEDULE = os.getenv('CRON_SCHEDULE', '02:00')
EXCLUDED_LIBRARIES_STR = os.getenv('EXCLUDED_LIBRARIES', '')
EXCLUDED_LIBRARIES = [lib.strip().lower() for lib in EXCLUDED_LIBRARIES_STR.split(',') if lib.strip()]

# Logic Mode Settings
# Note: RATING_MODE is kept for compatibility, but now only reflects the owner's rating.
# 'average' and 'any_high' will behave identically as we fetch a single rating.
RATING_MODE = os.getenv('RATING_MODE', 'average').lower()
SERIES_WATCH_MODE = os.getenv('SERIES_WATCH_MODE', 'full').lower()


def get_owner_rating(plex_server, rating_key):
    """
    Fetches the personal rating for a media item from the Plex server.
    """
    try:
        item = plex_server.fetchItem(int(rating_key))
        # userRating is on a 10-point scale
        return item.userRating if hasattr(item, 'userRating') else None
    except Exception as e:
        print(f"Error fetching rating for rating_key {rating_key} from Plex: {e}")
        return None

def delete_radarr_movie(tmdb_id):
    """
    Instructs Radarr to delete a movie and all its files by its TMDB ID.
    """
    if not RADARR_URL or not RADARR_API_KEY:
        print("Radarr URL or API Key is not configured. Skipping movie deletion.")
        return
        
    try:
        # Radarr's API uses tmdbId as a query param, not part of the path
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
            return

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

    except requests.exceptions.RequestException as e:
        print(f"Error communicating with Radarr for TMDB ID {tmdb_id}: {e}")

def delete_sonarr_series(tvdb_id):
    """
    Instructs Sonarr to delete a series and all its files by its TVDB ID.
    """
    if not SONARR_URL or not SONARR_API_KEY:
        print("Sonarr URL or API Key is not configured. Skipping series deletion.")
        return

    try:
        # Sonarr's API uses tvdbId as a query param
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
            return

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
            
    except requests.exceptions.RequestException as e:
        print(f"Error communicating with Sonarr for TVDB ID {tvdb_id}: {e}")

def process_media_item(plex_server, title, guid, rating_key, media_type):
    """
    Processes a single media item: fetches rating, evaluates it, and triggers deletion if criteria are met.
    Returns the media type ('movie' or 'series') if flagged for deletion, otherwise None.
    """
    print(f"\nProcessing {media_type.capitalize()}: {title} (Rating Key: {rating_key})")
    
    rating = get_owner_rating(plex_server, rating_key)

    if rating is None:
        print("No personal rating found. Skipping.")
        return None

    print(f"Found personal rating: {rating}. Threshold is {RATING_THRESHOLD}.")

    should_delete = False
    if rating < RATING_THRESHOLD:
        should_delete = True
        print(f"Decision: DELETE (Rating {rating} is below threshold {RATING_THRESHOLD}).")
    else:
        print(f"Decision: KEEP (Rating {rating} is at or above threshold {RATING_THRESHOLD}).")
    
    if should_delete:
        try:
            # For deletion, we need the DB-specific ID from the GUID (e.g., tmdb, tvdb)
            if 'tvdb' in guid:
                db_id = guid.split('//')[-1].split('?')[0]
                delete_sonarr_series(db_id)
            elif 'tmdb' in guid:
                db_id = guid.split('//')[-1].split('?')[0]
                delete_radarr_movie(db_id)
            else:
                 print(f"Warning: Could not determine service (Sonarr/Radarr) from GUID: {guid}")
                 return None
            
            return media_type # Return type if deletion was triggered
        except IndexError:
            print(f"Error: Could not parse database ID from GUID: {guid}")
    
    return None

def run_cleanup_job():
    """
    Main job function: Fetches watch history from Tautulli and processes eligible media.
    """
    print("\n" + "="*80)
    print(f"Starting PlexStarCleaner Job at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE DELETION'}")
    if EXCLUDED_LIBRARIES:
        print(f"Excluding libraries (case-insensitive): {', '.join(EXCLUDED_LIBRARIES)}")
    print("="*80 + "\n")

    movies_flagged = 0
    series_flagged = 0
    total_processed = 0

    required_vars = ['TAUTULLI_URL', 'TAUTULLI_API_KEY', 'PLEX_URL', 'PLEX_TOKEN']
    missing_vars = [var for var in required_vars if not globals().get(var)]
    if missing_vars:
        print(f"Error: Missing required environment variables: {', '.join(missing_vars)}. Aborting job.")
        return

    try:
        print("Connecting to Plex server...")
        plex_server = PlexServer(PLEX_URL, PLEX_TOKEN)
        print("Plex server connection successful.")
    except Exception as e:
        print(f"Error connecting to Plex server at {PLEX_URL}: {e}")
        return

    try:
        params = {'apikey': TAUTULLI_API_KEY, 'cmd': 'get_history', 'length': 5000}
        response = requests.get(f"{TAUTULLI_URL}/api/v2", params=params, timeout=60)
        response.raise_for_status()
        history_data = response.json()['response']['data']['data']
    except requests.exceptions.RequestException as e:
        print(f"Error fetching history from Tautulli: {e}")
        return
    except (KeyError, TypeError):
        print("Error parsing Tautulli response. Check API key and Tautulli status.")
        return

    media_to_process = {}
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=DAYS_DELAY)

    for item in history_data:
        if item.get('library_name', '').lower() in EXCLUDED_LIBRARIES:
            continue

        if item.get('media_type') not in ['movie', 'episode'] or not item.get('guid'):
            continue
        
        last_watched_date = datetime.fromtimestamp(item.get('date', 0), timezone.utc)
        
        if last_watched_date > cutoff_date:
            continue
        
        # Use a unique key for each item
        unique_id, title, media_type, guid, rating_key = None, None, None, None, None

        if item['media_type'] == 'movie' and item.get('watched_status') == 1:
            rating_key = item.get('rating_key')
            title = item.get('full_title')
            media_type = 'movie'
            guid = item.get('guid')
            unique_id = rating_key

        elif item['media_type'] == 'episode':
            # For series, we act on the show level
            rating_key = item.get('grandparent_rating_key')
            title = item.get('grandparent_title')
            media_type = 'series'
            guid = item.get('grandparent_guid')
            unique_id = rating_key
            # Ensure series is fully watched if mode is 'full'
            if SERIES_WATCH_MODE == 'full' and item.get('watched_status') != 1:
                # This logic is imperfect as it only checks the last watched episode.
                # A more robust solution would check the series' watched status in Plex,
                # but this maintains original script behavior for simplicity.
                continue

        if not all([unique_id, title, media_type, guid, rating_key]):
            continue
            
        # Store the most recently watched item's data
        if unique_id not in media_to_process or last_watched_date > media_to_process[unique_id]['last_watched']:
            media_to_process[unique_id] = {
                'title': title, 
                'guid': guid, 
                'media_type': media_type, 
                'rating_key': rating_key, 
                'last_watched': last_watched_date
            }
    
    if not media_to_process:
        print("No eligible media items found to process.")
    else:
        print(f"Found {len(media_to_process)} unique media items eligible for processing.")
        # Sort by last watched date to process oldest first
        sorted_media = sorted(media_to_process.values(), key=lambda x: x['last_watched'])
        total_processed = len(sorted_media)
        
        for media in sorted_media:
            result = process_media_item(
                plex_server=plex_server,
                title=media['title'], 
                guid=media['guid'], 
                rating_key=media['rating_key'],
                media_type=media['media_type']
            )
            if result == 'movie':
                movies_flagged += 1
            elif result == 'series':
                series_flagged += 1
    
    print("\n" + "="*80)
    print("PlexStarCleaner Job Finished")
    print(f"Execution Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 30)
    print("S U M M A R Y")
    print("-" * 30)
    print(f"Total Unique Media Processed: {total_processed}")
    action = "would have been deleted" if DRY_RUN else "were deleted"
    print(f"Movies flagged for deletion: {movies_flagged}")
    print(f"Series flagged for deletion: {series_flagged}")
    print(f"Total items that {action}: {movies_flagged + series_flagged}")
    print("="*80)


if __name__ == "__main__":
    run_cleanup_job()
    schedule.every().day.at(CRON_SCHEDULE).do(run_cleanup_job)
    print(f"\nScheduling job to run every day at {CRON_SCHEDULE}. Waiting for next scheduled run...")
    while True:
        schedule.run_pending()
        time.sleep(60)