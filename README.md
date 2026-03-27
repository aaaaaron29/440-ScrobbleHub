# ScrobbleHub - A Last.fm Listening History Tracker

A Python-based application that tracks your Last.fm listening history for data science analysis and recommendation system research. Designed to run continuously in the background, collecting scrobble data over weeks or months.

## Features

- **Automatic Sync**: Background job syncs your listening history every 30 minutes (configurable)
- **Complete History**: Fetches scrobbles, loved tracks, and artist/album metadata
- **SQLite Database**: Portable single-file storage, perfect for Jupyter notebook analysis
- **Pre-computed Metrics**: Listen counts, time patterns, streaks, and more
- **Data Export**: JSON and CSV export for pandas/ML workflows
- **Spotify-Ready Schema**: Placeholder tables for future audio features integration
- **Resilient Design**: Survives restarts, handles rate limits, deduplicates automatically

## Prerequisites

- Python 3.9+
- Last.fm account
- Last.fm API key

### Spotify Integration (Limited Access)

> **Important:** This app's Spotify integration is currently in **Development Mode**, which limits OAuth access to a maximum of 5 explicitly approved users. If you clone this repo and want to use Spotify features (playlist creation, audio previews, popularity data), your Spotify email must be manually added to the app's User Management in the [Spotify Developer Dashboard](https://developer.spotify.com/). Without this, Spotify features will fall back to text-based export.
>
> To request access, open an issue with your Spotify account email, or set up your own Spotify app credentials in a `.env` file (see [Spotify Setup](#spotify-setup) below).

## Getting Your Last.fm API Key

1. Go to [Last.fm API Account Creation](https://www.last.fm/api/account/create)
2. Log in with your Last.fm account
3. Fill in the application details:
   - **Application name**: "Personal Listening Tracker" (or anything you like)
   - **Application description**: "Personal music listening analytics"
   - **Callback URL**: Leave blank (not needed)
   - **Application homepage**: Leave blank or use any URL
4. Click "Submit"
5. You'll receive an **API Key** - save this! (You don't need the shared secret)

## Installation

### 1. Clone or Download

```bash
cd your-projects-folder
git clone <repo-url> lastfm-tracker
# OR download and extract the ZIP
```

### 2. Create Virtual Environment

```bash
cd lastfm-tracker
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the Application

```bash
python app.py
```

The application will start at `http://localhost:5000`

### 5. Configure

1. Open `http://localhost:5000` in your browser
2. Click "Get Started" or "Settings"
3. Enter your Last.fm username and API key
4. Click "Save & Connect"
5. The initial sync will start automatically, fetching your recent listening history

## Usage

### Dashboard

The web dashboard shows:
- **Stats**: Total scrobbles, unique artists/tracks, today's plays, current streak
- **Recent Scrobbles**: Scrollable list with album art
- **Top Artists/Tracks**: Filterable by time period (week/month/year/all)
- **Export Options**: Download your data as JSON or CSV

### Manual Sync

Click "Sync Now" to trigger an immediate sync outside the regular schedule.

### Export for Data Science

Use the export buttons or API endpoints directly:

```python
import pandas as pd
import requests

# Get all scrobbles as JSON
response = requests.get('http://localhost:5000/api/export?type=scrobbles&format=json')
scrobbles = pd.DataFrame(response.json()['data'])

# Or download CSV directly
scrobbles = pd.read_csv('http://localhost:5000/api/export?type=scrobbles&format=csv')
```

### Direct Database Access

For Jupyter notebooks, connect directly to SQLite:

```python
import sqlite3
import pandas as pd

conn = sqlite3.connect('data/lastfm_tracker.db')

# Get all scrobbles with track/artist info
query = """
    SELECT
        s.listened_at,
        t.name as track,
        a.name as artist,
        al.name as album,
        t.spotify_id
    FROM scrobbles s
    JOIN tracks t ON s.track_id = t.id
    JOIN artists a ON t.artist_id = a.id
    LEFT JOIN albums al ON t.album_id = al.id
    ORDER BY s.listened_at DESC
"""
df = pd.read_sql(query, conn)
```

## Configuration

### Sync Interval

Change the sync interval in the web interface (Settings), or set it programmatically:

```python
# In config.py
DEFAULT_SYNC_INTERVAL_MINUTES = 30  # Change to desired interval
```

### Initial Backfill

On first sync, the app fetches up to 50 pages (~10,000 scrobbles) of history. To fetch more:

```python
# In config.py
INITIAL_BACKFILL_PAGES = 100  # Fetch ~20,000 scrobbles initially
```

## Spotify Setup

To use Spotify features with your own credentials:

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and create a new app
2. Under app settings, check **Web API** and add `http://127.0.0.1:5000/api/spotify/callback` as a Redirect URI
3. Create a `.env` file in the project root:

```env
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
```

4. Add your Spotify email to the app's **User Management** in the dashboard (required for Development mode)
5. Restart the app and click "Connect Spotify" on the Discover page

**Note:** Spotify Development mode only allows up to 5 approved users. Use `127.0.0.1` (not `localhost`) for the redirect URI.

## Running as a Background Service (Windows)

### Option 1: Windows Task Scheduler (Recommended)

1. Open Task Scheduler (`taskschd.msc`)
2. Click "Create Task"
3. **General tab**:
   - Name: "Last.fm Tracker"
   - Check "Run whether user is logged on or not"
   - Check "Run with highest privileges"
4. **Triggers tab**:
   - New → Begin task: At startup
   - Check "Delay task for: 30 seconds"
5. **Actions tab**:
   - New → Action: Start a program
   - Program: `C:\path\to\lastfm-tracker\venv\Scripts\pythonw.exe`
   - Arguments: `run_service.py`
   - Start in: `C:\path\to\lastfm-tracker`
6. **Conditions tab**:
   - Uncheck "Start only if on AC power"
7. Click OK and enter your Windows password

### Option 2: NSSM (Non-Sucking Service Manager)

1. Download [NSSM](https://nssm.cc/download)
2. Run in admin command prompt:

```batch
nssm install LastfmTracker
```

3. Configure:
   - Path: `C:\path\to\lastfm-tracker\venv\Scripts\python.exe`
   - Startup directory: `C:\path\to\lastfm-tracker`
   - Arguments: `run_service.py`

4. Start the service:

```batch
nssm start LastfmTracker
```

### Option 3: Startup Folder (Simple)

1. Create a batch file `start_tracker.bat`:

```batch
@echo off
cd /d C:\path\to\lastfm-tracker
call venv\Scripts\activate
pythonw run_service.py
```

2. Create a shortcut to this batch file
3. Press `Win+R`, type `shell:startup`, and press Enter
4. Move the shortcut to this folder

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/config` | GET/POST | Get or save configuration |
| `/api/sync` | POST | Trigger manual sync |
| `/api/sync/status` | GET | Get sync status |
| `/api/scrobbles` | GET | Get scrobbles (paginated) |
| `/api/stats` | GET | Get statistics |
| `/api/top/artists` | GET | Get top artists |
| `/api/top/tracks` | GET | Get top tracks |
| `/api/loved` | GET | Get loved tracks |
| `/api/metrics/listening-patterns` | GET | Get time patterns |
| `/api/metrics/streaks` | GET | Get streak info |
| `/api/export` | GET | Export data |

See [claude.md](claude.md) for full API documentation.

## Database Schema

The database is designed for ML/analytics with:
- Normalized tables (artists, albums, tracks, scrobbles)
- Pre-indexed for common queries
- Placeholder columns for Spotify audio features
- Multi-user ready (single-user implemented)

See [claude.md](claude.md) for complete schema documentation.

## Future Roadmap

### Phase 1: Spotify Audio Features (Planned)

Add Spotify API integration to enrich tracks with audio features:
- Danceability, energy, valence (mood)
- Tempo, loudness, key
- Acousticness, instrumentalness

This will enable recommendation system features like:
- Mood-based playlists
- Similar track discovery
- Listening pattern analysis

### Phase 2: Multi-User Support

- User authentication
- Separate data per user
- Shared insights/comparisons

## Troubleshooting

### "Invalid API key" error
- Verify your API key at [last.fm/api](https://www.last.fm/api)
- Make sure there are no extra spaces

### Sync not running
- Check if the application is still running
- Look for errors in the console/logs
- Try a manual sync from the dashboard

### Database locked errors
- Only one instance should run at a time
- Close other Python processes accessing the database

### Missing scrobbles
- Last.fm API may have delays
- Wait a few minutes and sync again
- Check your Last.fm profile to confirm scrobbles are registered

## Project Structure

```
lastfm-tracker/
├── app.py              # Flask application & API endpoints
├── models.py           # SQLAlchemy database models
├── lastfm_client.py    # Last.fm API wrapper
├── sync_service.py     # Background sync logic
├── metrics.py          # Analytics calculations
├── config.py           # Configuration management
├── run_service.py      # Windows service runner
├── requirements.txt    # Python dependencies
├── README.md           # This file
├── claude.md           # Architecture documentation
├── data/               # SQLite database (created on first run)
├── static/
│   ├── css/style.css   # Dashboard styles
│   └── js/app.js       # Dashboard JavaScript
└── templates/
    └── index.html      # Dashboard template
```

## License

MIT License - Use freely for personal projects.

## Contributing

This is a personal data science project, but suggestions and improvements are welcome!
