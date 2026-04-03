"""
Flask application for Last.fm Listening History Tracker.
"""

import os
import csv
import io
import time
import logging
from datetime import datetime, timedelta
from functools import wraps

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, request, jsonify, render_template, Response, redirect
from flask_apscheduler import APScheduler
from sqlalchemy import func

from config import get_config, Config
from models import (
    db, init_db, migrate_db, User, Artist, Album, Track, Scrobble, LovedTrack, SyncLog,
    ArtistTag, SimilarArtist, Recommendation, RecommendationFeedback,
    ListeningSession, CoListeningPattern, TrackTag
)
from lastfm_client import LastFMClient, LastFMAuthError
from sync_service import SyncService, is_sync_running, run_scheduled_sync
from metrics import MetricsService, compute_all_metrics
from recommender import (
    generate_recommendations, record_feedback, get_recommendation_stats,
    RecommendationEngine
)
from spotify_client import (
    search_track as spotify_search_track, create_playlist as spotify_create_playlist,
    get_spotify_status, export_for_spotify, is_spotify_configured, SpotifyClient
)
from enhanced_sync_service import (
    EnhancedSyncService, is_enhanced_sync_running, run_enhanced_sync,
    get_enhanced_sync_status
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.config.from_object(get_config())

# Initialize database
init_db(app)
migrate_db(app)

# Initialize scheduler
scheduler = APScheduler()


def get_current_user():
    """Get the current user (single-user mode)."""
    return User.query.first()


def require_configured(f):
    """Decorator to require user configuration."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({'error': 'Not configured. Please set up your Last.fm credentials.'}), 400
        return f(*args, **kwargs)
    return decorated


# =============================================================================
# Frontend Routes
# =============================================================================

@app.route('/')
def index():
    """Serve the main dashboard."""
    return render_template('index.html')


@app.route('/discover')
def discover():
    """Serve the recommendation discovery page."""
    return render_template('discover.html')


# =============================================================================
# Configuration Endpoints
# =============================================================================

@app.route('/api/config', methods=['GET'])
def get_config_api():
    """Get current configuration."""
    user = get_current_user()

    if not user:
        return jsonify({
            'configured': False,
            'username': None,
            'last_sync': None,
            'sync_interval': Config.DEFAULT_SYNC_INTERVAL_MINUTES
        })

    return jsonify({
        'configured': True,
        'username': user.lastfm_username,
        'last_sync': user.last_sync_at.isoformat() if user.last_sync_at else None,
        'sync_interval': user.sync_interval_minutes,
        'total_scrobbles': user.total_scrobbles
    })


@app.route('/api/config', methods=['POST'])
def save_config():
    """Save Last.fm credentials."""
    data = request.get_json()

    username = data.get('username', '').strip()
    api_key = data.get('api_key', '').strip()
    sync_interval = data.get('sync_interval', Config.DEFAULT_SYNC_INTERVAL_MINUTES)

    if not username or not api_key:
        return jsonify({'error': 'Username and API key are required'}), 400

    # Verify credentials
    client = LastFMClient(api_key=api_key, username=username)
    try:
        if not client.verify_credentials():
            return jsonify({'error': 'Invalid API key or username'}), 400
    except LastFMAuthError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f'Failed to verify credentials: {e}'}), 400

    # Create or update user
    user = get_current_user()
    if user:
        user.lastfm_username = username
        user.api_key = api_key
        user.sync_interval_minutes = sync_interval
    else:
        user = User(
            lastfm_username=username,
            api_key=api_key,
            sync_interval_minutes=sync_interval
        )
        db.session.add(user)

    db.session.commit()

    # Update scheduler
    update_sync_schedule(user)

    return jsonify({'success': True, 'message': 'Configuration saved'})


# =============================================================================
# Sync Endpoints
# =============================================================================

@app.route('/api/sync', methods=['POST'])
@require_configured
def trigger_sync():
    """Trigger a manual sync."""
    if is_sync_running():
        return jsonify({'status': 'already_running', 'message': 'Sync already in progress'})

    user = get_current_user()
    data = request.get_json() if request.is_json else {}
    initial = data.get('initial', False)
    force_full = data.get('force_full', False)

    # Run sync in background thread
    from threading import Thread

    def do_sync():
        try:
            with app.app_context():
                logger.info(f"Starting sync (initial={initial}, force_full={force_full})")
                service = SyncService(user)
                success, message = service.full_sync(initial=initial, force_full=force_full)
                logger.info(f"Sync result: success={success}, message={message}")
                if success:
                    compute_all_metrics(user)
                    # Trigger artist image fetch after sync
                    fetch_artist_images_batch()
        except Exception as e:
            logger.exception(f"Sync thread error: {e}")

    thread = Thread(target=do_sync)
    thread.start()

    return jsonify({
        'status': 'started',
        'message': 'Sync started'
    })


@app.route('/api/sync/status', methods=['GET'])
def get_sync_status():
    """Get current sync status."""
    user = get_current_user()

    if not user:
        return jsonify({
            'configured': False,
            'is_syncing': False
        })

    # Get latest sync log
    latest_sync = SyncLog.query.filter_by(user_id=user.id)\
        .order_by(SyncLog.started_at.desc()).first()

    return jsonify({
        'configured': True,
        'is_syncing': is_sync_running(),
        'last_sync': user.last_sync_at.isoformat() if user.last_sync_at else None,
        'last_sync_status': latest_sync.status if latest_sync else None,
        'scrobbles_last_sync': latest_sync.scrobbles_fetched if latest_sync else 0,
        'total_scrobbles': user.total_scrobbles
    })


# =============================================================================
# Data Retrieval Endpoints
# =============================================================================

@app.route('/api/scrobbles', methods=['GET'])
@require_configured
def get_scrobbles():
    """Get recent scrobbles with pagination."""
    user = get_current_user()

    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 200)
    from_date = request.args.get('from')
    to_date = request.args.get('to')
    artist_filter = request.args.get('artist')

    query = db.session.query(
        Scrobble.id,
        Scrobble.listened_at,
        Track.id.label('track_id'),
        Track.name.label('track_name'),
        Artist.id.label('artist_id'),
        Artist.name.label('artist_name'),
        Album.name.label('album_name'),
        Album.image_url
    ).join(Track, Scrobble.track_id == Track.id)\
     .join(Artist, Track.artist_id == Artist.id)\
     .outerjoin(Album, Track.album_id == Album.id)\
     .filter(Scrobble.user_id == user.id)

    # Apply filters
    if from_date:
        try:
            from_dt = datetime.fromisoformat(from_date.replace('Z', '+00:00'))
            query = query.filter(Scrobble.listened_at >= from_dt)
        except ValueError:
            pass

    if to_date:
        try:
            to_dt = datetime.fromisoformat(to_date.replace('Z', '+00:00'))
            query = query.filter(Scrobble.listened_at <= to_dt)
        except ValueError:
            pass

    if artist_filter:
        query = query.filter(Artist.name.ilike(f'%{artist_filter}%'))

    # Order and paginate
    query = query.order_by(Scrobble.listened_at.desc())
    total = query.count()
    scrobbles = query.offset((page - 1) * per_page).limit(per_page).all()

    return jsonify({
        'scrobbles': [
            {
                'id': s.id,
                'track': s.track_name,
                'artist': s.artist_name,
                'album': s.album_name,
                'listened_at': s.listened_at.isoformat(),
                'image_url': s.image_url,
                'track_id': s.track_id,
                'artist_id': s.artist_id
            }
            for s in scrobbles
        ],
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': total,
            'pages': (total + per_page - 1) // per_page
        }
    })


@app.route('/api/stats', methods=['GET'])
@require_configured
def get_stats():
    """Get aggregated statistics."""
    user = get_current_user()
    service = MetricsService(user)
    return jsonify(service.get_basic_stats())


@app.route('/api/top/artists', methods=['GET'])
@require_configured
def get_top_artists():
    """Get top artists."""
    user = get_current_user()
    period = request.args.get('period', 'all')
    limit = min(request.args.get('limit', 10, type=int), 100)

    service = MetricsService(user)
    artists = service.get_top_artists(period=period, limit=limit)

    return jsonify({
        'artists': artists,
        'period': period
    })


@app.route('/api/top/tracks', methods=['GET'])
@require_configured
def get_top_tracks():
    """Get top tracks."""
    user = get_current_user()
    period = request.args.get('period', 'all')
    limit = min(request.args.get('limit', 10, type=int), 100)

    service = MetricsService(user)
    tracks = service.get_top_tracks(period=period, limit=limit)

    return jsonify({
        'tracks': tracks,
        'period': period
    })


@app.route('/api/top/albums', methods=['GET'])
@require_configured
def get_top_albums():
    """Get top albums."""
    user = get_current_user()
    period = request.args.get('period', 'all')
    limit = min(request.args.get('limit', 10, type=int), 100)

    service = MetricsService(user)
    albums = service.get_top_albums(period=period, limit=limit)

    return jsonify({
        'albums': albums,
        'period': period
    })


@app.route('/api/loved', methods=['GET'])
@require_configured
def get_loved_tracks():
    """Get loved tracks."""
    user = get_current_user()

    loved = db.session.query(
        LovedTrack.loved_at,
        Track.name.label('track_name'),
        Artist.name.label('artist_name'),
        Album.name.label('album_name'),
        Album.image_url
    ).join(Track, LovedTrack.track_id == Track.id)\
     .join(Artist, Track.artist_id == Artist.id)\
     .outerjoin(Album, Track.album_id == Album.id)\
     .filter(LovedTrack.user_id == user.id)\
     .order_by(LovedTrack.loved_at.desc())\
     .all()

    return jsonify({
        'loved_tracks': [
            {
                'track': l.track_name,
                'artist': l.artist_name,
                'album': l.album_name,
                'loved_at': l.loved_at.isoformat(),
                'image_url': l.image_url
            }
            for l in loved
        ],
        'total': len(loved)
    })


# =============================================================================
# Metrics Endpoints
# =============================================================================

@app.route('/api/metrics/listening-patterns', methods=['GET'])
@require_configured
def get_listening_patterns():
    """Get time-of-day listening patterns."""
    user = get_current_user()
    service = MetricsService(user)
    return jsonify(service.get_listening_patterns())


@app.route('/api/metrics/streaks', methods=['GET'])
@require_configured
def get_streaks():
    """Get listening streak information."""
    user = get_current_user()
    service = MetricsService(user)
    return jsonify(service.get_listening_streak())


@app.route('/api/metrics/activity', methods=['GET'])
@require_configured
def get_activity():
    """Get recent daily activity."""
    user = get_current_user()
    days = request.args.get('days', 30, type=int)

    service = MetricsService(user)
    return jsonify({
        'activity': service.get_recent_activity(days=days)
    })


# =============================================================================
# Recommendation Data Endpoints
# =============================================================================

@app.route('/api/recommendation-data', methods=['GET'])
@require_configured
def get_recommendation_data():
    """Get recommendation system data collection status and samples."""
    user = get_current_user()

    # Get counts
    total_artists = Artist.query.count()
    artists_with_tags = db.session.query(ArtistTag.artist_id).distinct().count()
    artists_with_similar = db.session.query(SimilarArtist.artist_id).distinct().count()
    total_tags = ArtistTag.query.count()
    total_similar = SimilarArtist.query.count()

    # Get unique tag names
    unique_tags = db.session.query(ArtistTag.tag).distinct().count()

    # Get top tags by frequency
    top_tags = db.session.query(
        ArtistTag.tag,
        func.count(ArtistTag.id).label('artist_count'),
        func.sum(ArtistTag.count).label('total_weight')
    ).group_by(ArtistTag.tag)\
     .order_by(func.count(ArtistTag.id).desc())\
     .limit(20).all()

    # Get sample artist tags (top 10 artists by plays)
    sample_artist_tags = db.session.query(
        Artist.id,
        Artist.name,
        Artist.image_url,
        func.count(Scrobble.id).label('play_count')
    ).outerjoin(Track, Track.artist_id == Artist.id)\
     .outerjoin(Scrobble, Scrobble.track_id == Track.id)\
     .filter(Artist.id.in_(db.session.query(ArtistTag.artist_id).distinct()))\
     .group_by(Artist.id)\
     .order_by(func.count(Scrobble.id).desc())\
     .limit(10).all()

    artist_tag_samples = []
    for artist in sample_artist_tags:
        tags = ArtistTag.query.filter_by(artist_id=artist.id)\
            .order_by(ArtistTag.count.desc()).limit(5).all()
        artist_tag_samples.append({
            'id': artist.id,
            'name': artist.name,
            'image_url': artist.image_url,
            'play_count': artist.play_count,
            'tags': [{'name': t.tag, 'count': t.count} for t in tags]
        })

    # Get sample similar artists
    sample_similar = db.session.query(
        Artist.id,
        Artist.name,
        Artist.image_url
    ).filter(Artist.id.in_(db.session.query(SimilarArtist.artist_id).distinct()))\
     .limit(10).all()

    similar_samples = []
    for artist in sample_similar:
        similar = SimilarArtist.query.filter_by(artist_id=artist.id)\
            .order_by(SimilarArtist.match_score.desc()).limit(5).all()
        similar_samples.append({
            'id': artist.id,
            'name': artist.name,
            'image_url': artist.image_url,
            'similar': [{'name': s.similar_artist_name, 'match': s.match_score} for s in similar]
        })

    return jsonify({
        'progress': {
            'total_artists': total_artists,
            'artists_with_tags': artists_with_tags,
            'artists_with_similar': artists_with_similar,
            'tags_progress_pct': round(artists_with_tags / total_artists * 100, 1) if total_artists > 0 else 0,
            'similar_progress_pct': round(artists_with_similar / total_artists * 100, 1) if total_artists > 0 else 0,
        },
        'stats': {
            'total_tags': total_tags,
            'unique_tags': unique_tags,
            'total_similar_relationships': total_similar,
        },
        'top_tags': [
            {'tag': t.tag, 'artist_count': t.artist_count, 'total_weight': t.total_weight}
            for t in top_tags
        ],
        'artist_tag_samples': artist_tag_samples,
        'similar_artist_samples': similar_samples,
    })


# =============================================================================
# Recommendation API Endpoints
# =============================================================================

@app.route('/api/recommendations/generate', methods=['POST'])
@require_configured
def api_generate_recommendations():
    """
    Generate personalized track recommendations.

    Request body:
    {
        "time_period": "week|month|year|all",
        "selected_artists": [artist_id, ...],  // optional
        "mode": "comfort_zone|branch_out",
        "popularity": "mainstream|balanced|niche"
    }
    """
    user = get_current_user()
    data = request.get_json() or {}

    time_period = data.get('time_period', 'month')
    selected_artists = data.get('selected_artists', None)
    mode = data.get('mode', 'comfort_zone')
    popularity = data.get('popularity', 'balanced')

    # Validate inputs
    if time_period not in ['week', 'month', 'year', 'all']:
        return jsonify({'error': 'Invalid time_period'}), 400
    if mode not in ['comfort_zone', 'branch_out']:
        return jsonify({'error': 'Invalid mode'}), 400
    if popularity not in ['mainstream', 'balanced', 'niche']:
        return jsonify({'error': 'Invalid popularity level'}), 400

    try:
        result = generate_recommendations(
            user_id=user.id,
            time_period=time_period,
            selected_artists=selected_artists,
            mode=mode,
            popularity_level=popularity
        )
        return jsonify(result)
    except Exception as e:
        logger.exception("Failed to generate recommendations")
        return jsonify({'error': str(e)}), 500


@app.route('/api/recommendations/feedback', methods=['POST'])
@require_configured
def api_recommendation_feedback():
    """
    Record user feedback on a recommendation.

    Request body:
    {
        "recommendation_id": int,  // optional
        "track_id": int,
        "feedback_type": "like|dislike|skip"
    }
    """
    user = get_current_user()
    data = request.get_json() or {}

    recommendation_id = data.get('recommendation_id')
    track_id = data.get('track_id')
    feedback_type = data.get('feedback_type')

    if not track_id or not feedback_type:
        return jsonify({'error': 'track_id and feedback_type are required'}), 400

    if feedback_type not in ['like', 'dislike', 'skip']:
        return jsonify({'error': 'Invalid feedback_type'}), 400

    success = record_feedback(
        user_id=user.id,
        recommendation_id=recommendation_id,
        track_id=track_id,
        feedback_type=feedback_type
    )

    if success:
        return jsonify({'success': True, 'message': 'Feedback recorded'})
    else:
        return jsonify({'error': 'Failed to record feedback'}), 500


@app.route('/api/recommendations/stats', methods=['GET'])
@require_configured
def api_recommendation_stats():
    """Get recommendation statistics for the current user."""
    user = get_current_user()
    stats = get_recommendation_stats(user.id)
    return jsonify(stats)


@app.route('/api/recommendations/history', methods=['GET'])
@require_configured
def api_recommendation_history():
    """Get recent recommendation sessions."""
    user = get_current_user()
    limit = request.args.get('limit', 10, type=int)

    # Get unique sessions
    sessions = db.session.query(
        Recommendation.session_id,
        Recommendation.mode,
        Recommendation.popularity_filter,
        Recommendation.generated_at,
        func.count(Recommendation.id).label('count'),
        func.avg(Recommendation.recommendation_score).label('avg_score')
    ).filter(
        Recommendation.user_id == user.id,
        Recommendation.session_id.isnot(None)
    ).group_by(
        Recommendation.session_id
    ).order_by(
        Recommendation.generated_at.desc()
    ).limit(limit).all()

    return jsonify({
        'sessions': [
            {
                'session_id': s.session_id,
                'mode': s.mode,
                'popularity_filter': s.popularity_filter,
                'generated_at': s.generated_at.isoformat() if s.generated_at else None,
                'recommendation_count': s.count,
                'avg_score': round(s.avg_score, 3) if s.avg_score else None
            }
            for s in sessions
        ]
    })


@app.route('/api/recommendations/liked', methods=['GET'])
@require_configured
def api_liked_tracks():
    """Get tracks the user has liked."""
    user = get_current_user()
    limit = request.args.get('limit', 50, type=int)

    liked = db.session.query(
        RecommendationFeedback, Track, Artist
    ).join(
        Track, RecommendationFeedback.track_id == Track.id
    ).join(
        Artist, Track.artist_id == Artist.id
    ).filter(
        RecommendationFeedback.user_id == user.id,
        RecommendationFeedback.feedback_type == 'like'
    ).order_by(
        RecommendationFeedback.timestamp.desc()
    ).limit(limit).all()

    return jsonify({
        'liked_tracks': [
            {
                'track_id': track.id,
                'track_name': track.name,
                'artist_name': artist.name,
                'album_name': track.album.name if track.album else None,
                'album_image_url': track.album.image_url if track.album else None,
                'preview_url': track.spotify_preview_url,
                'spotify_uri': track.spotify_uri,
                'liked_at': feedback.timestamp.isoformat() if feedback.timestamp else None
            }
            for feedback, track, artist in liked
        ],
        'total': len(liked)
    })


# =============================================================================
# Spotify Integration Endpoints
# =============================================================================

@app.route('/api/spotify/status', methods=['GET'])
def api_spotify_status():
    """Get Spotify integration status."""
    user = get_current_user()
    user_id = user.id if user else None
    return jsonify(get_spotify_status(user_id))


@app.route('/api/spotify/search-track', methods=['POST'])
@require_configured
def api_spotify_search():
    """
    Search Spotify for a track from our database.

    Request body:
    {
        "track_id": int
    }
    """
    data = request.get_json() or {}
    track_id = data.get('track_id')

    if not track_id:
        return jsonify({'error': 'track_id is required'}), 400

    result = spotify_search_track(track_id)
    return jsonify(result)


@app.route('/api/spotify/create-playlist', methods=['POST'])
@require_configured
def api_spotify_create_playlist():
    """
    Create a Spotify playlist from track IDs.

    Note: Currently returns export data for manual playlist creation
    while Spotify API access is pending.

    Request body:
    {
        "track_ids": [int, ...],
        "playlist_name": "My Discovery Playlist"
    }
    """
    user = get_current_user()
    data = request.get_json() or {}

    track_ids = data.get('track_ids', [])
    playlist_name = data.get('playlist_name', 'Last.fm Discovery Playlist')

    if not track_ids:
        return jsonify({'error': 'track_ids is required'}), 400

    result = spotify_create_playlist(user.id, track_ids, playlist_name)
    return jsonify(result)


@app.route('/api/spotify/export', methods=['POST'])
@require_configured
def api_spotify_export():
    """
    Export tracks for manual Spotify playlist creation.

    Request body:
    {
        "track_ids": [int, ...],
        "format": "json|text"
    }
    """
    data = request.get_json() or {}
    track_ids = data.get('track_ids', [])
    format_type = data.get('format', 'json')

    if not track_ids:
        return jsonify({'error': 'track_ids is required'}), 400

    result = export_for_spotify(track_ids, format=format_type)
    return jsonify(result)


@app.route('/api/spotify/authenticate', methods=['GET'])
@require_configured
def api_spotify_authenticate():
    """Start Spotify OAuth flow. Returns URL to redirect user to."""
    if not is_spotify_configured():
        return jsonify({
            'error': 'Spotify not configured. Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET.'
        }), 400

    user = get_current_user()
    client = SpotifyClient(user.id)
    auth_url = client.get_auth_url()

    return jsonify({'auth_url': auth_url})


@app.route('/api/spotify/callback')
def api_spotify_callback():
    """Handle Spotify OAuth callback."""
    code = request.args.get('code')
    error = request.args.get('error')

    if error:
        logger.warning(f"Spotify OAuth error: {error}")
        return redirect('/discover')

    if not code:
        return jsonify({'error': 'No authorization code received'}), 400

    user = get_current_user()
    if not user:
        return jsonify({'error': 'No user configured'}), 400

    client = SpotifyClient(user.id)
    try:
        client.handle_callback(code)
        logger.info("Spotify OAuth completed successfully")
    except Exception as e:
        logger.error(f"Spotify OAuth callback failed: {e}")

    return redirect('/discover')


@app.route('/api/spotify/disconnect', methods=['POST'])
@require_configured
def api_spotify_disconnect():
    """Disconnect Spotify account."""
    user = get_current_user()
    client = SpotifyClient(user.id)
    result = client.disconnect()
    return jsonify(result)


# =============================================================================
# Enhanced Sync Endpoints
# =============================================================================

@app.route('/api/enhanced-sync', methods=['POST'])
@require_configured
def api_trigger_enhanced_sync():
    """
    Trigger enhanced sync (tags, similar artists, co-listening patterns).

    This is designed to run on-demand or weekly, not with every sync.
    """
    if is_enhanced_sync_running():
        return jsonify({
            'status': 'already_running',
            'message': 'Enhanced sync already in progress'
        })

    user = get_current_user()
    data = request.get_json() or {}
    max_artists = data.get('max_artists', 50)
    max_tracks = data.get('max_tracks', 100)

    # Run in background thread
    from threading import Thread

    def do_enhanced_sync():
        with app.app_context():
            service = EnhancedSyncService(user)
            result = service.full_enhanced_sync(max_artists, max_tracks)
            logger.info(f"Enhanced sync completed: {result}")

    thread = Thread(target=do_enhanced_sync)
    thread.start()

    return jsonify({
        'status': 'started',
        'message': 'Enhanced sync started'
    })


@app.route('/api/enhanced-sync/status', methods=['GET'])
@require_configured
def api_enhanced_sync_status():
    """Get enhanced sync status and recommendation data availability."""
    user = get_current_user()
    status = get_enhanced_sync_status(user.id)
    return jsonify(status)


# =============================================================================
# Export Endpoints
# =============================================================================

@app.route('/api/export', methods=['GET'])
@require_configured
def export_data():
    """Export data in various formats."""
    user = get_current_user()
    format_type = request.args.get('format', 'json')
    data_type = request.args.get('type', 'scrobbles')
    from_date = request.args.get('from')
    to_date = request.args.get('to')

    if data_type == 'scrobbles':
        query = db.session.query(
            Scrobble.id,
            Scrobble.listened_at,
            Track.id.label('track_id'),
            Track.name.label('track_name'),
            Track.lastfm_mbid.label('track_mbid'),
            Track.spotify_id,
            Artist.id.label('artist_id'),
            Artist.name.label('artist_name'),
            Artist.lastfm_mbid.label('artist_mbid'),
            Album.id.label('album_id'),
            Album.name.label('album_name'),
            Album.lastfm_mbid.label('album_mbid')
        ).join(Track, Scrobble.track_id == Track.id)\
         .join(Artist, Track.artist_id == Artist.id)\
         .outerjoin(Album, Track.album_id == Album.id)\
         .filter(Scrobble.user_id == user.id)

        if from_date:
            try:
                from_dt = datetime.fromisoformat(from_date.replace('Z', '+00:00'))
                query = query.filter(Scrobble.listened_at >= from_dt)
            except ValueError:
                pass

        if to_date:
            try:
                to_dt = datetime.fromisoformat(to_date.replace('Z', '+00:00'))
                query = query.filter(Scrobble.listened_at <= to_dt)
            except ValueError:
                pass

        query = query.order_by(Scrobble.listened_at.desc())
        data = query.limit(Config.EXPORT_MAX_ROWS).all()

        columns = ['id', 'listened_at', 'track_id', 'track_name', 'track_mbid',
                   'spotify_id', 'artist_id', 'artist_name', 'artist_mbid',
                   'album_id', 'album_name', 'album_mbid']

    elif data_type == 'tracks':
        query = db.session.query(
            Track.id,
            Track.name,
            Track.lastfm_mbid,
            Track.spotify_id,
            Track.spotify_uri,
            Track.isrc,
            Artist.id.label('artist_id'),
            Artist.name.label('artist_name'),
            Album.id.label('album_id'),
            Album.name.label('album_name'),
            func.count(Scrobble.id).label('play_count')
        ).join(Artist, Track.artist_id == Artist.id)\
         .outerjoin(Album, Track.album_id == Album.id)\
         .outerjoin(Scrobble, Scrobble.track_id == Track.id)\
         .group_by(Track.id)\
         .order_by(func.count(Scrobble.id).desc())

        data = query.limit(Config.EXPORT_MAX_ROWS).all()
        columns = ['id', 'name', 'lastfm_mbid', 'spotify_id', 'spotify_uri',
                   'isrc', 'artist_id', 'artist_name', 'album_id', 'album_name', 'play_count']

    elif data_type == 'artists':
        query = db.session.query(
            Artist.id,
            Artist.name,
            Artist.lastfm_mbid,
            Artist.url,
            func.count(Scrobble.id).label('play_count')
        ).outerjoin(Track, Track.artist_id == Artist.id)\
         .outerjoin(Scrobble, Scrobble.track_id == Track.id)\
         .group_by(Artist.id)\
         .order_by(func.count(Scrobble.id).desc())

        data = query.limit(Config.EXPORT_MAX_ROWS).all()
        columns = ['id', 'name', 'lastfm_mbid', 'url', 'play_count']

    else:
        return jsonify({'error': 'Invalid data type'}), 400

    # Format output
    if format_type == 'csv':
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)

        for row in data:
            writer.writerow([
                getattr(row, col).isoformat() if isinstance(getattr(row, col), datetime) else getattr(row, col)
                for col in columns
            ])

        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={data_type}_export.csv'}
        )

    else:  # JSON
        result = []
        for row in data:
            item = {}
            for col in columns:
                val = getattr(row, col)
                if isinstance(val, datetime):
                    val = val.isoformat()
                item[col] = val
            result.append(item)

        return jsonify({
            'data': result,
            'count': len(result),
            'type': data_type
        })


# =============================================================================
# Scheduler Setup
# =============================================================================

def scheduled_sync_wrapper():
    """Wrapper for scheduled sync that handles app context."""
    with app.app_context():
        user = User.query.first()
        if not user:
            logger.warning("No user configured, skipping scheduled sync")
            return

        from sync_service import SyncService
        service = SyncService(user)
        success, message = service.full_sync(initial=False)

        if success:
            compute_all_metrics(user)
            logger.info(f"Scheduled sync completed: {message}")
            # Trigger artist image fetch after sync
            fetch_artist_images_batch()
        else:
            logger.error(f"Scheduled sync failed: {message}")


def fetch_artist_images_batch(batch_size: int = 25):
    """
    Fetch images for artists that don't have them yet.

    Rate-limited to ~1 request per second to be gentle on the API.
    Processes a batch of artists per call.
    """
    with app.app_context():
        user = User.query.first()
        if not user:
            return

        # Get artists without images, prioritizing those with most plays
        artists_needing_images = db.session.query(
            Artist.id,
            Artist.name,
            func.count(Scrobble.id).label('play_count')
        ).outerjoin(Track, Track.artist_id == Artist.id)\
         .outerjoin(Scrobble, Scrobble.track_id == Track.id)\
         .filter(Artist.image_url.is_(None))\
         .group_by(Artist.id)\
         .order_by(func.count(Scrobble.id).desc())\
         .limit(batch_size)\
         .all()

        if not artists_needing_images:
            logger.debug("No artists need images")
            return

        from lastfm_client import LastFMClient
        import time

        client = LastFMClient(
            api_key=user.api_key,
            username=user.lastfm_username,
            calls_per_second=1.0  # Rate limit to 1 call/second for image fetching
        )

        fetched_count = 0
        for artist_row in artists_needing_images:
            try:
                image_url = client.get_artist_image(artist_row.name)
                if image_url:
                    artist = Artist.query.get(artist_row.id)
                    if artist:
                        artist.image_url = image_url
                        db.session.commit()
                        fetched_count += 1
                        logger.debug(f"Fetched image for {artist_row.name}")
                else:
                    # Mark as checked by setting empty string (so we don't retry)
                    artist = Artist.query.get(artist_row.id)
                    if artist:
                        artist.image_url = ''  # Empty string = checked but no image
                        db.session.commit()
            except Exception as e:
                logger.warning(f"Failed to fetch image for {artist_row.name}: {e}")

            # Extra delay between requests
            time.sleep(0.5)

        if fetched_count > 0:
            logger.info(f"Fetched images for {fetched_count} artists")


def fetch_artist_tags_batch(batch_size: int = 25):
    """
    Fetch tags for artists that don't have them yet.
    Prioritizes artists with most plays.
    """
    with app.app_context():
        user = User.query.first()
        if not user:
            return

        # Get artists without tags, prioritizing those with most plays
        # Subquery to find artists that already have tags
        artists_with_tags = db.session.query(ArtistTag.artist_id).distinct()

        artists_needing_tags = db.session.query(
            Artist.id,
            Artist.name,
            func.count(Scrobble.id).label('play_count')
        ).outerjoin(Track, Track.artist_id == Artist.id)\
         .outerjoin(Scrobble, Scrobble.track_id == Track.id)\
         .filter(~Artist.id.in_(artists_with_tags))\
         .group_by(Artist.id)\
         .order_by(func.count(Scrobble.id).desc())\
         .limit(batch_size)\
         .all()

        if not artists_needing_tags:
            logger.debug("No artists need tags")
            return

        from lastfm_client import LastFMClient
        import time

        client = LastFMClient(
            api_key=user.api_key,
            username=user.lastfm_username,
            calls_per_second=1.0
        )

        fetched_count = 0
        for artist_row in artists_needing_tags:
            try:
                tags = client.get_artist_tags(artist_row.name, limit=10)
                for tag_data in tags:
                    tag_name = tag_data.get('name', '').lower().strip()
                    tag_count = int(tag_data.get('count', 0))
                    if tag_name:
                        artist_tag = ArtistTag(
                            artist_id=artist_row.id,
                            tag=tag_name,
                            count=tag_count
                        )
                        db.session.add(artist_tag)

                # Also fetch listener count for popularity filtering
                artist = Artist.query.get(artist_row.id)
                if artist and artist.lastfm_listeners is None:
                    try:
                        info = client.get_artist_info(artist_row.name)
                        listeners = info.get('stats', {}).get('listeners')
                        playcount = info.get('stats', {}).get('playcount')
                        if listeners:
                            artist.lastfm_listeners = int(listeners)
                        if playcount:
                            artist.lastfm_playcount = int(playcount)
                    except Exception:
                        pass

                db.session.commit()
                fetched_count += 1
                logger.debug(f"Fetched {len(tags)} tags for {artist_row.name}")
            except Exception as e:
                db.session.rollback()
                logger.warning(f"Failed to fetch tags for {artist_row.name}: {e}")

            time.sleep(0.5)

        if fetched_count > 0:
            logger.info(f"Fetched tags for {fetched_count} artists")


def fetch_similar_artists_batch(batch_size: int = 25):
    """
    Fetch similar artists for artists that don't have them yet.
    Prioritizes artists with most plays.
    """
    with app.app_context():
        user = User.query.first()
        if not user:
            return

        # Get artists without similar artists data
        artists_with_similar = db.session.query(SimilarArtist.artist_id).distinct()

        artists_needing_similar = db.session.query(
            Artist.id,
            Artist.name,
            func.count(Scrobble.id).label('play_count')
        ).outerjoin(Track, Track.artist_id == Artist.id)\
         .outerjoin(Scrobble, Scrobble.track_id == Track.id)\
         .filter(~Artist.id.in_(artists_with_similar))\
         .group_by(Artist.id)\
         .order_by(func.count(Scrobble.id).desc())\
         .limit(batch_size)\
         .all()

        if not artists_needing_similar:
            logger.debug("No artists need similar artists data")
            return

        from lastfm_client import LastFMClient
        import time

        client = LastFMClient(
            api_key=user.api_key,
            username=user.lastfm_username,
            calls_per_second=1.0
        )

        fetched_count = 0
        for artist_row in artists_needing_similar:
            try:
                similar_artists = client.get_similar_artists(artist_row.name, limit=20)
                for similar_data in similar_artists:
                    similar_name = similar_data.get('name', '').strip()
                    similar_mbid = similar_data.get('mbid') or None
                    match_score = float(similar_data.get('match', 0))
                    if similar_name:
                        similar = SimilarArtist(
                            artist_id=artist_row.id,
                            similar_artist_name=similar_name,
                            similar_artist_mbid=similar_mbid,
                            match_score=match_score
                        )
                        db.session.add(similar)
                db.session.commit()
                fetched_count += 1
                logger.debug(f"Fetched {len(similar_artists)} similar artists for {artist_row.name}")
            except Exception as e:
                db.session.rollback()
                logger.warning(f"Failed to fetch similar artists for {artist_row.name}: {e}")

            time.sleep(0.5)

        if fetched_count > 0:
            logger.info(f"Fetched similar artists for {fetched_count} artists")


def match_tracks_to_spotify_batch(batch_size: int = 25):
    """
    Batch match tracks to Spotify IDs and fetch popularity scores.

    Prioritizes tracks with most plays that don't have Spotify data.
    Uses client credentials flow (no user auth needed).
    """
    with app.app_context():
        if not is_spotify_configured():
            logger.debug("Spotify not configured, skipping track matching")
            return

        # Get tracks without spotify_id, prioritized by play count
        tracks_needing_match = db.session.query(
            Track.id,
            Track.name,
            Artist.name.label('artist_name'),
            func.count(Scrobble.id).label('play_count')
        ).join(Artist, Track.artist_id == Artist.id)\
         .outerjoin(Scrobble, Scrobble.track_id == Track.id)\
         .filter(Track.spotify_id.is_(None))\
         .group_by(Track.id)\
         .order_by(func.count(Scrobble.id).desc())\
         .limit(batch_size)\
         .all()

        if not tracks_needing_match:
            logger.debug("No tracks need Spotify matching")
            return

        client = SpotifyClient()

        matched_count = 0
        for track_row in tracks_needing_match:
            try:
                result = client.search_track(track_row.name, track_row.artist_name)
                track = Track.query.get(track_row.id)

                if result.get('found') and track:
                    track.spotify_id = result['spotify_id']
                    track.spotify_uri = result['spotify_uri']
                    if result.get('popularity') is not None:
                        track.spotify_popularity = result['popularity']
                        track.spotify_popularity_updated_at = datetime.utcnow()
                    if result.get('preview_url'):
                        track.spotify_preview_url = result['preview_url']
                    db.session.commit()
                    matched_count += 1
                    logger.debug(f"Matched '{track_row.name}' to Spotify: {result['spotify_id']}")
                elif track and not result.get('found') and not result.get('mock'):
                    # Mark as checked so we don't re-search
                    track.spotify_id = ''
                    db.session.commit()
            except Exception as e:
                logger.warning(f"Failed to match '{track_row.name}': {e}")
                db.session.rollback()

            time.sleep(0.5)

        if matched_count > 0:
            logger.info(f"Matched {matched_count} tracks to Spotify")


def refresh_spotify_popularity_batch(batch_size: int = 50):
    """
    Refresh Spotify popularity for tracks that have spotify_id but stale/missing popularity.

    Uses Spotify's batch /tracks endpoint (up to 50 IDs per call).
    """
    with app.app_context():
        if not is_spotify_configured():
            return

        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
        from spotipy.cache_handler import MemoryCacheHandler
        from config import get_config as _get_config

        config = _get_config()
        stale_threshold = datetime.utcnow() - timedelta(days=7)

        tracks_needing_update = Track.query.filter(
            Track.spotify_id.isnot(None),
            Track.spotify_id != '',
            db.or_(
                Track.spotify_popularity.is_(None),
                Track.spotify_popularity_updated_at.is_(None),
                Track.spotify_popularity_updated_at < stale_threshold
            )
        ).order_by(Track.spotify_popularity_updated_at.asc().nullsfirst())\
         .limit(batch_size)\
         .all()

        if not tracks_needing_update:
            return

        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=config.SPOTIFY_CLIENT_ID,
            client_secret=config.SPOTIFY_CLIENT_SECRET,
            cache_handler=MemoryCacheHandler()
        ))

        spotify_ids = [t.spotify_id for t in tracks_needing_update]
        track_map = {t.spotify_id: t for t in tracks_needing_update}

        try:
            results = sp.tracks(spotify_ids)
            updated = 0
            for sp_track in results['tracks']:
                if sp_track and sp_track['id'] in track_map:
                    db_track = track_map[sp_track['id']]
                    db_track.spotify_popularity = sp_track['popularity']
                    db_track.spotify_popularity_updated_at = datetime.utcnow()
                    updated += 1

            db.session.commit()
            if updated:
                logger.info(f"Updated Spotify popularity for {updated} tracks")
        except Exception as e:
            logger.warning(f"Failed to refresh Spotify popularity: {e}")
            db.session.rollback()


def update_sync_schedule(user: User):
    """Update the sync schedule based on user settings."""
    job_id = 'sync_job'

    # Remove existing job if any
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    # Add new job with user's interval
    scheduler.add_job(
        id=job_id,
        func=scheduled_sync_wrapper,
        trigger='interval',
        minutes=user.sync_interval_minutes,
        replace_existing=True
    )

    logger.info(f"Sync scheduled every {user.sync_interval_minutes} minutes")


def init_scheduler():
    """Initialize the background scheduler."""
    scheduler.init_app(app)
    scheduler.start()

    # Set up initial job if user exists
    with app.app_context():
        user = get_current_user()
        if user:
            update_sync_schedule(user)
            logger.info("Scheduler initialized with existing user configuration")

    # Add artist image fetch job (runs every 5 minutes)
    scheduler.add_job(
        id='artist_image_job',
        func=fetch_artist_images_batch,
        trigger='interval',
        minutes=5,
        replace_existing=True
    )
    logger.info("Artist image fetcher scheduled every 5 minutes")

    # Add artist tags fetch job (runs every 5 minutes)
    scheduler.add_job(
        id='artist_tags_job',
        func=fetch_artist_tags_batch,
        trigger='interval',
        minutes=5,
        replace_existing=True
    )
    logger.info("Artist tags fetcher scheduled every 5 minutes")

    # Add similar artists fetch job (runs every 5 minutes)
    scheduler.add_job(
        id='similar_artists_job',
        func=fetch_similar_artists_batch,
        trigger='interval',
        minutes=5,
        replace_existing=True
    )
    logger.info("Similar artists fetcher scheduled every 5 minutes")

    # Add Spotify track matching job (runs every 5 minutes)
    scheduler.add_job(
        id='spotify_match_job',
        func=match_tracks_to_spotify_batch,
        trigger='interval',
        minutes=5,
        replace_existing=True
    )
    logger.info("Spotify track matching scheduled every 5 minutes")

    # Add Spotify popularity refresh job (runs every 10 minutes)
    scheduler.add_job(
        id='spotify_popularity_job',
        func=refresh_spotify_popularity_batch,
        trigger='interval',
        minutes=10,
        replace_existing=True
    )
    logger.info("Spotify popularity refresh scheduled every 10 minutes")


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == '__main__':
    init_scheduler()
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
