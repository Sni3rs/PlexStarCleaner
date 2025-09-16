# PlexStarCleaner

A scheduled Docker container that automatically cleans up watched Plex media from Sonarr and Radarr based on community user ratings.

## How It Works

The script runs on a schedule and performs the following actions:

1.  Connects to the **Tautulli API** to get the complete watch history.
2.  Identifies unique, fully watched media items that haven't been touched in a configurable number of days.
3.  For each item, it fetches individual user ratings from the **Plex Community GraphQL API**.
4.  If the average user rating is below a configurable threshold, it connects to the **Radarr/Sonarr API** to delete the media and its associated files.

## Features

-   **Immediate & Scheduled Execution**: Runs once on container start-up and then daily on a schedule.
-   **Safe Dry Run Mode**: A `DRY_RUN` mode logs all actions it *would* take without deleting any files, perfect for initial setup and testing.
-   **Highly Configurable**: All parameters are controlled via environment variables.
-   **Rating Based**: Deletes media based on the average of actual user ratings, not the generic public score.
-   **Lightweight**: Built on a slim Python image for a small footprint.

## Configuration

This application is configured entirely through Docker environment variables.

| Variable | Description | Example Value |
| :--- | :--- | :--- |
| `TZ` | Your local timezone. [List of TZ database time zones](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones). | `Europe/Zurich` |
| `DRY_RUN` | **`true` for testing (no deletion)**, `false` to enable deletion. **Start with `true`!** | `true` |
| `DAYS_DELAY` | Days since last watched before media is considered for deletion. | `30` |
| `RATING_THRESHOLD` | Media with an average user rating *below* this is deleted (e.g., 6.5 deletes 6.4 and lower). | `6.5` |
| `CRON_SCHEDULE` | Time of day (HH:MM) in 24-hour format to run the daily job. | `02:00` |
| `TAUTULLI_URL` | Full URL to your Tautulli server. Use the container's IP if on a custom docker network. | `http://192.168.1.50:8181` |
| `TAUTULLI_API_KEY` | Your API key from Tautulli Settings > Web Interface > API. | `YourTautulliApiKey` |
| `RADARR_URL` | Full URL to your Radarr server. | `http://192.168.1.50:7878` |
| `RADARR_API_KEY` | Your API key from Radarr Settings > General > Security. | `YourRadarrApiKey` |
| `SONARR_URL` | Full URL to your Sonarr server. | `http://192.168.1.50:8989` |
| `SONARR_API_KEY` | Your API key from Sonarr Settings > General > Security. | `YourSonarrApiKey` |

## Deployment

This application is designed to be deployed as a Docker container. See the official guide for instructions on deploying to systems like Unraid.
