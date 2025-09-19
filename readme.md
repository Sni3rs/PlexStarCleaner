![logo.png](logo.png)

# PlexStarCleaner

A scheduled Docker container that automatically cleans up watched Plex media from Sonarr and Radarr based on your personal rating. It features a two-stage process with pre-deletion warnings.

## How It Works

The script runs on a daily schedule and operates in two distinct phases:

1.  **Warning Phase**: The script scans the Tautulli watch history for media that has not been watched for a specified number of days (`DAYS_DELAY_WARNING`). If an item's personal rating is below your threshold, a warning notification is sent via a Tautulli agent, listing the media and the users who watched it.
2.  **Deletion Phase**: After a further delay (`DAYS_DELAY_DELETION`), the script re-evaluates the media. If it still meets the criteria, the script instructs Radarr/Sonarr to delete the media and its associated files.

This gives users a grace period (e.g., one week) to re-rate a movie or show if they want to keep it.

## Features

-   **Two-Stage Deletion**: Warns users a week (configurable) before deleting media.
-   **User-Centric**: Deletes based on *your* personal Plex rating, not community scores.
-   **Tautulli Notifications**: Integrates with Tautulli's notification agents for detailed reports on warnings and deletions.
-   **Safe Dry Run Mode**: A `DRY_RUN` mode logs all actions it *would* take without sending notifications or deleting any files. Perfect for initial setup.
-   **Library Exclusion**: Ignore specific Plex libraries (e.g., for kids' shows, documentaries).
-   **Lightweight**: Built on a slim Python image.

---

## Configuration

This application is configured entirely through Docker environment variables.

### Critical Configuration
| Variable | Description | Example Value |
| :--- | :--- | :--- |
| **`PLEX_URL`** | **(Required)** Full URL to your Plex server. | `http://192.168.1.50:32400` |
| **`PLEX_TOKEN`** | **(Required)** Your X-Plex-Token. [How to find it](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/). | `YourSecretPlexToken` |
| **`TAUTULLI_URL`** | **(Required)** Full URL to your Tautulli server. | `http://192.168.1.50:8181` |
| **`TAUTULLI_API_KEY`** | **(Required)** Your API key from Tautulli Settings > Web Interface > API. | `YourTautulliApiKey` |

### Deletion Logic
| Variable | Description | Default |
| :--- | :--- | :--- |
| `DRY_RUN` | **`true` for testing (no deletion/notifications)**, `false` to enable. **Start with `true`!** | `true` |
| `DAYS_DELAY_WARNING` | Days since last watched to trigger a deletion *warning*. | `30` |
| `DAYS_DELAY_DELETION` | Days since last watched to perform the *deletion*. Must be greater than warning delay. | `37` |
| `RATING_THRESHOLD` | Media with a personal rating *below* this value will be deleted (e.g., `6.5` targets 6.4 and lower). | `6.5` |
| `EXCLUDED_LIBRARIES` | Comma-separated list of Plex library names to ignore. Case-insensitive. | `Kids,Documentaries` |

### Notifications (Optional but Recommended)
| Variable | Description | Example Value |
| :--- | :--- | :--- |
| `TAUTULLI_NOTIFIER_ID` | The ID of the Tautulli Notification Agent to send reports to. See setup guide below. | `14` |

### Service Configuration (Radarr/Sonarr)
| Variable | Description | Example Value |
| :--- | :--- | :--- |
| `RADARR_URL` | Full URL to your Radarr server. | `http://192.168.1.50:7878` |
| `RADARR_API_KEY` | Your API key from Radarr Settings > General. | `YourRadarrApiKey` |
| `SONARR_URL` | Full URL to your Sonarr server. | `http://192.168.1.50:8989` |
| `SONARR_API_KEY` | Your API key from Sonarr Settings > General. | `YourSonarrApiKey` |

### General Settings
| Variable | Description | Default |
| :--- | :--- | :--- |
| `TZ` | Your local timezone. [List of TZ database time zones](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones). | `Europe/Zurich` |
| `CRON_SCHEDULE` | Time of day (HH:MM) to run the daily job. | `02:00` |

---

## Setting Up Tautulli Notifications

To receive reports, you must provide the ID of a Tautulli Notification Agent.

1.  Open your Tautulli web interface.
2.  Go to **Settings > Notification Agents**.
3.  Click on an existing agent you want to use (e.g., Discord).
4.  Look at the URL in your browser's address bar. It will look like `.../edit_notifier?agent_id=XX`.
5.  The `XX` is your **Notifier ID**. Use this value for the `TAUTULLI_NOTIFIER_ID` environment variable.