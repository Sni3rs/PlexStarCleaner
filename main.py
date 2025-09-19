import os
import requests
import schedule
import time
from datetime import datetime, timedelta, timezone

# --- Environment Variables ---
# Service Configurations
TAUTULLI_URL = os.getenv('TAUTULLI_URL')
TAUTULLI_API_KEY = os.getenv('TAUTULLI_API_KEY')
RADARR_URL = os.getenv('RADARR_URL')
RADARR_API_KEY = os.getenv('RADARR_API_KEY')
SONARR_URL = os.getenv('SONARR_URL')
SONARR_API_KEY = os.getenv('SONARR_API_KEY')
PLEX_TOKEN = os.getenv('PLEX_TOKEN')

# Script Settings
DRY_RUN = os.getenv('DRY_RUN', 'true').lower() == 'true'
DAYS_DELAY = int(os.getenv('DAYS_DELAY', 30))
RATING_THRESHOLD = float(os.getenv('RATING_THRESHOLD', 6.5))
CRON_SCHEDULE = os.getenv('CRON_SCHEDULE', '02:00')
EXCLUDED_LIBRARIES_STR = os.getenv('EXCLUDED_LIBRARIES', '')
EXCLUDED_LIBRARIES = [lib.strip().lower() for lib in EXCLUDED_LIBRARIES_STR.split(',') if lib.strip()]


# Logic Mode Settings
RATING_MODE = os.getenv('RATING_MODE', 'average').lower()
SERIES_WATCH_MODE = os.getenv('SERIES_WATCH_MODE', 'full').lower()

# Plex Community GraphQL API Endpoint
PLEX_GRAPHQL_URL = "https://metadata.provider.plex.tv/graphql"


def get_plex_user_ratings(guid):
    """
    Queries the Plex Community GraphQL API to get individual user ratings for a media item.
    """
    if not PLEX_TOKEN:
        print("Error: PLEX_TOKEN is not set. Cannot query the Plex API.")
        return None

    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-Plex-Token': PLEX_TOKEN,
    }

    # MODIFIED: Using the new, more direct ActivityFeed query
    query = """
    query GetActivityFeed($metadataID: ID!) {
      activityFeed(
        first: 100
        types: [RATING, WATCH_RATING]
        includeDescendants: true
        metadataID: $metadataID
      ) {
        nodes {
          ... on ActivityRating {
            rating
          }
          ... on ActivityWatchRating {
            rating
          }
        }
      }
    }
    """
    try:
        # MODIFIED: Extract the metadata ID from the full GUID
        # e.g., "plex://movie/5d776c7f594b2b001e6f534d" -> "5d776c7f594b2b001e6f534d"
        metadata_id = guid.split('/')[-1]
    except IndexError:
        print(f"Error: Could not parse metadata ID from GUID: {guid}")
        return None
    
    # MODIFIED: Updated variables to match the new query
    variables = {'metadataID': metadata_id}
    
    try:
        response = requests.post(
            PLEX_GRAPHQL_URL,
            headers=headers,
            json={'query': query, 'variables': variables},
            timeout=20
        )
        response.raise_for_status()
        data = response.json().get('data', {}).get('activityFeed', {})
        
        if not data or not data.get('nodes'):
            return []
        
        # MODIFIED: Rating is now directly on a 10-point scale, no multiplication needed
        ratings = [node['rating'] for node in data['nodes'] if node.get('rating') is not None]
        return ratings
    except requests.exceptions.RequestException as e:
        print(f"Error querying Plex GQL API for GUID {guid}: {e}")
        return None

def delete_radarr_movie(tmdb_id):
    """
    Instructs Radarr to delete a movie and all its files by its TMDB ID.
    """
    if not RADARR_URL or not RADARR_API_KEY:
        print("Radarr URL or API Key is not configured. Skipping movie deletion.")
        return
        
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

def process_media_item(title, guid, media_type):
    """
    Processes a single media item: fetches ratings, evaluates them, and triggers deletion if criteria are met.
    Returns the media type ('movie' or 'series') if flagged for deletion, otherwise None.
    """
    print(f"\nProcessing {media_type.capitalize()}: {title} (GUID: {guid})")
    
    # GUID from Tautulli can sometimes contain agent info, which the GQL API dislikes.
    # We clean it to the base "plex://..." format.
    if '?' in guid:
        guid = guid.split('?')[0]
        
    user_ratings = get_plex_user_ratings(guid)

    if user_ratings is None:
        print("Skipping due to an error fetching ratings.")
        return None
    if not user_ratings:
        print("No user ratings found. Skipping.")
        return None

    print(f"Found {len(user_ratings)} user rating(s): {user_ratings}")

    should_delete = False
    if RATING_MODE == 'average':
        average_rating = sum(user_ratings) / len(user_ratings)
        print(f"Average user rating is {average_rating:.2f}. Threshold is {RATING_THRESHOLD}.")
        if average_rating < RATING_THRESHOLD:
            should_delete = True
            print("Decision: DELETE (Average rating is below threshold).")
        else:
            print("Decision: KEEP (Average rating is at or above threshold).")
    
    elif RATING_MODE == 'any_high':
        is_kept_by_a_rating = any(r >= RATING_THRESHOLD for r in user_ratings)
        if not is_kept_by_a_rating:
            should_delete = True
            print(f"Decision: DELETE (No single rating is at or above the {RATING_THRESHOLD} threshold).")
        else:
            print(f"Decision: KEEP (At least one user rated it at or above {RATING_THRESHOLD}).")
    else:
        print(f"Warning: Unknown RATING_MODE '{RATING_MODE}'. Defaulting to 'average' behavior.")
        average_rating = sum(user_ratings) / len(user_ratings)
        if average_rating < RATING_THRESHOLD:
            should_delete = True

    if should_delete:
        try:
            # For deletion, we need the DB-specific ID (e.g., tmdb12345)
            # which is different from the Plex metadata ID. Tautulli's GUID is best.
            if 'tvdb' in guid:
                db_id = guid.split('//')[-1]
                delete_sonarr_series(db_id)
            elif 'tmdb' in guid:
                db_id = guid.split('//')[-1]
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

    required_vars = ['TAUTULLI_URL', 'TAUTULLI_API_KEY', 'PLEX_TOKEN']
    missing_vars = [var for var in required_vars if not globals().get(var)]
    if missing_vars:
        print(f"Error: Missing required environment variables: {', '.join(missing_vars)}. Aborting job.")
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
        
        unique_id, title, media_type, guid = None, None, None, None

        if item['media_type'] == 'movie' and item.get('watched_status') == 1:
            unique_id, title, media_type, guid = item.get('rating_key'), item.get('full_title'), 'movie', item.get('guid')
        elif item['media_type'] == 'episode':
            unique_id, title, media_type, guid = item.get('grandparent_rating_key'), item.get('grandparent_title'), 'series', item.get('grandparent_guid')
            if SERIES_WATCH_MODE == 'full' and item.get('watched_status') != 1:
                continue

        if not all([unique_id, title, media_type, guid]):
            continue

        if unique_id not in media_to_process or last_watched_date > media_to_process[unique_id]['last_watched']:
            media_to_process[unique_id] = {'title': title, 'guid': guid, 'media_type': media_type, 'last_watched': last_watched_date}
    
    if not media_to_process:
        print("No eligible media items found to process.")
    else:
        print(f"Found {len(media_to_process)} unique media items eligible for processing.")
        sorted_media = sorted(media_to_process.values(), key=lambda x: x['last_watched'])
        total_processed = len(sorted_media)
        
        for media in sorted_media:
            result = process_media_item(title=media['title'], guid=media['guid'], media_type=media['media_type'])
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

