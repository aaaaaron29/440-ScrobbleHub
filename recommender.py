"""
Recommendation Engine for Last.fm Listening History Tracker.

Hybrid tag-based recommendation system with two modes:
- Comfort Zone: Finds tracks similar to user's existing taste
- Branch Out: Explores similar artists for discovery

See claude.md for algorithm documentation.
"""

import json
import math
import uuid
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from typing import List, Dict, Optional, Tuple

from sqlalchemy import func, desc
from models import (
    db, User, Artist, Album, Track, Scrobble, ArtistTag, TrackTag,
    SimilarArtist, Recommendation, RecommendationFeedback,
    ListeningSession, CoListeningPattern
)

logger = logging.getLogger(__name__)

# Algorithm weight configurations
COMFORT_ZONE_WEIGHTS = {
    'tag_similarity': 0.60,
    'co_listening': 0.30,
    'recency': 0.10
}

BRANCH_OUT_WEIGHTS = {
    'similar_artist': 0.50,
    'tag_overlap': 0.30,
    'popularity': 0.20
}

FEEDBACK_BOOST = 0.15  # +15% for liked tags
FEEDBACK_PENALTY = 0.15  # -15% for disliked tags


class RecommendationEngine:
    """Hybrid recommendation engine using Last.fm data."""

    def __init__(self, user_id: int):
        self.user_id = user_id
        self.user = User.query.get(user_id)
        self._tag_cache = {}
        self._feedback_weights = None
        self._scrobble_count_cache = {}  # Cache for batch popularity calculations

    def generate_recommendations(
        self,
        time_period: str = 'month',
        selected_artists: List[int] = None,
        mode: str = 'comfort_zone',
        popularity_level: str = 'balanced',
        limit: int = 25
    ) -> Dict:
        """
        Generate personalized track recommendations.

        Args:
            time_period: 'week', 'month', 'year', 'all'
            selected_artists: List of artist IDs to base recommendations on
            mode: 'comfort_zone' or 'branch_out'
            popularity_level: 'mainstream', 'balanced', 'niche'
            limit: Number of recommendations to return

        Returns:
            Dict with recommendations list and session metadata
        """
        session_id = str(uuid.uuid4())

        # Get user's listening history for the period
        period_start = self._get_period_start(time_period)
        user_tracks = self._get_user_tracks(period_start)
        user_track_ids = set(user_tracks.keys())

        # Get seed artists (selected or top from period)
        seed_artists = self._get_seed_artists(selected_artists, period_start)
        if not seed_artists:
            return {
                'recommendations': [],
                'session_id': session_id,
                'message': 'No listening history found for the selected period'
            }

        # Build user's tag profile
        user_tag_profile = self._build_tag_profile(seed_artists, user_tracks)

        # Load feedback weights
        self._feedback_weights = self._load_feedback_weights()

        # Generate candidate tracks based on mode
        if mode == 'comfort_zone':
            candidates = self._comfort_zone_candidates(
                seed_artists, user_tag_profile, user_tracks
            )
        else:  # branch_out
            candidates = self._branch_out_candidates(
                seed_artists, user_tag_profile, user_tracks
            )

        # Apply popularity filter
        candidates = self._apply_popularity_filter(candidates, popularity_level)

        # Apply feedback learning
        candidates = self.apply_feedback_weights(candidates)

        # Enforce diversity
        candidates = self.enforce_diversity(candidates)

        # Sort by score and limit
        candidates.sort(key=lambda x: x['score'], reverse=True)
        recommendations = candidates[:limit]

        # FALLBACK: If we got too few results, try relaxing constraints
        if len(recommendations) < 10:
            logger.warning(f"Only got {len(recommendations)} recommendations, applying fallback strategy")
            recommendations = self._apply_fallback_strategy(
                seed_artists, user_tag_profile, user_tracks, mode,
                popularity_level, limit, current_candidates=candidates
            )

        # Store recommendations in database
        self._store_recommendations(recommendations, session_id, mode, popularity_level)

        return {
            'recommendations': recommendations,
            'session_id': session_id,
            'mode': mode,
            'popularity_level': popularity_level,
            'seed_artists': [{'id': a.id, 'name': a.name} for a in seed_artists[:5]],
            'generated_at': datetime.utcnow().isoformat()
        }

    def calculate_tag_similarity(self, tags1: Dict[str, int], tags2: Dict[str, int]) -> float:
        """
        Calculate cosine similarity between two tag profiles.

        Args:
            tags1: Dict of {tag_name: weight}
            tags2: Dict of {tag_name: weight}

        Returns:
            Similarity score between 0 and 1
        """
        if not tags1 or not tags2:
            return 0.0

        # Get all unique tags
        all_tags = set(tags1.keys()) | set(tags2.keys())

        # Calculate dot product and magnitudes
        dot_product = 0
        mag1 = 0
        mag2 = 0

        for tag in all_tags:
            v1 = tags1.get(tag, 0)
            v2 = tags2.get(tag, 0)
            dot_product += v1 * v2
            mag1 += v1 * v1
            mag2 += v2 * v2

        if mag1 == 0 or mag2 == 0:
            return 0.0

        return dot_product / (math.sqrt(mag1) * math.sqrt(mag2))

    def apply_feedback_weights(self, candidates: List[Dict]) -> List[Dict]:
        """
        Adjust recommendation scores based on user feedback history.

        +15% boost for tags from liked tracks
        -15% penalty for tags from disliked tracks
        """
        if not self._feedback_weights:
            return candidates

        liked_tags = self._feedback_weights.get('liked_tags', {})
        disliked_tags = self._feedback_weights.get('disliked_tags', {})

        for candidate in candidates:
            track_tags = self._get_track_tags(candidate['track_id'])
            adjustment = 0

            for tag, weight in track_tags.items():
                tag_lower = tag.lower()
                if tag_lower in liked_tags:
                    adjustment += FEEDBACK_BOOST * (weight / 100)
                elif tag_lower in disliked_tags:
                    adjustment -= FEEDBACK_PENALTY * (weight / 100)

            # Apply adjustment (cap at reasonable bounds)
            candidate['score'] = max(0, min(1, candidate['score'] + adjustment))
            if adjustment != 0:
                candidate['feedback_adjusted'] = True

        return candidates

    def enforce_diversity(self, candidates: List[Dict], max_per_artist: int = 3) -> List[Dict]:
        """
        Enforce diversity: max 3 tracks per artist unless similarity > 0.8.

        This ensures recommendations aren't dominated by a single artist.
        """
        artist_counts = defaultdict(int)
        diverse_candidates = []

        # Sort by score first
        sorted_candidates = sorted(candidates, key=lambda x: x['score'], reverse=True)

        for candidate in sorted_candidates:
            artist_id = candidate['artist_id']
            score = candidate['score']

            # Allow more tracks from highly similar artists
            limit = max_per_artist + 2 if score > 0.8 else max_per_artist

            if artist_counts[artist_id] < limit:
                diverse_candidates.append(candidate)
                artist_counts[artist_id] += 1

        return diverse_candidates

    def _get_period_start(self, time_period: str) -> Optional[datetime]:
        """Convert period string to datetime."""
        now = datetime.utcnow()
        periods = {
            'week': now - timedelta(days=7),
            'month': now - timedelta(days=30),
            'year': now - timedelta(days=365),
            'all': None
        }
        return periods.get(time_period)

    def _get_user_tracks(self, period_start: Optional[datetime]) -> Dict[int, int]:
        """Get user's track play counts for the period."""
        query = db.session.query(
            Scrobble.track_id,
            func.count(Scrobble.id).label('play_count')
        ).filter(Scrobble.user_id == self.user_id)

        if period_start:
            query = query.filter(Scrobble.listened_at >= period_start)

        query = query.group_by(Scrobble.track_id)

        return {row.track_id: row.play_count for row in query.all()}

    def _get_seed_artists(
        self,
        selected_artists: Optional[List[int]],
        period_start: Optional[datetime]
    ) -> List[Artist]:
        """Get seed artists for recommendations."""
        if selected_artists:
            return Artist.query.filter(Artist.id.in_(selected_artists)).all()

        # Get top artists from period
        query = db.session.query(
            Artist,
            func.count(Scrobble.id).label('play_count')
        ).join(Track, Track.artist_id == Artist.id
        ).join(Scrobble, Scrobble.track_id == Track.id
        ).filter(Scrobble.user_id == self.user_id)

        if period_start:
            query = query.filter(Scrobble.listened_at >= period_start)

        query = query.group_by(Artist.id).order_by(desc('play_count')).limit(10)

        return [row[0] for row in query.all()]

    def _build_tag_profile(
        self,
        artists: List[Artist],
        user_tracks: Dict[int, int]
    ) -> Dict[str, float]:
        """Build weighted tag profile from user's listening."""
        tag_weights = defaultdict(float)

        # Get tags from seed artists
        for artist in artists:
            artist_tags = ArtistTag.query.filter_by(artist_id=artist.id).all()
            for at in artist_tags:
                tag_weights[at.tag.lower()] += at.count

        # Weight by play count from user tracks
        for track_id, play_count in user_tracks.items():
            track_tags = TrackTag.query.filter_by(track_id=track_id).all()
            for tt in track_tags:
                tag_weights[tt.tag.lower()] += tt.count * math.log(play_count + 1)

        # Normalize
        if tag_weights:
            max_weight = max(tag_weights.values())
            if max_weight > 0:
                tag_weights = {k: v / max_weight for k, v in tag_weights.items()}

        return dict(tag_weights)

    def _comfort_zone_candidates(
        self,
        seed_artists: List[Artist],
        user_tag_profile: Dict[str, float],
        user_tracks: Dict[int, int]
    ) -> List[Dict]:
        """
        Generate comfort zone candidates.
        60% tag similarity + 30% co-listening + 10% recency
        """
        candidates = []
        seed_artist_ids = {a.id for a in seed_artists}

        # Get tracks from seed artists that user hasn't played much
        tracks = Track.query.filter(
            Track.artist_id.in_(seed_artist_ids)
        ).all()

        # Also get tracks with similar tags
        top_tags = sorted(user_tag_profile.items(), key=lambda x: x[1], reverse=True)[:20]
        if top_tags:
            tag_names = [t[0] for t in top_tags]
            similar_track_ids = db.session.query(TrackTag.track_id).filter(
                TrackTag.tag.in_(tag_names)
            ).distinct().limit(500).all()
            similar_tracks = Track.query.filter(
                Track.id.in_([t[0] for t in similar_track_ids])
            ).all()
            tracks.extend(similar_tracks)

        # Get co-listening patterns
        co_listening = self._get_co_listening_scores(seed_artist_ids)

        # Batch prefetch scrobble counts to avoid N+1 query problem
        self._prefetch_scrobble_counts([t.id for t in tracks])

        seen_track_ids = set()
        for track in tracks:
            if track.id in seen_track_ids:
                continue
            seen_track_ids.add(track.id)

            # For Comfort Zone, we actually WANT to recommend tracks you love
            # Only skip if you've played it an extreme amount (avoid redundancy)
            if user_tracks.get(track.id, 0) > 50:
                continue

            # Calculate tag similarity
            track_tags = self._get_track_tags(track.id)
            tag_sim = self.calculate_tag_similarity(user_tag_profile, track_tags)

            # Get co-listening score
            co_score = co_listening.get(track.artist_id, 0)

            # Recency bonus (newer tracks get slight boost)
            recency = self._calculate_recency_score(track)

            # Weighted score
            score = (
                COMFORT_ZONE_WEIGHTS['tag_similarity'] * tag_sim +
                COMFORT_ZONE_WEIGHTS['co_listening'] * co_score +
                COMFORT_ZONE_WEIGHTS['recency'] * recency
            )

            if score > 0.05:  # Minimum threshold (lowered from 0.1)
                candidates.append(self._build_candidate(track, score, tag_sim, 'comfort_zone'))

        return candidates

    def _branch_out_candidates(
        self,
        seed_artists: List[Artist],
        user_tag_profile: Dict[str, float],
        user_tracks: Dict[int, int]
    ) -> List[Dict]:
        """
        Generate branch out candidates.
        50% similar artist network + 30% tag overlap + 20% popularity filter
        """
        candidates = []
        seed_artist_ids = {a.id for a in seed_artists}

        # Get similar artists from Last.fm data
        similar_artists = SimilarArtist.query.filter(
            SimilarArtist.artist_id.in_(seed_artist_ids)
        ).order_by(desc(SimilarArtist.match_score)).limit(50).all()

        # Find these artists in our database
        similar_artist_names = {sa.similar_artist_name.lower() for sa in similar_artists}
        similar_scores = {sa.similar_artist_name.lower(): sa.match_score for sa in similar_artists}

        found_artists = Artist.query.filter(
            func.lower(Artist.name).in_(similar_artist_names)
        ).all()

        # Collect all tracks from similar artists first (for batch prefetch)
        artist_tracks_map = {}
        all_track_ids = []
        for artist in found_artists:
            tracks = Track.query.filter_by(artist_id=artist.id).limit(20).all()
            artist_tracks_map[artist.id] = tracks
            all_track_ids.extend([t.id for t in tracks])

        # Batch prefetch scrobble counts to avoid N+1 query problem
        self._prefetch_scrobble_counts(all_track_ids)

        # Get tracks from similar artists
        for artist in found_artists:
            artist_tracks = artist_tracks_map[artist.id]
            similar_score = similar_scores.get(artist.name.lower(), 0.5)

            for track in artist_tracks:
                # Skip tracks user has already listened to
                if track.id in user_tracks:
                    continue

                # Calculate tag overlap
                track_tags = self._get_track_tags(track.id)
                tag_overlap = self.calculate_tag_similarity(user_tag_profile, track_tags)

                # Popularity score (inverse - prefer less popular for discovery)
                popularity = self._calculate_popularity_score(track, inverse=True)

                # Weighted score
                score = (
                    BRANCH_OUT_WEIGHTS['similar_artist'] * similar_score +
                    BRANCH_OUT_WEIGHTS['tag_overlap'] * tag_overlap +
                    BRANCH_OUT_WEIGHTS['popularity'] * popularity
                )

                if score > 0.05:  # Minimum threshold (lowered from 0.1)
                    reason = f"Similar to artists you like ({int(similar_score * 100)}% match)"
                    candidates.append(self._build_candidate(track, score, tag_overlap, 'branch_out', reason))

        return candidates

    def _apply_fallback_strategy(
        self,
        seed_artists: List[Artist],
        user_tag_profile: Dict[str, float],
        user_tracks: Dict[int, int],
        mode: str,
        popularity_level: str,
        limit: int,
        current_candidates: List[Dict]
    ) -> List[Dict]:
        """
        Apply fallback strategies when we have too few recommendations.

        Cascade:
        1. If we have some results, return what we have
        2. Relax popularity filter
        3. Lower score threshold
        4. Expand to more similar artists / broader tag search
        5. Ultimate fallback: top tracks from seed artists
        """
        candidates = current_candidates.copy()

        # Strategy 1: If we have ANY results, try relaxing popularity filter
        if len(candidates) < limit and popularity_level != 'balanced':
            logger.info("Fallback: Relaxing popularity filter to 'balanced'")
            if mode == 'comfort_zone':
                relaxed = self._comfort_zone_candidates(seed_artists, user_tag_profile, user_tracks)
            else:
                relaxed = self._branch_out_candidates(seed_artists, user_tag_profile, user_tracks)

            # Don't apply strict popularity filter
            relaxed = self.apply_feedback_weights(relaxed)
            relaxed = self.enforce_diversity(relaxed)
            relaxed.sort(key=lambda x: x['score'], reverse=True)
            candidates = relaxed[:limit]

        # Strategy 2: If still not enough, lower score threshold and expand search
        if len(candidates) < limit:
            logger.info("Fallback: Expanding search with lower threshold")

            # Get more tracks by lowering threshold to 0.05
            if mode == 'comfort_zone':
                # Get tracks with similar tags, much broader search
                top_tags = sorted(user_tag_profile.items(), key=lambda x: x[1], reverse=True)[:30]
                if top_tags:
                    tag_names = [t[0] for t in top_tags]
                    track_ids = db.session.query(TrackTag.track_id).filter(
                        TrackTag.tag.in_(tag_names)
                    ).distinct().limit(1000).all()
                    tracks = Track.query.filter(
                        Track.id.in_([t[0] for t in track_ids])
                    ).all()

                    for track in tracks:
                        if track.id in [c['track_id'] for c in candidates]:
                            continue

                        track_tags = self._get_track_tags(track.id)
                        tag_sim = self.calculate_tag_similarity(user_tag_profile, track_tags)

                        # Lower threshold
                        if tag_sim > 0.05:
                            candidates.append(self._build_candidate(track, tag_sim, tag_sim, mode))

            else:  # branch_out
                # Get more similar artists
                seed_artist_ids = {a.id for a in seed_artists}
                similar_artists = SimilarArtist.query.filter(
                    SimilarArtist.artist_id.in_(seed_artist_ids)
                ).order_by(desc(SimilarArtist.match_score)).limit(100).all()

                similar_artist_names = {sa.similar_artist_name.lower() for sa in similar_artists}
                similar_scores = {sa.similar_artist_name.lower(): sa.match_score for sa in similar_artists}

                found_artists = Artist.query.filter(
                    func.lower(Artist.name).in_(similar_artist_names)
                ).all()

                for artist in found_artists:
                    artist_tracks = Track.query.filter_by(artist_id=artist.id).limit(30).all()
                    similar_score = similar_scores.get(artist.name.lower(), 0.3)

                    for track in artist_tracks:
                        if track.id in [c['track_id'] for c in candidates]:
                            continue
                        if track.id in user_tracks:
                            continue

                        track_tags = self._get_track_tags(track.id)
                        tag_overlap = self.calculate_tag_similarity(user_tag_profile, track_tags)
                        score = similar_score * 0.7 + tag_overlap * 0.3

                        if score > 0.05:
                            candidates.append(self._build_candidate(track, score, tag_overlap, mode))

            candidates.sort(key=lambda x: x['score'], reverse=True)
            candidates = candidates[:limit]

        # Strategy 3: Ultimate fallback - just give top tracks from seed artists
        if len(candidates) < 5:
            logger.warning("Fallback: Using top tracks from seed artists as last resort")
            seed_artist_ids = {a.id for a in seed_artists}

            # Get most played tracks from these artists (by everyone, not just user)
            popular_tracks = db.session.query(
                Track,
                func.count(Scrobble.id).label('total_plays')
            ).join(Scrobble, Scrobble.track_id == Track.id
            ).filter(
                Track.artist_id.in_(seed_artist_ids)
            ).group_by(Track.id
            ).order_by(desc('total_plays')
            ).limit(limit).all()

            for track, play_count in popular_tracks:
                if track.id in [c['track_id'] for c in candidates]:
                    continue

                # Give a moderate score
                candidates.append(self._build_candidate(
                    track, 0.5, 0.5, mode,
                    custom_reason="Popular track from artists you selected"
                ))

        return candidates[:limit]

    def _get_track_tags(self, track_id: int) -> Dict[str, int]:
        """Get tags for a track (with caching)."""
        if track_id in self._tag_cache:
            return self._tag_cache[track_id]

        tags = {}

        # Get track-level tags
        track_tags = TrackTag.query.filter_by(track_id=track_id).all()
        for tt in track_tags:
            tags[tt.tag.lower()] = tt.count

        # Also include artist tags
        track = Track.query.get(track_id)
        if track:
            artist_tags = ArtistTag.query.filter_by(artist_id=track.artist_id).all()
            for at in artist_tags:
                tag = at.tag.lower()
                if tag not in tags:
                    tags[tag] = at.count // 2  # Half weight for artist tags

        self._tag_cache[track_id] = tags
        return tags

    def _get_co_listening_scores(self, seed_artist_ids: set) -> Dict[int, float]:
        """Get co-listening affinity scores for artists."""
        scores = {}

        patterns = CoListeningPattern.query.filter(
            CoListeningPattern.user_id == self.user_id,
            CoListeningPattern.artist_id_1.in_(seed_artist_ids)
        ).all()

        for pattern in patterns:
            scores[pattern.artist_id_2] = pattern.affinity_score or 0

        # Also check reverse direction
        patterns_rev = CoListeningPattern.query.filter(
            CoListeningPattern.user_id == self.user_id,
            CoListeningPattern.artist_id_2.in_(seed_artist_ids)
        ).all()

        for pattern in patterns_rev:
            if pattern.artist_id_1 not in scores:
                scores[pattern.artist_id_1] = pattern.affinity_score or 0

        return scores

    def _calculate_recency_score(self, track: Track) -> float:
        """Calculate recency score (newer tracks get higher scores)."""
        if not track.created_at:
            return 0.5

        days_old = (datetime.utcnow() - track.created_at).days
        # Exponential decay over 365 days
        return max(0, 1 - (days_old / 365))

    def _prefetch_scrobble_counts(self, track_ids: List[int]) -> None:
        """
        Batch fetch scrobble counts for multiple tracks in ONE query.
        Results are stored in _scrobble_count_cache for later use.

        This fixes the N+1 query problem where we'd otherwise make
        one DB call per track to calculate popularity.
        """
        if not track_ids:
            return

        # Filter out already cached IDs
        uncached_ids = [tid for tid in track_ids if tid not in self._scrobble_count_cache]
        if not uncached_ids:
            return

        # Single query to get all counts using GROUP BY
        counts = db.session.query(
            Scrobble.track_id,
            func.count(Scrobble.id).label('count')
        ).filter(
            Scrobble.track_id.in_(uncached_ids)
        ).group_by(Scrobble.track_id).all()

        # Store in cache
        for track_id, count in counts:
            self._scrobble_count_cache[track_id] = count

        # Set 0 for tracks with no scrobbles
        for tid in uncached_ids:
            if tid not in self._scrobble_count_cache:
                self._scrobble_count_cache[tid] = 0

    def _calculate_popularity_score(self, track: Track, inverse: bool = False) -> float:
        """Calculate popularity score.

        Priority: Spotify popularity > Last.fm artist listeners > local scrobble count.
        Last.fm listeners uses a log scale normalized against thresholds:
        - < 100k listeners  → niche (score 0.0-0.3)
        - 100k-1M listeners → mid (score 0.3-0.6)
        - > 1M listeners    → mainstream (score 0.6-1.0)
        """
        if track.spotify_popularity is not None:
            score = track.spotify_popularity / 100.0
        elif track.artist and track.artist.lastfm_listeners:
            listeners = track.artist.lastfm_listeners
            # Log-scale normalization: 10k=0.1, 100k=0.3, 500k=0.5, 1M=0.6, 10M=0.9
            score = min(1.0, math.log10(max(listeners, 1)) / 7.0)
        else:
            # Last resort: local scrobble count
            if track.id in self._scrobble_count_cache:
                scrobble_count = self._scrobble_count_cache[track.id]
            else:
                scrobble_count = Scrobble.query.filter_by(track_id=track.id).count()
                self._scrobble_count_cache[track.id] = scrobble_count

            if scrobble_count == 0:
                score = 0.1
            else:
                score = min(1, math.log(scrobble_count + 1) / 10)

        return 1 - score if inverse else score

    def _apply_popularity_filter(
        self,
        candidates: List[Dict],
        popularity_level: str
    ) -> List[Dict]:
        """Filter candidates based on popularity preference.

        Uses Last.fm listener counts as primary signal:
        - Niche: score < 0.4 (~under 500k Last.fm listeners)
        - Mainstream: score > 0.5 (~over 1M Last.fm listeners)
        - Balanced: no filter
        """
        if popularity_level == 'balanced':
            return candidates

        filtered = []
        for c in candidates:
            pop_score = c.get('popularity_score', 0.5)

            if popularity_level == 'mainstream' and pop_score > 0.5:
                filtered.append(c)
            elif popularity_level == 'niche' and pop_score < 0.4:
                filtered.append(c)

        # Safety: if filter removed everything, return unfiltered
        if not filtered and candidates:
            logger.warning(
                f"Popularity filter '{popularity_level}' removed all {len(candidates)} "
                f"candidates, returning unfiltered"
            )
            return candidates

        return filtered

    def _load_feedback_weights(self) -> Dict:
        """Load user's feedback history to weight recommendations."""
        liked_tags = defaultdict(int)
        disliked_tags = defaultdict(int)

        feedbacks = RecommendationFeedback.query.filter_by(
            user_id=self.user_id
        ).order_by(desc(RecommendationFeedback.timestamp)).limit(500).all()

        for fb in feedbacks:
            track_tags = self._get_track_tags(fb.track_id)
            for tag in track_tags:
                if fb.feedback_type == 'like':
                    liked_tags[tag] += 1
                elif fb.feedback_type == 'dislike':
                    disliked_tags[tag] += 1

        return {
            'liked_tags': dict(liked_tags),
            'disliked_tags': dict(disliked_tags)
        }

    def _build_candidate(
        self,
        track: Track,
        score: float,
        tag_similarity: float,
        mode: str,
        custom_reason: str = None
    ) -> Dict:
        """Build a candidate recommendation dict."""
        artist = Artist.query.get(track.artist_id)
        album = Album.query.get(track.album_id) if track.album_id else None

        if custom_reason:
            reason = custom_reason
        elif tag_similarity > 0.8:
            reason = f"{int(tag_similarity * 100)}% tag match with your taste"
        elif tag_similarity > 0.5:
            reason = f"Similar style ({int(tag_similarity * 100)}% match)"
        else:
            reason = "Recommended based on your listening patterns"

        return {
            'track_id': track.id,
            'track_name': track.name,
            'artist_id': artist.id if artist else None,
            'artist_name': artist.name if artist else 'Unknown',
            'album_name': album.name if album else None,
            'album_image_url': album.image_url if album else None,
            'score': round(score, 3),
            'tag_similarity': round(tag_similarity, 3),
            'reason': reason,
            'spotify_uri': track.spotify_uri,
            'lastfm_url': track.url,
            'popularity_score': self._calculate_popularity_score(track),
            'preview_url': track.spotify_preview_url
        }

    def _store_recommendations(
        self,
        recommendations: List[Dict],
        session_id: str,
        mode: str,
        popularity_level: str
    ):
        """Store generated recommendations in database."""
        db_recs = []
        for rec in recommendations:
            recommendation = Recommendation(
                user_id=self.user_id,
                track_id=rec['track_id'],
                recommendation_score=rec['score'],
                reason=rec['reason'],
                mode=mode,
                popularity_filter=popularity_level,
                session_id=session_id,
                generated_at=datetime.utcnow()
            )
            db.session.add(recommendation)
            db_recs.append((recommendation, rec))

        try:
            db.session.commit()
            # Set recommendation_id on each dict so frontend can send it with feedback
            for db_rec, rec_dict in db_recs:
                rec_dict['recommendation_id'] = db_rec.id
        except Exception as e:
            logger.error(f"Failed to store recommendations: {e}")
            db.session.rollback()


def generate_recommendations(
    user_id: int,
    time_period: str = 'month',
    selected_artists: List[int] = None,
    mode: str = 'comfort_zone',
    popularity_level: str = 'balanced'
) -> Dict:
    """
    Main entry point for generating recommendations.

    Args:
        user_id: User to generate recommendations for
        time_period: 'week', 'month', 'year', 'all'
        selected_artists: Optional list of artist IDs to seed from
        mode: 'comfort_zone' or 'branch_out'
        popularity_level: 'mainstream', 'balanced', 'niche'

    Returns:
        Dict with recommendations and session metadata
    """
    engine = RecommendationEngine(user_id)
    return engine.generate_recommendations(
        time_period=time_period,
        selected_artists=selected_artists,
        mode=mode,
        popularity_level=popularity_level
    )


def record_feedback(
    user_id: int,
    recommendation_id: Optional[int],
    track_id: int,
    feedback_type: str
) -> bool:
    """
    Record user feedback on a recommendation.

    Args:
        user_id: User providing feedback
        recommendation_id: ID of the recommendation (optional)
        track_id: Track being rated
        feedback_type: 'like', 'dislike', or 'skip'

    Returns:
        True if recorded successfully
    """
    # Update recommendation record if exists
    if recommendation_id:
        rec = Recommendation.query.get(recommendation_id)
        if rec and rec.user_id == user_id:
            rec.feedback = feedback_type
            rec.presented_at = datetime.utcnow()

    # Store detailed feedback
    feedback = RecommendationFeedback(
        recommendation_id=recommendation_id,
        user_id=user_id,
        track_id=track_id,
        feedback_type=feedback_type,
        timestamp=datetime.utcnow()
    )
    db.session.add(feedback)

    try:
        db.session.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to record feedback: {e}")
        db.session.rollback()
        return False


def get_recommendation_stats(user_id: int) -> Dict:
    """Get recommendation statistics for a user."""
    total = Recommendation.query.filter_by(user_id=user_id).count()

    # Query RecommendationFeedback (where feedback is actually stored)
    likes = RecommendationFeedback.query.filter_by(
        user_id=user_id, feedback_type='like'
    ).count()

    dislikes = RecommendationFeedback.query.filter_by(
        user_id=user_id, feedback_type='dislike'
    ).count()

    skips = RecommendationFeedback.query.filter_by(
        user_id=user_id, feedback_type='skip'
    ).count()

    # Calculate rates
    presented = likes + dislikes + skips
    like_rate = (likes / presented * 100) if presented > 0 else 0
    dislike_rate = (dislikes / presented * 100) if presented > 0 else 0

    # Find best performing mode (join feedback with recommendation to get mode)
    comfort_likes = db.session.query(RecommendationFeedback).join(
        Recommendation, RecommendationFeedback.recommendation_id == Recommendation.id
    ).filter(
        RecommendationFeedback.user_id == user_id,
        RecommendationFeedback.feedback_type == 'like',
        Recommendation.mode == 'comfort_zone'
    ).count()

    branch_likes = db.session.query(RecommendationFeedback).join(
        Recommendation, RecommendationFeedback.recommendation_id == Recommendation.id
    ).filter(
        RecommendationFeedback.user_id == user_id,
        RecommendationFeedback.feedback_type == 'like',
        Recommendation.mode == 'branch_out'
    ).count()

    best_mode = 'comfort_zone' if comfort_likes >= branch_likes else 'branch_out'

    return {
        'total_generated': total,
        'total_presented': presented,
        'likes': likes,
        'dislikes': dislikes,
        'skips': skips,
        'like_rate': round(like_rate, 1),
        'dislike_rate': round(dislike_rate, 1),
        'top_performing_mode': best_mode,
        'comfort_zone_likes': comfort_likes,
        'branch_out_likes': branch_likes
    }
