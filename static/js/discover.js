/**
 * Discover Page JavaScript
 * Handles the recommendation wizard flow and API interactions.
 */

// State management
const state = {
    currentStep: 1,
    timePeriod: 'month',
    selectedArtists: [],
    mode: 'comfort_zone',
    popularity: 'balanced',
    recommendations: [],
    sessionId: null,
    likedTracks: new Set()
};

// DOM Elements
const elements = {
    steps: document.querySelectorAll('.wizard-step'),
    panels: document.querySelectorAll('.wizard-panel'),
    periodButtons: document.querySelectorAll('.period-btn'),
    modeCards: document.querySelectorAll('.mode-card'),
    popularitySlider: document.getElementById('popularity-slider'),
    popularityValue: document.getElementById('popularity-value'),
    artistGrid: document.getElementById('artist-grid'),
    recommendationGrid: document.getElementById('recommendation-grid'),
    loadingOverlay: document.getElementById('loading-overlay')
};

// Popularity labels
const popularityLabels = ['mainstream', 'balanced', 'niche'];

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    initializeEventListeners();
    loadStats();
    loadTopArtists();
});

function initializeEventListeners() {
    // Time period selection
    elements.periodButtons.forEach(btn => {
        btn.addEventListener('click', () => selectPeriod(btn));
    });

    // Mode selection
    elements.modeCards.forEach(card => {
        card.addEventListener('click', () => selectMode(card));
    });

    // Popularity slider
    elements.popularitySlider.addEventListener('input', updatePopularity);

    // Navigation buttons
    document.getElementById('btn-next-1').addEventListener('click', () => goToStep(2));
    document.getElementById('btn-back-2').addEventListener('click', () => goToStep(1));
    document.getElementById('btn-next-2').addEventListener('click', () => goToStep(3));
    document.getElementById('btn-back-3').addEventListener('click', () => goToStep(2));
    document.getElementById('btn-next-3').addEventListener('click', () => goToStep(4));
    document.getElementById('btn-back-4').addEventListener('click', () => goToStep(3));

    // Generate button
    document.getElementById('btn-generate').addEventListener('click', generateRecommendations);

    // Results actions
    document.getElementById('btn-start-over').addEventListener('click', startOver);
    document.getElementById('btn-export-json').addEventListener('click', exportJSON);
    document.getElementById('btn-create-playlist').addEventListener('click', createPlaylist);
    document.getElementById('btn-add-liked').addEventListener('click', addLikedToPlaylist);
}

// Load recommendation stats
async function loadStats() {
    try {
        const response = await fetch('/api/recommendations/stats');
        const data = await response.json();

        document.getElementById('stat-generated').textContent = data.total_generated || 0;
        document.getElementById('stat-liked').textContent = data.likes || 0;
        document.getElementById('stat-like-rate').textContent = `${data.like_rate || 0}%`;

        const bestMode = data.top_performing_mode || '-';
        document.getElementById('stat-best-mode').textContent =
            bestMode === 'comfort_zone' ? 'Comfort' :
            bestMode === 'branch_out' ? 'Branch Out' : '-';
    } catch (error) {
        console.error('Failed to load stats:', error);
    }
}

// Load top artists for selection
async function loadTopArtists() {
    try {
        const period = state.timePeriod;
        const response = await fetch(`/api/top/artists?period=${period}&limit=20`);
        const data = await response.json();

        renderArtistGrid(data.artists);
    } catch (error) {
        console.error('Failed to load artists:', error);
        elements.artistGrid.innerHTML = '<p class="error">Failed to load artists</p>';
    }
}

function renderArtistGrid(artists) {
    if (!artists || artists.length === 0) {
        elements.artistGrid.innerHTML = '<p>No artists found for this period. Try a longer time range.</p>';
        return;
    }

    elements.artistGrid.innerHTML = artists.map(artist => `
        <label>
            <input type="checkbox" class="artist-checkbox" value="${artist.id}" data-name="${artist.name}">
            <div class="artist-card">
                <img src="${artist.image_url || '/static/img/default-artist.png'}"
                     alt="${artist.name}"
                     onerror="this.src='/static/img/default-artist.png'">
                <div class="artist-name">${artist.name}</div>
                <div class="play-count">${artist.play_count} plays</div>
            </div>
        </label>
    `).join('');

    // Add event listeners to checkboxes
    elements.artistGrid.querySelectorAll('.artist-checkbox').forEach(checkbox => {
        checkbox.addEventListener('change', updateSelectedArtists);
    });
}

function updateSelectedArtists() {
    state.selectedArtists = Array.from(
        elements.artistGrid.querySelectorAll('.artist-checkbox:checked')
    ).map(cb => parseInt(cb.value));
}

// Period selection
function selectPeriod(btn) {
    elements.periodButtons.forEach(b => b.classList.remove('selected'));
    btn.classList.add('selected');
    state.timePeriod = btn.dataset.period;

    // Reload artists for new period
    loadTopArtists();
}

// Mode selection
function selectMode(card) {
    elements.modeCards.forEach(c => c.classList.remove('selected'));
    card.classList.add('selected');
    state.mode = card.dataset.mode;
}

// Popularity slider
function updatePopularity() {
    const value = parseInt(elements.popularitySlider.value);
    state.popularity = popularityLabels[value];
    elements.popularityValue.textContent =
        state.popularity.charAt(0).toUpperCase() + state.popularity.slice(1);
}

// Step navigation
function goToStep(step) {
    // Update step indicators
    elements.steps.forEach(s => {
        const stepNum = parseInt(s.dataset.step);
        s.classList.remove('active', 'completed');
        if (stepNum < step) {
            s.classList.add('completed');
        } else if (stepNum === step) {
            s.classList.add('active');
        }
    });

    // Show correct panel
    elements.panels.forEach(p => p.classList.remove('active'));
    document.getElementById(`step-${step}`).classList.add('active');

    state.currentStep = step;
}

// Generate recommendations
async function generateRecommendations() {
    showLoading(true);

    try {
        const response = await fetch('/api/recommendations/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                time_period: state.timePeriod,
                selected_artists: state.selectedArtists.length > 0 ? state.selectedArtists : null,
                mode: state.mode,
                popularity: state.popularity
            })
        });

        const data = await response.json();

        if (data.error) {
            throw new Error(data.error);
        }

        state.recommendations = data.recommendations || [];
        state.sessionId = data.session_id;
        state.likedTracks.clear();

        renderRecommendations();
        goToStep(5);
        loadStats(); // Refresh stats
    } catch (error) {
        console.error('Failed to generate recommendations:', error);
        alert('Failed to generate recommendations: ' + error.message);
    } finally {
        showLoading(false);
    }
}

function renderRecommendations() {
    if (!state.recommendations || state.recommendations.length === 0) {
        elements.recommendationGrid.innerHTML = `
            <div class="empty-state">
                <p>No recommendations found. Try adjusting your settings or collecting more listening data.</p>
            </div>
        `;
        return;
    }

    elements.recommendationGrid.innerHTML = state.recommendations.map((rec, index) => `
        <div class="recommendation-card" data-track-id="${rec.track_id}" data-index="${index}">
            <img src="${rec.album_image_url || '/static/img/default-album.png'}"
                 alt="${rec.album_name || 'Album'}"
                 onerror="this.src='/static/img/default-album.png'">
            <div class="recommendation-info">
                <div class="track-name">${rec.track_name}</div>
                <div class="artist-name">${rec.artist_name}</div>
                <div class="reason">${rec.reason}</div>
            </div>
            <span class="score-badge">${Math.round(rec.score * 100)}%</span>
            <div class="feedback-buttons">
                <button class="feedback-btn like-btn" onclick="recordFeedback(${rec.track_id}, 'like', this)" title="Like">
                    &#128077;
                </button>
                <button class="feedback-btn dislike-btn" onclick="recordFeedback(${rec.track_id}, 'dislike', this)" title="Dislike">
                    &#128078;
                </button>
            </div>
        </div>
    `).join('');
}

// Record feedback
async function recordFeedback(trackId, feedbackType, button) {
    try {
        const response = await fetch('/api/recommendations/feedback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                track_id: trackId,
                feedback_type: feedbackType
            })
        });

        const data = await response.json();

        if (data.success) {
            // Update button styles
            const card = button.closest('.recommendation-card');
            const likeBtn = card.querySelector('.like-btn');
            const dislikeBtn = card.querySelector('.dislike-btn');

            likeBtn.classList.remove('liked');
            dislikeBtn.classList.remove('disliked');

            if (feedbackType === 'like') {
                likeBtn.classList.add('liked');
                state.likedTracks.add(trackId);
            } else if (feedbackType === 'dislike') {
                dislikeBtn.classList.add('disliked');
                state.likedTracks.delete(trackId);
            }

            // Update stats
            loadStats();
        }
    } catch (error) {
        console.error('Failed to record feedback:', error);
    }
}

// Export functions
function exportJSON() {
    const exportData = {
        generated_at: new Date().toISOString(),
        mode: state.mode,
        time_period: state.timePeriod,
        popularity: state.popularity,
        recommendations: state.recommendations.map(rec => ({
            track_name: rec.track_name,
            artist_name: rec.artist_name,
            album_name: rec.album_name,
            score: rec.score,
            reason: rec.reason,
            spotify_uri: rec.spotify_uri,
            lastfm_url: rec.lastfm_url
        }))
    };

    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `recommendations_${new Date().toISOString().split('T')[0]}.json`;
    a.click();
    URL.revokeObjectURL(url);
}

async function createPlaylist() {
    const trackIds = state.recommendations.map(r => r.track_id);

    try {
        const response = await fetch('/api/spotify/create-playlist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                track_ids: trackIds,
                playlist_name: `Discovery ${new Date().toLocaleDateString()}`
            })
        });

        const data = await response.json();

        if (data.mock) {
            // Show export data in a modal or download
            showPlaylistExport(data);
        } else if (data.playlist_url) {
            window.open(data.playlist_url, '_blank');
        }
    } catch (error) {
        console.error('Failed to create playlist:', error);
        alert('Failed to create playlist');
    }
}

function showPlaylistExport(data) {
    // Create a simple text export for manual playlist creation
    const trackList = data.tracks.map(t => `${t.name} - ${t.artist}`).join('\n');

    const message = `Spotify integration is pending.\n\nTo create your playlist manually:\n${data.instructions.join('\n')}\n\nTracks:\n${trackList}`;

    // Create downloadable text file
    const blob = new Blob([trackList], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `playlist_tracks.txt`;
    a.click();
    URL.revokeObjectURL(url);

    alert('Track list downloaded! Open Spotify and search for each track to add to your playlist.');
}

async function addLikedToPlaylist() {
    if (state.likedTracks.size === 0) {
        alert('No liked tracks to add. Like some tracks first!');
        return;
    }

    const trackIds = Array.from(state.likedTracks);

    try {
        const response = await fetch('/api/spotify/create-playlist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                track_ids: trackIds,
                playlist_name: `Liked Discoveries ${new Date().toLocaleDateString()}`
            })
        });

        const data = await response.json();
        showPlaylistExport(data);
    } catch (error) {
        console.error('Failed to create playlist:', error);
        alert('Failed to create playlist');
    }
}

// Start over
function startOver() {
    state.currentStep = 1;
    state.selectedArtists = [];
    state.recommendations = [];
    state.sessionId = null;
    state.likedTracks.clear();

    // Reset UI
    elements.periodButtons.forEach(b => {
        b.classList.toggle('selected', b.dataset.period === 'month');
    });
    state.timePeriod = 'month';

    elements.modeCards.forEach(c => {
        c.classList.toggle('selected', c.dataset.mode === 'comfort_zone');
    });
    state.mode = 'comfort_zone';

    elements.popularitySlider.value = 1;
    state.popularity = 'balanced';
    elements.popularityValue.textContent = 'Balanced';

    // Uncheck all artists
    elements.artistGrid.querySelectorAll('.artist-checkbox').forEach(cb => {
        cb.checked = false;
    });

    goToStep(1);
    loadTopArtists();
}

// Loading overlay
function showLoading(show) {
    elements.loadingOverlay.classList.toggle('active', show);
}

// Make recordFeedback available globally
window.recordFeedback = recordFeedback;
