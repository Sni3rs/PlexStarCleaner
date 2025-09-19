![logo.png](logo.png)

# PlexStarCleaner

A scheduled Docker container that automatically cleans up watched Plex media from Sonarr and Radarr based on user ratings. It features a two-stage process with pre-deletion warnings and flexible logic for multi-user households.

## How It Works

The script runs on a daily schedule and operates in two distinct phases:

1.  **Warning Phase**: The script scans the Tautulli watch history for media that has not been watched for a specified number of days (`DAYS_DELAY_WARNING`). It evaluates all user ratings based on the configured `RATING_MODE`. If the media is eligible for deletion, a warning notification is sent via a Tautulli agent, listing the media and the users who watched it.
2.  **Deletion Phase**: After a further delay (`DAYS_DELAY_DELETION`), the script re-evaluates the media. If it still meets the criteria, the script instructs Radarr/Sonarr to delete the media and its associated files, then sends a final report.

This gives users a grace period (e.g., one week) to re-rate a movie or show if they want to keep it.

## Features

-   **Two-Stage Deletion**: Warns users a week (configurable) before deleting media.
-   **Flexible Rating Logic**: Choose between deleting based on the *average* rating or keeping media if *any single user* rated it highly.
-   **Advanced Series Handling**: Decide whether to delete a series only when it's fully watched or as soon as a user gets "bored" and gives it a low rating.
-   **Tautulli Notifications**: Integrates with Tautulli's notification agents for detailed reports.
-   **Safe Dry Run Mode**: A `DRY_RUN` mode logs all actions it *would* take without sending notifications or deleting files.
-   **Library Exclusion**: Ignore specific Plex libraries.

---

## Configuration

This application is configured entirely through Docker environment variables.

### **Required Setup**
| Variable | Description | Example Value |
| :--- | :--- | :--- |
| `PLEX_URL` | **(Required)** Full URL to your Plex server. | `http://192.168.1.50:32400` |
| `PLEX_TOKEN` | **(Required)** Your X-Plex-Token. [How to find it](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/). | `YourSecretPlexToken` |
| `TAUTULLI_URL` | **(Required)** Full URL to your Tautulli server. | `http://192.168.1.50:8181` |
| `TAUTULLI_API_KEY` | **(Required)** Your API key from Tautulli Settings > Web Interface > API. | `YourTautulliApiKey` |

### **Deletion Logic**
| Variable | Description | Default |
| :--- | :--- | :--- |
| `DRY_RUN` | `true` for testing (no deletions/notifications), `false` to enable actions. **Start with `true`!** | `true` |
| `DAYS_DELAY_WARNING` | Days since last watch to trigger a deletion *warning*. | `30` |
| `DAYS_DELAY_DELETION`| Days since last watch to perform the *deletion*. Must be greater than warning delay. | `37` |
| `RATING_THRESHOLD` | The rating value used for comparison (e.g., 6.5). | `6.5` |
| `RATING_MODE` | Sets the rating logic. `average`: deletes if the average of all user ratings is below the threshold. `any_high`: keeps the item if *any single user* has rated it at or above the threshold. | `average` |
| `SERIES_WATCH_MODE` | Sets the condition for series. `full`: processes a series only after it's fully watched. `bored`: processes a series as soon as any episode has been watched and rated. | `full` |
| `EXCLUDED_LIBRARIES` | (Optional) Comma-separated list of Plex library names to ignore. Case-insensitive. | |

### **Notifications**
| Variable | Description | Example Value |
| :--- | :--- | :--- |
| `TAUTULLI_NOTIFIER_ID` | (Optional) The ID of the Tautulli Notification Agent to send reports to. See setup guide below. | `4` |

### **Arr Configuration**
| Variable | Description | Example Value |
| :--- | :--- | :--- |
| `RADARR_URL` | (Optional) Full URL to your Radarr server. | `http://192.168.1.50:7878` |
| `RADARR_API_KEY` | (Optional) Your API key from Radarr Settings > General. | `YourRadarrApiKey` |
| `SONARR_URL` | (Optional) Full URL to your Sonarr server. | `http://192.168.1.50:8989` |
| `SONARR_API_KEY` | (Optional) Your API key from Sonarr Settings > General. | `YourSonarrApiKey` |

### **General Settings**
| Variable | Description | Default |
| :--- | :--- | :--- |
| `TZ` | Your local timezone. [List of TZ database time zones](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones). | `Europe/Zurich` |
| `CRON_SCHEDULE` | Time of day (HH:MM) in 24-hour format to run the daily job. | `02:00` |

---

## Setting Up Tautulli Notifications

1.  Open your Tautulli web interface.
2.  Go to **Settings > Notification Agents**.
3.  Click on an existing agent or create a new one. **Important**: Ensure "Enable HTML Support" is checked if your agent supports it (like Telegram).
4.  Look at the URL in your browser's address bar: `.../edit_notifier?agent_id=XX`.
5.  The `XX` is your **Notifier ID**. Use this value for the `TAUTULLI_NOTIFIER_ID` variable.