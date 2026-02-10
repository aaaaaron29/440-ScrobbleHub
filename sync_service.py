"""
Background sync service for Last.fm data.
"""

import logging
from datetime import datetime
from typing import Optional, Tuple
from threading import Lock

from models import db, User, Artist, Album, Track, Scrobble, LovedTrack, SyncLog
from lastfm_client import LastFMClient, LastFMError, parse_lastfm_timestamp, get_image_url

logger = logging.getLogger(__name__)

# Global sync lock to prevent concurrent syncs
_sync_lock = Lock()
_is_syncing = False


class SyncService:
    """Service for syncing Last.fm data to local database."""

    def __init__(self, user: User):
        """
        Initialize sync service for a user.

        Args:
            user: User model instance
        """
        self.user = user
        self.client = LastFMClient(
            api_key=user.api_key,
            username=user.lastfm_username
        )
        self.sync_log: Optional[SyncLog] = None
        self.stats = {
            'scrobbles_added': 0,
            'tracks_added': 0,
            'artists_added': 0,
            'albums_added': 0,
            'loved_added': 0,
        }

    def _get_or_create_artist(self, name: str, mbid: Optional[str] = None,
                               url: Optional[str] = None, image_url: Optional[str] = None) -> Artist:
        """Get existing artist or create new one."""
        # Try to find by MBID first (more reliable)
        if mbid:
            artist = Artist.query.filter_by(lastfm_mbid=mbid).first()
            if artist:
                return artist

        # Try to find by name (case-insensitive)
        artist = Artist.query.filter(Artist.name.ilike(name)).first()
        if artist:
            # Update MBID if we now have it
            if mbid and not artist.lastfm_mbid:
                artist.lastfm_mbid = mbid
            return artist

        # Create new artist
        artist = Artist(
            name=name,
            lastfm_mbid=mbid,
            url=url,
            image_url=image_url
        )
        db.session.add(artist)
        db.session.flush()  # Get ID without committing
        self.stats['artists_added'] += 1
        return artist

    def _get_or_create_album(self, name: str, artist: Artist,
                              mbid: Optional[str] = None, image_url: Optional[str] = None) -> Optional[Album]:
        """Get existing album or create new one."""
        if not name:
            return None

        # Try MBID first
        if mbid:
            album = Album.query.filter_by(lastfm_mbid=mbid).first()
            if album:
                return album

        # Try name + artist
        album = Album.query.filter(
            Album.name.ilike(name),
            Album.artist_id == artist.id
        ).first()
        if album:
            if mbid and not album.lastfm_mbid:
                album.lastfm_mbid = mbid
            return album

        # Create new album
        album = Album(
            name=name,
            artist_id=artist.id,
            lastfm_mbid=mbid,
            image_url=image_url
        )
        db.session.add(album)
        db.session.flush()
        self.stats['albums_added'] += 1
        return album

    def _get_or_create_track(self, name: str, artist: Artist, album: Optional[Album] = None,
                              mbid: Optional[str] = None, url: Optional[str] = None) -> Track:
        """Get existing track or create new one."""
        # Try MBID first
        if mbid:
            track = Track.query.filter_by(lastfm_mbid=mbid).first()
            if track:
                return track

        # Try name + artist
        track = Track.query.filter(
            Track.name.ilike(name),
            Track.artist_id == artist.id
        ).first()
        if track:
            if mbid and not track.lastfm_mbid:
                track.lastfm_mbid = mbid
            return track

        # Create new track
        track = Track(
            name=name,
            artist_id=artist.id,
            album_id=album.id if album else None,
            lastfm_mbid=mbid,
            url=url
        )
        db.session.add(track)
        db.session.flush()
        self.stats['tracks_added'] += 1
        return track

    def _process_scrobble(self, scrobble_data: dict) -> bool:
        """
        Process a single scrobble from Last.fm API.

        Args:
            scrobble_data: Track dict from Last.fm API

        Returns:
            True if scrobble was added, False if duplicate
        """
        # Extract timestamp
        date_info = scrobble_data.get('date', {})
        timestamp_str = date_info.get('uts')
        if not timestamp_str:
            return False

        listened_at = parse_lastfm_timestamp(timestamp_str)
        if not listened_at:
            return False

        # Extract artist info
        artist_data = scrobble_data.get('artist', {})
        if isinstance(artist_data, str):
            artist_name = artist_data
            artist_mbid = None
        else:
            artist_name = artist_data.get('name') or artist_data.get('#text', 'Unknown')
            artist_mbid = artist_data.get('mbid') or None

        # Extract album info
        album_data = scrobble_data.get('album', {})
        if isinstance(album_data, str):
            album_name = album_data
            album_mbid = None
        else:
            album_name = album_data.get('#text', '')
            album_mbid = album_data.get('mbid') or None

        # Extract track info
        track_name = scrobble_data.get('name', 'Unknown')
        track_mbid = scrobble_data.get('mbid') or None
        track_url = scrobble_data.get('url')

        # Get image URL
        images = scrobble_data.get('image', [])
        image_url = get_image_url(images)

        # Get or create entities
        artist = self._get_or_create_artist(
            name=artist_name,
            mbid=artist_mbid
        )

        album = None
        if album_name:
            album = self._get_or_create_album(
                name=album_name,
                artist=artist,
                mbid=album_mbid,
                image_url=image_url
            )

        track = self._get_or_create_track(
            name=track_name,
            artist=artist,
            album=album,
            mbid=track_mbid,
            url=track_url
        )

        # Check for duplicate scrobble
        existing = Scrobble.query.filter_by(
            user_id=self.user.id,
            track_id=track.id,
            listened_at=listened_at
        ).first()

        if existing:
            return False

        # Create scrobble
        scrobble = Scrobble(
            user_id=self.user.id,
            track_id=track.id,
            listened_at=listened_at
        )
        db.session.add(scrobble)
        self.stats['scrobbles_added'] += 1
        return True

    def sync_recent_tracks(self, max_pages: Optional[int] = None,
                           from_timestamp: Optional[int] = None) -> int:
        """
        Sync recent tracks from Last.fm.

        Args:
            max_pages: Maximum pages to fetch (None for all)
            from_timestamp: Only fetch tracks after this Unix timestamp

        Returns:
            Number of new scrobbles added
        """
        logger.info(f"Starting recent tracks sync for {self.user.lastfm_username}")

        # Determine start timestamp
        if from_timestamp is None and self.user.last_sync_at:
            # last_sync_at is stored as UTC, need to convert properly
            # Use calendar.timegm to treat the datetime as UTC
            import calendar
            from_timestamp = calendar.timegm(self.user.last_sync_at.timetuple())
            logger.info(f"Using last_sync_at as from_timestamp: {self.user.last_sync_at} UTC ({from_timestamp})")
        else:
            logger.info(f"No from_timestamp filter, fetching all available (max_pages={max_pages})")

        count = 0
        batch_size = 100
        total_processed = 0

        try:
            for i, track in enumerate(self.client.iter_recent_tracks(
                from_ts=from_timestamp,
                max_pages=max_pages
            )):
                total_processed += 1
                if self._process_scrobble(track):
                    count += 1

                # Commit in batches
                if (i + 1) % batch_size == 0:
                    db.session.commit()
                    logger.info(f"Processed {i + 1} tracks, {count} new scrobbles")

            db.session.commit()
            logger.info(f"Sync complete: processed {total_processed} tracks, {count} new scrobbles added")
            return count

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error during sync: {e}")
            raise

    def sync_loved_tracks(self) -> int:
        """
        Sync loved tracks from Last.fm.

        Returns:
            Number of new loved tracks added
        """
        logger.info(f"Syncing loved tracks for {self.user.lastfm_username}")
        count = 0

        try:
            for track_data in self.client.iter_loved_tracks():
                # Extract data
                artist_data = track_data.get('artist', {})
                artist_name = artist_data.get('name', 'Unknown')
                artist_mbid = artist_data.get('mbid') or None

                track_name = track_data.get('name', 'Unknown')
                track_mbid = track_data.get('mbid') or None
                track_url = track_data.get('url')

                # Parse loved date
                date_info = track_data.get('date', {})
                loved_at = parse_lastfm_timestamp(date_info.get('uts'))
                if not loved_at:
                    loved_at = datetime.utcnow()

                # Get or create entities
                artist = self._get_or_create_artist(name=artist_name, mbid=artist_mbid)
                track = self._get_or_create_track(
                    name=track_name,
                    artist=artist,
                    mbid=track_mbid,
                    url=track_url
                )

                # Check for duplicate
                existing = LovedTrack.query.filter_by(
                    user_id=self.user.id,
                    track_id=track.id
                ).first()

                if not existing:
                    loved = LovedTrack(
                        user_id=self.user.id,
                        track_id=track.id,
                        loved_at=loved_at
                    )
                    db.session.add(loved)
                    count += 1
                    self.stats['loved_added'] += 1

            db.session.commit()
            logger.info(f"Loved tracks sync complete: {count} new")
            return count

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error syncing loved tracks: {e}")
            raise

    def full_sync(self, initial: bool = False, force_full: bool = False) -> Tuple[bool, str]:
        """
        Perform full sync operation.

        Args:
            initial: If True, fetch more historical data
            force_full: If True, ignore last_sync_at and fetch all available

        Returns:
            Tuple of (success, message)
        """
        global _is_syncing

        if not _sync_lock.acquire(blocking=False):
            return False, "Sync already in progress"

        _is_syncing = True

        try:
            # Create sync log entry
            self.sync_log = SyncLog(
                user_id=self.user.id,
                status='running'
            )
            db.session.add(self.sync_log)
            db.session.commit()

            # Determine max pages based on initial vs incremental
            max_pages = 50 if initial or force_full else 10

            # Force full sync by passing from_timestamp=0
            from_ts = 0 if force_full else None

            # Sync recent tracks
            scrobbles_count = self.sync_recent_tracks(max_pages=max_pages, from_timestamp=from_ts)

            # Sync loved tracks
            self.sync_loved_tracks()

            # Update user
            self.user.last_sync_at = datetime.utcnow()
            self.user.total_scrobbles = Scrobble.query.filter_by(user_id=self.user.id).count()

            # Update sync log
            self.sync_log.status = 'success'
            self.sync_log.completed_at = datetime.utcnow()
            self.sync_log.scrobbles_fetched = scrobbles_count

            db.session.commit()

            return True, f"Sync complete: {scrobbles_count} new scrobbles"

        except LastFMError as e:
            if self.sync_log:
                self.sync_log.status = 'failed'
                self.sync_log.error_message = str(e)
                self.sync_log.completed_at = datetime.utcnow()
                db.session.commit()
            return False, f"Last.fm API error: {e}"

        except Exception as e:
            logger.exception("Sync failed with unexpected error")
            if self.sync_log:
                self.sync_log.status = 'failed'
                self.sync_log.error_message = str(e)
                self.sync_log.completed_at = datetime.utcnow()
                db.session.commit()
            return False, f"Sync failed: {e}"

        finally:
            _is_syncing = False
            _sync_lock.release()


def is_sync_running() -> bool:
    """Check if a sync is currently running."""
    return _is_syncing


def run_scheduled_sync(app):
    """
    Run sync as scheduled job.
    Called by APScheduler.
    """
    with app.app_context():
        user = User.query.first()
        if not user:
            logger.warning("No user configured, skipping scheduled sync")
            return

        service = SyncService(user)
        success, message = service.full_sync(initial=False)

        if success:
            logger.info(f"Scheduled sync completed: {message}")
        else:
            logger.error(f"Scheduled sync failed: {message}")
