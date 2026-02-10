/**
 * Last.fm Listening Tracker - Frontend JavaScript
 */

// State
let currentPage = 1;
let isConfigured = false;
let syncCheckInterval = null;
let currentTab = 'overview';

// DOM Elements
const elements = {
    setupModal: document.getElementById('setup-modal'),
    setupForm: document.getElementById('setup-form'),
    setupError: document.getElementById('setup-error'),
    overviewTab: document.getElementById('overview-tab'),
    setupTab: document.getElementById('setup-tab'),
    dashboardTab: document.getElementById('dashboard-tab'),
    dataTab: document.getElementById('data-tab'),
    discoverTab: document.getElementById('discover-tab'),
    tabNav: document.getElementById('tab-nav'),
    notConfigured: document.getElementById('not-configured'),
    syncStatus: document.getElementById('sync-status'),
    btnSync: document.getElementById('btn-sync'),
    btnSettings: document.getElementById('btn-settings'),
    btnGetStarted: document.getElementById('btn-get-started'),
    btnCancelSetup: document.getElementById('btn-cancel-setup'),
    btnLoadMore: document.getElementById('btn-load-more'),
    scrobbleList: document.getElementById('scrobble-list'),
    scrobbleCount: document.getElementById('scrobble-count'),
    topArtistsList: document.getElementById('top-artists-list'),
    topTracksList: document.getElementById('top-tracks-list'),
    topAlbumsList: document.getElementById('top-albums-list'),
    artistPeriod: document.getElementById('artist-period'),
    trackPeriod: document.getElementById('track-period'),
    albumPeriod: document.getElementById('album-period'),
    statTotal: document.getElementById('stat-total'),
    statArtists: document.getElementById('stat-artists'),
    statTracks: document.getElementById('stat-tracks'),
    statToday: document.getElementById('stat-today'),
    statStreak: document.getElementById('stat-streak'),
    discoverProgress: document.getElementById('discover-progress'),
};

// API Helpers
async function api(endpoint, options = {}) {
    const response = await fetch(`/api${endpoint}`, {
        headers: {
            'Content-Type': 'application/json',
            ...options.headers,
        },
        ...options,
    });

    if (!response.ok) {
        const error = await response.json().catch(() => ({ error: 'Request failed' }));
        throw new Error(error.error || 'Request failed');
    }

    return response.json();
}

// Formatting Helpers
function formatNumber(num) {
    if (num >= 1000000) {
        return (num / 1000000).toFixed(1) + 'M';
    }
    if (num >= 1000) {
        return (num / 1000).toFixed(1) + 'K';
    }
    return num.toLocaleString();
}

function formatTimeAgo(dateStr) {
    const date = new Date(dateStr);
    const now = new Date();
    const diff = Math.floor((now - date) / 1000);

    if (diff < 60) return 'Just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;

    return date.toLocaleDateString();
}

function formatDate(dateStr) {
    if (!dateStr) return 'Never';
    const date = new Date(dateStr);
    return date.toLocaleString();
}

function getInitials(name) {
    if (!name) return '?';
    const words = name.trim().split(/\s+/);
    if (words.length === 1) {
        return words[0].substring(0, 2).toUpperCase();
    }
    return (words[0][0] + words[1][0]).toUpperCase();
}

// UI Helpers
function showModal(modal) {
    modal.classList.add('active');
}

function hideModal(modal) {
    modal.classList.remove('active');
}

function setLoading(element, loading) {
    if (loading) {
        element.innerHTML = '<div class="loading">Loading...</div>';
    }
}

// Tab Navigation
function switchTab(tabName) {
    currentTab = tabName;

    // Update tab buttons
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tabName);
    });

    // Update tab content
    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.remove('active');
    });

    const targetTab = document.getElementById(`${tabName}-tab`);
    if (targetTab) {
        targetTab.classList.add('active');
    }

    // Load data for specific tabs
    if (tabName === 'data') {
        loadRecommendationData();
    }
}

// Initialize Application
async function init() {
    try {
        const config = await api('/config');
        isConfigured = config.configured;

        if (isConfigured) {
            showDashboard();
            await loadAllData();
            startSyncStatusCheck();
            updateDiscoverProgress();
        } else {
            showNotConfigured();
        }
    } catch (error) {
        console.error('Initialization error:', error);
        showNotConfigured();
    }

    setupEventListeners();
}

function showDashboard() {
    elements.overviewTab.classList.add('active');
    elements.setupTab.classList.remove('active');
    elements.dashboardTab.classList.remove('active');
    elements.discoverTab.classList.remove('active');
    elements.notConfigured.style.display = 'none';
    elements.tabNav.style.display = 'flex';
    elements.btnSync.disabled = false;
}

function showNotConfigured() {
    elements.overviewTab.classList.remove('active');
    elements.setupTab.classList.remove('active');
    elements.dashboardTab.classList.remove('active');
    elements.discoverTab.classList.remove('active');
    elements.notConfigured.style.display = 'flex';
    elements.tabNav.style.display = 'none';
    elements.btnSync.disabled = true;
}

// Event Listeners
function setupEventListeners() {
    // Tab navigation
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });

    // Settings button
    elements.btnSettings.addEventListener('click', () => {
        loadCurrentConfig();
        showModal(elements.setupModal);
    });

    // Get started button
    elements.btnGetStarted.addEventListener('click', () => {
        showModal(elements.setupModal);
    });

    // Cancel setup
    elements.btnCancelSetup.addEventListener('click', () => {
        hideModal(elements.setupModal);
    });

    // Setup form submit
    elements.setupForm.addEventListener('submit', handleSetup);

    // Sync button
    elements.btnSync.addEventListener('click', handleSync);

    // Load more scrobbles
    elements.btnLoadMore.addEventListener('click', () => {
        currentPage++;
        loadScrobbles(true);
    });

    // Period selectors
    elements.artistPeriod.addEventListener('change', () => {
        loadTopArtists(elements.artistPeriod.value);
    });

    elements.trackPeriod.addEventListener('change', () => {
        loadTopTracks(elements.trackPeriod.value);
    });

    elements.albumPeriod.addEventListener('change', () => {
        loadTopAlbums(elements.albumPeriod.value);
    });

    // Close modal on background click
    elements.setupModal.addEventListener('click', (e) => {
        if (e.target === elements.setupModal) {
            hideModal(elements.setupModal);
        }
    });

    // Refresh data button
    const btnRefreshData = document.getElementById('btn-refresh-data');
    if (btnRefreshData) {
        btnRefreshData.addEventListener('click', loadRecommendationData);
    }
}

// Configuration
async function loadCurrentConfig() {
    try {
        const config = await api('/config');
        if (config.configured) {
            document.getElementById('username').value = config.username;
            document.getElementById('sync-interval').value = config.sync_interval;
        }
    } catch (error) {
        console.error('Failed to load config:', error);
    }
}

async function handleSetup(e) {
    e.preventDefault();

    const formData = new FormData(elements.setupForm);
    const data = {
        username: formData.get('username'),
        api_key: formData.get('api_key'),
        sync_interval: parseInt(formData.get('sync_interval')) || 30,
    };

    elements.setupError.textContent = '';

    try {
        await api('/config', {
            method: 'POST',
            body: JSON.stringify(data),
        });

        hideModal(elements.setupModal);
        isConfigured = true;
        showDashboard();

        // Trigger initial sync
        handleSync(true);

    } catch (error) {
        elements.setupError.textContent = error.message;
    }
}

// Sync
async function handleSync(initial = false) {
    try {
        elements.btnSync.disabled = true;
        updateSyncStatus('Syncing...', 'syncing');

        await api('/sync', {
            method: 'POST',
            body: JSON.stringify({ initial: initial === true }),
        });

        // Start checking sync status
        startSyncStatusCheck();

    } catch (error) {
        console.error('Sync error:', error);
        updateSyncStatus('Sync failed', 'error');
        elements.btnSync.disabled = false;
    }
}

function startSyncStatusCheck() {
    // Clear existing interval
    if (syncCheckInterval) {
        clearInterval(syncCheckInterval);
    }

    // Check immediately
    checkSyncStatus();

    // Then check every 3 seconds
    syncCheckInterval = setInterval(checkSyncStatus, 3000);
}

async function checkSyncStatus() {
    try {
        const status = await api('/sync/status');

        if (status.is_syncing) {
            updateSyncStatus('Syncing...', 'syncing');
            elements.btnSync.disabled = true;
        } else {
            const lastSync = status.last_sync ? formatTimeAgo(status.last_sync) : 'Never';
            updateSyncStatus(`Last sync: ${lastSync}`, 'success');
            elements.btnSync.disabled = false;

            // Reload data after sync completes
            if (status.scrobbles_last_sync > 0) {
                await loadAllData();
                updateDiscoverProgress();
            }

            // Stop checking frequently after sync completes
            clearInterval(syncCheckInterval);
            syncCheckInterval = setInterval(checkSyncStatus, 30000);
        }
    } catch (error) {
        console.error('Status check error:', error);
    }
}

function updateSyncStatus(text, className) {
    elements.syncStatus.textContent = text;
    elements.syncStatus.className = 'sync-status ' + className;
}

// Data Loading
async function loadAllData() {
    await Promise.all([
        loadStats(),
        loadScrobbles(),
        loadTopArtists(),
        loadTopTracks(),
        loadTopAlbums(),
        loadStreak(),
    ]);
}

async function loadStats() {
    try {
        const stats = await api('/stats');

        elements.statTotal.textContent = formatNumber(stats.total_scrobbles || 0);
        elements.statArtists.textContent = formatNumber(stats.unique_artists || 0);
        elements.statTracks.textContent = formatNumber(stats.unique_tracks || 0);
        elements.statToday.textContent = formatNumber(stats.scrobbles_today || 0);
    } catch (error) {
        console.error('Failed to load stats:', error);
    }
}

async function loadStreak() {
    try {
        const streak = await api('/metrics/streaks');
        elements.statStreak.textContent = `${streak.current_streak || 0} days`;
    } catch (error) {
        console.error('Failed to load streak:', error);
    }
}

async function loadScrobbles(append = false) {
    if (!append) {
        setLoading(elements.scrobbleList, true);
        currentPage = 1;
    }

    try {
        const data = await api(`/scrobbles?page=${currentPage}&per_page=50`);

        if (!append) {
            elements.scrobbleList.innerHTML = '';
        }

        elements.scrobbleCount.textContent = data.pagination.total;

        if (data.scrobbles.length === 0 && !append) {
            elements.scrobbleList.innerHTML = '<div class="loading">No songs yet. Sync to fetch your listening history.</div>';
            return;
        }

        data.scrobbles.forEach(scrobble => {
            const item = createScrobbleItem(scrobble);
            elements.scrobbleList.appendChild(item);
        });

        // Show/hide load more button
        const hasMore = currentPage < data.pagination.pages;
        elements.btnLoadMore.style.display = hasMore ? 'inline-block' : 'none';

    } catch (error) {
        console.error('Failed to load scrobbles:', error);
        elements.scrobbleList.innerHTML = '<div class="loading">Failed to load songs</div>';
    }
}

function createScrobbleItem(scrobble) {
    const div = document.createElement('div');
    div.className = 'scrobble-item';

    const imageHtml = scrobble.image_url
        ? `<img src="${scrobble.image_url}" alt="" class="scrobble-image">`
        : '<div class="scrobble-image placeholder">♪</div>';

    div.innerHTML = `
        ${imageHtml}
        <div class="scrobble-info">
            <div class="scrobble-track">${escapeHtml(scrobble.track)}</div>
            <div class="scrobble-artist">${escapeHtml(scrobble.artist)}${scrobble.album ? ' • ' + escapeHtml(scrobble.album) : ''}</div>
        </div>
        <div class="scrobble-time">${formatTimeAgo(scrobble.listened_at)}</div>
    `;

    return div;
}

async function loadTopArtists(period = 'all') {
    setLoading(elements.topArtistsList, true);

    try {
        const data = await api(`/top/artists?period=${period}&limit=10`);

        if (data.artists.length === 0) {
            elements.topArtistsList.innerHTML = '<div class="loading">No data yet</div>';
            return;
        }

        elements.topArtistsList.innerHTML = data.artists.map((artist, index) => {
            const rankClass = index === 0 ? 'gold' : index === 1 ? 'silver' : index === 2 ? 'bronze' : '';
            const initials = getInitials(artist.name);

            // Use image if available, otherwise fall back to initials avatar
            const avatarHtml = artist.image_url && artist.image_url.length > 0
                ? `<img src="${artist.image_url}" alt="" class="artist-image">`
                : `<div class="artist-avatar">${initials}</div>`;

            return `
                <div class="top-item">
                    <span class="top-rank ${rankClass}">${index + 1}</span>
                    ${avatarHtml}
                    <div class="top-info">
                        <div class="top-name">${escapeHtml(artist.name)}</div>
                    </div>
                    <div class="top-count">${formatNumber(artist.play_count)} plays</div>
                </div>
            `;
        }).join('');

    } catch (error) {
        console.error('Failed to load top artists:', error);
        elements.topArtistsList.innerHTML = '<div class="loading">Failed to load</div>';
    }
}

async function loadTopTracks(period = 'all') {
    setLoading(elements.topTracksList, true);

    try {
        const data = await api(`/top/tracks?period=${period}&limit=10`);

        if (data.tracks.length === 0) {
            elements.topTracksList.innerHTML = '<div class="loading">No data yet</div>';
            return;
        }

        elements.topTracksList.innerHTML = data.tracks.map((track, index) => {
            const rankClass = index === 0 ? 'gold' : index === 1 ? 'silver' : index === 2 ? 'bronze' : '';
            const imageHtml = track.image_url
                ? `<img src="${track.image_url}" alt="" class="top-image">`
                : `<div class="artist-avatar" style="border-radius: 4px;">${getInitials(track.artist)}</div>`;

            return `
                <div class="top-item">
                    <span class="top-rank ${rankClass}">${index + 1}</span>
                    ${imageHtml}
                    <div class="top-info">
                        <div class="top-name">${escapeHtml(track.name)}</div>
                        <div class="top-artist">${escapeHtml(track.artist)}</div>
                    </div>
                    <div class="top-count">${formatNumber(track.play_count)} plays</div>
                </div>
            `;
        }).join('');

    } catch (error) {
        console.error('Failed to load top tracks:', error);
        elements.topTracksList.innerHTML = '<div class="loading">Failed to load</div>';
    }
}

async function loadTopAlbums(period = 'all') {
    setLoading(elements.topAlbumsList, true);

    try {
        const data = await api(`/top/albums?period=${period}&limit=10`);

        if (data.albums.length === 0) {
            elements.topAlbumsList.innerHTML = '<div class="loading">No data yet</div>';
            return;
        }

        elements.topAlbumsList.innerHTML = data.albums.map((album, index) => {
            const rankClass = index === 0 ? 'gold' : index === 1 ? 'silver' : index === 2 ? 'bronze' : '';
            const imageHtml = album.image_url
                ? `<img src="${album.image_url}" alt="" class="top-image">`
                : `<div class="artist-avatar" style="border-radius: 4px;">${getInitials(album.name)}</div>`;

            return `
                <div class="top-item">
                    <span class="top-rank ${rankClass}">${index + 1}</span>
                    ${imageHtml}
                    <div class="top-info">
                        <div class="top-name">${escapeHtml(album.name)}</div>
                        <div class="top-artist">${escapeHtml(album.artist)}</div>
                    </div>
                    <div class="top-count">${formatNumber(album.play_count)} plays</div>
                </div>
            `;
        }).join('');

    } catch (error) {
        console.error('Failed to load top albums:', error);
        elements.topAlbumsList.innerHTML = '<div class="loading">Failed to load</div>';
    }
}

// Discover tab progress
function updateDiscoverProgress() {
    // Simulate progress based on scrobble count
    // Real implementation would check for Spotify API integration etc.
    const totalText = elements.statTotal.textContent;
    const total = parseInt(totalText.replace(/,/g, '')) || 0;

    // Progress: 10k scrobbles = 50%, Spotify integration would add more
    let progress = Math.min((total / 20000) * 50, 50);

    if (elements.discoverProgress) {
        elements.discoverProgress.style.width = `${progress}%`;
    }
}

// ML Data Tab
async function loadRecommendationData() {
    try {
        const data = await api('/recommendation-data');

        // Update progress bars
        const tagsProgressBar = document.getElementById('tags-progress-bar');
        const similarProgressBar = document.getElementById('similar-progress-bar');
        const tagsProgressText = document.getElementById('tags-progress-text');
        const similarProgressText = document.getElementById('similar-progress-text');
        const tagsProgressPct = document.getElementById('tags-progress-pct');
        const similarProgressPct = document.getElementById('similar-progress-pct');

        if (tagsProgressBar) {
            tagsProgressBar.style.width = `${data.progress.tags_progress_pct}%`;
        }
        if (similarProgressBar) {
            similarProgressBar.style.width = `${data.progress.similar_progress_pct}%`;
        }
        if (tagsProgressText) {
            tagsProgressText.textContent = `${data.progress.artists_with_tags} / ${data.progress.total_artists} artists`;
        }
        if (similarProgressText) {
            similarProgressText.textContent = `${data.progress.artists_with_similar} / ${data.progress.total_artists} artists`;
        }
        if (tagsProgressPct) {
            tagsProgressPct.textContent = `${data.progress.tags_progress_pct}%`;
        }
        if (similarProgressPct) {
            similarProgressPct.textContent = `${data.progress.similar_progress_pct}%`;
        }

        // Update stats
        document.getElementById('total-tags').textContent = formatNumber(data.stats.total_tags);
        document.getElementById('unique-tags').textContent = formatNumber(data.stats.unique_tags);
        document.getElementById('total-similar').textContent = formatNumber(data.stats.total_similar_relationships);

        // Render top tags cloud
        const tagsCloud = document.getElementById('tags-cloud');
        if (data.top_tags.length === 0) {
            tagsCloud.innerHTML = '<div class="loading">No tags collected yet. Data is being fetched in the background.</div>';
        } else {
            const maxCount = Math.max(...data.top_tags.map(t => t.artist_count));
            tagsCloud.innerHTML = data.top_tags.map(tag => {
                const size = 0.8 + (tag.artist_count / maxCount) * 1.2;
                const opacity = 0.5 + (tag.artist_count / maxCount) * 0.5;
                return `<span class="tag-chip" style="font-size: ${size}rem; opacity: ${opacity};" title="${tag.artist_count} artists">${escapeHtml(tag.tag)}</span>`;
            }).join('');
        }

        // Render artist tags samples
        const artistTagsList = document.getElementById('artist-tags-list');
        if (data.artist_tag_samples.length === 0) {
            artistTagsList.innerHTML = '<div class="loading">No artist tags yet.</div>';
        } else {
            artistTagsList.innerHTML = data.artist_tag_samples.map(artist => {
                const imageHtml = artist.image_url && artist.image_url.length > 0
                    ? `<img src="${artist.image_url}" alt="" class="artist-image">`
                    : `<div class="artist-avatar">${getInitials(artist.name)}</div>`;
                const tagsHtml = artist.tags.map(t =>
                    `<span class="mini-tag">${escapeHtml(t.name)}</span>`
                ).join('');
                return `
                    <div class="artist-tag-item">
                        ${imageHtml}
                        <div class="artist-tag-info">
                            <div class="artist-tag-name">${escapeHtml(artist.name)}</div>
                            <div class="artist-tag-tags">${tagsHtml}</div>
                        </div>
                        <div class="artist-tag-plays">${formatNumber(artist.play_count)} plays</div>
                    </div>
                `;
            }).join('');
        }

        // Render similar artists samples
        const similarArtistsList = document.getElementById('similar-artists-list');
        if (data.similar_artist_samples.length === 0) {
            similarArtistsList.innerHTML = '<div class="loading">No similar artist data yet.</div>';
        } else {
            similarArtistsList.innerHTML = data.similar_artist_samples.map(artist => {
                const imageHtml = artist.image_url && artist.image_url.length > 0
                    ? `<img src="${artist.image_url}" alt="" class="artist-image">`
                    : `<div class="artist-avatar">${getInitials(artist.name)}</div>`;
                const similarHtml = artist.similar.map(s =>
                    `<span class="similar-chip" title="Match: ${(s.match * 100).toFixed(0)}%">${escapeHtml(s.name)}</span>`
                ).join(' → ');
                return `
                    <div class="similar-artist-item">
                        ${imageHtml}
                        <div class="similar-artist-info">
                            <div class="similar-artist-name">${escapeHtml(artist.name)}</div>
                            <div class="similar-artist-chain">${similarHtml}</div>
                        </div>
                    </div>
                `;
            }).join('');
        }

    } catch (error) {
        console.error('Failed to load recommendation data:', error);
    }
}

// Export
function exportData(type, format) {
    const url = `/api/export?type=${type}&format=${format}`;

    if (format === 'csv') {
        // Download CSV file
        window.location.href = url;
    } else {
        // Open JSON in new tab
        window.open(url, '_blank');
    }
}

// Utility
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
}

// Initialize on load
document.addEventListener('DOMContentLoaded', init);
