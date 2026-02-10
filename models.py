"""
Database models for Last.fm Listening History Tracker.
See claude.md for complete schema documentation.
"""

from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Index, UniqueConstraint

db = SQLAlchemy()


class User(db.Model):
    """User account - single user for now, multi-user ready."""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    lastfm_username = db.Column(db.Text, unique=True, nullable=False)
    api_key = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_sync_at = db.Column(db.DateTime, nullable=True)
    total_scrobbles = db.Column(db.Integer, default=0)
    sync_interval_minutes = db.Column(db.Integer, default=30)

    # Relationships
    scrobbles = db.relationship('Scrobble', backref='user', lazy='dynamic')
    loved_tracks = db.relationship('LovedTrack', backref='user', lazy='dynamic')
    sync_logs = db.relationship('SyncLog', backref='user', lazy='dynamic')
    metrics = db.relationship('UserMetric', backref='user', lazy='dynamic')


class Artist(db.Model):
    """Normalized artist data."""
    __tablename__ = 'artists'

    id = db.Column(db.Integer, primary_key=True)
    lastfm_mbid = db.Column(db.Text, nullable=True, index=True)
    name = db.Column(db.Text, nullable=False, index=True)
    url = db.Column(db.Text, nullable=True)
    image_url = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # Relationships
    albums = db.relationship('Album', backref='artist', lazy='dynamic')
    tracks = db.relationship('Track', backref='artist', lazy='dynamic')


class Album(db.Model):
    """Normalized album data."""
    __tablename__ = 'albums'

    id = db.Column(db.Integer, primary_key=True)
    lastfm_mbid = db.Column(db.Text, nullable=True, index=True)
    name = db.Column(db.Text, nullable=False)
    artist_id = db.Column(db.Integer, db.ForeignKey('artists.id'), nullable=True, index=True)
    image_url = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # Relationships
    tracks = db.relationship('Track', backref='album', lazy='dynamic')


class Track(db.Model):
    """Normalized track data with Spotify placeholders."""
    __tablename__ = 'tracks'

    id = db.Column(db.Integer, primary_key=True)
    lastfm_mbid = db.Column(db.Text, nullable=True, index=True)
    name = db.Column(db.Text, nullable=False)
    artist_id = db.Column(db.Integer, db.ForeignKey('artists.id'), nullable=False, index=True)
    album_id = db.Column(db.Integer, db.ForeignKey('albums.id'), nullable=True)
    url = db.Column(db.Text, nullable=True)
    duration_ms = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # Spotify placeholders (for future integration)
    spotify_id = db.Column(db.Text, nullable=True, index=True)
    spotify_uri = db.Column(db.Text, nullable=True)
    isrc = db.Column(db.Text, nullable=True)

    # Relationships
    scrobbles = db.relationship('Scrobble', backref='track', lazy='dynamic')
    loved_by = db.relationship('LovedTrack', backref='track', lazy='dynamic')
    audio_features = db.relationship('AudioFeature', backref='track', uselist=False)


class AudioFeature(db.Model):
    """Spotify audio features placeholder table."""
    __tablename__ = 'audio_features'

    id = db.Column(db.Integer, primary_key=True)
    track_id = db.Column(db.Integer, db.ForeignKey('tracks.id'), unique=True, nullable=False, index=True)
    spotify_id = db.Column(db.Text, nullable=True, index=True)

    # Audio features from Spotify
    danceability = db.Column(db.Float, nullable=True)
    energy = db.Column(db.Float, nullable=True)
    valence = db.Column(db.Float, nullable=True)
    tempo = db.Column(db.Float, nullable=True)
    loudness = db.Column(db.Float, nullable=True)
    speechiness = db.Column(db.Float, nullable=True)
    acousticness = db.Column(db.Float, nullable=True)
    instrumentalness = db.Column(db.Float, nullable=True)
    liveness = db.Column(db.Float, nullable=True)
    key = db.Column(db.Integer, nullable=True)
    mode = db.Column(db.Integer, nullable=True)
    time_signature = db.Column(db.Integer, nullable=True)
    fetched_at = db.Column(db.DateTime, nullable=True)


class Scrobble(db.Model):
    """Main listening history table."""
    __tablename__ = 'scrobbles'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    track_id = db.Column(db.Integer, db.ForeignKey('tracks.id'), nullable=False, index=True)
    listened_at = db.Column(db.DateTime, nullable=False)
    listened_at_local = db.Column(db.DateTime, nullable=True)
    source = db.Column(db.Text, nullable=True)

    __table_args__ = (
        UniqueConstraint('user_id', 'track_id', 'listened_at', name='uq_scrobbles'),
        Index('idx_scrobbles_user_time', 'user_id', 'listened_at'),
    )


class LovedTrack(db.Model):
    """User's loved/favorited tracks."""
    __tablename__ = 'loved_tracks'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    track_id = db.Column(db.Integer, db.ForeignKey('tracks.id'), nullable=False)
    loved_at = db.Column(db.DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint('user_id', 'track_id', name='uq_loved'),
    )


class SyncLog(db.Model):
    """Audit log for sync operations."""
    __tablename__ = 'sync_log'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.Text, nullable=False, default='running')
    scrobbles_fetched = db.Column(db.Integer, default=0)
    error_message = db.Column(db.Text, nullable=True)


class UserMetric(db.Model):
    """Pre-computed metrics cache."""
    __tablename__ = 'user_metrics'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    metric_type = db.Column(db.Text, nullable=False)
    metric_key = db.Column(db.Text, nullable=True)
    metric_value = db.Column(db.Float, nullable=False)
    period_start = db.Column(db.Date, nullable=True)
    period_end = db.Column(db.Date, nullable=True)
    computed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_metrics_user_type', 'user_id', 'metric_type'),
    )


# =============================================================================
# Recommendation System Models
# =============================================================================

class ArtistTag(db.Model):
    """Tags associated with artists from Last.fm (genres, moods, etc.)."""
    __tablename__ = 'artist_tags'

    id = db.Column(db.Integer, primary_key=True)
    artist_id = db.Column(db.Integer, db.ForeignKey('artists.id'), nullable=False, index=True)
    tag = db.Column(db.Text, nullable=False)
    count = db.Column(db.Integer, default=0)  # Tag weight/popularity
    fetched_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('artist_id', 'tag', name='uq_artist_tag'),
        Index('idx_artist_tags_tag', 'tag'),
    )


class TrackTag(db.Model):
    """Tags associated with tracks from Last.fm."""
    __tablename__ = 'track_tags'

    id = db.Column(db.Integer, primary_key=True)
    track_id = db.Column(db.Integer, db.ForeignKey('tracks.id'), nullable=False, index=True)
    tag = db.Column(db.Text, nullable=False)
    count = db.Column(db.Integer, default=0)
    fetched_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('track_id', 'tag', name='uq_track_tag'),
        Index('idx_track_tags_tag', 'tag'),
    )


class SimilarArtist(db.Model):
    """Similar artist relationships from Last.fm."""
    __tablename__ = 'similar_artists'

    id = db.Column(db.Integer, primary_key=True)
    artist_id = db.Column(db.Integer, db.ForeignKey('artists.id'), nullable=False, index=True)
    similar_artist_name = db.Column(db.Text, nullable=False)  # Store name, may not be in our DB
    similar_artist_mbid = db.Column(db.Text, nullable=True)
    match_score = db.Column(db.Float, nullable=False)  # 0.0 to 1.0 similarity score
    fetched_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('artist_id', 'similar_artist_name', name='uq_similar_artist'),
        Index('idx_similar_match', 'match_score'),
    )


class Recommendation(db.Model):
    """Generated recommendations for users."""
    __tablename__ = 'recommendations'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    track_id = db.Column(db.Integer, db.ForeignKey('tracks.id'), nullable=False, index=True)
    recommendation_score = db.Column(db.Float, nullable=False)
    reason = db.Column(db.Text, nullable=True)  # Human-readable explanation
    mode = db.Column(db.Text, nullable=False)  # 'comfort_zone' or 'branch_out'
    popularity_filter = db.Column(db.Text, nullable=True)  # 'mainstream', 'balanced', 'niche'
    session_id = db.Column(db.Text, nullable=True, index=True)  # Group recommendations by session
    generated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    presented_at = db.Column(db.DateTime, nullable=True)  # When shown to user
    feedback = db.Column(db.Text, nullable=True)  # 'like', 'dislike', 'skip', None

    # Relationships
    track = db.relationship('Track', backref='recommendations')

    __table_args__ = (
        Index('idx_recommendations_user_session', 'user_id', 'session_id'),
        Index('idx_recommendations_generated', 'generated_at'),
    )


class RecommendationFeedback(db.Model):
    """Detailed feedback on recommendations for learning."""
    __tablename__ = 'recommendation_feedback'

    id = db.Column(db.Integer, primary_key=True)
    recommendation_id = db.Column(db.Integer, db.ForeignKey('recommendations.id'), nullable=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    track_id = db.Column(db.Integer, db.ForeignKey('tracks.id'), nullable=False, index=True)
    feedback_type = db.Column(db.Text, nullable=False)  # 'like', 'dislike', 'skip'
    source_tags = db.Column(db.Text, nullable=True)  # JSON array of tags that led to this rec
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_feedback_user_type', 'user_id', 'feedback_type'),
    )


class ListeningSession(db.Model):
    """Track co-listening patterns - artists/tracks played together."""
    __tablename__ = 'listening_sessions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    session_start = db.Column(db.DateTime, nullable=False)
    session_end = db.Column(db.DateTime, nullable=True)
    track_ids = db.Column(db.Text, nullable=True)  # JSON array of track IDs
    artist_ids = db.Column(db.Text, nullable=True)  # JSON array of artist IDs
    track_count = db.Column(db.Integer, default=0)

    __table_args__ = (
        Index('idx_sessions_user_time', 'user_id', 'session_start'),
    )


class CoListeningPattern(db.Model):
    """Pre-computed co-listening relationships between artists."""
    __tablename__ = 'co_listening_patterns'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    artist_id_1 = db.Column(db.Integer, db.ForeignKey('artists.id'), nullable=False, index=True)
    artist_id_2 = db.Column(db.Integer, db.ForeignKey('artists.id'), nullable=False, index=True)
    co_occurrence_count = db.Column(db.Integer, default=0)  # Times played in same session
    affinity_score = db.Column(db.Float, nullable=True)  # Normalized 0-1 score
    computed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('user_id', 'artist_id_1', 'artist_id_2', name='uq_co_listening'),
        Index('idx_co_listening_artists', 'artist_id_1', 'artist_id_2'),
    )


def init_db(app):
    """Initialize database with app context."""
    db.init_app(app)
    with app.app_context():
        db.create_all()
