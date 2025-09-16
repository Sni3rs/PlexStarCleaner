import os
import requests
import schedule
import time
import logging
from datetime import datetime, timedelta, timezone

# --- Configuration ---
# Set up basic logging to print to stdout
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load configuration from environment variables
# Service URLs and API Keys
TAUTULLI_URL = os.getenv('TAUTULLI_URL')
TAUTULLI_API_KEY = os.getenv('TAUTULLI_API_KEY')
RADARR_URL = os.getenv('RADARR_URL')
RADARR_API_KEY = os.getenv('RADARR_API_KEY')
SONARR_URL = os.getenv('SONARR_URL')
SONARR_API_KEY = os.getenv('SONARR_API_KEY')

# Script Logic Parameters
DRY_RUN = os.getenv('DRY_RUN', 'true').lower() == 'true'
DAYS_DELAY = int(os.getenv('DAYS_DELAY', '30'))
RATING_THRESHOLD = float(os.getenv('RATING_THRESHOLD', '6.5'))
CRON_SCHEDULE = os.getenv('CRON_SCHEDULE', '02:00')

# Plex Community GraphQL API Endpoint
PLEX_GRAPHQL_URL = "https://rating.community.plex.tv/graphql"

# --- Helper Functions for API Calls ---

def get_tautulli_history():
    """Fetches the entire watch history from Tautulli."""
    try:
        logging.info("Fetching watch history from Tautulli...")
        params = {
            'apikey': TAUTULLI_API_KEY,
            'cmd': 'get_history',
            'length': 10000 # Fetch a large number of items to approximate 'all'
        }
        response = requests.get(f"{TAUTULLI_URL}/api/v2", params=params, timeout=30)
        response.raise_for_status()
        data = response.json().get('response', {}).get('data', {}).get('data', [])
        logging.info(f"Successfully fetched {len(data)} watch history items.")
        return data
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching Tautulli history: {e}")
        return None

def is_series_fully_watched(series_rating_key):
    """Checks if a series is fully unwatched using Tautulli's media info."""
    try:
        params = {
            'apikey': TAUTULLI_API_KEY,
            'cmd': 'get_media_info',
            'rating_key': series_rating_key
        }
        response = requests.get(f"{TAUTULLI_URL}/api/v2", params=params, timeout=10)
        response.raise_for_status()
        media_info = response.json().get('response', {}).get('data', {})
        unwatched_count = int(media_info.get('unwatched_leaf_count', 1))
        return unwatched_count == 0
    except requests.exceptions.RequestException as e:
        logging.error(f"Error checking series status for rating_key {series_rating_key}: {e}")
        return False


def get_plex_community_rating(guid):
    """Fetches individual user ratings from the Plex Community GraphQL API and calculates the average."""
    query = """
    query ($guid: String!) {
      ratings(guid: $guid) {
        rating
      }
    }
    """
    variables = {'guid': guid}
    try:
        response = requests.post(PLEX_GRAPHQL_URL, json={'query': query, 'variables': variables}, timeout=15)
        response.raise_for_status()
        data = response.json()
        ratings_data = data.get('data', {}).get('ratings', [])
        if not ratings_data:
            return None # No ratings found
        
        ratings = [r['rating'] for r in ratings_data if r.get('rating') is not None]
        if not ratings:
            return None

        average_rating = sum(ratings) / len(ratings)
        return average_rating
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching rating for GUID {guid}: {e}")
        return None
    except (KeyError, TypeError) as e:
        logging.error(f"Error parsing rating response for GUID {guid}: {e}")
        return None

def find_and_delete_movie(guid):
    """Finds a movie in Radarr by GUID and deletes it."""
    # Radarr doesn't directly search by GUID, so we find it by IMDB or TMDB ID from the GUID
    imdb_id = None
    if 'imdb' in guid:
        imdb_id = guid.split('//')[1].split('?')[0]
    
    if not imdb_id:
        logging.warning(f"Could not parse IMDB ID from GUID: {guid}. Skipping movie.")
        return

    try:
        # 1. Find the movie in Radarr
        logging.info(f"Searching Radarr for movie with IMDB ID: {imdb_id}")
        params = {'apikey': RADARR_API_KEY}
        response = requests.get(f"{RADARR_URL}/api/v3/movie", params=params, timeout=30)
        response.raise_for_status()
        movies = response.json()
        
        movie_id_to_delete = None
        for movie in movies:
            if movie.get('imdbId') == imdb_id:
                movie_id_to_delete = movie.get('id')
                logging.info(f"Found movie '{movie.get('title')}' in Radarr (ID: {movie_id_to_delete}).")
                break
        
        if not movie_id_to_delete:
            logging.warning(f"Movie with IMDB ID {imdb_id} not found in Radarr.")
            return

        # 2. Delete the movie
        if DRY_RUN:
            logging.info(f"[DRY RUN] Would delete movie '{movie.get('title')}' from Radarr.")
        else:
            logging.info(f"Deleting movie '{movie.get('title')}' from Radarr...")
            delete_url = f"{RADARR_URL}/api/v3/movie/{movie_id_to_delete}"
            delete_params = {
                'apikey': RADARR_API_KEY,
                'deleteFiles': 'true',
                'addImportExclusion': 'true'
            }
            del_response = requests.delete(delete_url, params=delete_params, timeout=60)
            del_response.raise_for_status()
            logging.info(f"Successfully deleted movie '{movie.get('title')}' from Radarr.")

    except requests.exceptions.RequestException as e:
        logging.error(f"Radarr API error for movie with IMDB ID {imdb_id}: {e}")

def find_and_delete_series(guid):
    """Finds a series in Sonarr by GUID and deletes it."""
    tvdb_id = None
    if 'tvdb' in guid:
        tvdb_id = guid.split('//')[1].split('?')[0]

    if not tvdb_id:
        logging.warning(f"Could not parse TVDB ID from GUID: {guid}. Skipping series.")
        return

    try:
        # 1. Find the series in Sonarr
        logging.info(f"Searching Sonarr for series with TVDB ID: {tvdb_id}")
        params = {'apikey': SONARR_API_KEY, 'tvdbId': tvdb_id}
        response = requests.get(f"{SONARR_URL}/api/v3/series", params=params, timeout=30)
        response.raise_for_status()
        series_list = response.json()
        
        if not series_list:
            logging.warning(f"Series with TVDB ID {tvdb_id} not found in Sonarr.")
            return

        series_to_delete = series_list[0]
        series_id = series_to_delete.get('id')
        series_title = series_to_delete.get('title')
        logging.info(f"Found series '{series_title}' in Sonarr (ID: {series_id}).")

        # 2. Delete the series
        if DRY_RUN:
            logging.info(f"[DRY RUN] Would delete series '{series_title}' from Sonarr.")
        else:
            logging.info(f"Deleting series '{series_title}' from Sonarr...")
            delete_url = f"{SONARR_URL}/api/v3/series/{series_id}"
            delete_params = {
                'apikey': SONARR_API_KEY,
                'deleteFiles': 'true',
                'addImportExclusion': 'true'
            }
            del_response = requests.delete(delete_url, params=delete_params, timeout=120)
            del_response.raise_for_status()
            logging.info(f"Successfully deleted series '{series_title}' from Sonarr.")

    except requests.exceptions.RequestException as e:
        logging.error(f"Sonarr API error for series with TVDB ID {tvdb_id}: {e}")

# --- Main Job Logic ---

def cleanup_job():
    """The main job that performs the cleanup logic."""
    logging.info("=======================================")
    logging.info("Starting PlexStarCleaner cleanup job...")
    logging.info(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE DELETION'}")
    logging.info(f"Days Delay: {DAYS_DELAY}")
    logging.info(f"Rating Threshold: {RATING_THRESHOLD}")
    logging.info("=======================================")

    history = get_tautulli_history()
    if history is None:
        logging.error("Could not fetch Tautulli history. Aborting job.")
        return

    # A dictionary to hold the latest watch entry for each unique media item
    # Key: rating_key for movies, grandparent_rating_key for series
    # Value: The full history entry
    latest_watched_media = {}

    for item in history:
        # We only care about fully watched items
        if item.get('watched_status') == 1:
            key = None
            if item.get('media_type') == 'movie':
                key = item.get('rating_key')
            elif item.get('media_type') == 'episode':
                key = item.get('grandparent_rating_key')
            
            if key:
                # If this media is already in our list, check if this watch is more recent
                if key not in latest_watched_media or item.get('date') > latest_watched_media[key].get('date'):
                    latest_watched_media[key] = item
    
    logging.info(f"Found {len(latest_watched_media)} unique watched media items to process.")
    
    # Cutoff date for deletion consideration
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=DAYS_DELAY)

    for key, item in latest_watched_media.items():
        media_title = item.get('title') if item.get('media_type') == 'movie' else item.get('grandparent_title')
        last_played_ts = item.get('date')
        last_played_date = datetime.fromtimestamp(last_played_ts, tz=timezone.utc)

        logging.info(f"Processing '{media_title}' (Last Watched: {last_played_date.strftime('%Y-%m-%d')})")

        # 1. Check if the media was watched long enough ago
        if last_played_date > cutoff_date:
            logging.info(f"-> Skipping '{media_title}', watched too recently.")
            continue
        
        # 2. For series, confirm it's fully watched
        if item.get('media_type') == 'episode':
            series_key = item.get('grandparent_rating_key')
            if not is_series_fully_watched(series_key):
                logging.info(f"-> Skipping series '{media_title}', not all episodes are watched.")
                continue
            logging.info(f"-> Series '{media_title}' is confirmed fully watched.")

        # 3. Get community rating
        guid = item.get('guid')
        if not guid:
            logging.warning(f"-> Skipping '{media_title}', no GUID found.")
            continue
            
        avg_rating = get_plex_community_rating(guid)
        if avg_rating is None:
            logging.info(f"-> Skipping '{media_title}', no user ratings found.")
            continue
        
        logging.info(f"-> Average community rating for '{media_title}' is {avg_rating:.2f}.")

        # 4. Compare rating and take action
        if avg_rating < RATING_THRESHOLD:
            logging.info(f"-> Rating ({avg_rating:.2f}) is below threshold ({RATING_THRESHOLD}). Queuing for deletion.")
            if item.get('media_type') == 'movie':
                find_and_delete_movie(guid)
            elif item.get('media_type') == 'episode':
                find_and_delete_series(guid)
        else:
            logging.info(f"-> Rating ({avg_rating:.2f}) is above threshold. Keeping item.")

    logging.info("Cleanup job finished.")
    logging.info("=======================================\n")


# --- Scheduler ---
def main():
    """Main function to run the script."""
    # Check for essential environment variables on startup
    required_vars = ['TAUTULLI_URL', 'TAUTULLI_API_KEY', 'RADARR_URL', 'RADARR_API_KEY', 'SONARR_URL', 'SONARR_API_KEY']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        logging.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        exit(1)

    logging.info("Application starting up...")
    
    # Run the job once immediately on startup
    cleanup_job()

    # Schedule the job to run daily at the specified time
    logging.info(f"Scheduling job to run every day at {CRON_SCHEDULE}.")
    schedule.every().day.at(CRON_SCHEDULE).do(cleanup_job)

    # Main loop to run the scheduler
    while True:
        schedule.run_pending()
        time.sleep(60) # Check every minute

if __name__ == "__main__":
    main()
