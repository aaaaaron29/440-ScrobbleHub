"""
Enhanced Sync Service for Last.fm Listening History Tracker.

Extends the base sync_service.py with additional data collection for recommendations:
- Similar artist relationships
- Detailed tag data for artists and tracks
- Co-listening pattern analysis

These operations are designed to run weekly or on-demand, not with every sync,
to avoid hitting Last.fm API rate limits.

See claude.md for documentation.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple
from collections import defaultdict
from threading import Lock

from models import (
    db, User, Artist, Track, Scrobble, ArtistTag, TrackTag,
    SimilarArtist, ListeningSession, CoListeningPattern
)
from lastfm_client import LastFMClient, LastFMError

logger = logging.getLogger(__name__)

# Lock for enhanced sync operations
_enhanced_sync_lock = Lock()
_is_enhanced_syncing = False

# Session gap threshold - if more than 30 minutes between scrobbles, new session
SESSION_GAP_MINUTES = 30


class EnhancedSyncService:
    """
    Extended sync service for recommendation-related data.

    Fetches additional metadata from Last.fm that isn't collected
    during regular scrobble syncing.
    """

    def __init__(self, user: User):
        self.user = user
        self.client = LastFMClient(
            api_key=user.api_key,
            username=user.lastfm_username
        )
        self.stats = {
            'artists_processed': 0,
            'tracks_processed': 0,
            'tags_added': 0,
            'similar_artists_added': 0,
            'sessions_detected': 0,
            'co_patterns_computed': 0
        }

    def fetch_similar_artists(self, artist_id: int, limit: int = 20) -> int:
        """
        Fetch and store similar artists for a given artist.

        Args:
            artist_id: Artist ID from our database
            limit: Maximum similar artists to fetch

        Returns:
            Number of similar artists added
        """
        artist = Artist.query.get(artist_id)
        if not artist:
            logger.warning(f"Artist {artist_id} not found")
            return 0

        try:
            similar = self.client.get_similar_artists(artist.name, limit=limit)
            count = 0

            for sim_data in similar:
                sim_name = sim_data.get('name')
                if not sim_name:
                    continue

                # Get match score (Last.fm returns as string like "0.95")
                match_score = float(sim_data.get('match', 0))
                mbid = sim_data.get('mbid') or None

                # Check if relationship already exists
                existing = SimilarArtist.query.filter_by(
                    artist_id=artist_id,
                    similar_artist_name=sim_name
                ).first()

                if existing:
                    # Update match score if it changed significantly
                    if abs(existing.match_score - match_score) > 0.05:
                        existing.match_score = match_score
                        existing.fetched_at = datetime.utcnow()
                else:
                    # Create new relationship
                    sim_artist = SimilarArtist(
                        artist_id=artist_id,
                        similar_artist_name=sim_name,
                        similar_artist_mbid=mbid,
                        match_score=match_score,
                        fetched_at=datetime.utcnow()
                    )
                    db.session.add(sim_artist)
                    count += 1

            db.session.commit()
            self.stats['similar_artists_added'] += count
            return count

        except LastFMError as e:
            logger.error(f"Failed to fetch similar artists for {artist.name}: {e}")
            return 0

    def fetch_artist_tags(self, artist_id: int, limit: int = 15) -> int:
        """
        Fetch and store tags for an artist.

        Args:
            artist_id: Artist ID from our database
            limit: Maximum tags to fetch

        Returns:
            Number of tags added
        """
        artist = Artist.query.get(artist_id)
        if not artist:
            return 0

        try:
            tags = self.client.get_artist_tags(artist.name, limit=limit)
            count = 0

            for tag_data in tags:
                tag_name = tag_data.get('name')
                if not tag_name:
                    continue

                # Normalize tag name (lowercase, strip whitespace)
                tag_name = tag_name.lower().strip()

                # Get tag count (weight)
                tag_count = int(tag_data.get('count', 0))

                # Check if tag already exists
                existing = ArtistTag.query.filter_by(
                    artist_id=artist_id,
                    tag=tag_name
                ).first()

                if existing:
                    existing.count = tag_count
                    existing.fetched_at = datetime.utcnow()
                else:
                    artist_tag = ArtistTag(
                        artist_id=artist_id,
                        tag=tag_name,
                        count=tag_count,
                        fetched_at=datetime.utcnow()
                    )
                    db.session.add(artist_tag)
                    count += 1

            db.session.commit()
            self.stats['tags_added'] += count
            return count

        except LastFMError as e:
            logger.error(f"Failed to fetch tags for artist {artist.name}: {e}")
            return 0

    def fetch_track_tags(self, track_id: int, limit: int = 10) -> int:
        """
        Fetch and store tags for a track.

        Args:
            track_id: Track ID from our database
            limit: Maximum tags to fetch

        Returns:
            Number of tags added
        """
        track = Track.query.get(track_id)
        if not track or not track.artist:
            return 0

        try:
            tags = self.client.get_track_tags(track.artist.name, track.name, limit=limit)
            count = 0

            for tag_data in tags:
                tag_name = tag_data.get('name')
                if not tag_name:
                    continue

                tag_name = tag_name.lower().strip()
                tag_count = int(tag_data.get('count', 0))

                existing = TrackTag.query.filter_by(
                    track_id=track_id,
                    tag=tag_name
                ).first()

                if existing:
                    existing.count = tag_count
                    existing.fetched_at = datetime.utcnow()
                else:
                    track_tag = TrackTag(
                        track_id=track_id,
                        tag=tag_name,
                        count=tag_count,
                        fetched_at=datetime.utcnow()
                    )
                    db.session.add(track_tag)
                    count += 1

            db.session.commit()
            self.stats['tags_added'] += count
            return count

        except LastFMError as e:
            logger.error(f"Failed to fetch tags for track {track.name}: {e}")
            return 0

    def fetch_detailed_tags(
        self,
        artist_ids: List[int] = None,
        track_ids: List[int] = None,
        max_artists: int = 50,
        max_tracks: int = 100
    ) -> Dict:
        """
        Batch fetch tags for multiple artists and tracks.

        If no IDs provided, fetches for top played items.

        Args:
            artist_ids: Specific artists to process
            track_ids: Specific tracks to process
            max_artists: Maximum artists to process
            max_tracks: Maximum tracks to process

        Returns:
            Dict with counts of processed items
        """
        # Get top artists if none specified
        if artist_ids is None:
            top_artists = db.session.query(
                Artist.id
            ).join(Track, Track.artist_id == Artist.id
            ).join(Scrobble, Scrobble.track_id == Track.id
            ).filter(Scrobble.user_id == self.user.id
            ).group_by(Artist.id
            ).order_by(db.func.count(Scrobble.id).desc()
            ).limit(max_artists).all()
            artist_ids = [a[0] for a in top_artists]

        # Get top tracks if none specified
        if track_ids is None:
            top_tracks = db.session.query(
                Track.id
            ).join(Scrobble, Scrobble.track_id == Track.id
            ).filter(Scrobble.user_id == self.user.id
            ).group_by(Track.id
            ).order_by(db.func.count(Scrobble.id).desc()
            ).limit(max_tracks).all()
            track_ids = [t[0] for t in top_tracks]

        artists_processed = 0
        tracks_processed = 0

        # Fetch artist tags
        for artist_id in artist_ids:
            # Check if we already have recent tags
            recent_tag = ArtistTag.query.filter(
                ArtistTag.artist_id == artist_id,
                ArtistTag.fetched_at > datetime.utcnow() - timedelta(days=7)
            ).first()

            if not recent_tag:
                self.fetch_artist_tags(artist_id)
                artists_processed += 1

                # Also fetch similar artists while we're at it
                self.fetch_similar_artists(artist_id)

        # Fetch track tags
        for track_id in track_ids:
            recent_tag = TrackTag.query.filter(
                TrackTag.track_id == track_id,
                TrackTag.fetched_at > datetime.utcnow() - timedelta(days=7)
            ).first()

            if not recent_tag:
                self.fetch_track_tags(track_id)
                tracks_processed += 1

        self.stats['artists_processed'] = artists_processed
        self.stats['tracks_processed'] = tracks_processed

        return {
            'artists_processed': artists_processed,
            'tracks_processed': tracks_processed,
            'tags_added': self.stats['tags_added'],
            'similar_artists_added': self.stats['similar_artists_added']
        }

    def detect_listening_sessions(self, days_back: int = 30) -> int:
        """
        Analyze scrobbles to detect listening sessions.

        A session is a continuous listening period where gaps between
        scrobbles are less than SESSION_GAP_MINUTES.

        Args:
            days_back: How many days of history to analyze

        Returns:
            Number of sessions detected
        """
        since = datetime.utcnow() - timedelta(days=days_back)

        # Get scrobbles ordered by time
        scrobbles = Scrobble.query.filter(
            Scrobble.user_id == self.user.id,
            Scrobble.listened_at >= since
        ).order_by(Scrobble.listened_at).all()

        if not scrobbles:
            return 0

        sessions = []
        current_session = {
            'start': scrobbles[0].listened_at,
            'end': scrobbles[0].listened_at,
            'track_ids': [scrobbles[0].track_id],
            'artist_ids': set()
        }

        # Get artist for first track
        first_track = Track.query.get(scrobbles[0].track_id)
        if first_track:
            current_session['artist_ids'].add(first_track.artist_id)

        for i in range(1, len(scrobbles)):
            scrobble = scrobbles[i]
            prev_scrobble = scrobbles[i - 1]

            gap = (scrobble.listened_at - prev_scrobble.listened_at).total_seconds() / 60

            if gap > SESSION_GAP_MINUTES:
                # End current session and start new one
                sessions.append(current_session)
                current_session = {
                    'start': scrobble.listened_at,
                    'end': scrobble.listened_at,
                    'track_ids': [scrobble.track_id],
                    'artist_ids': set()
                }
            else:
                # Continue current session
                current_session['end'] = scrobble.listened_at
                current_session['track_ids'].append(scrobble.track_id)

            # Add artist
            track = Track.query.get(scrobble.track_id)
            if track:
                current_session['artist_ids'].add(track.artist_id)

        # Don't forget the last session
        sessions.append(current_session)

        # Store sessions (only if they have multiple tracks)
        count = 0
        for session in sessions:
            if len(session['track_ids']) >= 2:
                # Check if session already exists (by start time)
                existing = ListeningSession.query.filter_by(
                    user_id=self.user.id,
                    session_start=session['start']
                ).first()

                if not existing:
                    ls = ListeningSession(
                        user_id=self.user.id,
                        session_start=session['start'],
                        session_end=session['end'],
                        track_ids=json.dumps(session['track_ids']),
                        artist_ids=json.dumps(list(session['artist_ids'])),
                        track_count=len(session['track_ids'])
                    )
                    db.session.add(ls)
                    count += 1

        db.session.commit()
        self.stats['sessions_detected'] = count
        return count

    def compute_co_listening_patterns(self) -> int:
        """
        Compute co-listening patterns from detected sessions.

        For each pair of artists that appear in the same session,
        increment their co-occurrence count and compute affinity score.

        Returns:
            Number of patterns computed/updated
        """
        sessions = ListeningSession.query.filter_by(
            user_id=self.user.id
        ).all()

        # Count co-occurrences
        co_occurrences = defaultdict(int)
        artist_session_counts = defaultdict(int)

        for session in sessions:
            if not session.artist_ids:
                continue

            artist_ids = json.loads(session.artist_ids)

            for artist_id in artist_ids:
                artist_session_counts[artist_id] += 1

            # Count pairs
            for i, a1 in enumerate(artist_ids):
                for a2 in artist_ids[i + 1:]:
                    # Always store with smaller ID first for consistency
                    pair = tuple(sorted([a1, a2]))
                    co_occurrences[pair] += 1

        # Compute and store patterns
        count = 0
        for (a1, a2), co_count in co_occurrences.items():
            # Compute Jaccard-like affinity score
            total_sessions = artist_session_counts[a1] + artist_session_counts[a2] - co_count
            affinity = co_count / total_sessions if total_sessions > 0 else 0

            # Update or create pattern
            existing = CoListeningPattern.query.filter_by(
                user_id=self.user.id,
                artist_id_1=a1,
                artist_id_2=a2
            ).first()

            if existing:
                existing.co_occurrence_count = co_count
                existing.affinity_score = affinity
                existing.computed_at = datetime.utcnow()
            else:
                pattern = CoListeningPattern(
                    user_id=self.user.id,
                    artist_id_1=a1,
                    artist_id_2=a2,
                    co_occurrence_count=co_count,
                    affinity_score=affinity,
                    computed_at=datetime.utcnow()
                )
                db.session.add(pattern)
                count += 1

        db.session.commit()
        self.stats['co_patterns_computed'] = count
        return count

    def track_co_listening_patterns(self) -> Dict:
        """
        Full co-listening pattern analysis pipeline.

        1. Detect listening sessions
        2. Compute co-occurrence patterns

        Returns:
            Dict with analysis results
        """
        sessions = self.detect_listening_sessions()
        patterns = self.compute_co_listening_patterns()

        return {
            'sessions_detected': sessions,
            'patterns_computed': patterns
        }

    def full_enhanced_sync(self, max_artists: int = 50, max_tracks: int = 100) -> Dict:
        """
        Run complete enhanced sync operation.

        This is designed to run weekly or on-demand, not with every scrobble sync.

        Args:
            max_artists: Maximum artists to process
            max_tracks: Maximum tracks to process

        Returns:
            Dict with all sync statistics
        """
        global _is_enhanced_syncing

        if not _enhanced_sync_lock.acquire(blocking=False):
            return {'error': 'Enhanced sync already in progress'}

        _is_enhanced_syncing = True

        try:
            logger.info(f"Starting enhanced sync for {self.user.lastfm_username}")

            # Fetch detailed tags and similar artists
            tag_results = self.fetch_detailed_tags(
                max_artists=max_artists,
                max_tracks=max_tracks
            )

            # Analyze co-listening patterns
            pattern_results = self.track_co_listening_patterns()

            results = {
                'status': 'success',
                'timestamp': datetime.utcnow().isoformat(),
                **tag_results,
                **pattern_results
            }

            logger.info(f"Enhanced sync complete: {results}")
            return results

        except Exception as e:
            logger.exception("Enhanced sync failed")
            return {'status': 'error', 'error': str(e)}

        finally:
            _is_enhanced_syncing = False
            _enhanced_sync_lock.release()


def is_enhanced_sync_running() -> bool:
    """Check if enhanced sync is currently running."""
    return _is_enhanced_syncing


def run_enhanced_sync(app, max_artists: int = 50, max_tracks: int = 100) -> Dict:
    """
    Run enhanced sync as a scheduled or on-demand job.

    Args:
        app: Flask app for context
        max_artists: Maximum artists to process
        max_tracks: Maximum tracks to process

    Returns:
        Dict with sync results
    """
    with app.app_context():
        user = User.query.first()
        if not user:
            logger.warning("No user configured, skipping enhanced sync")
            return {'error': 'No user configured'}

        service = EnhancedSyncService(user)
        return service.full_enhanced_sync(max_artists, max_tracks)


def get_enhanced_sync_status(user_id: int) -> Dict:
    """
    Get status of recommendation data for a user.

    Returns counts of tags, similar artists, and patterns.
    """
    artist_tags = ArtistTag.query.join(Artist).join(Track).join(Scrobble).filter(
        Scrobble.user_id == user_id
    ).distinct().count()

    track_tags = TrackTag.query.join(Track).join(Scrobble).filter(
        Scrobble.user_id == user_id
    ).distinct().count()

    similar_artists = SimilarArtist.query.join(Artist).join(Track).join(Scrobble).filter(
        Scrobble.user_id == user_id
    ).distinct().count()

    sessions = ListeningSession.query.filter_by(user_id=user_id).count()

    patterns = CoListeningPattern.query.filter_by(user_id=user_id).count()

    # Check when last enhanced sync ran
    latest_tag = ArtistTag.query.order_by(ArtistTag.fetched_at.desc()).first()
    last_sync = latest_tag.fetched_at if latest_tag else None

    return {
        'artist_tags': artist_tags,
        'track_tags': track_tags,
        'similar_artists': similar_artists,
        'listening_sessions': sessions,
        'co_listening_patterns': patterns,
        'last_enhanced_sync': last_sync.isoformat() if last_sync else None,
        'is_syncing': _is_enhanced_syncing
    }
