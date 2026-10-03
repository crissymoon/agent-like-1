
(function () {
    'use strict';

    /* --- State --- */
    var allArticles = [];
    var filteredArticles = [];
    var displayedCount = 0;
    var BATCH_SIZE = 12;
    var isLoading = false;
    var allLoaded = false;
    var currentQuery = '';
    var recents = [];

    /* --- DOM refs --- */
    var grid        = document.getElementById('articlesGrid');
    var sentinel    = document.getElementById('sentinel');
    var loaderMsg   = document.getElementById('loaderMsg');
    var searchInput = document.getElementById('searchInput');
    var statsCount  = document.getElementById('statsCount');
    var recentsSection = document.getElementById('recentsSection');
    var recentsRow  = document.getElementById('recentsRow');
    var clearBtn    = document.getElementById('clearRecents');

    /* --- Tracking --- */
    function sendTrack(type, data) {
        data.type = type;
        var xhr = new XMLHttpRequest();
        xhr.open('POST', 'track.php', true);
        xhr.setRequestHeader('Content-Type', 'application/json');
        xhr.send(JSON.stringify(data));
    }

    // Track page visit (once)
    sendTrack('visit', {
        page: window.location.pathname,
        referrer: document.referrer || ''
    });

    /* --- Path prefix: local vs live --- */
    var PAGES_PREFIX = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
        ? '/pages'
        : '/docs/pages';

    /* --- Theme --- */
    var modeBtn = document.getElementById('modeToggle');
    var saved = localStorage.getItem('pagesTheme');
    if (saved === 'dark' || saved === null) {
        document.body.classList.add('dark');
        if (saved === null) localStorage.setItem('pagesTheme', 'dark');
    }
    modeBtn.addEventListener('click', function () {
        var isDark = document.body.classList.toggle('dark');
        localStorage.setItem('pagesTheme', isDark ? 'dark' : 'light');
    });

    /* --- Fuzzy match --- */
    function fuzzyMatch(query, target) {
        if (!query) return true;
        query = query.toLowerCase();
        target = target.toLowerCase();
        if (target.indexOf(query) !== -1) return true;
        var qi = 0;
        for (var ti = 0; ti < target.length && qi < query.length; ti++) {
            if (query[qi] === target[ti]) qi++;
        }
        return qi === query.length;
    }

    function fuzzyScore(query, target) {
        if (!query) return 0;
        query = query.toLowerCase();
        target = target.toLowerCase();
        var idx = target.indexOf(query);
        if (idx === 0) return 100 + query.length;
        if (idx > 0) return 80 + query.length - idx;
        var qi = 0, score = 0;
        for (var ti = 0; ti < target.length && qi < query.length; ti++) {
            if (query[qi] === target[ti]) {
                score += (target.length - ti) / target.length * 10;
                qi++;
            }
        }
        return qi === query.length ? Math.round(score) : -1;
    }

    /* --- Search --- */
    function performSearch() {
        currentQuery = searchInput.value.trim();
        if (!currentQuery) {
            filteredArticles = allArticles.slice();
        } else {
            var scored = [];
            for (var i = 0; i < allArticles.length; i++) {
                var a = allArticles[i];
                var text = (a.title || '') + ' ' + (a.description || '') + ' ' + (a.tags || []).join(' ');
                if (fuzzyMatch(currentQuery, text)) {
                    var s = fuzzyScore(currentQuery, text);
                    if (s >= 0) scored.push({ article: a, score: s });
                }
            }
            scored.sort(function (a, b) { return b.score - a.score; });
            filteredArticles = scored.map(function (s) { return s.article; });
        }
        displayedCount = 0;
        allLoaded = false;
        grid.innerHTML = '';
        loaderMsg.className = 'loader-msg';
        loaderMsg.textContent = 'Loading...';
        loadMore();
        updateStats();
    }

    /* --- Render cards --- */
    function renderCards(articles) {
        var frag = document.createDocumentFragment();
        for (var i = 0; i < articles.length; i++) {
            var a = articles[i];
            var card = document.createElement('div');
            card.className = 'article-card';
            card.setAttribute('data-id', a.id);

            var title = document.createElement('div');
            title.className = 'article-title';
            title.textContent = a.title;

            var meta = document.createElement('div');
            meta.className = 'article-meta';
            meta.textContent = a.created + (a.source_md ? '  \u2022  ' + a.source_md : '');

            var desc = document.createElement('div');
            desc.className = 'article-desc';
            desc.textContent = a.description || 'No description.';

            card.appendChild(title);
            card.appendChild(meta);
            card.appendChild(desc);

            if (a.tags && a.tags.length) {
                var tagsWrap = document.createElement('div');
                tagsWrap.className = 'article-tags';
                for (var t = 0; t < a.tags.length; t++) {
                    var tag = document.createElement('span');
                    tag.className = 'article-tag';
                    tag.textContent = a.tags[t];
                    tagsWrap.appendChild(tag);
                }
                card.appendChild(tagsWrap);
            }

            card.addEventListener('click', (function (id, titleText) {
                return function () {
                    trackRecent(id, titleText);
                    sendTrack('click', { article_id: id, article_title: titleText });
                    window.location.href = PAGES_PREFIX + '/' + id + '.html';
                };
            })(a.id, a.title));

            frag.appendChild(card);
        }
        grid.appendChild(frag);
    }

    /* --- Load more (batched) --- */
    function loadMore() {
        if (isLoading || allLoaded) return;
        var remaining = filteredArticles.length - displayedCount;
        if (remaining <= 0) {
            allLoaded = true;
            loaderMsg.className = 'loader-msg done';
            if (filteredArticles.length === 0 && !currentQuery) {
                grid.innerHTML = '<div class="empty-state"><h2>No Articles Yet</h2><p>Export HTML from the MD Editor to populate this page.</p></div>';
            } else if (filteredArticles.length === 0 && currentQuery) {
                grid.innerHTML = '<div class="no-results">No articles match your search.</div>';
            }
            return;
        }
        isLoading = true;
        var batch = filteredArticles.slice(displayedCount, displayedCount + BATCH_SIZE);
        renderCards(batch);
        displayedCount += batch.length;
        isLoading = false;
        updateStats();
        if (displayedCount >= filteredArticles.length) {
            allLoaded = true;
            loaderMsg.className = 'loader-msg done';
        }
    }

    /* --- Stats --- */
    function updateStats() {
        var total = filteredArticles.length;
        var shown = Math.min(displayedCount, total);
        var label = total + ' article' + (total !== 1 ? 's' : '');
        if (currentQuery) {
            label = shown + ' of ' + total + ' article' + (total !== 1 ? 's' : '') + ' matched';
        }
        statsCount.textContent = label;
    }

    /* --- Recents --- */
    var MAX_VISIBLE_CHIPS = 3;
    var MAX_RECENTS = 15;

    function loadRecents() {
        try {
            var stored = localStorage.getItem('pagesRecents');
            recents = stored ? JSON.parse(stored) : [];
        } catch (e) { recents = []; }
        renderRecents();
    }

    function renderRecents() {
        recentsRow.innerHTML = '';
        var dropdownWrap = document.createElement('div');
        dropdownWrap.className = 'recents-dropdown-wrap';
        dropdownWrap.id = 'recentsDropdownWrap';
        dropdownWrap.style.display = 'none';

        if (!recents.length) {
            recentsSection.className = 'recents-section';
            return;
        }
        recentsSection.className = 'recents-section has-recents';

        var visible = recents.slice(0, MAX_VISIBLE_CHIPS);
        var extra = recents.slice(MAX_VISIBLE_CHIPS);

        for (var i = 0; i < visible.length; i++) {
            recentsRow.appendChild(makeChip(recents[i].id, recents[i].title));
        }

        if (extra.length) {
            var moreBtn = document.createElement('button');
            moreBtn.className = 'recents-more-btn';
            moreBtn.id = 'recentsMoreBtn';
            moreBtn.textContent = 'Show more (' + extra.length + ')';
            moreBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                var dd = document.getElementById('recentsDropdown');
                dd.classList.toggle('open');
            });
            dropdownWrap.appendChild(moreBtn);

            var dd = document.createElement('div');
            dd.className = 'recents-dropdown';
            dd.id = 'recentsDropdown';
            for (var j = 0; j < extra.length; j++) {
                var item = document.createElement('div');
                item.className = 'recents-dropdown-item';
                item.textContent = truncate(extra[j].title, 20);
                item.title = extra[j].title;
                item.addEventListener('click', (function (id) {
                    return function () { window.location.href = PAGES_PREFIX + '/' + id + '.html'; };
                })(extra[j].id));
                dd.appendChild(item);
            }
            dropdownWrap.appendChild(dd);
            dropdownWrap.style.display = 'inline-block';
            recentsRow.appendChild(dropdownWrap);
        }
    }

    function truncate(str, max) {
        return str.length > max ? str.slice(0, max) + '...' : str;
    }

    function makeChip(id, title) {
        var chip = document.createElement('span');
        chip.className = 'recents-chip';
        chip.textContent = truncate(title, 20);
        chip.title = title;
        chip.addEventListener('click', function () {
            window.location.href = PAGES_PREFIX + '/' + id + '.html';
        });
        return chip;
    }

    function trackRecent(id, title) {
        if (!id) return;
        recents = recents.filter(function (r) { return r.id !== id; });
        recents.unshift({ id: id, title: title });
        if (recents.length > MAX_RECENTS) recents.length = MAX_RECENTS;
        try { localStorage.setItem('pagesRecents', JSON.stringify(recents)); } catch (e) {}
        renderRecents();
    }

    clearBtn.addEventListener('click', function () {
        recents = [];
        try { localStorage.setItem('pagesRecents', JSON.stringify([])); } catch (e) {}
        renderRecents();
    });

    document.addEventListener('click', function () {
        var dd = document.getElementById('recentsDropdown');
        if (dd) dd.classList.remove('open');
    });

    /* --- Fetch data --- */
    function getCacheBustedExportsUrl() {
        var version = '';
        try {
            version = String(Date.now()) + '-' + String(Math.random()).slice(2);
        } catch (e) {
            version = String(Date.now());
        }
        return 'exports.json?v=' + encodeURIComponent(version);
    }

    function fetchExports(retries) {
        retries = retries || 0;
        var urls = [getCacheBustedExportsUrl(), getCacheBustedExportsUrl()];
        if (retries >= urls.length) {
            grid.innerHTML = '<div class="empty-state"><h2>No Articles Yet</h2><p>Export HTML from the MD Editor to populate this page.</p></div>';
            loaderMsg.className = 'loader-msg done';
            dataLoaded = true;
            return;
        }
        fetch(urls[retries], {
            cache: 'no-store',
            headers: {
                'Cache-Control': 'no-cache, no-store, max-age=0',
                'Pragma': 'no-cache'
            }
        })
            .then(function (r) {
                if (!r.ok) throw new Error('Failed to load exports.json');
                return r.json();
            })
            .then(function (data) { handleExports(data); })
            .catch(function () { fetchExports(retries + 1); });
    }

    function handleExports(data) {
        if (!Array.isArray(data) || !data.length) {
            grid.innerHTML = '<div class="empty-state"><h2>No Articles Yet</h2><p>Export HTML from the MD Editor to populate this page.</p></div>';
            loaderMsg.className = 'loader-msg done';
            dataLoaded = true;
            return;
        }
        allArticles = data.slice().sort(function (a, b) {
            return (b.created || '').localeCompare(a.created || '');
        });
        filteredArticles = allArticles.slice();
        dataLoaded = true;
        loadMore();
        updateStats();
        loadRecents();
    }

    /* --- Infinite scroll via IntersectionObserver --- */
    var dataLoaded = false;
    var observer = new IntersectionObserver(function (entries) {
        if (entries[0].isIntersecting && dataLoaded) {
            loadMore();
        }
    }, { rootMargin: '200px' });
    observer.observe(sentinel);

    /* --- Debounced search --- */
    var searchTimer;
    searchInput.addEventListener('input', function () {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(performSearch, 250);
    });

    /* --- Kick off --- */
    fetchExports(0);
})();
