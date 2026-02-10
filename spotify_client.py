"""
Spotify Integration Client for Last.fm Listening History Tracker.

NOTE: Spotify developer accounts are currently blocked. This module contains
mock implementations that simulate the expected behavior. When Spotify API
access becomes available, replace mock functions with real implementations.

See claude.md for integration documentation.
"""

import json
import logging
from typing import Dict, List, Optional
from datetime import datetime

from models import db, Track, User

logger = logging.getLogger(__name__)

# Configuration - TODO: Move to config.py when Spotify API available
SPOTIFY_CLIENT_ID = None  # TODO: Activate when Spotify API available
SPOTIFY_CLIENT_SECRET = None  # TODO: Activate when Spotify API available
SPOTIFY_REDIRECT_URI = "http://localhost:5000/api/spotify/callback"

# Mock mode flag - set to False when real Spotify integration is ready
MOCK_MODE = True


class SpotifyClient:
    """
    Spotify API client for track matching and playlist creation.

    Currently operates in mock mode. When Spotify API becomes available:
    1. Set MOCK_MODE = False
    2. Configure SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET
    3. Implement OAuth flow in authenticate_spotify()
    """

    def __init__(self, user_id: int):
        self.user_id = user_id
        self.user = User.query.get(user_id)
        self.access_token = None
        self.refresh_token = None

    def is_authenticated(self) -> bool:
        """Check if user has valid Spotify authentication."""
        if MOCK_MODE:
            return False
        # TODO: Activate when Spotify API available
        # Check if user has stored tokens and they're not expired
        return self.access_token is not None

    def authenticate_spotify(self) -> Dict:
        """
        Initiate Spotify OAuth flow.

        TODO: Activate when Spotify API available
        Returns auth URL for user to visit, or error if already authenticated.
        """
        if MOCK_MODE:
            return {
                'status': 'mock_mode',
                'message': 'Spotify integration pending. Developer access required.',
                'mock': True,
                'instructions': 'Export recommendations to JSON and create playlist manually on Spotify.'
            }

        # TODO: Implement real OAuth flow
        # auth_url = f"https://accounts.spotify.com/authorize?client_id={SPOTIFY_CLIENT_ID}&..."
        # return {'status': 'redirect', 'auth_url': auth_url}
        pass

    def handle_callback(self, code: str) -> Dict:
        """
        Handle OAuth callback and exchange code for tokens.

        TODO: Activate when Spotify API available
        """
        if MOCK_MODE:
            return {'status': 'mock_mode', 'mock': True}

        # TODO: Exchange code for access_token and refresh_token
        # Store tokens for user
        pass

    def search_track(self, track_name: str, artist_name: str) -> Dict:
        """
        Search Spotify for a track by name and artist.

        Args:
            track_name: Name of the track
            artist_name: Name of the artist

        Returns:
            Dict with spotify_id, spotify_uri, preview_url, or mock response
        """
        if MOCK_MODE:
            # Return mock response that indicates Spotify matching is pending
            return {
                'found': False,
                'mock': True,
                'message': 'Spotify search unavailable - integration pending',
                'track_name': track_name,
                'artist_name': artist_name,
                'suggestion': f'Search manually: "{track_name}" by {artist_name}'
            }

        # TODO: Activate when Spotify API available
        # response = requests.get(
        #     "https://api.spotify.com/v1/search",
        #     headers={"Authorization": f"Bearer {self.access_token}"},
        #     params={
        #         "q": f"track:{track_name} artist:{artist_name}",
        #         "type": "track",
        #         "limit": 1
        #     }
        # )
        # if response.ok and response.json()['tracks']['items']:
        #     track = response.json()['tracks']['items'][0]
        #     return {
        #         'found': True,
        #         'spotify_id': track['id'],
        #         'spotify_uri': track['uri'],
        #         'preview_url': track.get('preview_url'),
        #         'album_image': track['album']['images'][0]['url'] if track['album']['images'] else None
        #     }
        pass

    def get_playback_url(self, spotify_id: str) -> Dict:
        """
        Get Spotify playback URL/URI for a track.

        TODO: Activate when Spotify API available
        """
        if MOCK_MODE:
            return {
                'mock': True,
                'spotify_uri': f'spotify:track:{spotify_id}' if spotify_id else None,
                'web_url': f'https://open.spotify.com/track/{spotify_id}' if spotify_id else None,
                'message': 'Direct playback unavailable - use Spotify app'
            }

        # TODO: Return actual playback controls
        pass

    def create_playlist(
        self,
        track_ids: List[int],
        playlist_name: str,
        description: str = None
    ) -> Dict:
        """
        Create a Spotify playlist from track IDs.

        Args:
            track_ids: List of our internal track IDs
            playlist_name: Name for the new playlist
            description: Optional playlist description

        Returns:
            Dict with playlist_url or mock response with export data
        """
        # Get tracks from our database
        tracks = Track.query.filter(Track.id.in_(track_ids)).all()

        if MOCK_MODE:
            # Return exportable data for manual playlist creation
            track_list = []
            for track in tracks:
                track_list.append({
                    'track_id': track.id,
                    'name': track.name,
                    'artist': track.artist.name if track.artist else 'Unknown',
                    'spotify_uri': track.spotify_uri,
                    'search_query': f"{track.name} {track.artist.name if track.artist else ''}"
                })

            return {
                'mock': True,
                'status': 'export_ready',
                'message': 'Spotify playlist creation unavailable. Use exported data to create manually.',
                'playlist_name': playlist_name,
                'track_count': len(track_list),
                'tracks': track_list,
                'export_format': 'Copy track names to search in Spotify',
                'instructions': [
                    '1. Open Spotify and create a new playlist',
                    f'2. Name it: {playlist_name}',
                    '3. Search for each track below and add to playlist',
                    '4. Or use Spotify\'s "Add songs" feature with track names'
                ]
            }

        # TODO: Activate when Spotify API available
        # 1. Create playlist via API
        # 2. Match our track_ids to Spotify URIs
        # 3. Add tracks to playlist
        # 4. Return playlist URL
        pass

    def add_to_playlist(self, playlist_id: str, track_ids: List[int]) -> Dict:
        """
        Add tracks to an existing Spotify playlist.

        TODO: Activate when Spotify API available
        """
        if MOCK_MODE:
            return {
                'mock': True,
                'message': 'Cannot add to Spotify playlist - integration pending',
                'track_count': len(track_ids)
            }

        # TODO: Implement real playlist addition
        pass

    def match_tracks_batch(self, track_ids: List[int]) -> Dict:
        """
        Batch match multiple tracks to Spotify.

        Used for pre-populating spotify_uri in tracks table.

        TODO: Activate when Spotify API available
        """
        tracks = Track.query.filter(Track.id.in_(track_ids)).all()

        if MOCK_MODE:
            return {
                'mock': True,
                'message': 'Batch matching unavailable',
                'tracks_requested': len(track_ids),
                'tracks_matched': 0,
                'tracks_pending': [
                    {'id': t.id, 'name': t.name, 'artist': t.artist.name if t.artist else None}
                    for t in tracks[:10]  # Sample
                ]
            }

        # TODO: Implement batch matching with rate limiting
        # Spotify allows 100 tracks per request to /audio-features
        # For search, implement with delays to respect rate limits
        pass


def authenticate_spotify(user_id: int) -> Dict:
    """Convenience function to start Spotify OAuth."""
    client = SpotifyClient(user_id)
    return client.authenticate_spotify()


def search_track(track_id: int) -> Dict:
    """
    Search Spotify for a track from our database.

    Args:
        track_id: Our internal track ID

    Returns:
        Spotify match results or mock response
    """
    track = Track.query.get(track_id)
    if not track:
        return {'error': 'Track not found', 'track_id': track_id}

    # If we already have Spotify URI, return it
    if track.spotify_uri:
        return {
            'found': True,
            'cached': True,
            'spotify_uri': track.spotify_uri,
            'spotify_id': track.spotify_id
        }

    # Search Spotify
    client = SpotifyClient(None)  # No user needed for search in mock mode
    artist_name = track.artist.name if track.artist else ''
    result = client.search_track(track.name, artist_name)

    # TODO: When real implementation, store spotify_uri in track record
    # if result.get('found') and result.get('spotify_uri'):
    #     track.spotify_uri = result['spotify_uri']
    #     track.spotify_id = result['spotify_id']
    #     db.session.commit()

    return result


def create_playlist(user_id: int, track_ids: List[int], playlist_name: str) -> Dict:
    """Convenience function to create playlist."""
    client = SpotifyClient(user_id)
    return client.create_playlist(track_ids, playlist_name)


def get_spotify_status() -> Dict:
    """Get current Spotify integration status."""
    return {
        'enabled': not MOCK_MODE,
        'mock_mode': MOCK_MODE,
        'status': 'pending' if MOCK_MODE else 'active',
        'message': 'Spotify integration pending developer access' if MOCK_MODE else 'Connected',
        'features_available': {
            'search': not MOCK_MODE,
            'playback': not MOCK_MODE,
            'playlist_creation': not MOCK_MODE,
            'audio_features': not MOCK_MODE
        },
        'workaround': 'Export recommendations to JSON for manual Spotify playlist creation' if MOCK_MODE else None
    }


def export_for_spotify(track_ids: List[int], format: str = 'json') -> Dict:
    """
    Export tracks in a format suitable for manual Spotify import.

    Since direct integration is pending, this provides data users can
    use to manually create playlists.
    """
    tracks = Track.query.filter(Track.id.in_(track_ids)).all()

    export_data = []
    for track in tracks:
        export_data.append({
            'name': track.name,
            'artist': track.artist.name if track.artist else 'Unknown',
            'album': track.album.name if track.album else None,
            'search_query': f"{track.name} - {track.artist.name if track.artist else ''}",
            'lastfm_url': track.url,
            'spotify_uri': track.spotify_uri  # May be None
        })

    if format == 'text':
        # Simple text format for easy copy-paste
        lines = [f"{t['name']} - {t['artist']}" for t in export_data]
        return {
            'format': 'text',
            'content': '\n'.join(lines),
            'track_count': len(lines)
        }

    return {
        'format': 'json',
        'tracks': export_data,
        'track_count': len(export_data),
        'instructions': 'Search each track on Spotify to add to your playlist'
    }
