"""
Last.fm API client for fetching user listening data.
"""

import time
import logging
from datetime import datetime
from typing import Optional, Generator
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class LastFMError(Exception):
    """Base exception for Last.fm API errors."""
    pass


class LastFMAuthError(LastFMError):
    """Authentication/API key error."""
    pass


class LastFMRateLimitError(LastFMError):
    """Rate limit exceeded."""
    pass


class LastFMClient:
    """Client for interacting with Last.fm API."""

    BASE_URL = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self, api_key: str, username: str, calls_per_second: float = 5.0):
        """
        Initialize Last.fm client.

        Args:
            api_key: Last.fm API key
            username: Last.fm username to fetch data for
            calls_per_second: Rate limit for API calls
        """
        self.api_key = api_key
        self.username = username
        self.min_call_interval = 1.0 / calls_per_second
        self.last_call_time = 0

        # Setup session with retry logic
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _rate_limit(self):
        """Enforce rate limiting between API calls."""
        elapsed = time.time() - self.last_call_time
        if elapsed < self.min_call_interval:
            time.sleep(self.min_call_interval - elapsed)
        self.last_call_time = time.time()

    def _request(self, method: str, **params) -> dict:
        """
        Make API request with rate limiting and error handling.

        Args:
            method: Last.fm API method name
            **params: Additional API parameters

        Returns:
            JSON response as dict
        """
        self._rate_limit()

        params.update({
            'method': method,
            'api_key': self.api_key,
            'format': 'json',
            'user': self.username,
        })

        try:
            response = self.session.get(
                self.BASE_URL,
                params=params,
                timeout=30
            )
            response.raise_for_status()
            data = response.json()

            # Check for API errors
            if 'error' in data:
                error_code = data.get('error')
                error_msg = data.get('message', 'Unknown error')

                if error_code in [4, 6, 10]:  # Auth errors
                    raise LastFMAuthError(f"Authentication error: {error_msg}")
                elif error_code == 29:  # Rate limit
                    raise LastFMRateLimitError("Rate limit exceeded")
                else:
                    raise LastFMError(f"API error {error_code}: {error_msg}")

            return data

        except requests.exceptions.Timeout:
            raise LastFMError("API request timed out")
        except requests.exceptions.RequestException as e:
            raise LastFMError(f"Request failed: {str(e)}")

    def get_user_info(self) -> dict:
        """
        Get user profile information.

        Returns:
            User info dict with playcount, registered date, etc.
        """
        data = self._request('user.getinfo')
        return data.get('user', {})

    def get_recent_tracks(
        self,
        limit: int = 200,
        page: int = 1,
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None
    ) -> dict:
        """
        Get user's recent tracks (scrobbles).

        Args:
            limit: Number of tracks per page (max 200)
            page: Page number (1-indexed)
            from_ts: Start timestamp (Unix)
            to_ts: End timestamp (Unix)

        Returns:
            Dict with tracks and pagination info
        """
        params = {
            'limit': min(limit, 200),
            'page': page,
            'extended': 1,  # Get additional track info
        }

        if from_ts:
            params['from'] = from_ts
        if to_ts:
            params['to'] = to_ts

        data = self._request('user.getrecenttracks', **params)
        return data.get('recenttracks', {})

    def iter_recent_tracks(
        self,
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
        max_pages: Optional[int] = None
    ) -> Generator[dict, None, None]:
        """
        Iterate through all recent tracks with pagination.

        Args:
            from_ts: Start timestamp (Unix)
            to_ts: End timestamp (Unix)
            max_pages: Maximum pages to fetch (None for all)

        Yields:
            Track dicts
        """
        page = 1
        while True:
            if max_pages and page > max_pages:
                break

            result = self.get_recent_tracks(
                limit=200,
                page=page,
                from_ts=from_ts,
                to_ts=to_ts
            )

            tracks = result.get('track', [])
            if not tracks:
                break

            # Handle single track response (returns dict instead of list)
            if isinstance(tracks, dict):
                tracks = [tracks]

            for track in tracks:
                # Skip currently playing track (no timestamp)
                if track.get('@attr', {}).get('nowplaying') == 'true':
                    continue
                yield track

            # Check if more pages
            attr = result.get('@attr', {})
            total_pages = int(attr.get('totalPages', 1))

            if page >= total_pages:
                break

            page += 1
            logger.debug(f"Fetched page {page-1}/{total_pages}")

    def get_loved_tracks(self, limit: int = 50, page: int = 1) -> dict:
        """
        Get user's loved tracks.

        Args:
            limit: Number of tracks per page
            page: Page number

        Returns:
            Dict with loved tracks
        """
        data = self._request('user.getlovedtracks', limit=limit, page=page)
        return data.get('lovedtracks', {})

    def iter_loved_tracks(self, max_pages: Optional[int] = None) -> Generator[dict, None, None]:
        """
        Iterate through all loved tracks.

        Args:
            max_pages: Maximum pages to fetch

        Yields:
            Loved track dicts
        """
        page = 1
        while True:
            if max_pages and page > max_pages:
                break

            result = self.get_loved_tracks(limit=50, page=page)
            tracks = result.get('track', [])

            if not tracks:
                break

            if isinstance(tracks, dict):
                tracks = [tracks]

            for track in tracks:
                yield track

            attr = result.get('@attr', {})
            total_pages = int(attr.get('totalPages', 1))

            if page >= total_pages:
                break

            page += 1

    def get_top_artists(self, period: str = 'overall', limit: int = 50, page: int = 1) -> dict:
        """
        Get user's top artists.

        Args:
            period: Time period ('overall', '7day', '1month', '3month', '6month', '12month')
            limit: Number of artists per page
            page: Page number

        Returns:
            Dict with top artists
        """
        data = self._request('user.gettopartists', period=period, limit=limit, page=page)
        return data.get('topartists', {})

    def get_top_tracks(self, period: str = 'overall', limit: int = 50, page: int = 1) -> dict:
        """
        Get user's top tracks.

        Args:
            period: Time period
            limit: Number of tracks per page
            page: Page number

        Returns:
            Dict with top tracks
        """
        data = self._request('user.gettoptracks', period=period, limit=limit, page=page)
        return data.get('toptracks', {})

    def get_track_info(self, track: str, artist: str) -> dict:
        """
        Get detailed track information.

        Args:
            track: Track name
            artist: Artist name

        Returns:
            Track info dict
        """
        data = self._request('track.getInfo', track=track, artist=artist, autocorrect=1)
        return data.get('track', {})

    def get_artist_info(self, artist: str) -> dict:
        """
        Get detailed artist information.

        Args:
            artist: Artist name

        Returns:
            Artist info dict
        """
        data = self._request('artist.getInfo', artist=artist, autocorrect=1)
        return data.get('artist', {})

    def get_artist_image(self, artist: str) -> Optional[str]:
        """
        Get artist image URL.

        Args:
            artist: Artist name

        Returns:
            Image URL or None
        """
        try:
            info = self.get_artist_info(artist)
            images = info.get('image', [])
            return get_image_url(images, size='extralarge')
        except Exception:
            return None

    def get_artist_tags(self, artist: str, limit: int = 10) -> list:
        """
        Get top tags for an artist.

        Args:
            artist: Artist name
            limit: Maximum number of tags to return

        Returns:
            List of tag dicts with 'name' and 'count' keys
        """
        try:
            data = self._request('artist.getTopTags', artist=artist, autocorrect=1)
            tags = data.get('toptags', {}).get('tag', [])
            if isinstance(tags, dict):
                tags = [tags]
            return tags[:limit]
        except Exception as e:
            logger.warning(f"Failed to get tags for artist {artist}: {e}")
            return []

    def get_track_tags(self, artist: str, track: str, limit: int = 10) -> list:
        """
        Get top tags for a track.

        Args:
            artist: Artist name
            track: Track name
            limit: Maximum number of tags to return

        Returns:
            List of tag dicts with 'name' and 'count' keys
        """
        try:
            data = self._request('track.getTopTags', artist=artist, track=track, autocorrect=1)
            tags = data.get('toptags', {}).get('tag', [])
            if isinstance(tags, dict):
                tags = [tags]
            return tags[:limit]
        except Exception as e:
            logger.warning(f"Failed to get tags for track {track} by {artist}: {e}")
            return []

    def get_similar_artists(self, artist: str, limit: int = 20) -> list:
        """
        Get similar artists.

        Args:
            artist: Artist name
            limit: Maximum number of similar artists to return

        Returns:
            List of similar artist dicts with 'name', 'mbid', and 'match' keys
        """
        try:
            data = self._request('artist.getSimilar', artist=artist, limit=limit, autocorrect=1)
            similar = data.get('similarartists', {}).get('artist', [])
            if isinstance(similar, dict):
                similar = [similar]
            return similar
        except Exception as e:
            logger.warning(f"Failed to get similar artists for {artist}: {e}")
            return []

    def verify_credentials(self) -> bool:
        """
        Verify API key and username are valid.

        Returns:
            True if valid, raises exception otherwise
        """
        try:
            self.get_user_info()
            return True
        except LastFMAuthError:
            return False


def parse_lastfm_timestamp(ts_str: str) -> Optional[datetime]:
    """
    Parse Last.fm timestamp to datetime.

    Args:
        ts_str: Timestamp string from Last.fm API

    Returns:
        datetime object or None
    """
    if not ts_str:
        return None

    try:
        # Last.fm uses Unix timestamps in 'uts' field
        return datetime.utcfromtimestamp(int(ts_str))
    except (ValueError, TypeError):
        pass

    # Try parsing date string format
    formats = [
        '%d %b %Y, %H:%M',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%dT%H:%M:%SZ',
    ]

    for fmt in formats:
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue

    return None


def get_image_url(images: list, size: str = 'large') -> Optional[str]:
    """
    Extract image URL from Last.fm image array.

    Args:
        images: List of image dicts from Last.fm
        size: Preferred size ('small', 'medium', 'large', 'extralarge')

    Returns:
        Image URL or None
    """
    if not images:
        return None

    size_priority = ['extralarge', 'large', 'medium', 'small']

    # Try preferred size first
    if size in size_priority:
        size_priority.remove(size)
        size_priority.insert(0, size)

    for pref_size in size_priority:
        for img in images:
            if img.get('size') == pref_size and img.get('#text'):
                return img['#text']

    # Return any available image
    for img in images:
        if img.get('#text'):
            return img['#text']

    return None
