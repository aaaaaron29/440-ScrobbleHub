"""
Pre-computed metrics for listening analysis.
"""

from datetime import datetime, date, timedelta
from typing import Dict, List, Optional
from collections import defaultdict

from sqlalchemy import func, extract, case
from models import (
    db, User, Artist, Album, Track, Scrobble, UserMetric,
    Recommendation, RecommendationFeedback, ArtistTag, TrackTag
)


class MetricsService:
    """Service for computing and caching listening metrics."""

    def __init__(self, user: User):
        """Initialize metrics service for a user."""
        self.user = user

    def get_basic_stats(self) -> Dict:
        """
        Get basic listening statistics.

        Returns:
            Dict with total counts and date range
        """
        base_query = Scrobble.query.filter_by(user_id=self.user.id)

        total_scrobbles = base_query.count()

        unique_tracks = db.session.query(func.count(func.distinct(Scrobble.track_id)))\
            .filter(Scrobble.user_id == self.user.id).scalar()

        unique_artists = db.session.query(func.count(func.distinct(Track.artist_id)))\
            .join(Scrobble, Scrobble.track_id == Track.id)\
            .filter(Scrobble.user_id == self.user.id).scalar()

        unique_albums = db.session.query(func.count(func.distinct(Track.album_id)))\
            .join(Scrobble, Scrobble.track_id == Track.id)\
            .filter(Scrobble.user_id == self.user.id)\
            .filter(Track.album_id.isnot(None)).scalar()

        first_scrobble = base_query.order_by(Scrobble.listened_at.asc()).first()
        last_scrobble = base_query.order_by(Scrobble.listened_at.desc()).first()

        # Time-based counts
        today = datetime.utcnow().date()
        week_ago = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)

        scrobbles_today = base_query.filter(
            func.date(Scrobble.listened_at) == today
        ).count()

        scrobbles_week = base_query.filter(
            Scrobble.listened_at >= datetime.combine(week_ago, datetime.min.time())
        ).count()

        scrobbles_month = base_query.filter(
            Scrobble.listened_at >= datetime.combine(month_ago, datetime.min.time())
        ).count()

        return {
            'total_scrobbles': total_scrobbles,
            'unique_tracks': unique_tracks or 0,
            'unique_artists': unique_artists or 0,
            'unique_albums': unique_albums or 0,
            'first_scrobble': first_scrobble.listened_at.isoformat() if first_scrobble else None,
            'last_scrobble': last_scrobble.listened_at.isoformat() if last_scrobble else None,
            'scrobbles_today': scrobbles_today,
            'scrobbles_this_week': scrobbles_week,
            'scrobbles_this_month': scrobbles_month,
        }

    def get_top_artists(self, period: str = 'all', limit: int = 10) -> List[Dict]:
        """
        Get top artists by play count.

        Args:
            period: 'week', 'month', 'year', 'all'
            limit: Number of results

        Returns:
            List of artist dicts with play counts
        """
        query = db.session.query(
            Artist.id,
            Artist.name,
            Artist.image_url,
            func.count(Scrobble.id).label('play_count')
        ).join(Track, Track.artist_id == Artist.id)\
         .join(Scrobble, Scrobble.track_id == Track.id)\
         .filter(Scrobble.user_id == self.user.id)

        # Apply time filter
        query = self._apply_period_filter(query, period)

        results = query.group_by(Artist.id)\
            .order_by(func.count(Scrobble.id).desc())\
            .limit(limit)\
            .all()

        return [
            {
                'id': r.id,
                'name': r.name,
                'image_url': r.image_url,
                'play_count': r.play_count
            }
            for r in results
        ]

    def get_top_tracks(self, period: str = 'all', limit: int = 10) -> List[Dict]:
        """
        Get top tracks by play count.

        Args:
            period: 'week', 'month', 'year', 'all'
            limit: Number of results

        Returns:
            List of track dicts with play counts
        """
        query = db.session.query(
            Track.id,
            Track.name,
            Artist.name.label('artist_name'),
            Album.name.label('album_name'),
            Album.image_url,
            func.count(Scrobble.id).label('play_count')
        ).join(Artist, Track.artist_id == Artist.id)\
         .outerjoin(Album, Track.album_id == Album.id)\
         .join(Scrobble, Scrobble.track_id == Track.id)\
         .filter(Scrobble.user_id == self.user.id)

        query = self._apply_period_filter(query, period)

        results = query.group_by(Track.id)\
            .order_by(func.count(Scrobble.id).desc())\
            .limit(limit)\
            .all()

        return [
            {
                'id': r.id,
                'name': r.name,
                'artist': r.artist_name,
                'album': r.album_name,
                'image_url': r.image_url,
                'play_count': r.play_count
            }
            for r in results
        ]

    def get_top_albums(self, period: str = 'all', limit: int = 10) -> List[Dict]:
        """
        Get top albums by play count.

        Args:
            period: 'week', 'month', 'year', 'all'
            limit: Number of results

        Returns:
            List of album dicts with play counts
        """
        query = db.session.query(
            Album.id,
            Album.name,
            Artist.name.label('artist_name'),
            Album.image_url,
            func.count(Scrobble.id).label('play_count')
        ).join(Track, Track.album_id == Album.id)\
         .join(Artist, Album.artist_id == Artist.id)\
         .join(Scrobble, Scrobble.track_id == Track.id)\
         .filter(Scrobble.user_id == self.user.id)

        query = self._apply_period_filter(query, period)

        results = query.group_by(Album.id)\
            .order_by(func.count(Scrobble.id).desc())\
            .limit(limit)\
            .all()

        return [
            {
                'id': r.id,
                'name': r.name,
                'artist': r.artist_name,
                'image_url': r.image_url,
                'play_count': r.play_count
            }
            for r in results
        ]

    def get_listening_patterns(self) -> Dict:
        """
        Get time-based listening patterns.

        Returns:
            Dict with hourly and daily distributions
        """
        # Hourly distribution
        hourly = db.session.query(
            extract('hour', Scrobble.listened_at).label('hour'),
            func.count(Scrobble.id).label('count')
        ).filter(Scrobble.user_id == self.user.id)\
         .group_by(extract('hour', Scrobble.listened_at))\
         .all()

        hourly_data = {int(h.hour): h.count for h in hourly}
        hourly_result = [
            {'hour': h, 'count': hourly_data.get(h, 0)}
            for h in range(24)
        ]

        # Daily distribution (0=Monday, 6=Sunday in Python)
        # SQLite uses 0=Sunday, so we adjust
        daily = db.session.query(
            extract('dow', Scrobble.listened_at).label('day'),
            func.count(Scrobble.id).label('count')
        ).filter(Scrobble.user_id == self.user.id)\
         .group_by(extract('dow', Scrobble.listened_at))\
         .all()

        day_names = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
        daily_data = {int(d.day): d.count for d in daily}
        daily_result = [
            {'day': day_names[d], 'count': daily_data.get(d, 0)}
            for d in range(7)
        ]

        return {
            'hourly': hourly_result,
            'daily': daily_result
        }

    def get_listening_streak(self) -> Dict:
        """
        Calculate listening streak information.

        Returns:
            Dict with current streak, longest streak, and dates
        """
        # Get all unique listening dates
        dates_query = db.session.query(
            func.date(Scrobble.listened_at).label('listen_date')
        ).filter(Scrobble.user_id == self.user.id)\
         .distinct()\
         .order_by(func.date(Scrobble.listened_at).desc())\
         .all()

        if not dates_query:
            return {
                'current_streak': 0,
                'longest_streak': 0,
                'streak_start': None,
                'streak_end': None
            }

        # Convert to set of date objects
        listen_dates = set()
        for row in dates_query:
            if row.listen_date:
                if isinstance(row.listen_date, str):
                    listen_dates.add(datetime.strptime(row.listen_date, '%Y-%m-%d').date())
                else:
                    listen_dates.add(row.listen_date)

        if not listen_dates:
            return {
                'current_streak': 0,
                'longest_streak': 0,
                'streak_start': None,
                'streak_end': None
            }

        today = date.today()

        # Calculate current streak
        current_streak = 0
        current_date = today if today in listen_dates else today - timedelta(days=1)

        if current_date not in listen_dates:
            current_streak = 0
            streak_start = None
        else:
            while current_date in listen_dates:
                current_streak += 1
                current_date -= timedelta(days=1)
            streak_start = current_date + timedelta(days=1)

        # Calculate longest streak
        sorted_dates = sorted(listen_dates)
        longest_streak = 0
        current_run = 1
        longest_start = sorted_dates[0] if sorted_dates else None
        longest_end = sorted_dates[0] if sorted_dates else None
        run_start = sorted_dates[0] if sorted_dates else None

        for i in range(1, len(sorted_dates)):
            if sorted_dates[i] - sorted_dates[i-1] == timedelta(days=1):
                current_run += 1
            else:
                if current_run > longest_streak:
                    longest_streak = current_run
                    longest_start = run_start
                    longest_end = sorted_dates[i-1]
                current_run = 1
                run_start = sorted_dates[i]

        # Check final run
        if current_run > longest_streak:
            longest_streak = current_run
            longest_start = run_start
            longest_end = sorted_dates[-1] if sorted_dates else None

        return {
            'current_streak': current_streak,
            'longest_streak': longest_streak,
            'current_streak_start': streak_start.isoformat() if streak_start else None,
            'longest_streak_start': longest_start.isoformat() if longest_start else None,
            'longest_streak_end': longest_end.isoformat() if longest_end else None
        }

    def get_recent_activity(self, days: int = 30) -> List[Dict]:
        """
        Get daily scrobble counts for recent period.

        Args:
            days: Number of days to include

        Returns:
            List of daily activity dicts
        """
        start_date = datetime.utcnow() - timedelta(days=days)

        daily = db.session.query(
            func.date(Scrobble.listened_at).label('date'),
            func.count(Scrobble.id).label('count')
        ).filter(
            Scrobble.user_id == self.user.id,
            Scrobble.listened_at >= start_date
        ).group_by(func.date(Scrobble.listened_at))\
         .order_by(func.date(Scrobble.listened_at))\
         .all()

        return [
            {
                'date': str(d.date),
                'count': d.count
            }
            for d in daily
        ]

    def _apply_period_filter(self, query, period: str):
        """Apply time period filter to query."""
        now = datetime.utcnow()

        if period == 'today':
            # Get start of today (midnight UTC)
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == 'week':
            start = now - timedelta(days=7)
        elif period == 'month':
            start = now - timedelta(days=30)
        elif period == 'year':
            start = now - timedelta(days=365)
        else:
            return query

        return query.filter(Scrobble.listened_at >= start)

    # =========================================================================
    # Recommendation Metrics
    # =========================================================================

    def recommendation_effectiveness_score(self) -> Dict:
        """
        Calculate recommendation effectiveness based on user feedback.

        Returns:
            Dict with effectiveness metrics
        """
        total = Recommendation.query.filter_by(user_id=self.user.id).count()
        if total == 0:
            return {
                'effectiveness_score': 0,
                'total_recommendations': 0,
                'feedback_rate': 0,
                'like_ratio': 0,
                'confidence': 'low'
            }

        likes = Recommendation.query.filter_by(
            user_id=self.user.id, feedback='like'
        ).count()
        dislikes = Recommendation.query.filter_by(
            user_id=self.user.id, feedback='dislike'
        ).count()
        skips = Recommendation.query.filter_by(
            user_id=self.user.id, feedback='skip'
        ).count()

        with_feedback = likes + dislikes + skips
        feedback_rate = (with_feedback / total * 100) if total > 0 else 0

        # Calculate effectiveness score
        # Likes contribute positively, dislikes negatively, skips are neutral
        if with_feedback > 0:
            like_ratio = likes / with_feedback
            dislike_ratio = dislikes / with_feedback
            # Effectiveness: weighted combination
            effectiveness = (likes - dislikes * 0.5) / with_feedback * 100
            effectiveness = max(0, min(100, effectiveness))  # Clamp 0-100
        else:
            like_ratio = 0
            dislike_ratio = 0
            effectiveness = 0

        # Confidence level based on sample size
        if with_feedback < 10:
            confidence = 'low'
        elif with_feedback < 50:
            confidence = 'medium'
        else:
            confidence = 'high'

        return {
            'effectiveness_score': round(effectiveness, 1),
            'total_recommendations': total,
            'total_with_feedback': with_feedback,
            'feedback_rate': round(feedback_rate, 1),
            'likes': likes,
            'dislikes': dislikes,
            'skips': skips,
            'like_ratio': round(like_ratio * 100, 1),
            'confidence': confidence
        }

    def tag_preference_weights(self) -> Dict:
        """
        Calculate user's preferred tags based on likes and listening history.

        Returns:
            Dict with weighted tag preferences
        """
        # Get tags from liked recommendations
        liked_recs = Recommendation.query.filter_by(
            user_id=self.user.id, feedback='like'
        ).all()

        liked_tag_weights = defaultdict(float)
        disliked_tag_weights = defaultdict(float)

        # Process liked tracks
        for rec in liked_recs:
            # Get track tags
            track_tags = TrackTag.query.filter_by(track_id=rec.track_id).all()
            for tt in track_tags:
                liked_tag_weights[tt.tag.lower()] += tt.count

            # Get artist tags
            track = Track.query.get(rec.track_id)
            if track:
                artist_tags = ArtistTag.query.filter_by(artist_id=track.artist_id).all()
                for at in artist_tags:
                    liked_tag_weights[at.tag.lower()] += at.count * 0.5

        # Process disliked tracks
        disliked_recs = Recommendation.query.filter_by(
            user_id=self.user.id, feedback='dislike'
        ).all()

        for rec in disliked_recs:
            track_tags = TrackTag.query.filter_by(track_id=rec.track_id).all()
            for tt in track_tags:
                disliked_tag_weights[tt.tag.lower()] += tt.count

        # Also weight by listening history
        top_tracks = self.get_top_tracks(period='all', limit=100)
        for track_data in top_tracks:
            track_tags = TrackTag.query.filter_by(track_id=track_data['id']).all()
            play_weight = track_data['play_count'] / 10  # Normalize
            for tt in track_tags:
                liked_tag_weights[tt.tag.lower()] += tt.count * play_weight * 0.3

        # Normalize and sort
        if liked_tag_weights:
            max_liked = max(liked_tag_weights.values())
            liked_normalized = {
                k: round(v / max_liked * 100, 1)
                for k, v in liked_tag_weights.items()
            }
        else:
            liked_normalized = {}

        if disliked_tag_weights:
            max_disliked = max(disliked_tag_weights.values())
            disliked_normalized = {
                k: round(v / max_disliked * 100, 1)
                for k, v in disliked_tag_weights.items()
            }
        else:
            disliked_normalized = {}

        # Top preferred tags
        top_preferred = sorted(
            liked_normalized.items(),
            key=lambda x: x[1],
            reverse=True
        )[:20]

        # Top avoided tags
        top_avoided = sorted(
            disliked_normalized.items(),
            key=lambda x: x[1],
            reverse=True
        )[:10]

        return {
            'preferred_tags': [{'tag': k, 'weight': v} for k, v in top_preferred],
            'avoided_tags': [{'tag': k, 'weight': v} for k, v in top_avoided],
            'total_liked_tags': len(liked_normalized),
            'total_disliked_tags': len(disliked_normalized)
        }

    def discovery_diversity_score(self) -> Dict:
        """
        Calculate how diverse the user's recommendations have been.

        Measures variety across artists, genres, and modes.

        Returns:
            Dict with diversity metrics
        """
        # Get all recommendations with feedback
        recs = Recommendation.query.filter(
            Recommendation.user_id == self.user.id,
            Recommendation.feedback.isnot(None)
        ).all()

        if not recs:
            return {
                'diversity_score': 0,
                'unique_artists_recommended': 0,
                'unique_genres': 0,
                'mode_distribution': {},
                'confidence': 'low'
            }

        # Count unique artists
        artist_ids = set()
        genres = set()

        for rec in recs:
            track = Track.query.get(rec.track_id)
            if track:
                artist_ids.add(track.artist_id)

                # Get genres from artist tags
                artist_tags = ArtistTag.query.filter_by(artist_id=track.artist_id).all()
                for at in artist_tags:
                    genres.add(at.tag.lower())

        # Mode distribution
        comfort_count = sum(1 for r in recs if r.mode == 'comfort_zone')
        branch_count = sum(1 for r in recs if r.mode == 'branch_out')

        # Calculate diversity score
        # Based on: unique artists ratio, genre variety, mode balance
        unique_artist_ratio = len(artist_ids) / len(recs) if recs else 0
        genre_variety = min(1, len(genres) / 50)  # Cap at 50 genres
        mode_balance = 1 - abs(comfort_count - branch_count) / len(recs) if recs else 0

        diversity_score = (
            unique_artist_ratio * 40 +
            genre_variety * 40 +
            mode_balance * 20
        )

        # Confidence
        if len(recs) < 20:
            confidence = 'low'
        elif len(recs) < 100:
            confidence = 'medium'
        else:
            confidence = 'high'

        return {
            'diversity_score': round(diversity_score, 1),
            'unique_artists_recommended': len(artist_ids),
            'unique_genres': len(genres),
            'total_recommendations_reviewed': len(recs),
            'mode_distribution': {
                'comfort_zone': comfort_count,
                'branch_out': branch_count
            },
            'artist_variety_ratio': round(unique_artist_ratio * 100, 1),
            'confidence': confidence
        }


def compute_all_metrics(user: User):
    """
    Compute and cache all metrics for a user.
    Called after sync completion.
    """
    service = MetricsService(user)

    # Clear old cached metrics
    UserMetric.query.filter_by(user_id=user.id).delete()

    now = datetime.utcnow()

    # Cache basic stats
    stats = service.get_basic_stats()
    for key, value in stats.items():
        if isinstance(value, (int, float)):
            metric = UserMetric(
                user_id=user.id,
                metric_type='basic_stat',
                metric_key=key,
                metric_value=float(value),
                computed_at=now
            )
            db.session.add(metric)

    # Cache streak info
    streak = service.get_listening_streak()
    for key in ['current_streak', 'longest_streak']:
        metric = UserMetric(
            user_id=user.id,
            metric_type='streak',
            metric_key=key,
            metric_value=float(streak.get(key, 0)),
            computed_at=now
        )
        db.session.add(metric)

    db.session.commit()
