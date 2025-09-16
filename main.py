import os
import requests
import schedule
import time
from datetime import datetime, timedelta, timezone

# --- Environment Variables ---
# Tautulli Configuration
TAUTULLI_URL = os.getenv('TAUTULLI_URL')
TAUTULLI_API_KEY = os.getenv('TAUTULLI_API_KEY')
# Radarr Configuration
RADARR_URL = os.getenv('RADARR_URL')
RADARR_API_KEY = os.getenv('RADARR_API_KEY')
# Sonarr Configuration
SONARR_URL = os.getenv('SONARR_URL')
SONARR_API_KEY = os.getenv('SONARR_API_KEY')

# Script Settings
DRY_RUN = os.getenv('DRY_RUN', 'true').lower() == 'true'
DAYS_DELAY = int(os.getenv('DAYS_DELAY', 30))
RATING_THRESHOLD = float(os.getenv('RATING_THRESHOLD', 6.5))
CRON_SCHEDULE = os.getenv('CRON_SCHEDULE', '02:00')

# --- NEW: Logic Mode Settings ---
RATING_MODE = os.getenv('RATING_MODE', 'average').lower()
SERIES_WATCH_MODE = os.getenv('SERIES_WATCH_MODE', 'full').lower()

# Plex Community GraphQL API Endpoint
PLEX_GRAPHQL_URL = "https://metadata.provider.plex.tv/library/arts"


def get_plex_user_ratings(guid):
    """
    Queries the Plex Community GraphQL API to get individual user ratings for a media item.
    It requires the media's GUID (e.g., "plex://movie/5d776825c82247001e360a65").
    """
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    query = """
    query GetUserReviews($guid: String!) {
      media(guid: $guid) {
        ... on Movie {
          userReviews(first: 500) {
            nodes {
              rating
              user {
                title
              }
            }
          }
        }
        ... on Show {
          userReviews(first: 500) {
            nodes {
              rating
              user {
                title
              }
            }
          }
        }
      }
    }
    """
    variables = {'guid': guid}
    try:
        response = requests.post(
            PLEX_GRAPHQL_URL,
            headers=headers,
            json={'query': query, 'variables': variables},
            timeout=20
        )
        response.raise_for_status()
        data = response.json().get('data', {}).get('media', {})
        if not data or not data.get('userReviews') or not data['userReviews'].get('nodes'):
            return []
        
        # Ratings are returned as "X/5", so we multiply by 2 to get a score out of 10.
        ratings = [node['rating'] * 2 for node in data['userReviews']['nodes'] if node.get('rating') is not None]
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
        # First, find the movie's internal Radarr ID from its TMDB ID
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
        
        # Then, delete the movie by its Radarr ID
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
        # First, find the series' internal Sonarr ID from its TVDB ID
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

        # Then, delete the series by its Sonarr ID
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
    """
    print(f"\nProcessing {media_type.capitalize()}: {title} (GUID: {guid})")
    
    user_ratings = get_plex_user_ratings(guid)

    if user_ratings is None:
        print("Skipping due to an error fetching ratings.")
        return
    if not user_ratings:
        print("No user ratings found. Skipping.")
        return

    print(f"Found {len(user_ratings)} user rating(s): {user_ratings}")

    # --- RATING LOGIC BASED ON RATING_MODE ---
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
        # Check if any single rating is at or above the threshold
        is_kept_by_a_rating = any(r >= RATING_THRESHOLD for r in user_ratings)
        if not is_kept_by_a_rating:
            should_delete = True
            print(f"Decision: DELETE (No single rating is at or above the {RATING_THRESHOLD} threshold).")
        else:
            print(f"Decision: KEEP (At least one user rated it at or above {RATING_THRESHOLD}).")

    else: # Default behavior if mode is misconfigured
        print(f"Warning: Unknown RATING_MODE '{RATING_MODE}'. Defaulting to 'average' behavior.")
        average_rating = sum(user_ratings) / len(user_ratings)
        if average_rating < RATING_THRESHOLD:
            should_delete = True

    # --- DELETION ACTION ---
    if should_delete:
        # Extract the DB ID (TMDB for movies, TVDB for shows) from the GUID
        try:
            db_id = guid.split('/')[-1].split('?')[0]
            if 'tvdb' in guid:
                delete_sonarr_series(db_id)
            elif 'tmdb' in guid:
                delete_radarr_movie(db_id)
            else:
                print(f"Warning: Could not determine service (Sonarr/Radarr) from GUID: {guid}")
        except IndexError:
            print(f"Error: Could not parse database ID from GUID: {guid}")


def run_cleanup_job():
    """
    Main job function: Fetches watch history from Tautulli and processes eligible media.
    """
    print("\n" + "="*50)
    print(f"Starting PlexStarCleaner Job at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE DELETION'}")
    print(f"Rating Mode: {RATING_MODE.upper()}")
    print(f"Series Watch Mode: {SERIES_WATCH_MODE.upper()}")
    print(f"Days Delay: {DAYS_DELAY}")
    print(f"Rating Threshold: {RATING_THRESHOLD}")
    print("="*50 + "\n")

    if not all([TAUTULLI_URL, TAUTULLI_API_KEY]):
        print("Tautulli URL or API key is missing. Cannot proceed.")
        return

    try:
        # Fetch watch history from Tautulli
        params = {
            'apikey': TAUTULLI_API_KEY,
            'cmd': 'get_history',
            'length': 5000  # Get a large number of recent items
        }
        response = requests.get(f"{TAUTULLI_URL}/api/v2", params=params, timeout=60)
        response.raise_for_status()
        history_data = response.json()['response']['data']['data']
    except requests.exceptions.RequestException as e:
        print(f"Error fetching history from Tautulli: {e}")
        return
    except (KeyError, TypeError):
        print("Error parsing Tautulli response. Check API key and Tautulli status.")
        return

    # This dictionary will store the latest watch time for each unique media item
    media_to_process = {}
    
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=DAYS_DELAY)

    for item in history_data:
        # Ensure item is a movie or TV show and has necessary data
        if item.get('media_type') not in ['movie', 'episode'] or not item.get('guid'):
            continue
        
        last_watched_date = datetime.fromtimestamp(item.get('date', 0), timezone.utc)
        
        # Skip items watched more recently than the delay period
        if last_watched_date > cutoff_date:
            continue
        
        # --- LOGIC BASED ON SERIES_WATCH_MODE ---
        unique_id = None
        title = None
        media_type = None
        guid = None

        if item['media_type'] == 'movie' and item.get('watched_status') == 1:
            unique_id = item.get('rating_key')
            title = item.get('full_title')
            media_type = 'movie'
            guid = item.get('guid')
        elif item['media_type'] == 'episode':
            unique_id = item.get('grandparent_rating_key')
            title = item.get('grandparent_title')
            media_type = 'series'
            guid = item.get('grandparent_guid')
            
            # If mode is 'full', only consider fully watched series
            if SERIES_WATCH_MODE == 'full' and item.get('watched_status') != 1:
                continue # Skip this item, series is not fully watched

        if not all([unique_id, title, media_type, guid]):
            continue

        # We only care about the most recent watch for any given item
        if unique_id not in media_to_process or last_watched_date > media_to_process[unique_id]['last_watched']:
            media_to_process[unique_id] = {
                'title': title,
                'guid': guid,
                'media_type': media_type,
                'last_watched': last_watched_date
            }
    
    if not media_to_process:
        print("No eligible media items found to process.")
        return

    print(f"Found {len(media_to_process)} unique media items eligible for processing.")
    
    # Sort items by last watched date for logical processing order
    sorted_media = sorted(media_to_process.values(), key=lambda x: x['last_watched'])
    
    for media in sorted_media:
        process_media_item(
            title=media['title'],
            guid=media['guid'],
            media_type=media['media_type']
        )
    
    print("\n" + "="*50)
    print(f"PlexStarCleaner Job Finished at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*50)

if __name__ == "__main__":
    # Run the job once immediately on startup
    run_cleanup_job()
    
    # Then schedule it to run daily
    schedule.every().day.at(CRON_SCHEDULE).do(run_cleanup_job)
    
    print(f"\nScheduling job to run every day at {CRON_SCHEDULE}.")
    print("Waiting for next scheduled run...")
    
    while True:
        schedule.run_pending()
        time.sleep(60)

