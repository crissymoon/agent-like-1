/* ==========================================================================
   XCM-AUTOMATE  |  Page Layout Tool  |  JS
   ========================================================================== */

(function () {
    'use strict';

    // Only auto-initialize the full UI (grid, lightbox, filters) when the
    // dedicated container element #plGrid exists on the page.  Otherwise
    // just expose the LAYOUTS array for external consumers (e.g. the
    // wireframe gallery page).
    var hasUI = !!document.getElementById('plGrid');

    // -------------------------------------------------------------------------
    // SVG wireframe builders — returns an SVG string for each layout
    // Each canvas is 800 x 500 (16:10)
    // -------------------------------------------------------------------------
    var W = 800, H = 500;

    function svgOpen(extra) {
        return '<svg viewBox="0 0 ' + W + ' ' + H + '" xmlns="http://www.w3.org/2000/svg" ' + (extra || '') + '>';
    }
    function svgClose() { return '</svg>'; }

    function rect(x, y, w, h, cls, rx) {
        return '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" class="' + cls + '"' + (rx ? ' rx="' + rx + '"' : '') + '/>';
    }
    function text(x, y, str, cls, anchor, size) {
        return '<text x="' + x + '" y="' + y + '" class="' + cls + '" text-anchor="' + (anchor || 'middle') + '" font-family="system-ui,sans-serif" font-size="' + (size || 10) + '">' + str + '</text>';
    }
    function line(x1, y1, x2, y2, cls) {
        return '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 + '" class="' + cls + '"/>';
    }

    // Reusable nav bar
    function nav(h) {
        h = h || 36;
        return rect(0, 0, W, h, 'wf-nav')
            + rect(16, 12, 60, 12, 'wf-nav-lt')
            + rect(W - 140, 11, 30, 14, 'wf-nav-lt')
            + rect(W - 100, 11, 30, 14, 'wf-nav-lt')
            + rect(W - 60,  11, 30, 14, 'wf-nav-lt')
            + rect(W - 18,  10, 2,  16, 'wf-nav-lt');
    }

    // Reusable footer
    function footer(y, h) {
        h = h || 44;
        return rect(0, y, W, h, 'wf-foot')
            + rect(16, y + 10, 50, 8, 'wf-nav-lt')
            + rect(16, y + 24, 80, 5, 'wf-nav-lt')
            + rect(W - 16 - 100, y + 10, 30, 6, 'wf-nav-lt')
            + rect(W - 16 - 60, y + 10, 30, 6, 'wf-nav-lt')
            + rect(W - 16 - 20, y + 10, 30, 6, 'wf-nav-lt');
    }

    // Pill / badge
    function pill(x, y, w, h, cls) {
        return '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" class="' + cls + '" rx="' + Math.round(h / 2) + '"/>';
    }

    // -------------------------------------------------------------------------
    // Layout definitions
    // -------------------------------------------------------------------------
    var LAYOUTS = [

        // 1 ----------------------------------------------------------------
        {
            id: 'hero-centered',
            name: 'Centered Hero',
            cat: 'marketing',
            desc: 'Full-width hero with centered headline, sub-copy, and dual CTA. Strong above-the-fold impact.',
            tags: ['marketing', 'hero', 'saas'],
            zones: [
                { color: '#1a1208', name: 'Nav', desc: 'Sticky navigation bar with brand and links.' },
                { color: '#fce8d5', name: 'Hero', desc: 'Full-width section with centered headline, sub-text, and primary + ghost CTA buttons.' },
                { color: '#e8e2d8', name: 'Social Proof Strip', desc: 'Thin band of logo lock-ups or trust signals.' },
                { color: '#ffffff', name: 'Features', desc: '3-column icon + text feature grid.' },
                { color: '#fde8d5', name: 'CTA Band', desc: 'Colored call-to-action banner.' },
                { color: '#0a0806', name: 'Footer', desc: 'Multi-column footer with links and copyright.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                // hero
                s += rect(0, 36, W, 200, 'wf-hero');
                s += rect(220, 68, 360, 22, 'wf-hero-lt');
                s += rect(160, 100, 480, 14, 'wf-nav-lt');
                s += rect(200, 120, 440, 10, 'wf-nav-lt');
                s += rect(W / 2 - 80, 146, 70, 22, 'wf-white');
                s += rect(W / 2 + 16, 146, 70, 22, 'wf-nav-lt');
                // social proof
                s += rect(0, 236, W, 32, 'wf-block');
                for (var i = 0; i < 5; i++) {
                    s += rect(80 + i * 130, 248, 70, 10, 'wf-block2');
                }
                // features
                s += rect(60,  288, 200, 60, 'wf-card');
                s += rect(300, 288, 200, 60, 'wf-card');
                s += rect(540, 288, 200, 60, 'wf-card');
                s += rect(100, 296, 20, 20, 'wf-accent');
                s += rect(340, 296, 20, 20, 'wf-accent');
                s += rect(580, 296, 20, 20, 'wf-accent');
                s += rect(100, 322, 120, 6, 'wf-line');
                s += rect(100, 334, 100, 5, 'wf-text-b');
                s += rect(340, 322, 120, 6, 'wf-line');
                s += rect(340, 334, 100, 5, 'wf-text-b');
                s += rect(580, 322, 120, 6, 'wf-line');
                s += rect(580, 334, 100, 5, 'wf-text-b');
                // cta band
                s += rect(0, 368, W, 52, 'wf-cta');
                s += rect(W / 2 - 130, 378, 200, 10, 'wf-nav-lt');
                s += rect(W / 2 - 70, 396, 140, 16, 'wf-white');
                // footer
                s += footer(420, 44);
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 2 ----------------------------------------------------------------
        {
            id: 'split-hero',
            name: 'Split Hero',
            cat: 'marketing',
            desc: 'Left text / right image hero split. Balances visual weight with editorial hierarchy.',
            tags: ['marketing', 'hero', 'editorial'],
            zones: [
                { color: '#1a1208', name: 'Nav', desc: 'Sticky navigation.' },
                { color: '#ff7000', name: 'Hero — Text Column', desc: 'Left half: headline, body copy, CTA button.' },
                { color: '#ff9040', name: 'Hero — Image Column', desc: 'Right half: full-height product or hero image.' },
                { color: '#f5f2ed', name: 'Three-Column Features', desc: 'Icon + headline + text feature cards.' },
                { color: '#0a0806', name: 'Footer', desc: 'Dark multi-column footer.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                // hero split
                s += rect(0, 36, W / 2, 200, 'wf-hero');
                s += rect(W / 2, 36, W / 2, 200, 'wf-img');
                s += rect(28, 70, 220, 18, 'wf-nav-lt');
                s += rect(28, 98, 300, 10, 'wf-nav-lt');
                s += rect(28, 114, 280, 9,  'wf-nav-lt');
                s += rect(28, 130, 200, 8,  'wf-nav-lt');
                s += rect(28, 150, 100, 22, 'wf-white');
                // image placeholder lines
                s += line(W / 2, 36, W, 236, 'wf-grid-line');
                s += line(W, 36, W / 2, 236, 'wf-grid-line');
                // features
                s += rect(60,  256, 200, 72, 'wf-card');
                s += rect(300, 256, 200, 72, 'wf-card');
                s += rect(540, 256, 200, 72, 'wf-card');
                for (var i = 0; i < 3; i++) {
                    var fx = [80, 320, 560][i];
                    s += rect(fx, 264, 18, 18, 'wf-accent');
                    s += rect(fx, 288, 130, 6, 'wf-line');
                    s += rect(fx, 300, 110, 5, 'wf-text-b');
                    s += rect(fx, 311, 120, 5, 'wf-text-b');
                }
                // footer
                s += footer(348, 44);
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 3 ----------------------------------------------------------------
        {
            id: 'saas-dashboard',
            name: 'SaaS App Shell',
            cat: 'dashboard',
            desc: 'Top nav + left sidebar + main content area. The standard shell for web apps and dashboards.',
            tags: ['dashboard', 'saas', 'app'],
            zones: [
                { color: '#1a1208', name: 'Top Nav', desc: 'Brand, search, avatar, notifications.' },
                { color: '#c8b898', name: 'Sidebar', desc: '220px sidebar with nav items and sections.' },
                { color: '#f5f2ed', name: 'Stat Cards', desc: 'KPI summary row of 4 metric cards.' },
                { color: '#ffffff', name: 'Main Content', desc: 'Chart or data grid occupying the remaining canvas.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                // sidebar
                s += rect(0, 36, 160, H - 36, 'wf-sidebar');
                for (var i = 0; i < 8; i++) {
                    s += rect(14, 56 + i * 32, 130, 16, i === 0 ? 'wf-accent' : 'wf-block');
                }
                // stat cards
                var cw = (W - 160 - 40) / 4 - 10;
                for (var j = 0; j < 4; j++) {
                    var cx = 172 + j * (cw + 10);
                    s += rect(cx, 46, cw, 68, 'wf-card');
                    s += rect(cx + 8, 54, cw - 60, 8, 'wf-text-b');
                    s += rect(cx + 8, 68, cw - 30, 16, 'wf-line');
                    s += rect(cx + 8, 90, 50, 8, 'wf-green');
                }
                // chart area
                s += rect(172, 124, W - 172 - 12, 200, 'wf-card');
                s += rect(180, 132, 120, 8, 'wf-line');
                // fake bar chart
                var barW = 20, gap = 16;
                var baseX = 190, baseY = 300;
                var bars = [80, 50, 110, 65, 90, 120, 40, 70, 95, 55, 100, 75];
                for (var k = 0; k < bars.length; k++) {
                    s += rect(baseX + k * (barW + gap), baseY - bars[k], barW, bars[k], k % 3 === 0 ? 'wf-accent' : 'wf-block');
                }
                s += line(180, 300, W - 12, 300, 'wf-grid-line');
                // second row — two panels
                s += rect(172, 334, (W - 172 - 22) / 2, 120, 'wf-card');
                s += rect(172 + (W - 172 - 22) / 2 + 10, 334, (W - 172 - 22) / 2, 120, 'wf-card');
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 4 ----------------------------------------------------------------
        {
            id: 'editorial-long',
            name: 'Editorial Long-Form',
            cat: 'editorial',
            desc: 'Centered narrow content column with aside, pull-quotes, and sticky sidebar TOC. Optimised for reading.',
            tags: ['editorial', 'blog', 'article'],
            zones: [
                { color: '#1a1208', name: 'Nav', desc: 'Minimal top navigation.' },
                { color: '#f5f2ed', name: 'Article Header', desc: 'Category tag, large headline, byline, and hero image.' },
                { color: '#ffffff', name: 'Content Column', desc: '680px centred text column — body, pull-quotes, inline images.' },
                { color: '#c8b898', name: 'Sidebar', desc: 'Sticky table of contents and related articles.' },
                { color: '#0a0806', name: 'Footer', desc: 'Slim dark footer.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                // article header
                s += rect(0, 36, W, 120, 'wf-img');
                s += line(0, 36, W, H - 44, 'wf-grid-line');
                s += line(W, 36, 0, H - 44, 'wf-grid-line');
                s += rect(60, 54, 80, 10, 'wf-accent');
                s += rect(60, 72, 400, 22, 'wf-white');
                s += rect(60, 102, 150, 8, 'wf-nav-lt');
                // content + sidebar
                s += rect(60,  164, 490, H - 164 - 44, 'wf-white');
                s += rect(570, 164, 170, H - 164 - 44, 'wf-sidebar');
                // text lines
                for (var i = 0; i < 10; i++) {
                    s += rect(76, 176 + i * 22, i === 2 ? 360 : (i % 4 === 3 ? 280 : 440), 8, 'wf-text-b');
                }
                // pull quote
                s += rect(76, 406, 4, 60, 'wf-accent');
                s += rect(86, 410, 380, 10, 'wf-line');
                s += rect(86, 426, 340, 9,  'wf-line');
                s += rect(86, 441, 300, 9,  'wf-line');
                // sidebar lines
                s += rect(582, 174, 120, 8, 'wf-line');
                for (var j = 0; j < 6; j++) {
                    s += rect(582, 192 + j * 20, 140, 6, 'wf-block');
                }
                s += footer(H - 44, 44);
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 5 ----------------------------------------------------------------
        {
            id: 'pricing-page',
            name: 'Pricing Page',
            cat: 'saas',
            desc: 'Toggle-able billing period, 3-tier pricing cards with highlighted plan, FAQ, and strong CTA.',
            tags: ['saas', 'pricing', 'marketing'],
            zones: [
                { color: '#1a1208', name: 'Nav', desc: 'Sticky nav.' },
                { color: '#f5f2ed', name: 'Header', desc: 'Centred headline + billing toggle (monthly / annual).' },
                { color: '#ffffff', name: 'Pricing Cards', desc: '3-column grid — one card highlighted with primary colour.' },
                { color: '#c8b898', name: 'Feature Comparison', desc: 'Optional feature grid below cards.' },
                { color: '#ff7000', name: 'CTA Banner', desc: 'Dark gradient call-to-action.' },
                { color: '#0a0806', name: 'Footer', desc: 'Standard footer.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                // header
                s += rect(200, 48, 400, 22, 'wf-line');
                s += rect(240, 78, 320, 12, 'wf-text-b');
                s += rect(W / 2 - 70, 98, 140, 22, 'wf-block');
                // pricing cards
                var cardX = [50, 290, 530], cardH = 210;
                s += rect(cardX[0], 134, 220, cardH, 'wf-card');
                s += rect(cardX[1], 124, 220, cardH + 10, 'wf-hero');
                s += rect(cardX[2], 134, 220, cardH, 'wf-card');
                // plan names
                s += rect(cardX[0] + 14, 148, 80, 8, 'wf-text-b');
                s += rect(cardX[0] + 14, 162, 60, 16, 'wf-line');
                s += rect(cardX[0] + 14, 184, 130, 5, 'wf-text-b');
                s += rect(cardX[0] + 14, 194, 110, 5, 'wf-text-b');
                s += rect(cardX[0] + 14, 204, 120, 5, 'wf-text-b');
                s += rect(cardX[0] + 14, 218, 100, 5, 'wf-text-b');
                s += rect(cardX[0] + 14, 310, 90, 18, 'wf-block2');

                s += rect(cardX[1] + 14, 138, 80, 8, 'wf-nav-lt');
                s += rect(cardX[1] + 14, 152, 60, 18, 'wf-white');
                s += rect(cardX[1] + 14, 176, 130, 5, 'wf-nav-lt');
                s += rect(cardX[1] + 14, 187, 110, 5, 'wf-nav-lt');
                s += rect(cardX[1] + 14, 197, 120, 5, 'wf-nav-lt');
                s += rect(cardX[1] + 14, 208, 100, 5, 'wf-nav-lt');
                s += rect(cardX[1] + 14, 304, 90, 20, 'wf-white');

                s += rect(cardX[2] + 14, 148, 80, 8, 'wf-text-b');
                s += rect(cardX[2] + 14, 162, 60, 16, 'wf-line');
                s += rect(cardX[2] + 14, 184, 130, 5, 'wf-text-b');
                s += rect(cardX[2] + 14, 194, 110, 5, 'wf-text-b');
                s += rect(cardX[2] + 14, 204, 120, 5, 'wf-text-b');
                s += rect(cardX[2] + 14, 218, 100, 5, 'wf-text-b');
                s += rect(cardX[2] + 14, 310, 90, 18, 'wf-block2');

                // comparison rows
                s += rect(50, 354, 700, 6, 'wf-block');
                s += rect(50, 366, 700, 6, 'wf-block');
                s += rect(50, 378, 700, 6, 'wf-block');
                // cta
                s += rect(0, 398, W, 58, 'wf-hero');
                s += rect(220, 408, 360, 12, 'wf-nav-lt');
                s += rect(W / 2 - 60, 426, 120, 18, 'wf-white');
                s += footer(456, 44);
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 6 ----------------------------------------------------------------
        {
            id: 'portfolio-grid',
            name: 'Portfolio / Work Grid',
            cat: 'portfolio',
            desc: 'Masonry-style project grid with filterable categories, case study previews, and contact CTA.',
            tags: ['portfolio', 'creative', 'agency'],
            zones: [
                { color: '#1a1208', name: 'Nav', desc: 'Sticky nav with logo and contact CTA.' },
                { color: '#f5f2ed', name: 'Page Header', desc: 'Short intro headline + category filter pills.' },
                { color: '#ffffff', name: 'Project Grid', desc: 'Masonry / auto grid of hover-revealed project cards.' },
                { color: '#ff7000', name: 'CTA', desc: 'Invitation to start a project.' },
                { color: '#0a0806', name: 'Footer', desc: 'Footer with social links.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                // page header
                s += rect(200, 48, 400, 18, 'wf-line');
                s += rect(240, 72, 320, 10, 'wf-text-b');
                // filter pills
                var pills = ['All', 'Brand', 'Digital', 'Product', 'Motion'];
                pills.forEach(function (p, i) {
                    s += rect(170 + i * 90, 90, 76, 18, i === 0 ? 'wf-accent' : 'wf-block');
                });
                // project cards — asymmetric grid
                s += rect(20,  118, 240, 160, 'wf-img');
                s += rect(270, 118, 160, 76,  'wf-img');
                s += rect(440, 118, 340, 160, 'wf-img');
                s += rect(270, 202, 160, 76,  'wf-block');
                s += rect(20,  286, 180, 120, 'wf-block');
                s += rect(208, 286, 220, 120, 'wf-img');
                s += rect(436, 286, 160, 56,  'wf-img');
                s += rect(436, 350, 160, 56,  'wf-block');
                s += rect(604, 286, 176, 120, 'wf-img');
                // cta
                s += rect(0, 416, W, 40, 'wf-hero');
                s += rect(260, 424, 280, 10, 'wf-nav-lt');
                s += footer(456, 44);
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 7 ----------------------------------------------------------------
        {
            id: 'ecomm-product',
            name: 'E-Commerce Product Page',
            cat: 'ecommerce',
            desc: 'Product image gallery left, purchase info right, with reviews, related items, and sticky Add-to-Cart.',
            tags: ['ecommerce', 'product', 'retail'],
            zones: [
                { color: '#1a1208', name: 'Nav + Breadcrumb', desc: 'Full nav with cart icon + breadcrumb trail.' },
                { color: '#a89878', name: 'Image Gallery', desc: 'Main product image with thumbnail strip below.' },
                { color: '#ffffff', name: 'Purchase Panel', desc: 'Title, price, variant selectors, quantity, Add to Cart, trust signals.' },
                { color: '#f5f2ed', name: 'Tabs (Details/Reviews)', desc: 'Tabbed section for description, specs, and reviews.' },
                { color: '#c8b898', name: 'Related Products', desc: 'Horizontal scroll or 4-column grid of related items.' },
                { color: '#0a0806', name: 'Footer', desc: 'Store footer.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                // breadcrumb
                s += rect(20, 44, 300, 8, 'wf-text-b');
                // gallery
                s += rect(20,  60, 340, 260, 'wf-img');
                s += line(20, 60, 360, 320, 'wf-grid-line');
                s += line(360, 60, 20, 320, 'wf-grid-line');
                for (var i = 0; i < 4; i++) {
                    s += rect(20 + i * 86, 326, 76, 52, i === 0 ? 'wf-block2' : 'wf-block');
                }
                // purchase panel
                s += rect(380, 60, 400, 320, 'wf-white');
                s += rect(392, 72, 200, 16, 'wf-line');
                s += rect(392, 94, 100, 14, 'wf-accent');
                s += rect(392, 114, 280, 6, 'wf-text-b');
                s += rect(392, 126, 250, 6, 'wf-text-b');
                s += rect(392, 142, 80, 8, 'wf-text-b');
                // colour swatches
                for (var j = 0; j < 5; j++) {
                    s += '<rect x="' + (392 + j * 28) + '" y="156" width="20" height="20" class="' + ['wf-accent','wf-hero','wf-orange','wf-green','wf-block2'][j] + '" rx="10"/>';
                }
                s += rect(392, 184, 140, 8, 'wf-text-b');
                s += rect(392, 198, 100, 24, 'wf-block');
                s += rect(502, 198, 200, 24, 'wf-accent');
                s += rect(392, 232, 300, 6, 'wf-text-b');
                s += rect(392, 244, 280, 6, 'wf-text-b');
                s += rect(392, 256, 260, 6, 'wf-text-b');
                // tabs
                s += rect(20, 388, 760, 44, 'wf-sidebar');
                s += rect(20, 388, 120, 44, 'wf-white');
                s += rect(146, 396, 80, 10, 'wf-text-b');
                s += rect(236, 396, 80, 10, 'wf-text-b');
                // related products (4 mini cards)
                for (var k = 0; k < 4; k++) {
                    s += rect(20 + k * 192, 440, 172, 46, 'wf-card');
                    s += rect(28 + k * 192, 448, 60, 36, 'wf-block');
                    s += rect(96 + k * 192, 454, 80, 7, 'wf-line');
                    s += rect(96 + k * 192, 467, 50, 7, 'wf-accent');
                }
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 8 ----------------------------------------------------------------
        {
            id: 'landing-video-hero',
            name: 'Video Hero Landing',
            cat: 'marketing',
            desc: 'Fullscreen video or image hero with overlay headline, scroll indicator, and floating stats below.',
            tags: ['marketing', 'hero', 'brand'],
            zones: [
                { color: '#1a1208', name: 'Transparent Nav', desc: 'Nav overlaid on hero with white text.' },
                { color: '#ff7000', name: 'Video / Image Hero', desc: 'Full-viewport dark overlay on autoplay video.' },
                { color: '#f5f2ed', name: 'Floating Stats Row', desc: 'Stat cards that float partially over the hero bottom edge.' },
                { color: '#ffffff', name: 'About / Story', desc: 'Two-column text + pull image.' },
                { color: '#5a4020', name: 'CTA', desc: 'Coloured action band.' },
                { color: '#0a0806', name: 'Footer', desc: 'Footer.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                // fullscreen hero
                s += rect(0, 0, W, 260, 'wf-hero');
                s += line(0, 0, W, 260, 'wf-grid-line');
                s += line(W, 0, 0, 260, 'wf-grid-line');
                // nav transparent overlay
                s += rect(0, 0, W, 36, 'wf-hero');
                s += rect(16, 12, 60, 12, 'wf-nav-lt');
                s += rect(W - 140, 11, 30, 14, 'wf-nav-lt');
                s += rect(W - 100, 11, 30, 14, 'wf-nav-lt');
                s += rect(W - 60, 11, 30, 14, 'wf-nav-lt');
                // hero text
                s += rect(160, 90, 480, 24, 'wf-nav-lt');
                s += rect(220, 122, 360, 12, 'wf-nav-lt');
                s += rect(W / 2 - 50, 148, 100, 22, 'wf-white');
                // scroll indicator
                s += rect(W / 2 - 6, 234, 12, 20, 'wf-nav-lt');
                // floating stats
                s += rect(60,  234, 160, 60, 'wf-white');
                s += rect(240, 234, 160, 60, 'wf-white');
                s += rect(420, 234, 160, 60, 'wf-white');
                s += rect(600, 234, 140, 60, 'wf-white');
                for (var i = 0; i < 4; i++) {
                    s += rect([68,248,428,608][i], 244, 60, 14, 'wf-line');
                    s += rect([68,248,428,608][i], 263, 100, 7, 'wf-text-b');
                }
                // about
                s += rect(40,  314, 340, 90, 'wf-white');
                s += rect(420, 314, 340, 90, 'wf-img');
                s += rect(52, 322, 180, 12, 'wf-line');
                s += rect(52, 340, 300, 6, 'wf-text-b');
                s += rect(52, 352, 280, 6, 'wf-text-b');
                s += rect(52, 364, 260, 6, 'wf-text-b');
                s += rect(52, 376, 100, 18, 'wf-accent');
                // cta band
                s += rect(0, 416, W, 40, 'wf-cta');
                s += rect(220, 424, 360, 10, 'wf-nav-lt');
                s += footer(456, 44);
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 9 ----------------------------------------------------------------
        {
            id: 'feature-alternating',
            name: 'Alternating Feature Sections',
            cat: 'saas',
            desc: 'Text left / image right then image left / text right sections stacked. Classic SaaS explainer pattern.',
            tags: ['saas', 'marketing', 'explainer'],
            zones: [
                { color: '#1a1208', name: 'Nav', desc: 'Sticky nav.' },
                { color: '#ff7000', name: 'Hero', desc: 'Compact centred hero.' },
                { color: '#ffffff', name: 'Feature Row A', desc: 'Text left, screenshot right.' },
                { color: '#f5f2ed', name: 'Feature Row B', desc: 'Screenshot left, text right — alternated.' },
                { color: '#ffffff', name: 'Feature Row C', desc: 'Text left, screenshot right — repeat.' },
                { color: '#5a4020', name: 'CTA', desc: 'Bold call-to-action band.' },
                { color: '#0a0806', name: 'Footer', desc: 'Footer.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                // compact hero
                s += rect(0, 36, W, 80, 'wf-hero');
                s += rect(200, 52, 400, 16, 'wf-nav-lt');
                s += rect(250, 75, 300, 10, 'wf-nav-lt');
                // feature A
                s += rect(0, 116, W, 90, 'wf-white');
                s += rect(28, 126, 240, 10, 'wf-line');
                s += rect(28, 142, 320, 6, 'wf-text-b');
                s += rect(28, 154, 300, 6, 'wf-text-b');
                s += rect(28, 166, 260, 6, 'wf-text-b');
                s += rect(28, 180, 80, 16, 'wf-accent');
                s += rect(400, 124, 372, 74, 'wf-img');
                s += line(400, 124, 772, 198, 'wf-grid-line');
                s += line(772, 124, 400, 198, 'wf-grid-line');
                // feature B
                s += rect(0, 206, W, 90, 'wf-sidebar');
                s += rect(28, 214, 372, 74, 'wf-img');
                s += line(28, 214, 400, 288, 'wf-grid-line');
                s += line(400, 214, 28, 288, 'wf-grid-line');
                s += rect(420, 214, 240, 10, 'wf-line');
                s += rect(420, 230, 320, 6, 'wf-text-b');
                s += rect(420, 242, 300, 6, 'wf-text-b');
                s += rect(420, 254, 260, 6, 'wf-text-b');
                s += rect(420, 268, 80, 16, 'wf-accent');
                // feature C
                s += rect(0, 296, W, 90, 'wf-white');
                s += rect(28, 306, 240, 10, 'wf-line');
                s += rect(28, 322, 320, 6, 'wf-text-b');
                s += rect(28, 334, 300, 6, 'wf-text-b');
                s += rect(28, 346, 260, 6, 'wf-text-b');
                s += rect(28, 360, 80, 16, 'wf-accent');
                s += rect(400, 304, 372, 74, 'wf-img');
                s += line(400, 304, 772, 378, 'wf-grid-line');
                s += line(772, 304, 400, 378, 'wf-grid-line');
                // cta
                s += rect(0, 396, W, 56, 'wf-cta');
                s += rect(200, 408, 400, 10, 'wf-nav-lt');
                s += rect(W / 2 - 60, 426, 120, 16, 'wf-white');
                s += footer(452, 44);
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 10 ----------------------------------------------------------------
        {
            id: 'magazine-home',
            name: 'Magazine / News Homepage',
            cat: 'editorial',
            desc: 'Full editorial layout: hero story, two-column feature grid, breaking news strip, category sections.',
            tags: ['editorial', 'news', 'magazine'],
            zones: [
                { color: '#1a1208', name: 'Top Bar + Nav', desc: 'Date/region bar, large nav with category links.' },
                { color: '#a89878', name: 'Hero Story', desc: 'Full-width lead story image with headline overlay.' },
                { color: '#ffffff', name: '2-Column Feature Grid', desc: 'Two secondary stories side by side.' },
                { color: '#f5f2ed', name: 'Breaking Strip', desc: 'Horizontal scrolling news ticker.' },
                { color: '#f5f2ed', name: 'Category Section', desc: 'Multi-column grid of article cards per category.' },
                { color: '#0a0806', name: 'Footer', desc: 'Footer with categories and social.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                // top bar
                s += rect(0, 0, W, 16, 'wf-accent');
                // nav
                s += rect(0, 16, W, 36, 'wf-white');
                s += rect(16, 24, 80, 18, 'wf-line');
                for (var i = 0; i < 6; i++) {
                    s += rect(120 + i * 100, 25, 80, 8, 'wf-text-b');
                }
                // hero story
                s += rect(0, 52, W, 170, 'wf-img');
                s += line(0, 52, W, 222, 'wf-grid-line');
                s += line(W, 52, 0, 222, 'wf-grid-line');
                s += rect(0, 160, W, 62, 'wf-hero');
                s += rect(16, 168, 300, 16, 'wf-white');
                s += rect(16, 190, 400, 8, 'wf-nav-lt');
                s += rect(16, 203, 100, 8, 'wf-nav-lt');
                // two feature stories
                s += rect(0,   222, 395, 80, 'wf-white');
                s += rect(405, 222, 395, 80, 'wf-white');
                s += rect(0,   222, 395, 50, 'wf-block');
                s += rect(405, 222, 395, 50, 'wf-block');
                s += rect(14,  278, 200, 8, 'wf-line');
                s += rect(14,  291, 250, 6, 'wf-text-b');
                s += rect(419, 278, 200, 8, 'wf-line');
                s += rect(419, 291, 250, 6, 'wf-text-b');
                // ticker strip
                s += rect(0, 302, W, 22, 'wf-accent');
                s += rect(6, 308, 48, 8, 'wf-white');
                for (var j = 0; j < 5; j++) {
                    s += rect(66 + j * 140, 309, 120, 6, 'wf-nav-lt');
                }
                // category grid
                var cols = 4, cw2 = (W - 40) / cols, ch = 80;
                for (var k = 0; k < cols; k++) {
                    s += rect(16 + k * (cw2 + 8), 334, cw2, ch, 'wf-card');
                    s += rect(16 + k * (cw2 + 8), 334, cw2, 44, 'wf-block');
                    s += rect(24 + k * (cw2 + 8), 384, cw2 - 16, 7, 'wf-line');
                    s += rect(24 + k * (cw2 + 8), 397, cw2 - 30, 6, 'wf-text-b');
                }
                s += footer(424, 44);
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 11 ---------------------------------------------------------------
        {
            id: 'waitlist-minimal',
            name: 'Coming Soon / Waitlist',
            cat: 'marketing',
            desc: 'Minimal full-screen layout — bold headline, email capture, and countdown timer. Zero distractions.',
            tags: ['marketing', 'launch', 'minimal'],
            zones: [
                { color: '#f5f2ed', name: 'Full-Screen Container', desc: 'Full viewport, centered flex layout.' },
                { color: '#1a1208', name: 'Brand Mark', desc: 'Logo / wordmark top-center.' },
                { color: '#ff7000', name: 'Headline Block', desc: 'Large bold headline and sub-copy.' },
                { color: '#5a4020', name: 'Email Capture', desc: 'Inline email input + CTA button.' },
                { color: '#c8b898', name: 'Countdown', desc: 'Days / Hours / Mins / Secs tiles.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                // brand mark
                s += rect(W / 2 - 40, 60, 80, 24, 'wf-nav');
                // headline
                s += rect(120, 110, 560, 32, 'wf-line');
                s += rect(200, 150, 400, 16, 'wf-text-b');
                s += rect(230, 173, 340, 12, 'wf-text-b');
                // email capture
                s += rect(160, 206, 360, 38, 'wf-white');
                s += rect(528, 206, 112, 38, 'wf-accent');
                // countdown tiles
                var tiles = 4;
                var tw = 110, tgap = 16;
                var startX = (W - tiles * tw - (tiles - 1) * tgap) / 2;
                for (var i = 0; i < tiles; i++) {
                    s += rect(startX + i * (tw + tgap), 264, tw, 80, 'wf-card');
                    s += rect(startX + i * (tw + tgap) + 22, 282, tw - 44, 32, 'wf-accent');
                    s += rect(startX + i * (tw + tgap) + 30, 320, tw - 60, 10, 'wf-text-b');
                }
                // fine print
                s += rect(230, 360, 340, 8, 'wf-text-b');
                s += rect(270, 374, 260, 8, 'wf-text-b');
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 12 ---------------------------------------------------------------
        {
            id: 'analytics-dash',
            name: 'Analytics Dashboard',
            cat: 'dashboard',
            desc: 'Dense analytics layout: KPI row, line/area chart, data table, and quick-action panel.',
            tags: ['dashboard', 'analytics', 'saas'],
            zones: [
                { color: '#1a1208', name: 'Top Nav', desc: 'App nav with search and account controls.' },
                { color: '#c8b898', name: 'Left Nav', desc: 'Icon + label sidebar with collapse toggle.' },
                { color: '#f5f2ed', name: 'KPI Cards', desc: '4 metric cards with trend sparkline.' },
                { color: '#ffffff', name: 'Line Chart', desc: 'Wide multi-series line chart with legend.' },
                { color: '#ffffff', name: 'Data Table', desc: 'Sortable table with pagination.' },
                { color: '#c8b898', name: 'Filters Panel', desc: 'Right panel: date range, segment filters.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                // sidebar
                s += rect(0, 36, 50, H - 36, 'wf-nav');
                for (var i = 0; i < 7; i++) {
                    s += rect(12, 56 + i * 44, 26, 20, 'wf-nav-lt');
                }
                // KPI cards
                var kw = (W - 50 - 200 - 40) / 4 - 8;
                for (var j = 0; j < 4; j++) {
                    var kx = 58 + j * (kw + 8);
                    s += rect(kx, 44, kw, 66, 'wf-card');
                    s += rect(kx + 6, 52, kw - 40, 8, 'wf-text-b');
                    s += rect(kx + 6, 66, kw - 20, 14, 'wf-line');
                    // sparkline
                    s += '<polyline points="' + [kx+6, kx+kw/3, kx+2*kw/3, kx+kw-6].map(function(px, idx){ return px + ',' + (100 - [0,8,4,12][idx]); }).join(' ') + '" fill="none" stroke="#ff7000" stroke-width="1.5"/>';
                }
                // line chart
                s += rect(58, 120, W - 58 - 200 - 8, 160, 'wf-card');
                s += rect(66, 128, 160, 8, 'wf-line');
                // chart lines
                var chartW = W - 58 - 200 - 8 - 16;
                var pts1 = '', pts2 = '';
                for (var k = 0; k < 14; k++) {
                    var px = 66 + k * (chartW / 13);
                    var py1 = 220 - Math.sin(k * 0.5) * 40 - Math.random() * 15;
                    var py2 = 240 - Math.sin(k * 0.4 + 1) * 30 - Math.random() * 10;
                    pts1 += px + ',' + py1 + ' ';
                    pts2 += px + ',' + py2 + ' ';
                }
                s += '<polyline points="' + pts1 + '" fill="none" stroke="#5a4020" stroke-width="2"/>';
                s += '<polyline points="' + pts2 + '" fill="none" stroke="#ff7000" stroke-width="2" stroke-dasharray="4 3"/>';
                s += line(66, 264, 66 + chartW, 264, 'wf-grid-line');
                // table
                s += rect(58, 290, W - 58 - 200 - 8, 160, 'wf-card');
                s += rect(58, 290, W - 58 - 200 - 8, 20, 'wf-sidebar');
                for (var r = 0; r < 6; r++) {
                    s += rect(66, 318 + r * 20, W - 58 - 200 - 8 - 16, 12, r % 2 === 0 ? 'wf-bg' : 'wf-white');
                    s += rect(66, 321 + r * 20, 80, 6, 'wf-text-b');
                    s += rect(200, 321 + r * 20, 60, 6, 'wf-text-b');
                    s += rect(310, 321 + r * 20, 50, 6, 'wf-text-b');
                }
                // right filters panel
                s += rect(W - 192, 44, 184, H - 44, 'wf-sidebar');
                s += rect(W - 186, 52, 120, 8, 'wf-line');
                for (var f = 0; f < 6; f++) {
                    s += rect(W - 186, 70 + f * 42, 160, 30, 'wf-white');
                    s += rect(W - 180, 76 + f * 42, 100, 6, 'wf-text-b');
                    s += rect(W - 180, 88 + f * 42, 150, 12, 'wf-block');
                }
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 13 ---------------------------------------------------------------
        {
            id: 'mobile-first-stack',
            name: 'Mobile-First Stacked',
            cat: 'marketing',
            desc: 'Single-column stacked layout — hero, feature pills, testimonial strip, and sticky bottom CTA. Optimised for mobile conversion.',
            tags: ['marketing', 'mobile', 'conversion'],
            zones: [
                { color: '#1a1208', name: 'Nav', desc: 'Slim nav with hamburger.' },
                { color: '#ff7000', name: 'Hero', desc: 'Full-width stacked text + image below.' },
                { color: '#f5f2ed', name: 'Feature Pills', desc: 'Scrolling chip row of feature labels.' },
                { color: '#ffffff', name: 'Feature Stack', desc: 'Single-column icon + text rows.' },
                { color: '#c8b898', name: 'Social Proof', desc: 'Avatar faces + star ratings strip.' },
                { color: '#5a4020', name: 'Sticky CTA', desc: 'Fixed bottom bar with CTA button.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                // hero
                s += rect(0, 36, W, 130, 'wf-hero');
                s += rect(80, 56, 640, 20, 'wf-nav-lt');
                s += rect(140, 84, 520, 12, 'wf-nav-lt');
                s += rect(W / 2 - 80, 112, 160, 28, 'wf-white');
                // feature pills
                s += rect(0, 166, W, 32, 'wf-sidebar');
                for (var i = 0; i < 6; i++) {
                    s += rect(16 + i * 122, 173, 108, 16, i === 0 ? 'wf-accent' : 'wf-block');
                }
                // feature stack
                var features = 4;
                for (var j = 0; j < features; j++) {
                    s += rect(40, 210 + j * 40, 720, 32, 'wf-white');
                    s += rect(52, 218 + j * 40, 18, 18, 'wf-accent');
                    s += rect(80, 221 + j * 40, 180, 8, 'wf-line');
                    s += rect(80, 234 + j * 40, 280, 6, 'wf-text-b');
                }
                // social proof
                s += rect(0, 374, W, 50, 'wf-sidebar');
                for (var k = 0; k < 5; k++) {
                    s += '<circle cx="' + (80 + k * 34) + '" cy="399" r="14" class="wf-block2"/>';
                }
                s += rect(260, 390, 100, 8, 'wf-line');
                s += rect(260, 404, 140, 6, 'wf-text-b');
                for (var st = 0; st < 5; st++) {
                    s += rect(440 + st * 26, 392, 18, 14, 'wf-orange');
                }
                // sticky CTA
                s += rect(0, 464, W, 36, 'wf-accent');
                s += rect(W / 2 - 100, 473, 200, 18, 'wf-white');
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 14 ---------------------------------------------------------------
        {
            id: 'onboarding-steps',
            name: 'Onboarding / Steps',
            cat: 'saas',
            desc: 'Stepped wizard layout with progress indicator, side context panel, and centered form area.',
            tags: ['saas', 'onboarding', 'form'],
            zones: [
                { color: '#1a1208', name: 'Top Bar', desc: 'Logo + step counter + exit link.' },
                { color: '#c8b898', name: 'Progress Bar', desc: 'Horizontal step indicators showing current progress.' },
                { color: '#ffffff', name: 'Form Step', desc: 'Centered form with heading, inputs, and Next button.' },
                { color: '#f5f2ed', name: 'Context Panel', desc: 'Right panel with tips, preview, or testimonial.' },
                { color: '#5a4020', name: 'Next / Submit Button', desc: 'Primary action anchored to bottom of form.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                // top bar
                s += rect(0, 0, W, 48, 'wf-white');
                s += rect(20, 16, 80, 16, 'wf-line');
                s += rect(W - 100, 18, 80, 12, 'wf-text-b');
                // progress steps
                s += rect(0, 48, W, 40, 'wf-sidebar');
                var steps = 5;
                var sw = (W - 80) / steps;
                for (var i = 0; i < steps; i++) {
                    var sc = i < 2 ? 'wf-accent' : (i === 2 ? 'wf-cta' : 'wf-block');
                    s += '<circle cx="' + (40 + i * sw + sw / 2) + '" cy="68" r="10" class="' + sc + '"/>';
                    if (i < steps - 1) {
                        s += line(40 + i * sw + sw / 2 + 10, 68, 40 + (i + 1) * sw + sw / 2 - 10, 68, i < 2 ? 'wf-grid-line' : 'wf-grid-line');
                    }
                    s += rect(40 + i * sw + sw / 2 - 30, 82, 60, 6, 'wf-text-b');
                }
                // form area
                s += rect(40, 100, 500, 340, 'wf-white');
                s += rect(60, 118, 200, 16, 'wf-line');
                s += rect(60, 140, 420, 8, 'wf-text-b');
                // inputs
                s += rect(60, 162, 420, 30, 'wf-block');
                s += rect(60, 204, 420, 30, 'wf-block');
                s += rect(60, 246, 200, 30, 'wf-block');
                s += rect(280, 246, 200, 30, 'wf-block');
                s += rect(60, 288, 420, 30, 'wf-block');
                s += rect(60, 330, 420, 50, 'wf-block');
                // next button
                s += rect(60, 394, 420, 34, 'wf-cta');
                // context panel
                s += rect(560, 100, 220, 340, 'wf-sidebar');
                s += rect(574, 116, 140, 10, 'wf-line');
                s += rect(574, 134, 180, 6, 'wf-text-b');
                s += rect(574, 146, 160, 6, 'wf-text-b');
                s += rect(574, 158, 170, 6, 'wf-text-b');
                s += rect(574, 180, 192, 120, 'wf-block');
                s += rect(574, 310, 100, 8, 'wf-line');
                s += rect(574, 324, 180, 5, 'wf-text-b');
                s += rect(574, 335, 160, 5, 'wf-text-b');
                s += rect(574, 346, 100, 5, 'wf-text-b');
                s += nav(48);
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 15 ---------------------------------------------------------------
        {
            id: 'fullscreen-split-auth',
            name: 'Split Auth / Login',
            cat: 'saas',
            desc: 'Left: brand/visual panel. Right: login or signup form. Clean, conversion-focused auth layout.',
            tags: ['saas', 'auth', 'form'],
            zones: [
                { color: '#1a1208', name: 'Brand Panel', desc: 'Full-height left panel with logo, tagline, and testimonial or visual.' },
                { color: '#ffffff', name: 'Form Panel', desc: 'Right panel: headline, social auth, divider, email/password, submit, link to signup.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                // brand panel
                s += rect(0, 0, W / 2, H, 'wf-nav');
                s += line(0, 0, W / 2, H, 'wf-grid-line');
                s += line(W / 2, 0, 0, H, 'wf-grid-line');
                s += rect(40, 50, 100, 28, 'wf-nav-lt');
                s += rect(40, 180, 280, 30, 'wf-nav-lt');
                s += rect(40, 218, 310, 10, 'wf-nav-lt');
                s += rect(40, 234, 260, 10, 'wf-nav-lt');
                s += rect(40, 360, 260, 60, 'wf-hero');
                s += rect(52, 370, 200, 8, 'wf-nav-lt');
                s += rect(52, 384, 180, 7, 'wf-nav-lt');
                s += rect(52, 397, 100, 6, 'wf-nav-lt');
                // form panel
                s += rect(W / 2, 0, W / 2, H, 'wf-white');
                var fx = W / 2 + 50;
                s += rect(fx, 80, 280, 22, 'wf-line');
                s += rect(fx, 108, 280, 10, 'wf-text-b');
                // social buttons
                s += rect(fx, 132, 130, 28, 'wf-block');
                s += rect(fx + 150, 132, 130, 28, 'wf-block');
                // divider
                s += line(fx, 172, fx + 280, 172, 'wf-grid-line');
                s += rect(fx + 110, 166, 60, 12, 'wf-white');
                s += rect(fx + 120, 169, 40, 6, 'wf-text-b');
                // inputs
                s += rect(fx, 188, 280, 32, 'wf-block');
                s += rect(fx, 228, 280, 32, 'wf-block');
                s += rect(fx + 180, 268, 100, 10, 'wf-text-b');
                s += rect(fx, 286, 280, 36, 'wf-accent');
                // link
                s += rect(fx + 60, 334, 160, 8, 'wf-text-b');
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 16 ---------------------------------------------------------------
        {
            id: 'dark-saas-hero',
            name: 'Dark SaaS Hero',
            cat: 'saas',
            desc: 'Dark-mode hero with glow effect, product screenshot, and feature chip row. Popular for dev tools.',
            tags: ['saas', 'dark', 'hero'],
            zones: [
                { color: '#0a0806', name: 'Dark Background', desc: 'Near-black base with subtle gradient glow behind hero.' },
                { color: '#1a1208', name: 'Nav', desc: 'Dark sticky nav with glow accent.' },
                { color: '#ff7000', name: 'Glow + Headline', desc: 'Radial glow behind large headline and CTA.' },
                { color: '#0a0806', name: 'Product Screenshot', desc: 'Dark-bordered screenshot with glow drop shadow.' },
                { color: '#1a1208', name: 'Feature Chips', desc: 'Row of icon + label feature chips.' },
                { color: '#0a0806', name: 'Footer', desc: 'Dark footer.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-foot');
                // glow radial
                s += '<radialGradient id="gl" cx="50%" cy="50%" r="50%"><stop offset="0%" stop-color="#ff7000" stop-opacity="0.35"/><stop offset="100%" stop-color="#1a1208" stop-opacity="0"/></radialGradient>';
                s += '<rect x="100" y="36" width="600" height="180" fill="url(#gl)"/>';
                // nav dark
                s += rect(0, 0, W, 36, 'wf-nav');
                s += rect(16, 12, 60, 12, 'wf-nav-lt');
                s += rect(W - 140, 11, 30, 14, 'wf-nav-lt');
                s += rect(W - 100, 11, 30, 14, 'wf-nav-lt');
                s += rect(W - 58,  11, 48, 14, 'wf-accent');
                // headline
                s += rect(160, 58, 480, 26, 'wf-nav-lt');
                s += rect(200, 92, 400, 14, 'wf-nav-lt');
                s += rect(240, 112, 320, 10, 'wf-nav-lt');
                s += rect(W / 2 - 80, 136, 72, 22, 'wf-accent');
                s += rect(W / 2 + 6,  136, 70, 22, 'wf-nav-lt');
                // screenshot
                s += rect(120, 172, 560, 150, 'wf-nav');
                s += rect(122, 174, 556, 146, 'wf-hero');
                // fake terminal / UI bars
                s += rect(130, 182, 80, 8, 'wf-nav-lt');
                for (var i = 0; i < 6; i++) {
                    s += rect(130, 196 + i * 18, 200 + (i % 3) * 50, 6, i % 2 === 0 ? 'wf-nav-lt' : 'wf-accent');
                }
                // feature chips
                var chips = 5, chipW = 120, chipGap = 12;
                var chipStart = (W - chips * chipW - (chips - 1) * chipGap) / 2;
                for (var j = 0; j < chips; j++) {
                    s += rect(chipStart + j * (chipW + chipGap), 332, chipW, 24, 'wf-nav');
                    s += rect(chipStart + j * (chipW + chipGap) + 6, 340, 10, 10, 'wf-accent');
                    s += rect(chipStart + j * (chipW + chipGap) + 22, 342, 80, 6, 'wf-nav-lt');
                }
                // logos row
                for (var k = 0; k < 6; k++) {
                    s += rect(40 + k * 120, 368, 80, 14, 'wf-nav-lt');
                }
                // footer dark
                s += rect(0, 396, W, 8, 'wf-nav');
                s += rect(0, 404, W, H - 404, 'wf-foot');
                s += rect(16, 412, 50, 8, 'wf-nav-lt');
                for (var fl = 0; fl < 4; fl++) {
                    s += rect(W - 340 + fl * 80, 412, 60, 6, 'wf-nav-lt');
                }
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 17 ---------------------------------------------------------------
        {
            id: 'ecomm-category',
            name: 'E-Commerce Category / PLP',
            cat: 'ecommerce',
            desc: 'Product listing page with sticky left filter panel, sort bar, and responsive product card grid.',
            tags: ['ecommerce', 'listing', 'grid'],
            zones: [
                { color: '#1a1208', name: 'Nav + Cart Bar', desc: 'Nav with cart count and search.' },
                { color: '#f5f2ed', name: 'Breadcrumb + Page Title', desc: 'Category heading and breadcrumb.' },
                { color: '#c8b898', name: 'Filter Panel', desc: 'Sticky left panel: price range, colour, size, rating.' },
                { color: '#ffffff', name: 'Sort Bar + Products', desc: 'Results count, sort select, 3-column product grid.' },
                { color: '#0a0806', name: 'Footer', desc: 'Store footer.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                // breadcrumb
                s += rect(20, 44, 300, 8, 'wf-text-b');
                s += rect(20, 58, 200, 14, 'wf-line');
                // filter panel
                s += rect(20, 80, 160, H - 80 - 44, 'wf-sidebar');
                for (var i = 0; i < 5; i++) {
                    s += rect(30, 90 + i * 64, 80, 8, 'wf-line');
                    s += rect(30, 104 + i * 64, 130, 6, 'wf-block');
                    s += rect(30, 116 + i * 64, 130, 6, 'wf-block');
                    s += rect(30, 128 + i * 64, 100, 6, 'wf-block');
                }
                // sort bar
                s += rect(192, 80, W - 212, 28, 'wf-white');
                s += rect(200, 88, 100, 10, 'wf-text-b');
                s += rect(W - 212 - 100, 84, 120, 20, 'wf-block');
                // product grid 3 col
                var cols = 3, cw = Math.floor((W - 212 - 24) / cols) - 8;
                for (var r = 0; r < 2; r++) {
                    for (var c = 0; c < cols; c++) {
                        var px = 192 + c * (cw + 8);
                        var py = 118 + r * 140;
                        s += rect(px, py, cw, 128, 'wf-card');
                        s += rect(px, py, cw, 80, 'wf-block');
                        s += rect(px + 8, py + 86, cw - 80, 8, 'wf-line');
                        s += rect(px + 8, py + 100, 60, 10, 'wf-accent');
                        s += rect(px + 8, py + 114, 80, 6, 'wf-text-b');
                    }
                }
                s += footer(H - 44, 44);
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 18 ---------------------------------------------------------------
        {
            id: 'profile-card-layout',
            name: 'Personal Profile / Resume',
            cat: 'portfolio',
            desc: 'Two-column profile layout: avatar + contact sidebar, main area with timeline, skills, and projects.',
            tags: ['portfolio', 'resume', 'profile'],
            zones: [
                { color: '#1a1208', name: 'Header Band', desc: 'Full-width coloured header with name and title.' },
                { color: '#c8b898', name: 'Profile Sidebar', desc: 'Avatar, contact details, skills list, social links.' },
                { color: '#ffffff', name: 'Timeline Section', desc: 'Vertical timeline of experience and education.' },
                { color: '#f5f2ed', name: 'Skills / Tools', desc: 'Progress bars or chip grid of skill indicators.' },
                { color: '#5a4020', name: 'CTA', desc: 'Download CV or Contact button row.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                // header
                s += rect(0, 0, W, 90, 'wf-nav');
                s += '<circle cx="60" cy="60" r="36" class="wf-block2"/>';
                s += rect(110, 28, 200, 20, 'wf-white');
                s += rect(110, 54, 150, 10, 'wf-nav-lt');
                s += rect(110, 70, 180, 8, 'wf-nav-lt');
                s += rect(W - 160, 32, 130, 24, 'wf-accent');
                // sidebar
                s += rect(0, 90, 180, H - 90, 'wf-sidebar');
                s += rect(16, 108, 100, 8, 'wf-line');
                for (var i = 0; i < 5; i++) {
                    s += rect(16, 124 + i * 24, 148, 8, 'wf-block');
                }
                s += rect(16, 252, 100, 8, 'wf-line');
                for (var j = 0; j < 4; j++) {
                    var sw2 = [120, 100, 140, 90][j];
                    s += rect(16, 268 + j * 20, sw2, 8, 'wf-block');
                }
                s += rect(16, 356, 100, 8, 'wf-line');
                for (var sk = 0; sk < 5; sk++) {
                    s += rect(16, 372 + sk * 16, 148, 7, 'wf-accent');
                    s += rect(16, 372 + sk * 16, [120, 100, 140, 90, 130][sk], 7, 'wf-accent');
                }
                // timeline
                s += rect(196, 90, W - 196, H - 90, 'wf-white');
                s += line(220, 100, 220, H - 10, 'wf-grid-line');
                for (var t = 0; t < 4; t++) {
                    s += '<circle cx="220" cy="' + (116 + t * 90) + '" r="6" class="wf-accent"/>';
                    s += rect(238, 110 + t * 90, 200, 10, 'wf-line');
                    s += rect(238, 126 + t * 90, 100, 7, 'wf-text-b');
                    s += rect(238, 139 + t * 90, 350, 6, 'wf-text-b');
                    s += rect(238, 151 + t * 90, 320, 6, 'wf-text-b');
                    s += rect(238, 163 + t * 90, 280, 6, 'wf-text-b');
                }
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 19 ---------------------------------------------------------------
        {
            id: 'editorial-magazine',
            name: 'Magazine Grid',
            cat: 'editorial',
            desc: 'Hero article at top, 2x2 card grid below. Classic magazine-style editorial landing.',
            tags: ['editorial', 'blog', 'marketing'],
            zones: [
                { color: '#1a1208', name: 'Nav', desc: 'Sticky navigation bar with brand and section links.' },
                { color: '#ff7000', name: 'Featured Article', desc: 'Large hero banner with featured article title, excerpt, and author byline.' },
                { color: '#d4c4a8', name: 'Category Bar', desc: 'Horizontal strip of category / topic filter links.' },
                { color: '#f5f2ed', name: 'Article Grid', desc: '2x2 card grid of article thumbnails with titles, dates, and category tags.' },
                { color: '#c8b898', name: 'Newsletter Signup', desc: 'Email capture bar encouraging readers to subscribe for updates.' },
                { color: '#0a0806', name: 'Footer', desc: 'Multi-column footer with links, social icons, and copyright.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                s += rect(0, 36, W, 160, 'wf-hero');
                s += rect(60, 66, 320, 20, 'wf-nav-lt');
                s += rect(60, 96, 400, 10, 'wf-nav-lt');
                s += rect(60, 112, 200, 8, 'wf-nav-lt');
                s += rect(60, 132, 100, 22, 'wf-white');
                s += rect(0, 196, W, 24, 'wf-block');
                for (var c = 0; c < 5; c++) s += rect(60 + c * 140, 202, 80, 12, 'wf-block2');
                s += rect(40, 232, 350, 100, 'wf-card');
                s += rect(410, 232, 350, 100, 'wf-card');
                s += rect(40, 342, 350, 100, 'wf-card');
                s += rect(410, 342, 350, 100, 'wf-card');
                s += footer(456);
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 20 ---------------------------------------------------------------
        {
            id: 'editorial-sidebar-right',
            name: 'Blog with Right Sidebar',
            cat: 'editorial',
            desc: 'Classic blog layout with main content left and a sidebar for categories, recent posts, and ads.',
            tags: ['editorial', 'blog'],
            zones: [
                { color: '#1a1208', name: 'Nav', desc: 'Sticky navigation bar.' },
                { color: '#d4c4a8', name: 'Page Header', desc: 'Page title and breadcrumb navigation.' },
                { color: '#ffffff', name: 'Content Column', desc: 'Main article content area with text, images, and pull quotes.' },
                { color: '#f5f2ed', name: 'Sidebar', desc: 'Right sidebar with search, categories, recent posts, and ad banner.' },
                { color: '#c8b898', name: 'Related Posts', desc: 'Three-card row of related articles below the content.' },
                { color: '#0a0806', name: 'Footer', desc: 'Multi-column footer with links and copyright.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                s += rect(0, 36, W, 30, 'wf-block');
                s += rect(30, 44, 200, 10, 'wf-block2');
                s += rect(30, 76, 500, 350, 'wf-white');
                for (var r = 0; r < 6; r++) {
                    s += rect(50, 90 + r * 50, 460, 6, 'wf-text-b');
                    s += rect(50, 100 + r * 50, 420, 6, 'wf-text-b');
                    s += rect(50, 110 + r * 50, 380, 6, 'wf-text-b');
                }
                s += rect(550, 76, 220, 350, 'wf-card');
                s += rect(566, 90, 188, 10, 'wf-line');
                s += rect(566, 110, 188, 60, 'wf-block');
                s += rect(566, 180, 140, 8, 'wf-text-b');
                s += rect(566, 196, 140, 8, 'wf-text-b');
                s += rect(566, 212, 140, 8, 'wf-text-b');
                s += rect(566, 236, 188, 80, 'wf-accent');
                s += footer(436);
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 21 ---------------------------------------------------------------
        {
            id: 'editorial-newsletter',
            name: 'Newsletter Landing',
            cat: 'editorial',
            desc: 'Focused landing page for newsletter signups with testimonials and past issue previews.',
            tags: ['editorial', 'marketing'],
            zones: [
                { color: '#1a1208', name: 'Nav', desc: 'Minimal navigation with brand and subscribe CTA.' },
                { color: '#ff7000', name: 'Hero', desc: 'Centered headline with email capture form.' },
                { color: '#f5f2ed', name: 'Social Proof Strip', desc: 'Subscriber count and reader trust signals.' },
                { color: '#ffffff', name: 'Past Issues', desc: 'Card grid previewing recent newsletter editions.' },
                { color: '#c8b898', name: 'Testimonials', desc: 'Reader quotes and feedback.' },
                { color: '#5a4020', name: 'CTA Band', desc: 'Final subscribe banner with email input.' },
                { color: '#0a0806', name: 'Footer', desc: 'Compact footer with links.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                s += rect(0, 36, W, 140, 'wf-hero');
                s += rect(220, 66, 360, 18, 'wf-nav-lt');
                s += rect(240, 96, 320, 12, 'wf-nav-lt');
                s += rect(260, 118, 280, 22, 'wf-white');
                s += rect(0, 176, W, 24, 'wf-block');
                for (var i = 0; i < 3; i++) s += rect(60 + i * 240, 210, 210, 80, 'wf-card');
                s += rect(60, 300, 320, 60, 'wf-card');
                s += rect(420, 300, 320, 60, 'wf-card');
                s += rect(0, 370, W, 46, 'wf-hero');
                s += rect(260, 382, 180, 10, 'wf-nav-lt');
                s += rect(280, 398, 240, 8, 'wf-nav-lt');
                s += footer(420);
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 22 ---------------------------------------------------------------
        {
            id: 'portfolio-agency',
            name: 'Agency Showcase',
            cat: 'portfolio',
            desc: 'Bold hero, services grid, featured case studies, and team section for creative agencies.',
            tags: ['portfolio', 'marketing', 'agency'],
            zones: [
                { color: '#1a1208', name: 'Nav', desc: 'Sticky navigation with brand and contact CTA.' },
                { color: '#ff7000', name: 'Hero', desc: 'Bold agency tagline with background video or image.' },
                { color: '#f5f2ed', name: 'Services', desc: 'Four-column icon grid of core service offerings.' },
                { color: '#ffffff', name: 'Case Studies', desc: 'Large image cards showing featured projects and results.' },
                { color: '#c8b898', name: 'Testimonials', desc: 'Client logo bar and rotating testimonial quotes.' },
                { color: '#d4c4a8', name: 'Team', desc: 'Photo + name + role grid of key team members.' },
                { color: '#5a4020', name: 'CTA Band', desc: 'Bold banner inviting visitors to start a project.' },
                { color: '#0a0806', name: 'Footer', desc: 'Multi-column footer with office address and social links.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                s += rect(0, 36, W, 120, 'wf-hero');
                s += rect(200, 70, 400, 20, 'wf-nav-lt');
                s += rect(250, 100, 300, 10, 'wf-nav-lt');
                s += rect(340, 118, 120, 20, 'wf-white');
                for (var i = 0; i < 4; i++) s += rect(40 + i * 185, 168, 160, 54, 'wf-card');
                s += rect(40, 232, 355, 80, 'wf-card');
                s += rect(405, 232, 355, 80, 'wf-card');
                for (var t = 0; t < 4; t++) s += rect(60 + t * 180, 322, 130, 70, 'wf-card');
                s += rect(0, 400, W, 40, 'wf-hero');
                s += rect(300, 412, 200, 12, 'wf-nav-lt');
                s += footer(444);
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 23 ---------------------------------------------------------------
        {
            id: 'portfolio-photography',
            name: 'Photography Portfolio',
            cat: 'portfolio',
            desc: 'Full-width image hero with masonry gallery, about section, and booking form.',
            tags: ['portfolio', 'gallery'],
            zones: [
                { color: '#d4c4a8', name: 'Transparent Nav', desc: 'Overlaid navigation on hero image.' },
                { color: '#ff7000', name: 'Hero', desc: 'Full-bleed hero image with photographer name overlay.' },
                { color: '#f5f2ed', name: 'Gallery', desc: 'Masonry grid of portfolio images with hover captions.' },
                { color: '#c8b898', name: 'Testimonials', desc: 'Client feedback quotes.' },
                { color: '#ffffff', name: 'About', desc: 'Photo + bio text about the photographer.' },
                { color: '#d4c4a8', name: 'Contact', desc: 'Booking inquiry form with availability calendar link.' },
                { color: '#0a0806', name: 'Footer', desc: 'Minimal footer with social links and copyright.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                s += rect(0, 0, W, 180, 'wf-hero');
                s += rect(260, 60, 280, 24, 'wf-nav-lt');
                s += rect(300, 96, 200, 10, 'wf-nav-lt');
                var gx = [30, 210, 390, 570];
                for (var r = 0; r < 2; r++) {
                    for (var c = 0; c < 4; c++) {
                        var gh = [80, 100, 90, 70][c];
                        s += rect(gx[c], 190 + r * 120, 170, gh, 'wf-card');
                    }
                }
                s += rect(40, 430, 350, 40, 'wf-block');
                s += rect(410, 430, 350, 40, 'wf-block');
                s += rect(40, 480, 350, 44, 'wf-card');
                s += rect(410, 480, 350, 44, 'wf-card');
                s += footer(534);
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 24 ---------------------------------------------------------------
        {
            id: 'portfolio-minimal',
            name: 'Minimal Portfolio',
            cat: 'portfolio',
            desc: 'Clean single-column portfolio with large project images, brief descriptions, and contact CTA.',
            tags: ['portfolio', 'minimal'],
            zones: [
                { color: '#1a1208', name: 'Nav', desc: 'Minimal navigation with brand and menu toggle.' },
                { color: '#ffffff', name: 'Profile Header', desc: 'Name, title, and one-sentence bio centered.' },
                { color: '#f5f2ed', name: 'Project Grid', desc: 'Alternating large image + description project entries.' },
                { color: '#c8b898', name: 'Skills / Tools', desc: 'Horizontal chip list of technologies and tools.' },
                { color: '#5a4020', name: 'CTA', desc: 'Centered call-to-action to hire or get in touch.' },
                { color: '#0a0806', name: 'Footer', desc: 'Simple footer with email and social links.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                s += rect(200, 50, 400, 16, 'wf-line');
                s += rect(260, 74, 280, 10, 'wf-text-b');
                s += rect(60, 100, 680, 140, 'wf-card');
                s += rect(80, 110, 300, 120, 'wf-img');
                s += rect(400, 118, 300, 12, 'wf-line');
                s += rect(400, 138, 320, 8, 'wf-text-b');
                s += rect(400, 152, 280, 8, 'wf-text-b');
                s += rect(60, 252, 680, 140, 'wf-card');
                s += rect(360, 262, 360, 120, 'wf-img');
                s += rect(80, 270, 260, 12, 'wf-line');
                s += rect(80, 290, 240, 8, 'wf-text-b');
                s += rect(80, 304, 200, 8, 'wf-text-b');
                s += rect(0, 404, W, 40, 'wf-hero');
                s += rect(300, 416, 200, 14, 'wf-nav-lt');
                s += footer(450);
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 25 ---------------------------------------------------------------
        {
            id: 'ecomm-collection',
            name: 'Collection Page',
            cat: 'ecommerce',
            desc: 'Category banner, filter sidebar, and multi-row product grid with sort bar.',
            tags: ['ecommerce', 'shop'],
            zones: [
                { color: '#1a1208', name: 'Nav + Cart Bar', desc: 'Navigation with search, account, and cart icons.' },
                { color: '#ff7000', name: 'Collection Banner', desc: 'Category hero image with collection title and description.' },
                { color: '#d4c4a8', name: 'Sort Bar', desc: 'Sort-by dropdown, view toggle, and result count.' },
                { color: '#c8b898', name: 'Filter Sidebar', desc: 'Left sidebar with price range, size, colour, and brand filters.' },
                { color: '#ffffff', name: 'Product Grid', desc: 'Multi-row product card grid with image, name, price, and quick-add button.' },
                { color: '#f5f2ed', name: 'Pagination', desc: 'Page numbers and next/previous controls.' },
                { color: '#0a0806', name: 'Footer', desc: 'Footer with newsletter signup, links, and payment icons.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                s += rect(0, 36, W, 80, 'wf-hero');
                s += rect(250, 60, 300, 18, 'wf-nav-lt');
                s += rect(280, 84, 240, 10, 'wf-nav-lt');
                s += rect(0, 116, W, 20, 'wf-block');
                s += rect(0, 136, 160, H - 136 - 44, 'wf-sidebar');
                s += rect(16, 150, 128, 8, 'wf-line');
                for (var f = 0; f < 5; f++) s += rect(16, 166 + f * 20, 120, 8, 'wf-block');
                var cols = 4, pw = 140, gap = 14, ox = 176;
                for (var row = 0; row < 3; row++) {
                    for (var col = 0; col < cols; col++) {
                        var px = ox + col * (pw + gap);
                        var py = 144 + row * 100;
                        s += rect(px, py, pw, 80, 'wf-card');
                        s += rect(px + 10, py + 6, pw - 20, 50, 'wf-img');
                        s += rect(px + 10, py + 60, 80, 6, 'wf-text-b');
                        s += rect(px + 10, py + 70, 40, 6, 'wf-accent');
                    }
                }
                s += footer(456);
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 26 ---------------------------------------------------------------
        {
            id: 'ecomm-checkout',
            name: 'Checkout Page',
            cat: 'ecommerce',
            desc: 'Two-column checkout: shipping + payment form left, order summary right.',
            tags: ['ecommerce', 'app'],
            zones: [
                { color: '#1a1208', name: 'Nav', desc: 'Minimal checkout nav with brand and security badge.' },
                { color: '#d4c4a8', name: 'Progress Bar', desc: 'Step indicator: Cart > Shipping > Payment > Confirm.' },
                { color: '#ffffff', name: 'Shipping + Payment Form', desc: 'Left column with address fields, shipping options, and payment input.' },
                { color: '#f5f2ed', name: 'Order Summary', desc: 'Right column with item list, quantities, subtotal, tax, and total.' },
                { color: '#5a4020', name: 'Place Order CTA', desc: 'Prominent button to submit the order.' },
                { color: '#0a0806', name: 'Footer', desc: 'Minimal footer with support link and legal.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                s += rect(0, 36, W, 24, 'wf-block');
                for (var st = 0; st < 4; st++) {
                    s += rect(160 + st * 130, 44, 80, 8, st < 2 ? 'wf-accent' : 'wf-block2');
                }
                s += rect(40, 72, 440, 340, 'wf-white');
                s += rect(60, 86, 200, 10, 'wf-line');
                for (var fi = 0; fi < 6; fi++) {
                    s += rect(60, 106 + fi * 40, 400, 24, 'wf-block');
                }
                s += rect(500, 72, 260, 260, 'wf-card');
                s += rect(516, 86, 200, 10, 'wf-line');
                for (var it = 0; it < 3; it++) {
                    s += rect(516, 106 + it * 50, 40, 36, 'wf-img');
                    s += rect(566, 110 + it * 50, 120, 8, 'wf-text-b');
                    s += rect(566, 124 + it * 50, 60, 8, 'wf-accent');
                }
                s += rect(516, 280, 228, 1, 'wf-grid-line');
                s += rect(516, 292, 100, 10, 'wf-line');
                s += rect(680, 292, 60, 10, 'wf-accent');
                s += rect(500, 344, 260, 36, 'wf-accent');
                s += footer(420);
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 27 ---------------------------------------------------------------
        {
            id: 'ecomm-landing',
            name: 'Product Launch',
            cat: 'ecommerce',
            desc: 'Single-product launch page with hero image, feature highlights, social proof, and pre-order CTA.',
            tags: ['ecommerce', 'marketing'],
            zones: [
                { color: '#1a1208', name: 'Nav', desc: 'Navigation with brand and pre-order button.' },
                { color: '#ff7000', name: 'Hero', desc: 'Full-width product image with launch headline and countdown.' },
                { color: '#d4c4a8', name: 'Stats', desc: 'Key product numbers -- units sold, reviews, awards.' },
                { color: '#f5f2ed', name: 'Features', desc: 'Icon grid of product highlights and specs.' },
                { color: '#ffffff', name: 'Gallery', desc: 'Multi-angle product image carousel.' },
                { color: '#c8b898', name: 'Testimonials', desc: 'Early reviewer quotes and ratings.' },
                { color: '#5a4020', name: 'CTA Band', desc: 'Pre-order banner with price and buy button.' },
                { color: '#0a0806', name: 'Footer', desc: 'Footer with warranty info, links, and payment icons.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                s += rect(0, 36, W, 150, 'wf-hero');
                s += rect(60, 66, 320, 22, 'wf-nav-lt');
                s += rect(60, 98, 260, 10, 'wf-nav-lt');
                s += rect(60, 118, 130, 24, 'wf-white');
                s += rect(540, 56, 200, 120, 'wf-img');
                s += rect(0, 186, W, 30, 'wf-block');
                for (var k = 0; k < 4; k++) s += rect(80 + k * 170, 192, 100, 16, 'wf-block2');
                for (var f = 0; f < 4; f++) s += rect(40 + f * 185, 226, 160, 56, 'wf-card');
                s += rect(40, 292, 220, 80, 'wf-card');
                s += rect(280, 292, 220, 80, 'wf-card');
                s += rect(520, 292, 220, 80, 'wf-card');
                s += rect(0, 382, W, 40, 'wf-hero');
                s += rect(280, 394, 240, 14, 'wf-nav-lt');
                s += footer(426);
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 28 ---------------------------------------------------------------
        {
            id: 'dashboard-analytics',
            name: 'Analytics Dashboard',
            cat: 'dashboard',
            desc: 'KPI cards at top, full-width chart, and data table below with export controls.',
            tags: ['dashboard', 'saas', 'app'],
            zones: [
                { color: '#1a1208', name: 'Top Bar + Nav', desc: 'App header with brand, search, notifications, and avatar.' },
                { color: '#c8b898', name: 'Left Nav', desc: 'Vertical icon sidebar with navigation links.' },
                { color: '#d4c4a8', name: 'KPI Cards', desc: 'Four metric cards showing revenue, users, sessions, and conversion.' },
                { color: '#ffffff', name: 'Chart Panel', desc: 'Full-width area or line chart with date range picker.' },
                { color: '#f5f2ed', name: 'Data Table', desc: 'Sortable data table with pagination and export button.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                s += rect(0, 36, 48, H - 36, 'wf-sidebar');
                for (var ni = 0; ni < 5; ni++) s += rect(14, 50 + ni * 34, 20, 20, 'wf-block');
                for (var k = 0; k < 4; k++) s += rect(64 + k * 180, 46, 164, 56, 'wf-card');
                s += rect(64, 114, 720, 180, 'wf-white');
                s += rect(80, 130, 690, 150, 'wf-block');
                s += rect(64, 304, 720, 150, 'wf-card');
                s += rect(80, 316, 688, 10, 'wf-line');
                for (var r = 0; r < 5; r++) {
                    s += rect(80, 334 + r * 22, 688, 8, 'wf-text-b');
                }
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 29 ---------------------------------------------------------------
        {
            id: 'dashboard-crm',
            name: 'CRM Dashboard',
            cat: 'dashboard',
            desc: 'Deals pipeline view with sidebar filters, kanban columns, and activity feed.',
            tags: ['dashboard', 'saas', 'app'],
            zones: [
                { color: '#1a1208', name: 'Top Bar + Nav', desc: 'App header with brand, global search, and user menu.' },
                { color: '#c8b898', name: 'Left Nav', desc: 'Collapsible sidebar with workspace sections and settings.' },
                { color: '#d4c4a8', name: 'Pipeline Header', desc: 'Title bar with view toggle, search, and add-deal button.' },
                { color: '#ffffff', name: 'Kanban Columns', desc: 'Horizontal kanban board with deal cards across stages.' },
                { color: '#f5f2ed', name: 'Activity Feed', desc: 'Right panel showing recent deal activity and notes.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                s += rect(0, 36, 48, H - 36, 'wf-sidebar');
                for (var ni = 0; ni < 5; ni++) s += rect(14, 50 + ni * 34, 20, 20, 'wf-block');
                s += rect(48, 36, W - 48, 28, 'wf-block');
                s += rect(64, 42, 120, 14, 'wf-block2');
                s += rect(W - 140, 42, 110, 14, 'wf-accent');
                var kCols = 4, kw = 110, kGap = 10;
                for (var c = 0; c < kCols; c++) {
                    var kx = 60 + c * (kw + kGap);
                    s += rect(kx, 72, kw, 14, 'wf-line');
                    for (var cd = 0; cd < 4; cd++) {
                        s += rect(kx, 92 + cd * 70, kw, 58, 'wf-card');
                    }
                }
                s += rect(W - 200, 72, 184, H - 72 - 10, 'wf-card');
                s += rect(W - 186, 86, 150, 10, 'wf-line');
                for (var a = 0; a < 8; a++) {
                    s += rect(W - 186, 106 + a * 40, 156, 6, 'wf-text-b');
                    s += rect(W - 186, 116 + a * 40, 120, 6, 'wf-text-b');
                }
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 30 ---------------------------------------------------------------
        {
            id: 'dashboard-settings',
            name: 'Settings Page',
            cat: 'dashboard',
            desc: 'Tabbed settings page with left section nav, form fields, toggles, and save bar.',
            tags: ['dashboard', 'app'],
            zones: [
                { color: '#1a1208', name: 'Top Bar + Nav', desc: 'App header with brand and user menu.' },
                { color: '#c8b898', name: 'Left Nav', desc: 'Vertical icon sidebar.' },
                { color: '#d4c4a8', name: 'Settings Sidebar', desc: 'Left column with section links: Profile, Security, Billing, Team, API.' },
                { color: '#ffffff', name: 'Main Content', desc: 'Right area with form fields, toggles, and option groups.' },
                { color: '#5a4020', name: 'Sticky Save Bar', desc: 'Bottom bar with cancel and save buttons.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                s += rect(0, 36, 48, H - 36, 'wf-sidebar');
                for (var ni = 0; ni < 5; ni++) s += rect(14, 50 + ni * 34, 20, 20, 'wf-block');
                s += rect(48, 36, 160, H - 36, 'wf-card');
                s += rect(64, 50, 120, 10, 'wf-line');
                for (var si = 0; si < 6; si++) {
                    s += rect(64, 70 + si * 28, 128, 16, si === 0 ? 'wf-accent' : 'wf-block');
                }
                s += rect(224, 36, W - 224, H - 36 - 44, 'wf-white');
                s += rect(244, 52, 200, 14, 'wf-line');
                for (var fi = 0; fi < 5; fi++) {
                    s += rect(244, 80 + fi * 56, 500, 8, 'wf-text-b');
                    s += rect(244, 96 + fi * 56, 500, 28, 'wf-block');
                }
                s += rect(224, H - 44, W - 224, 44, 'wf-nav');
                s += rect(W - 180, H - 32, 70, 20, 'wf-block2');
                s += rect(W - 100, H - 32, 80, 20, 'wf-accent');
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 31 ---------------------------------------------------------------
        {
            id: 'marketing-comparison',
            name: 'Comparison / VS Page',
            cat: 'marketing',
            desc: 'Side-by-side product comparison with feature table, hero, and CTA.',
            tags: ['marketing', 'saas'],
            zones: [
                { color: '#1a1208', name: 'Nav', desc: 'Sticky navigation bar.' },
                { color: '#ff7000', name: 'Hero', desc: 'Headline framing the comparison and value proposition.' },
                { color: '#f5f2ed', name: 'Comparison Table', desc: 'Feature-by-feature table with check marks comparing products.' },
                { color: '#c8b898', name: 'Testimonials', desc: 'Customer quotes from users who switched.' },
                { color: '#5a4020', name: 'CTA Band', desc: 'Bold banner to start a free trial.' },
                { color: '#0a0806', name: 'Footer', desc: 'Multi-column footer.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                s += rect(0, 36, W, 100, 'wf-hero');
                s += rect(200, 60, 400, 20, 'wf-nav-lt');
                s += rect(220, 88, 360, 10, 'wf-nav-lt');
                s += rect(100, 148, 600, 200, 'wf-white');
                s += rect(100, 148, 200, 24, 'wf-block');
                s += rect(300, 148, 200, 24, 'wf-accent');
                s += rect(500, 148, 200, 24, 'wf-block');
                for (var r = 0; r < 7; r++) {
                    var ry = 180 + r * 24;
                    s += rect(110, ry, 180, 8, 'wf-text-b');
                    s += rect(340, ry, 120, 8, 'wf-accent');
                    s += rect(540, ry, 120, 8, 'wf-block');
                }
                s += rect(60, 360, 320, 44, 'wf-card');
                s += rect(420, 360, 320, 44, 'wf-card');
                s += rect(0, 416, W, 36, 'wf-hero');
                s += rect(300, 426, 200, 14, 'wf-nav-lt');
                s += footer(456);
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 32 ---------------------------------------------------------------
        {
            id: 'marketing-about',
            name: 'About Us Page',
            cat: 'marketing',
            desc: 'Company story page with mission statement, team grid, timeline, and values section.',
            tags: ['marketing'],
            zones: [
                { color: '#1a1208', name: 'Nav', desc: 'Sticky navigation bar.' },
                { color: '#ff7000', name: 'Hero', desc: 'Centered headline with company mission statement.' },
                { color: '#ffffff', name: 'Story / About', desc: 'Two-column text + image telling the company origin.' },
                { color: '#f5f2ed', name: 'Team', desc: 'Photo + name + role grid of team members.' },
                { color: '#c8b898', name: 'Stats', desc: 'Key company metrics: employees, customers, countries.' },
                { color: '#d4c4a8', name: 'CTA Band', desc: 'Join the team or contact banner.' },
                { color: '#0a0806', name: 'Footer', desc: 'Multi-column footer.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                s += rect(0, 36, W, 100, 'wf-hero');
                s += rect(220, 60, 360, 18, 'wf-nav-lt');
                s += rect(260, 88, 280, 10, 'wf-nav-lt');
                s += rect(60, 148, 320, 110, 'wf-white');
                s += rect(80, 158, 280, 10, 'wf-line');
                s += rect(80, 176, 260, 8, 'wf-text-b');
                s += rect(80, 190, 240, 8, 'wf-text-b');
                s += rect(400, 148, 340, 110, 'wf-img');
                for (var t = 0; t < 4; t++) {
                    s += rect(60 + t * 180, 270, 155, 80, 'wf-card');
                    s += '<circle cx="' + (137 + t * 180) + '" cy="290" r="16" class="wf-accent"/>';
                    s += rect(100 + t * 180, 312, 80, 8, 'wf-line');
                    s += rect(108 + t * 180, 326, 60, 6, 'wf-text-b');
                }
                s += rect(0, 360, W, 40, 'wf-block');
                for (var st = 0; st < 4; st++) s += rect(80 + st * 180, 370, 100, 16, 'wf-block2');
                s += rect(0, 408, W, 36, 'wf-hero');
                s += rect(300, 418, 200, 12, 'wf-nav-lt');
                s += footer(448);
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 33 ---------------------------------------------------------------
        {
            id: 'saas-integrations',
            name: 'Integrations Page',
            cat: 'saas',
            desc: 'Search-driven integrations directory with category filters and integration cards.',
            tags: ['saas', 'marketing'],
            zones: [
                { color: '#1a1208', name: 'Nav', desc: 'Sticky navigation bar.' },
                { color: '#ff7000', name: 'Hero', desc: 'Headline and search bar for finding integrations.' },
                { color: '#d4c4a8', name: 'Category Tabs', desc: 'Horizontal filter tabs by integration category.' },
                { color: '#f5f2ed', name: 'Integration Grid', desc: 'Card grid showing logo, name, description, and connect button per integration.' },
                { color: '#5a4020', name: 'CTA Band', desc: 'Banner encouraging API or custom integration requests.' },
                { color: '#0a0806', name: 'Footer', desc: 'Multi-column footer.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                s += rect(0, 36, W, 100, 'wf-hero');
                s += rect(220, 56, 360, 18, 'wf-nav-lt');
                s += rect(240, 82, 320, 24, 'wf-white');
                s += rect(0, 136, W, 22, 'wf-block');
                for (var c = 0; c < 5; c++) s += rect(80 + c * 130, 140, 90, 12, 'wf-block2');
                for (var row = 0; row < 3; row++) {
                    for (var col = 0; col < 4; col++) {
                        var ix = 40 + col * 185;
                        var iy = 170 + row * 76;
                        s += rect(ix, iy, 170, 66, 'wf-card');
                        s += rect(ix + 10, iy + 10, 30, 30, 'wf-accent');
                        s += rect(ix + 50, iy + 14, 100, 8, 'wf-line');
                        s += rect(ix + 50, iy + 28, 80, 6, 'wf-text-b');
                        s += rect(ix + 10, iy + 48, 50, 12, 'wf-accent');
                    }
                }
                s += rect(0, 400, W, 36, 'wf-hero');
                s += rect(280, 410, 240, 14, 'wf-nav-lt');
                s += footer(440);
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 34 ---------------------------------------------------------------
        {
            id: 'saas-changelog',
            name: 'Changelog / Updates',
            cat: 'saas',
            desc: 'Chronological changelog with version badges, descriptions, and category tags.',
            tags: ['saas', 'editorial'],
            zones: [
                { color: '#1a1208', name: 'Nav', desc: 'Sticky navigation bar.' },
                { color: '#d4c4a8', name: 'Page Header', desc: 'Page title and description.' },
                { color: '#ffffff', name: 'Timeline', desc: 'Vertical timeline of version releases with badges, dates, and descriptions.' },
                { color: '#c8b898', name: 'Email Capture', desc: 'Subscribe bar to get notified of new updates.' },
                { color: '#0a0806', name: 'Footer', desc: 'Compact footer.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                s += rect(0, 36, W, 50, 'wf-block');
                s += rect(260, 50, 280, 16, 'wf-block2');
                s += rect(280, 72, 240, 8, 'wf-block2');
                s += rect(150, 98, 500, H - 98 - 90, 'wf-white');
                s += line(400, 108, 400, H - 100, 'wf-grid-line');
                for (var i = 0; i < 5; i++) {
                    var ty = 116 + i * 60;
                    s += '<circle cx="400" cy="' + (ty + 4) + '" r="5" class="wf-accent"/>';
                    s += rect(420, ty, 200, 10, 'wf-line');
                    s += rect(420, ty + 16, 180, 6, 'wf-text-b');
                    s += rect(420, ty + 28, 160, 6, 'wf-text-b');
                    s += rect(310, ty, 70, 10, 'wf-accent');
                }
                s += rect(0, H - 80, W, 36, 'wf-block');
                s += rect(260, H - 68, 280, 14, 'wf-block2');
                s += footer(H - 44);
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 35 ---------------------------------------------------------------
        {
            id: 'marketing-event',
            name: 'Event / Conference',
            cat: 'marketing',
            desc: 'Event landing page with countdown, speaker grid, schedule, sponsors, and registration CTA.',
            tags: ['marketing', 'event'],
            zones: [
                { color: '#1a1208', name: 'Nav', desc: 'Sticky navigation with event name and register button.' },
                { color: '#ff7000', name: 'Hero', desc: 'Event title, date, venue, and countdown timer.' },
                { color: '#ff9040', name: 'Speakers', desc: 'Photo + name + talk title grid of speakers.' },
                { color: '#ffffff', name: 'Schedule', desc: 'Day-by-day timeline of sessions and talks.' },
                { color: '#d4c4a8', name: 'Social Proof Strip', desc: 'Sponsor logo bar.' },
                { color: '#ff7000', name: 'CTA Band', desc: 'Register now banner with early-bird pricing.' },
                { color: '#0a0806', name: 'Footer', desc: 'Footer with venue address and social links.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                s += rect(0, 36, W, 130, 'wf-hero');
                s += rect(200, 58, 400, 22, 'wf-nav-lt');
                s += rect(240, 88, 320, 10, 'wf-nav-lt');
                for (var cd = 0; cd < 4; cd++) s += rect(220 + cd * 90, 108, 70, 30, 'wf-white');
                for (var sp = 0; sp < 4; sp++) {
                    s += '<circle cx="' + (120 + sp * 180) + '" cy="196" r="24" class="wf-accent"/>';
                    s += rect(80 + sp * 180, 226, 80, 8, 'wf-line');
                    s += rect(80 + sp * 180, 238, 60, 6, 'wf-text-b');
                }
                s += rect(100, 260, 600, 100, 'wf-white');
                for (var sc = 0; sc < 4; sc++) {
                    s += rect(120, 270 + sc * 22, 560, 8, 'wf-block');
                }
                s += rect(0, 370, W, 24, 'wf-block');
                for (var lo = 0; lo < 5; lo++) s += rect(80 + lo * 130, 376, 80, 12, 'wf-block2');
                s += rect(0, 400, W, 40, 'wf-hero');
                s += rect(280, 412, 240, 14, 'wf-nav-lt');
                s += footer(444);
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

        // 36 ---------------------------------------------------------------
        {
            id: 'ecomm-product-single',
            name: 'Single Product Detail',
            cat: 'ecommerce',
            desc: 'Detailed product page with image gallery, specs table, reviews, and upsell suggestions.',
            tags: ['ecommerce', 'shop'],
            zones: [
                { color: '#1a1208', name: 'Nav + Cart Bar', desc: 'Navigation with search, favourites, and cart.' },
                { color: '#d4c4a8', name: 'Breadcrumb', desc: 'Category path breadcrumb trail.' },
                { color: '#ffffff', name: 'Image Gallery', desc: 'Left column with main image and thumbnail strip.' },
                { color: '#f5f2ed', name: 'Purchase Panel', desc: 'Right column with title, price, variants, quantity, and add-to-cart button.' },
                { color: '#c8b898', name: 'Tabs (Details/Reviews)', desc: 'Tabbed section for description, specifications, and customer reviews.' },
                { color: '#d4c4a8', name: 'Related Products', desc: 'Four-card row of related or upsell product suggestions.' },
                { color: '#0a0806', name: 'Footer', desc: 'Footer with delivery info, payment icons, and links.' },
            ],
            svg: (function () {
                var s = svgOpen();
                s += rect(0, 0, W, H, 'wf-bg');
                s += rect(0, 36, W, 16, 'wf-block');
                s += rect(20, 40, 200, 8, 'wf-block2');
                s += rect(30, 60, 360, 220, 'wf-img');
                s += rect(410, 60, 360, 20, 'wf-line');
                s += rect(410, 88, 200, 16, 'wf-accent');
                s += rect(410, 116, 120, 24, 'wf-block');
                s += rect(540, 116, 120, 24, 'wf-block');
                s += rect(410, 152, 180, 8, 'wf-text-b');
                s += rect(410, 172, 200, 32, 'wf-accent');
                s += rect(30, 290, 740, 20, 'wf-block');
                s += rect(30, 310, 740, 80, 'wf-white');
                for (var rr = 0; rr < 3; rr++) s += rect(50, 320 + rr * 22, 700, 8, 'wf-text-b');
                for (var p = 0; p < 4; p++) {
                    s += rect(30 + p * 190, 400, 175, 56, 'wf-card');
                    s += rect(40 + p * 190, 408, 70, 36, 'wf-img');
                    s += rect(118 + p * 190, 412, 70, 8, 'wf-text-b');
                    s += rect(118 + p * 190, 426, 40, 8, 'wf-accent');
                }
                s += nav();
                return s + svgClose();
            }()),
            svgFull: null,
        },

    ];

    // -------------------------------------------------------------------------
    // Render
    // -------------------------------------------------------------------------
    var grid, lightbox, backdrop, panel, lbTitle, lbMeta, lbBody, lbTags,
        lbCounter, prevBtn, nextBtn, closeBtn, filterBtns, stylePreviewBtn,
        addToQuoteBtn, QUOTE_KEY, activeIdx, filteredIds;

    if (hasUI) {
        grid        = document.getElementById('plGrid');
        lightbox    = document.getElementById('plLightbox');
        backdrop    = document.getElementById('plBackdrop');
        panel       = document.getElementById('plPanel');
        lbTitle     = document.getElementById('plLbTitle');
        lbMeta      = document.getElementById('plLbMeta');
        lbBody      = document.getElementById('plLbBody');
        lbTags      = document.getElementById('plLbTags');
        lbCounter   = document.getElementById('plCounter');
        prevBtn     = document.getElementById('plPrev');
        nextBtn     = document.getElementById('plNext');
        closeBtn    = document.getElementById('plClose');
        filterBtns  = document.querySelectorAll('.pl-filter-btn');
        stylePreviewBtn = document.getElementById('plStylePreview');

        addToQuoteBtn = document.getElementById('plAddToQuote');
        QUOTE_KEY = 'xcm_pl_quote';
    }

    function getQuoteList() {
        var key = typeof QUOTE_KEY !== 'undefined' ? QUOTE_KEY : 'xcm_pl_quote';
        try { return JSON.parse(localStorage.getItem(key) || '[]'); } catch(e) { return []; }
    }
    function saveQuoteList(list) {
        var key = typeof QUOTE_KEY !== 'undefined' ? QUOTE_KEY : 'xcm_pl_quote';
        try { localStorage.setItem(key, JSON.stringify(list)); } catch(e) {}
    }

    activeIdx   = 0;
    filteredIds = LAYOUTS.map(function(l){ return l.id; });

    // -------------------------------------------------------------------------
    // "Preview with My Style" — reads Landing Builder live state from localStorage
    // and opens a styled HTML preview of the current layout in a new tab.
    // -------------------------------------------------------------------------
    var LB_LIVE_KEY = 'xcm_lb_live_state';

    // -----------------------------------------------------------------
    // buildZoneHTML  — map a wireframe zone to styled HTML.
    //
    // *  zone.name is ALWAYS used as the visible section label so
    //    the preview mirrors the wireframe breakdown exactly.
    // *  When the Landing Builder has been opened, its rendered
    //    section HTML is reused verbatim (hero, features, pricing …)
    //    so images, copy, and colours are identical to the live design.
    // *  Structural / chrome zones (sidebar, chart, table, filter …)
    //    render as compact labelled placeholder bars instead of being
    //    silently skipped.
    // -----------------------------------------------------------------

    /**
     * Try to pull a rendered section from the LB sectionMap.
     * Returns the HTML string or '' if nothing matched.
     * When found, re-labels the section__label to the zone's name.
     */
    function lbSection(type, sectionMap, zoneName) {
        if (!sectionMap || !sectionMap[type]) return '';
        // Replace the first lb-section__label contents with the zone name
        var html = sectionMap[type];
        html = html.replace(
            /(<p class="lb-section__label">)[^<]*(<\/p>)/,
            '$1' + zoneName + '$2'
        );
        return html;
    }

    function buildZoneHTML(zone, brand, links, seen, sectionMap) {
        var n  = zone.name.toLowerCase();
        var d  = (zone.desc || '').toLowerCase();
        var nm = zone.name;   // original-case for labels

        // ==== Navigation ====
        if ((n.indexOf('nav') !== -1 || n === 'top bar' || n === 'top bar + nav' ||
             n === 'transparent nav') && n.indexOf('left') === -1) {
            if (seen.nav) return '';
            seen.nav = true;
            // Use the LB's actual nav HTML when available
            if (sectionMap && sectionMap._navHTML) return sectionMap._navHTML;
            return '<nav class="lb-nav"><div class="lb-nav__inner">' +
                   '<a href="#" class="lb-nav__brand">' + brand + '</a>' +
                   '<div class="lb-nav__links">' + links + '</div>' +
                   '</div></nav>';
        }

        // ==== Footer ====
        if (n.indexOf('footer') !== -1) {
            if (seen.footer) return '';
            seen.footer = true;
            var fh = lbSection('footer', sectionMap, nm);
            if (fh) return fh;
            return '<footer class="lb-footer lb-section"><div class="lb-container">' +
                   '<p style="text-align:center;opacity:.5;font-size:.8rem;">' +
                   '&copy; 2026 ' + brand + '. All rights reserved.</p>' +
                   '</div></footer>';
        }

        // ==== Hero ====
        if (n.indexOf('hero') !== -1 || n === 'headline block' || n === 'glow + headline') {
            if (seen.hero) return '';
            seen.hero = true;
            var hh = lbSection('hero', sectionMap, nm);
            if (hh) return hh;
            return '<section class="lb-section lb-hero lb-hero--center"><div class="lb-container"><div class="lb-hero__inner"><div class="lb-hero__content">' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<p class="lb-hero__tag">Welcome</p>' +
                   '<h1 class="lb-hero__h1">Transform the Way You Do Business</h1>' +
                   '<p class="lb-hero__sub">The all-in-one platform trusted by 12,000+ teams to automate workflows, centralize data, and deliver measurable results.</p>' +
                   '<a href="#" class="lb-btn">Start Free Trial</a>' +
                   '</div></div></div></section>';
        }

        // ==== CTA ====
        if (n.indexOf('cta') !== -1 || n.indexOf('call-to-action') !== -1 || n === 'sticky cta') {
            if (seen.cta) return '';
            seen.cta = true;
            var ch = lbSection('cta', sectionMap, nm);
            if (ch) return ch;
            return '<section class="lb-section lb-cta"><div class="lb-container">' +
                   '<p class="lb-section__label" style="color:rgba(255,255,255,.65);">' + nm + '</p>' +
                   '<h2>Ready to Transform Your Business?</h2>' +
                   '<p>Join 12,000+ teams already using Vertex. Setup takes less than five minutes.</p>' +
                   '<div class="lb-cta-btn-wrap"><a href="#" class="lb-btn lb-btn--cta">Start Free -- No Credit Card Required</a></div>' +
                   '</div></section>';
        }

        // ==== Feature Row (alternating text+image) ====
        if (n.indexOf('feature row') !== -1) {
            if (!seen._frc) seen._frc = 0;
            var idx = seen._frc++;
            var flip = idx % 2 === 1;
            // Try to pull the text-image section from LB for the first row
            if (idx === 0) {
                var tih = lbSection('text-image', sectionMap, nm);
                if (tih) return tih;
            }
            var heads = ['A Smarter Way to Manage Operations', 'Seamless Integrations', 'Built for Scale'];
            var descs = [
                'Your entire team works from a single source of truth. Automate repetitive tasks and free up time for the work that moves the needle.',
                'Connect with the tools you already use in just a few clicks -- Slack, HubSpot, Salesforce, and 200 more.',
                'From startup to enterprise, the platform grows with you. Handle millions of records without breaking a sweat.',
            ];
            var hd = heads[idx % heads.length];
            var bd = descs[idx % descs.length];
            var textCol = '<div><p class="lb-section__label">' + nm + '</p>' +
                          '<h2 class="lb-ti__headline">' + hd + '</h2>' +
                          '<p class="lb-ti__body">' + bd + '</p>' +
                          '<a href="#" class="lb-btn">Learn More</a></div>';
            var imgCol  = '<div class="lb-ti__placeholder">Screenshot</div>';
            return '<section class="lb-section"><div class="lb-container">' +
                   '<div class="lb-ti__grid' + (flip ? ' lb-ti__grid--left' : '') + '">' +
                   (flip ? imgCol + textCol : textCol + imgCol) +
                   '</div></div></section>';
        }

        // ==== Features card grid ====
        if (n.indexOf('feature') !== -1) {
            if (seen.features) return '';
            seen.features = true;
            var feh = lbSection('features', sectionMap, nm);
            if (feh) return feh;
            return '<section class="lb-section"><div class="lb-container">' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<h2 class="lb-section__title">Built for Speed, Scale, and Simplicity</h2>' +
                   '<div class="lb-features__grid">' +
                   '<div class="lb-card"><div class="lb-feature__icon">&#9670;</div><div class="lb-feature__title">Instant Setup</div><div class="lb-feature__desc">Go live in under five minutes with zero configuration required.</div></div>' +
                   '<div class="lb-card"><div class="lb-feature__icon">&#9650;</div><div class="lb-feature__title">Enterprise Scale</div><div class="lb-feature__desc">Handle millions of records and concurrent users without breaking a sweat.</div></div>' +
                   '<div class="lb-card"><div class="lb-feature__icon">&#9632;</div><div class="lb-feature__title">Real-Time Analytics</div><div class="lb-feature__desc">Understand your data at a glance with live dashboards and custom reports.</div></div>' +
                   '<div class="lb-card"><div class="lb-feature__icon">&#9679;</div><div class="lb-feature__title">24/7 Support</div><div class="lb-feature__desc">Our expert team is available around the clock for any issue, big or small.</div></div>' +
                   '</div></div></section>';
        }

        // ==== Social proof / trust / logos ====
        if (n.indexOf('social') !== -1 || n.indexOf('trust') !== -1 ||
            n.indexOf('proof') !== -1 || (n.indexOf('logo') !== -1 && n.indexOf('login') === -1)) {
            if (seen.social) return '';
            seen.social = true;
            return '<section class="lb-section" style="padding:1.25rem 0;">' +
                   '<div class="lb-container"><p class="lb-section__label" style="text-align:center;margin-bottom:.75rem;">' + nm + '</p>' +
                   '<div style="display:flex;align-items:center;justify-content:center;gap:2.5rem;flex-wrap:wrap;opacity:.38;font-size:.75rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;">' +
                   '<span>Clearpath</span><span>Northlake</span><span>Nomad</span><span>Apexion</span><span>Streamline</span>' +
                   '</div></div></section>';
        }

        // ==== Pricing ====
        if (n.indexOf('pricing') !== -1 || (n.indexOf('plan') !== -1 && n.indexOf('placeholder') === -1)) {
            if (seen.pricing) return '';
            seen.pricing = true;
            var ph = lbSection('pricing', sectionMap, nm);
            if (ph) return ph;
            return '<section class="lb-section"><div class="lb-container">' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<h2 class="lb-section__title">Plans That Grow With You</h2>' +
                   '<div class="lb-pricing__grid">' +
                   '<div class="lb-plan"><div class="lb-plan__name">Starter</div><div><span class="lb-plan__price">$0</span><span class="lb-plan__period">/mo</span></div><ul class="lb-plan__features"><li>Up to 5 users</li><li>10 active workflows</li><li>Email support</li></ul><a href="#" class="lb-btn" style="margin-top:auto;text-align:center;">Get Started Free</a></div>' +
                   '<div class="lb-plan lb-plan--highlight"><div class="lb-plan__name">Growth</div><div><span class="lb-plan__price">$49</span><span class="lb-plan__period">/mo</span></div><ul class="lb-plan__features"><li>Up to 25 users</li><li>Unlimited workflows</li><li>Priority support</li><li>Advanced analytics</li></ul><a href="#" class="lb-btn" style="margin-top:auto;text-align:center;">Start 14-Day Trial</a></div>' +
                   '<div class="lb-plan"><div class="lb-plan__name">Enterprise</div><div><span class="lb-plan__price">$149</span><span class="lb-plan__period">/mo</span></div><ul class="lb-plan__features"><li>Unlimited users</li><li>Custom integrations</li><li>Dedicated account manager</li><li>SLA guarantee</li></ul><a href="#" class="lb-btn" style="margin-top:auto;text-align:center;">Contact Sales</a></div>' +
                   '</div></div></section>';
        }

        // ==== Testimonials ====
        if (n.indexOf('testimonial') !== -1 || (n.indexOf('review') !== -1 && n.indexOf('preview') === -1) ||
            (n.indexOf('customer') !== -1 && n.indexOf('cart') === -1)) {
            if (seen.testimonials) return '';
            seen.testimonials = true;
            var th = lbSection('testimonials', sectionMap, nm);
            if (th) return th;
            return '<section class="lb-section"><div class="lb-container">' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<h2 class="lb-section__title">Results That Speak for Themselves</h2>' +
                   '<div class="lb-test__grid">' +
                   '<div class="lb-test__card"><p class="lb-test__quote">Vertex cut our onboarding time by 60 percent. What used to take a full week now takes a single morning.</p><div class="lb-test__name">Sarah Mitchell</div><div class="lb-test__title">Head of Operations, Clearpath</div></div>' +
                   '<div class="lb-test__card"><p class="lb-test__quote">The analytics alone were worth switching. I finally have visibility across every department in real time.</p><div class="lb-test__name">James Reyes</div><div class="lb-test__title">CEO, Northlake Digital</div></div>' +
                   '</div></div></section>';
        }

        // ==== FAQ ====
        if (n.indexOf('faq') !== -1 || n.indexOf('question') !== -1) {
            if (seen.faq) return '';
            seen.faq = true;
            var fqh = lbSection('faq', sectionMap, nm);
            if (fqh) return fqh;
            return '<section class="lb-section"><div class="lb-container">' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<h2 class="lb-section__title">Common Questions, Honest Answers</h2>' +
                   '<div class="lb-faq__list">' +
                   '<div class="lb-faq__item"><p class="lb-faq__q">Is there a contract or lock-in period?</p><p class="lb-faq__a">No contracts. Cancel or downgrade at any time directly from your account settings.</p></div>' +
                   '<div class="lb-faq__item"><p class="lb-faq__q">Can I migrate data from another platform?</p><p class="lb-faq__a">Yes. Our dedicated onboarding team handles your full data migration at no additional cost.</p></div>' +
                   '<div class="lb-faq__item"><p class="lb-faq__q">Is my data secure?</p><p class="lb-faq__a">Vertex is SOC 2 Type II certified and uses AES-256 encryption for all data at rest and in transit.</p></div>' +
                   '</div></div></section>';
        }

        // ==== Stats / KPI ====
        if (n.indexOf('stat') !== -1 || n.indexOf('kpi') !== -1) {
            if (seen.stats) return '';
            seen.stats = true;
            return '<section class="lb-section" style="padding:2rem 0;"><div class="lb-container">' +
                   '<p class="lb-section__label" style="text-align:center;margin-bottom:1rem;">' + nm + '</p>' +
                   '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1.25rem;">' +
                   '<div class="lb-card" style="text-align:center;"><div style="font-size:2rem;font-weight:800;">12K+</div><div style="font-size:.8rem;opacity:.6;margin-top:.3rem;">Happy Customers</div></div>' +
                   '<div class="lb-card" style="text-align:center;"><div style="font-size:2rem;font-weight:800;">99.9%</div><div style="font-size:.8rem;opacity:.6;margin-top:.3rem;">Uptime</div></div>' +
                   '<div class="lb-card" style="text-align:center;"><div style="font-size:2rem;font-weight:800;">4.9</div><div style="font-size:.8rem;opacity:.6;margin-top:.3rem;">Avg Rating</div></div>' +
                   '<div class="lb-card" style="text-align:center;"><div style="font-size:2rem;font-weight:800;">24h</div><div style="font-size:.8rem;opacity:.6;margin-top:.3rem;">Support Response</div></div>' +
                   '</div></div></section>';
        }

        // ==== About / Story ====
        if (n.indexOf('about') !== -1 || n.indexOf('story') !== -1) {
            if (seen.about) return '';
            seen.about = true;
            var abh = lbSection('text-image', sectionMap, nm);
            if (abh) return abh;
            return '<section class="lb-section"><div class="lb-container">' +
                   '<div class="lb-ti__grid"><div>' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<h2 class="lb-ti__headline">A Smarter Way to Manage Operations</h2>' +
                   '<p class="lb-ti__body">With Vertex, your entire team works from a single source of truth. Automate repetitive tasks, eliminate human error, and free up time for the work that actually moves the needle.</p>' +
                   '<a href="#" class="lb-btn">Our Story</a></div>' +
                   '<div class="lb-ti__placeholder">Image Placeholder</div>' +
                   '</div></div></section>';
        }

        // ==== Contact ====
        if (n.indexOf('contact') !== -1) {
            if (seen.contact) return '';
            seen.contact = true;
            var coh = lbSection('contact', sectionMap, nm);
            if (coh) return coh;
            return '<section class="lb-section"><div class="lb-container">' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<h2 class="lb-section__title">Talk to Our Team</h2>' +
                   '<div class="lb-contact__grid"><p>Have a specific use case or need a custom quote? Our solutions team responds within one business day.</p>' +
                   '<form class="lb-contact__form">' +
                   '<div><label class="lb-contact__label">Name</label><input type="text" placeholder="Your name"></div>' +
                   '<div><label class="lb-contact__label">Email</label><input type="email" placeholder="your@email.com"></div>' +
                   '<div><label class="lb-contact__label">Phone</label><input type="tel" placeholder="+1 000 000 0000"></div>' +
                   '<div><label class="lb-contact__label">Message</label><textarea rows="4" placeholder="Your message..."></textarea></div>' +
                   '<button type="submit" class="lb-btn">Send Message</button>' +
                   '</form></div></div></section>';
        }

        // ==== Email capture / waitlist ====
        if (n.indexOf('email') !== -1 || n.indexOf('waitlist') !== -1 || n.indexOf('capture') !== -1) {
            if (seen.email) return '';
            seen.email = true;
            return '<section class="lb-section" style="text-align:center;"><div class="lb-container">' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<h2 class="lb-section__title">Stay in the Loop</h2>' +
                   '<p class="lb-section__sub" style="margin-left:auto;margin-right:auto;">Be the first to know when we launch new features and updates.</p>' +
                   '<div style="display:flex;gap:.5rem;max-width:440px;margin:1.5rem auto 0;">' +
                   '<input type="email" placeholder="you@example.com" style="flex:1;">' +
                   '<a href="#" class="lb-btn">Notify Me</a></div>' +
                   '</div></section>';
        }

        // ==== Media / Gallery / Image ====
        if (n.indexOf('screenshot') !== -1 || n.indexOf('gallery') !== -1 ||
            (n.indexOf('image') !== -1 && n.indexOf('text') === -1)) {
            if (seen.screenshot) return '';
            seen.screenshot = true;
            var mh = lbSection('media', sectionMap, nm);
            if (mh) return mh;
            return '<section class="lb-section" style="padding-top:0;"><div class="lb-container">' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<div class="lb-ti__placeholder" style="max-height:360px;">Product Screenshot</div>' +
                   '</div></section>';
        }

        // ==== Profile / avatar / resume header ====
        if (n.indexOf('profile') !== -1 || n.indexOf('avatar') !== -1 || n.indexOf('resume') !== -1 ||
            n.indexOf('header band') !== -1) {
            if (seen.profile) return '';
            seen.profile = true;
            return '<section class="lb-section lb-hero lb-hero--center" style="padding:3rem 0;"><div class="lb-container"><div class="lb-hero__inner"><div class="lb-hero__content">' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<h1 class="lb-hero__h1" style="font-size:2rem;">Alex Johnson</h1>' +
                   '<p class="lb-hero__sub">Full-Stack Developer &amp; Designer</p>' +
                   '<div><a href="#" class="lb-btn">Download CV</a>&nbsp;&nbsp;<a href="#" class="lb-btn lb-btn--outline">Contact Me</a></div>' +
                   '</div></div></div></section>';
        }

        // ==== Article / editorial content ====
        if ((n.indexOf('article') !== -1 || n.indexOf('editorial') !== -1 ||
            (n.indexOf('content') !== -1 && n !== 'main content')) &&
            n.indexOf('context') === -1) {
            if (seen.article) return '';
            seen.article = true;
            return '<section class="lb-section"><div class="lb-container" style="max-width:680px;">' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<h2 class="lb-section__title">The Complete Guide to Modern Web Design</h2>' +
                   '<p class="lb-section__sub">By Sarah Mitchell -- February 21, 2026</p>' +
                   '<p style="line-height:1.8;margin-top:1.25rem;">Lorem ipsum dolor sit amet, consectetur adipiscing elit. Pellentesque vehicula, purus vel facilisis placerat, orci lacus malesuada quam, nec pulvinar urna sem vel enim. Praesent hendrerit dolor vel sapien dignissim, vitae interdum libero fringilla.</p>' +
                   '<div style="border-left:3px solid currentColor;padding-left:1.5rem;margin:2rem 0;opacity:.85;"><p style="font-style:italic;font-size:1.1rem;line-height:1.6;">Good design is as little design as possible. Less, but better, because it concentrates on the essential aspects.</p><p style="font-size:.82rem;opacity:.6;margin-top:.5rem;">-- Dieter Rams</p></div>' +
                   '<div class="lb-ti__placeholder" style="margin:2rem 0;max-height:280px;">Inline Image</div>' +
                   '<p style="line-height:1.8;">Nulla facilisi. Sed convallis nunc id nisl auctor, a finibus augue iaculis. Proin pretium, turpis vel fringilla interdum, mauris ipsum convallis libero, at venenatis ligula erat at diam.</p>' +
                   '</div></section>';
        }

        // ==== Page header / breadcrumb + title ====
        if ((n.indexOf('header') !== -1 && n.indexOf('band') === -1) ||
             n.indexOf('page title') !== -1 || n.indexOf('breadcrumb') !== -1) {
            if (seen.header) return '';
            seen.header = true;
            return '<section class="lb-section" style="padding:2.5rem 0 1rem;"><div class="lb-container">' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<h2 class="lb-section__title" style="text-align:center;">Page Title</h2>' +
                   '<p class="lb-section__sub" style="text-align:center;margin-left:auto;margin-right:auto;">' + zone.desc + '</p>' +
                   '</div></section>';
        }

        // ==== Form / steps / onboarding ====
        if (n.indexOf('form step') !== -1 || n.indexOf('onboarding') !== -1 ||
            n.indexOf('wizard') !== -1) {
            if (seen.form) return '';
            seen.form = true;
            return '<section class="lb-section"><div class="lb-container" style="max-width:600px;">' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<h2 class="lb-section__title" style="text-align:center;">Get Started</h2>' +
                   '<p class="lb-section__sub" style="text-align:center;margin-left:auto;margin-right:auto;">' + zone.desc + '</p>' +
                   '<form class="lb-contact__form" style="margin-top:1.5rem;">' +
                   '<div><label class="lb-contact__label">Full Name</label><input type="text" placeholder="Your name"></div>' +
                   '<div style="margin-top:.75rem;"><label class="lb-contact__label">Email</label><input type="email" placeholder="you@example.com"></div>' +
                   '<div style="margin-top:.75rem;"><label class="lb-contact__label">Company</label><input type="text" placeholder="Your company"></div>' +
                   '<button type="submit" class="lb-btn" style="margin-top:1rem;width:100%;text-align:center;">Continue</button>' +
                   '</form></div></section>';
        }

        // ==== Auth / login / signup ====
        if (n.indexOf('auth') !== -1 || n.indexOf('login') !== -1 || n.indexOf('sign') !== -1 ||
            n.indexOf('brand panel') !== -1 || n.indexOf('form panel') !== -1) {
            if (seen.auth) return '';
            seen.auth = true;
            return '<section class="lb-section"><div class="lb-container">' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<div style="display:grid;grid-template-columns:1fr 1fr;gap:3rem;align-items:center;min-height:400px;">' +
                   '<div style="text-align:center;"><h2 class="lb-section__title" style="margin-bottom:.5rem;">' + brand + '</h2>' +
                   '<p class="lb-section__sub" style="margin-left:auto;margin-right:auto;">Welcome back. Sign in to continue.</p></div>' +
                   '<form class="lb-contact__form">' +
                   '<div><label class="lb-contact__label">Email</label><input type="email" placeholder="you@example.com"></div>' +
                   '<div style="margin-top:.75rem;"><label class="lb-contact__label">Password</label><input type="password" placeholder="Your password"></div>' +
                   '<button type="submit" class="lb-btn" style="margin-top:1rem;width:100%;text-align:center;">Sign In</button>' +
                   '<p style="text-align:center;font-size:.82rem;opacity:.6;margin-top:.75rem;">Don\'t have an account? <a href="#">Sign up</a></p>' +
                   '</form></div></div></section>';
        }

        // ==== Countdown / timer ====
        if (n.indexOf('countdown') !== -1 || n.indexOf('timer') !== -1) {
            if (seen.countdown) return '';
            seen.countdown = true;
            return '<section class="lb-section" style="padding:2rem 0;"><div class="lb-container">' +
                   '<p class="lb-section__label" style="text-align:center;margin-bottom:1rem;">' + nm + '</p>' +
                   '<div style="display:flex;justify-content:center;gap:1.25rem;flex-wrap:wrap;">' +
                   '<div class="lb-card" style="text-align:center;min-width:100px;"><div style="font-size:2.2rem;font-weight:800;">14</div><div style="font-size:.75rem;opacity:.5;margin-top:.25rem;">Days</div></div>' +
                   '<div class="lb-card" style="text-align:center;min-width:100px;"><div style="font-size:2.2rem;font-weight:800;">06</div><div style="font-size:.75rem;opacity:.5;margin-top:.25rem;">Hours</div></div>' +
                   '<div class="lb-card" style="text-align:center;min-width:100px;"><div style="font-size:2.2rem;font-weight:800;">42</div><div style="font-size:.75rem;opacity:.5;margin-top:.25rem;">Minutes</div></div>' +
                   '<div class="lb-card" style="text-align:center;min-width:100px;"><div style="font-size:2.2rem;font-weight:800;">09</div><div style="font-size:.75rem;opacity:.5;margin-top:.25rem;">Seconds</div></div>' +
                   '</div></div></section>';
        }

        // ==== Product grid / listing / related ====
        if (n.indexOf('product') !== -1 || n.indexOf('related') !== -1 ||
            n.indexOf('listing') !== -1 || n.indexOf('sort bar') !== -1) {
            if (seen.products) return '';
            seen.products = true;
            return '<section class="lb-section"><div class="lb-container">' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<h2 class="lb-section__title">Products</h2>' +
                   '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:1.25rem;margin-top:1.5rem;">' +
                   '<div class="lb-card"><div class="lb-ti__placeholder" style="aspect-ratio:1/1;margin-bottom:1rem;">Image</div><div class="lb-feature__title">Product A</div><div style="font-size:.85rem;opacity:.6;margin-top:.25rem;">$49.00</div></div>' +
                   '<div class="lb-card"><div class="lb-ti__placeholder" style="aspect-ratio:1/1;margin-bottom:1rem;">Image</div><div class="lb-feature__title">Product B</div><div style="font-size:.85rem;opacity:.6;margin-top:.25rem;">$79.00</div></div>' +
                   '<div class="lb-card"><div class="lb-ti__placeholder" style="aspect-ratio:1/1;margin-bottom:1rem;">Image</div><div class="lb-feature__title">Product C</div><div style="font-size:.85rem;opacity:.6;margin-top:.25rem;">$129.00</div></div>' +
                   '</div></div></section>';
        }

        // ==== Purchase / cart ====
        if (n.indexOf('purchase') !== -1 || n.indexOf('cart') !== -1 || n.indexOf('checkout') !== -1) {
            if (seen.purchase) return '';
            seen.purchase = true;
            return '<section class="lb-section"><div class="lb-container" style="max-width:600px;">' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<h2 class="lb-section__title">Product Name</h2>' +
                   '<p style="font-size:1.5rem;font-weight:800;margin:.75rem 0;">$99.00</p>' +
                   '<p class="lb-section__sub">' + zone.desc + '</p>' +
                   '<a href="#" class="lb-btn" style="margin-top:1rem;">Add to Cart</a>' +
                   '</div></section>';
        }

        // ==== Tabs / details ====
        if (n.indexOf('tab') !== -1 || n.indexOf('detail') !== -1) {
            if (seen.tabs) return '';
            seen.tabs = true;
            return '<section class="lb-section" style="padding:1.5rem 0;"><div class="lb-container">' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<div style="display:flex;gap:0;border-bottom:2px solid rgba(0,0,0,.1);margin-bottom:1.5rem;">' +
                   '<span style="padding:.6rem 1.25rem;font-weight:700;font-size:.85rem;border-bottom:2px solid currentColor;margin-bottom:-2px;">Details</span>' +
                   '<span style="padding:.6rem 1.25rem;font-size:.85rem;opacity:.5;">Specs</span>' +
                   '<span style="padding:.6rem 1.25rem;font-size:.85rem;opacity:.5;">Reviews</span></div>' +
                   '<p style="line-height:1.8;">This is a detailed description of the product. It covers materials, usage, and care instructions for the customer.</p>' +
                   '</div></section>';
        }

        // ==== Timeline / experience ====
        if (n.indexOf('timeline') !== -1 || n.indexOf('experience') !== -1) {
            if (seen.timeline) return '';
            seen.timeline = true;
            return '<section class="lb-section"><div class="lb-container" style="max-width:680px;">' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<h2 class="lb-section__title">Experience</h2>' +
                   '<div style="border-left:2px solid rgba(0,0,0,.1);padding-left:1.5rem;margin-top:1.5rem;display:flex;flex-direction:column;gap:1.75rem;">' +
                   '<div><div style="font-weight:700;">Senior Developer</div><div style="font-size:.82rem;opacity:.5;">2024 - Present</div><p style="font-size:.9rem;opacity:.75;margin-top:.35rem;">Led development of core platform features and mentored junior engineers.</p></div>' +
                   '<div><div style="font-weight:700;">Developer</div><div style="font-size:.82rem;opacity:.5;">2022 - 2024</div><p style="font-size:.9rem;opacity:.75;margin-top:.35rem;">Built and maintained client-facing web applications.</p></div>' +
                   '<div><div style="font-weight:700;">Junior Developer</div><div style="font-size:.82rem;opacity:.5;">2020 - 2022</div><p style="font-size:.9rem;opacity:.75;margin-top:.35rem;">Contributed to frontend development and bug fixes.</p></div>' +
                   '</div></div></section>';
        }

        // ==== Skills / tools ====
        if (n.indexOf('skill') !== -1 || n.indexOf('tool') !== -1) {
            if (seen.skills) return '';
            seen.skills = true;
            return '<section class="lb-section"><div class="lb-container" style="max-width:600px;">' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<h2 class="lb-section__title">Skills</h2>' +
                   '<div style="display:flex;flex-direction:column;gap:1rem;margin-top:1.5rem;">' +
                   '<div><div style="font-size:.82rem;font-weight:600;margin-bottom:.3rem;">JavaScript</div><div style="height:8px;background:rgba(0,0,0,.08);"><div style="height:100%;width:92%;background:currentColor;opacity:.35;"></div></div></div>' +
                   '<div><div style="font-size:.82rem;font-weight:600;margin-bottom:.3rem;">React / Vue</div><div style="height:8px;background:rgba(0,0,0,.08);"><div style="height:100%;width:85%;background:currentColor;opacity:.35;"></div></div></div>' +
                   '<div><div style="font-size:.82rem;font-weight:600;margin-bottom:.3rem;">Node.js</div><div style="height:8px;background:rgba(0,0,0,.08);"><div style="height:100%;width:78%;background:currentColor;opacity:.35;"></div></div></div>' +
                   '<div><div style="font-size:.82rem;font-weight:600;margin-bottom:.3rem;">Design</div><div style="height:8px;background:rgba(0,0,0,.08);"><div style="height:100%;width:70%;background:currentColor;opacity:.35;"></div></div></div>' +
                   '</div></div></section>';
        }

        // ==== Project grid / portfolio ====
        if (n.indexOf('project') !== -1 || n.indexOf('portfolio') !== -1 || n.indexOf('work') !== -1 ||
            n.indexOf('category') !== -1 || n.indexOf('grid') !== -1) {
            if (seen.projects) return '';
            seen.projects = true;
            var mgH = lbSection('media', sectionMap, nm);
            if (mgH) return mgH;
            return '<section class="lb-section"><div class="lb-container">' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<h2 class="lb-section__title">Recent Work</h2>' +
                   '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1.25rem;margin-top:1.5rem;">' +
                   '<div class="lb-card"><div class="lb-ti__placeholder" style="aspect-ratio:16/10;margin-bottom:1rem;">Project</div><div class="lb-feature__title">Brand Identity</div><div style="font-size:.8rem;opacity:.5;margin-top:.25rem;">Design</div></div>' +
                   '<div class="lb-card"><div class="lb-ti__placeholder" style="aspect-ratio:16/10;margin-bottom:1rem;">Project</div><div class="lb-feature__title">Web Application</div><div style="font-size:.8rem;opacity:.5;margin-top:.25rem;">Development</div></div>' +
                   '<div class="lb-card"><div class="lb-ti__placeholder" style="aspect-ratio:16/10;margin-bottom:1rem;">Project</div><div class="lb-feature__title">Product Design</div><div style="font-size:.8rem;opacity:.5;margin-top:.25rem;">UX / UI</div></div>' +
                   '</div></div></section>';
        }

        // ==== Breaking / ticker ====
        if (n.indexOf('breaking') !== -1 || n.indexOf('ticker') !== -1) {
            if (seen.ticker) return '';
            seen.ticker = true;
            return '<section class="lb-section" style="padding:.75rem 0;"><div class="lb-container" style="display:flex;align-items:center;gap:1.25rem;overflow:hidden;">' +
                   '<span style="font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;white-space:nowrap;opacity:.8;">' + nm + '</span>' +
                   '<span style="font-size:.82rem;white-space:nowrap;opacity:.65;">Latest updates and news from across the platform</span>' +
                   '</div></section>';
        }

        // ==== Brand mark ====
        if (n.indexOf('brand') !== -1 && n.indexOf('panel') === -1) {
            if (seen.brandmark) return '';
            seen.brandmark = true;
            return '<div style="text-align:center;padding:2rem 0;">' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<span style="font-size:1.25rem;font-weight:800;letter-spacing:.06em;">' + brand + '</span></div>';
        }

        // ==== Progress bar / step indicator ====
        if (n.indexOf('progress') !== -1 || n.indexOf('step') !== -1) {
            if (seen.progress) return '';
            seen.progress = true;
            return '<section class="lb-section" style="padding:1.25rem 0;"><div class="lb-container">' +
                   '<p class="lb-section__label">' + nm + '</p>' +
                   '<div style="display:flex;align-items:center;justify-content:center;gap:.5rem;margin-top:.75rem;">' +
                   '<div style="width:2rem;height:2rem;display:flex;align-items:center;justify-content:center;font-size:.75rem;font-weight:700;background:currentColor;color:var(--bg,#fff);opacity:.9;">1</div>' +
                   '<div style="flex:1;max-width:60px;height:2px;background:currentColor;opacity:.3;"></div>' +
                   '<div style="width:2rem;height:2rem;display:flex;align-items:center;justify-content:center;font-size:.75rem;font-weight:700;background:currentColor;color:var(--bg,#fff);opacity:.9;">2</div>' +
                   '<div style="flex:1;max-width:60px;height:2px;background:currentColor;opacity:.3;"></div>' +
                   '<div style="width:2rem;height:2rem;display:flex;align-items:center;justify-content:center;font-size:.75rem;font-weight:700;background:currentColor;color:var(--bg,#fff);opacity:.15;">3</div>' +
                   '<div style="flex:1;max-width:60px;height:2px;background:currentColor;opacity:.1;"></div>' +
                   '<div style="width:2rem;height:2rem;display:flex;align-items:center;justify-content:center;font-size:.75rem;font-weight:700;background:currentColor;color:var(--bg,#fff);opacity:.15;">4</div>' +
                   '</div></div></section>';
        }

        // ==== Structural / chrome zones -- labelled placeholder bars ====
        // These represent dashboard panels, sidebars, charts, tables, etc.
        // that have no landing-page equivalent, but should still appear in
        // the preview so every wireframe zone is accounted for.
        if (n.indexOf('sidebar') !== -1 || n.indexOf('filter') !== -1 ||
            n.indexOf('panel') !== -1  || n.indexOf('chart') !== -1 ||
            n.indexOf('table') !== -1  || n.indexOf('widget') !== -1 ||
            n.indexOf('left nav') !== -1 || n.indexOf('main content') !== -1 ||
            n.indexOf('dark background') !== -1 || n.indexOf('full-screen container') !== -1 ||
            n.indexOf('background') !== -1 || n.indexOf('button') !== -1 ||
            n.indexOf('submit') !== -1) {
            var structKey = 'struct_' + n.replace(/\s+/g, '_');
            if (seen[structKey]) return '';
            seen[structKey] = true;
            return '<section class="lb-section" style="padding:1rem 0;"><div class="lb-container">' +
                   '<div class="lb-card" style="padding:.85rem 1.25rem;opacity:.55;display:flex;align-items:center;gap:1rem;">' +
                   '<span style="font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;">' + nm + '</span>' +
                   '<span style="font-size:.75rem;opacity:.6;"> -- ' + zone.desc + '</span>' +
                   '</div></div></section>';
        }

        // ==== Generic fallback -- labelled with zone name & description ====
        var genericKey = 'generic_' + n.replace(/\s+/g, '_');
        if (seen[genericKey]) return '';
        seen[genericKey] = true;
        return '<section class="lb-section"><div class="lb-container">' +
               '<p class="lb-section__label">' + nm + '</p>' +
               '<h2 class="lb-section__title">' + nm + '</h2>' +
               '<p class="lb-section__sub">' + zone.desc + '</p>' +
               '</div></section>';
    }

    // -------------------------------------------------------------------------
    // Vertex starter CSS — used as fallback when no LB design has been saved.
    // Matches the Landing Builder's default state: Inter font, neumorphic preset,
    // primary #231c54, secondary #6a60a9, base size 17px, spacing 112px, radius 0.
    // -------------------------------------------------------------------------
    var VERTEX_STARTER = (function () {
        var p  = '#231c54';   // primary
        var s2 = '#6a60a9';   // secondary  (hsl 248,30%,52%)
        var ac = '#070514';   // accent     (hsl 248,60%,5%)
        var tx = '#0e0c1d';   // text       (hsl 248,40%,8%)
        var bg = '#ededf1';   // body/card  (lighten primary 0.92)
        var ib = '#e5e4ea';   // input bg   (lighten primary 0.88)
        var sp = 112;
        var css = [
            '*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }',
            'html { font-size: 17px; }',
            "body { font-family: 'Inter', sans-serif; color: " + tx + '; background: ' + bg + '; line-height: 1.65; }',
            'a { color: ' + p + '; text-decoration: none; }',
            'h1, h2, h3, h4 { font-weight: 800; line-height: 1.2; }',
            '.lb-container { max-width: 1200px; margin: 0 auto; padding: 0 1.5rem; }',
            '.lb-section { padding: ' + sp + 'px 0; }',
            // Nav
            '.lb-nav { background: ' + bg + '; position: sticky; top: 0; z-index: 100; box-shadow: 6px 0 12px rgba(0,0,0,0.08); }',
            '.lb-nav__inner { max-width: 1200px; margin: 0 auto; padding: 0 1.5rem; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 0; }',
            '.lb-nav__brand { font-weight: 800; color: ' + tx + '; font-size: 1.05rem; letter-spacing: 0.04em; text-decoration: none; flex-shrink: 0; padding: 1rem 0; }',
            '.lb-nav__links { display: flex; align-items: center; gap: 0.25rem; }',
            '.lb-nav a:not(.lb-nav__brand):not(.lb-hamburger), .lb-nav button:not(.lb-hamburger) { color: ' + tx + '; font-size: 0.9rem; font-weight: 500; padding: 0.4rem 0.8rem; background: transparent; border: none; cursor: pointer; font-family: inherit; text-decoration: none; border-radius: 0; }',
            '.lb-nav a:not(.lb-nav__brand):not(.lb-hamburger):hover, .lb-nav button:not(.lb-hamburger):hover { background: rgba(0,0,0,0.05); }',
            '.lb-sidebar-topbar { display: none; }',
            '.lb-hamburger { display: none; flex-direction: column; justify-content: center; align-items: center; width: 40px; height: 40px; background: transparent; border: none; cursor: pointer; padding: 8px; gap: 5px; flex-shrink: 0; }',
            '.lb-hamburger span { display: block; width: 22px; height: 2px; background: ' + tx + '; }',
            // Buttons
            '.lb-btn { display: inline-block; padding: 0.65rem 1.75rem; font-size: 0.95rem; font-weight: 600; font-family: inherit; cursor: pointer; border: 2px solid transparent; border-radius: 0; background: ' + s2 + '; color: #ffffff; transition: opacity 0.15s; letter-spacing: 0.02em; }',
            '.lb-btn:hover { opacity: 0.85; }',
            '.lb-btn--outline { background: transparent; border-color: ' + p + '; color: ' + p + '; }',
            '.lb-btn--cta { background: #ffffff; color: ' + p + '; border-color: #ffffff; padding: 0.9rem 2.75rem; font-size: 1.05rem; font-weight: 700; letter-spacing: 0.04em; box-shadow: 0 4px 24px rgba(0,0,0,0.22); }',
            '.lb-btn--cta:hover { background: ' + s2 + '; color: #ffffff; border-color: ' + s2 + '; opacity: 1; }',
            '.lb-cta-btn-wrap { margin-top: 0.5rem; }',
            '.lb-cta-sub { font-size: 0.8rem; opacity: 0.65; margin-top: 1.25rem; }',
            // Cards
            '.lb-card { background: ' + bg + '; border: none; border-radius: 0; box-shadow: 6px 6px 12px rgba(0,0,0,0.12), -6px -6px 12px rgba(255,255,255,0.7); padding: 1.75rem; }',
            // Inputs
            'input, textarea, select { background: ' + ib + '; border: none; border-radius: 0; padding: 0.6rem 0.85rem; font-family: inherit; font-size: 0.95rem; color: ' + tx + '; width: 100%; outline: none; }',
            // Hero
            '.lb-hero { position: relative; overflow: hidden; }',
            '.lb-hero--center { text-align: center; }',
            '.lb-hero--center .lb-hero__inner { display: flex; flex-direction: column; align-items: center; gap: 1.5rem; }',
            '.lb-hero--left .lb-hero__inner, .lb-hero--right .lb-hero__inner { display: grid; grid-template-columns: 1fr 1fr; align-items: center; gap: 3rem; }',
            '.lb-hero--right .lb-hero__content { order: 2; }',
            '.lb-hero--right .lb-hero__image { order: 1; }',
            '.lb-hero__tag { font-size: 0.75rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.1em; color: ' + s2 + '; margin-bottom: 0.5rem; }',
            '.lb-hero__h1 { font-size: clamp(2rem, 5vw, 3.5rem); margin-bottom: 1rem; }',
            '.lb-hero__sub { font-size: 1.1rem; opacity: 0.8; margin-bottom: 1.5rem; max-width: 560px; }',
            '.lb-hero__placeholder { width: 100%; aspect-ratio: 16/9; background: #d8d6e8; border-radius: 0; display: flex; align-items: center; justify-content: center; font-size: 0.85rem; opacity: 0.6; }',
            // Features
            '.lb-features__grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1.5rem; margin-top: 2.5rem; }',
            '.lb-feature__icon { font-size: 1.75rem; margin-bottom: 0.75rem; }',
            '.lb-feature__title { font-size: 1rem; font-weight: 700; margin-bottom: 0.4rem; }',
            '.lb-feature__desc { font-size: 0.875rem; opacity: 0.75; }',
            // Text + Image
            '.lb-ti__grid { display: grid; grid-template-columns: 1fr 1fr; gap: 3rem; align-items: center; }',
            '.lb-ti__grid--left { direction: rtl; }',
            '.lb-ti__grid--left > * { direction: ltr; }',
            '.lb-ti__placeholder { width: 100%; aspect-ratio: 4/3; background: #d8d6e8; border-radius: 0; display: flex; align-items: center; justify-content: center; font-size: 0.85rem; opacity: 0.6; }',
            '.lb-ti__headline { font-size: 1.75rem; margin-bottom: 1rem; }',
            '.lb-ti__body { font-size: 0.95rem; opacity: 0.8; line-height: 1.75; }',
            // CTA
            '.lb-cta { background: ' + p + '; color: #ffffff; text-align: center; }',
            '.lb-cta h2 { font-size: clamp(1.6rem, 3.5vw, 2.6rem); margin-bottom: 1rem; max-width: 700px; margin-left: auto; margin-right: auto; }',
            '.lb-cta p { opacity: 0.88; margin-bottom: 2.5rem; max-width: 600px; margin-left: auto; margin-right: auto; }',
            '.lb-cta .lb-section__label { color: rgba(255,255,255,0.7); }',
            // Pricing
            '.lb-pricing__grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 1.5rem; margin-top: 2.5rem; }',
            '.lb-plan { border: none; border-radius: 0; background: ' + bg + '; padding: 2rem 1.5rem; box-shadow: 6px 6px 12px rgba(0,0,0,0.12), -6px -6px 12px rgba(255,255,255,0.7); display: flex; flex-direction: column; gap: 1rem; }',
            '.lb-plan--highlight { box-shadow: 0 0 0 2px ' + s2 + ', 6px 6px 12px rgba(0,0,0,0.12), -6px -6px 12px rgba(255,255,255,0.7); }',
            '.lb-plan__name { font-size: 0.8rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: ' + s2 + '; }',
            '.lb-plan__price { font-size: 2.5rem; font-weight: 800; line-height: 1; }',
            '.lb-plan__period { font-size: 0.85rem; opacity: 0.6; }',
            '.lb-plan__features { list-style: none; display: flex; flex-direction: column; gap: 0.5rem; font-size: 0.875rem; flex: 1; }',
            '.lb-plan__features li::before { content: "\\2713  "; color: ' + s2 + '; font-weight: 700; }',
            // FAQ
            '.lb-faq__list { margin-top: 2rem; display: flex; flex-direction: column; gap: 1rem; }',
            '.lb-faq__item { border: none; border-radius: 0; background: ' + bg + '; padding: 1.25rem 1.5rem; box-shadow: 6px 6px 12px rgba(0,0,0,0.12), -6px -6px 12px rgba(255,255,255,0.7); }',
            '.lb-faq__q { font-weight: 700; margin-bottom: 0.5rem; }',
            '.lb-faq__a { font-size: 0.9rem; opacity: 0.75; }',
            // Testimonials
            '.lb-test__grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1.5rem; margin-top: 2.5rem; }',
            '.lb-test__card { border: none; border-radius: 0; background: ' + bg + '; padding: 1.75rem; box-shadow: 6px 6px 12px rgba(0,0,0,0.12), -6px -6px 12px rgba(255,255,255,0.7); color: ' + tx + '; }',
            '.lb-test__quote { font-size: 1rem; font-style: italic; margin-bottom: 1rem; opacity: 0.85; }',
            '.lb-test__quote::before { content: "\\201C"; }',
            '.lb-test__quote::after  { content: "\\201D"; }',
            '.lb-test__name { font-weight: 700; font-size: 0.875rem; }',
            '.lb-test__title { font-size: 0.8rem; opacity: 0.6; }',
            // Contact
            '.lb-contact__grid { display: grid; grid-template-columns: 1fr 1fr; gap: 3rem; align-items: flex-start; margin-top: 2rem; }',
            '.lb-contact__form { display: flex; flex-direction: column; gap: 0.85rem; }',
            '.lb-contact__label { font-size: 0.8rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.3rem; display: block; }',
            // Footer
            '.lb-footer { background: ' + ac + '; color: rgba(255,255,255,0.8); padding: ' + Math.round(sp * 0.6) + 'px 0; }',
            '.lb-footer__grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 2rem; margin-bottom: 2rem; }',
            '.lb-footer__col-title { font-size: 0.75rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: rgba(255,255,255,0.5); margin-bottom: 0.75rem; }',
            '.lb-footer__col-links { list-style: none; display: flex; flex-direction: column; gap: 0.5rem; }',
            '.lb-footer__col-links a { color: rgba(255,255,255,0.75); font-size: 0.875rem; }',
            '.lb-footer__bottom { border-top: 1px solid rgba(255,255,255,0.12); padding-top: 1.5rem; font-size: 0.8rem; opacity: 0.5; text-align: center; }',
            // Section label / title
            '.lb-section__label { font-size: 0.75rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.1em; color: ' + s2 + '; margin-bottom: 0.5rem; }',
            '.lb-section__title { font-size: clamp(1.5rem, 3vw, 2.25rem); margin-bottom: 1rem; }',
            '.lb-section__sub { font-size: 1rem; opacity: 0.7; max-width: 560px; margin-bottom: 1rem; }',
            // Media
            '.lb-media-grid { display: grid; gap: 1rem; }',
            '.lb-media-grid__item { margin: 0; overflow: hidden; }',
            '.lb-media-grid__item img { width: 100%; height: 260px; object-fit: cover; display: block; }',
            // Responsive
            '@media (max-width: 768px) {',
            '  .lb-hamburger { display: flex; }',
            '  .lb-nav__links { display: none; width: 100%; flex-direction: column; align-items: stretch; background: ' + bg + '; }',
            '  .lb-hero--left .lb-hero__inner, .lb-hero--right .lb-hero__inner { grid-template-columns: 1fr; }',
            '  .lb-ti__grid { grid-template-columns: 1fr; }',
            '  .lb-contact__grid { grid-template-columns: 1fr; }',
            '}',
        ].join('\n');
        return {
            css:         css,
            fontLink:    'https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap',
            navBrand:    'Vertex',
            navLinks:    [
                { label: 'Features',  href: '#' },
                { label: 'Solutions', href: '#' },
                { label: 'Pricing',   href: '#' },
                { label: 'Contact',   href: '#' },
            ],
            stylePreset: 'neumorphic',
        };
    }());

    function buildStyledPreview(layout) {
        var raw = null;
        try { raw = JSON.parse(localStorage.getItem(LB_LIVE_KEY) || 'null'); } catch(e) {}

        // Fall back to the Vertex starter design when nothing has been saved yet
        if (!raw || !raw.css) {
            raw = VERTEX_STARTER;
        }

        var brand = raw.navBrand || 'Vertex';
        var links = (raw.navLinks || VERTEX_STARTER.navLinks)
            .map(function(l) { return '<a href="' + l.href + '">' + l.label + '</a>'; })
            .join('');

        // Build a sectionMap that includes the LB's rendered section HTML
        // and the nav HTML so buildZoneHTML can reuse actual LB content.
        var sMap = raw.sectionMap || null;
        if (sMap && raw.navHTML) {
            sMap._navHTML = raw.navHTML;
        }

        var seen = {};
        var sections = layout.zones.map(function(z) {
            return buildZoneHTML(z, brand, links, seen, sMap);
        }).join('\n');

        var fontTag = raw.fontLink ? '<link rel="stylesheet" href="' + raw.fontLink + '">' : '';

        var html = [
            '<!DOCTYPE html>',
            '<html lang="en">',
            '<head>',
            '<meta charset="UTF-8">',
            '<meta name="viewport" content="width=device-width,initial-scale=1">',
            '<base href="' + window.location.origin + '/">',
            '<title>' + layout.name + ' -- Styled Preview</title>',
            fontTag,
            '<style>',
            raw.css,
            '</style>',
            '</head>',
            '<body>',
            sections,
            '</body>',
            '</html>',
        ].join('\n');

        var blob = new Blob([html], { type: 'text/html' });
        var url  = URL.createObjectURL(blob);
        var win  = window.open(url, '_blank');
        if (win) {
            win.addEventListener('load', function() { URL.revokeObjectURL(url); });
        }
    }

    // -- Zone descriptions are shown as text-only in the breakdown.
    //    No colored chips to avoid mismatches with SVG rendering.

    function buildCard(layout) {
        var card = document.createElement('div');
        card.className = 'pl-card';
        card.dataset.id  = layout.id;
        card.dataset.cat = layout.tags.join(' ');

        var preview = document.createElement('div');
        preview.className = 'pl-card__preview';
        preview.innerHTML = layout.svg; // security-allow: layout is a row of the LAYOUTS constant in this file and svg is its field, so the markup is authored here and arrived from nowhere

        var info = document.createElement('div');
        info.className = 'pl-card__info';

        var name = document.createElement('div');
        name.className = 'pl-card__name';
        name.textContent = layout.name;

        var desc = document.createElement('div');
        desc.className = 'pl-card__desc';
        desc.textContent = layout.desc;

        var tags = document.createElement('div');
        tags.className = 'pl-card__tags';
        layout.tags.forEach(function(t) {
            var chip = document.createElement('span');
            chip.className = 'pl-tag';
            chip.textContent = t;
            tags.appendChild(chip);
        });

        info.appendChild(name);
        info.appendChild(desc);
        info.appendChild(tags);
        card.appendChild(preview);
        card.appendChild(info);

        card.addEventListener('click', function() {
            var idx = filteredIds.indexOf(layout.id);
            if (idx === -1) return;
            openLightbox(idx);
        });

        return card;
    }

    function renderGrid(cat) {
        grid.innerHTML = '';
        filteredIds = [];
        LAYOUTS.forEach(function(layout) {
            var show = cat === 'all' || layout.tags.indexOf(cat) !== -1;
            if (show) {
                filteredIds.push(layout.id);
                grid.appendChild(buildCard(layout));
            }
        });
    }

    function openLightbox(filteredIdx) {
        activeIdx = filteredIdx;
        populateLightbox();
        lightbox.classList.add('open');
        document.body.style.overflow = 'hidden';
    }

    function closeLightbox() {
        lightbox.classList.remove('open');
        document.body.style.overflow = '';
    }

    function populateLightbox() {
        var id = filteredIds[activeIdx];
        var layout = LAYOUTS.filter(function(l){ return l.id === id; })[0];
        if (!layout) return;

        lbTitle.textContent = layout.name;
        lbMeta.textContent  = layout.tags.map(function(t){ return t.charAt(0).toUpperCase() + t.slice(1); }).join('  /  ');
        lbCounter.textContent = (activeIdx + 1) + ' / ' + filteredIds.length;

        // Large SVG
        lbBody.innerHTML = layout.svg; // security-allow: the same authored field as the card preview, read from the LAYOUTS constant in this file

        // Zone breakdown
        var breakdown = document.createElement('div');
        breakdown.className = 'pl-breakdown';
        layout.zones.forEach(function(zone) {
            var row = document.createElement('div');
            row.className = 'pl-breakdown__row';

            var content = document.createElement('div');
            content.className = 'pl-breakdown__content';

            var zoneName = document.createElement('div');
            zoneName.className = 'pl-breakdown__zone-name';
            zoneName.textContent = zone.name;

            var zoneDesc = document.createElement('div');
            zoneDesc.className = 'pl-breakdown__zone-desc';
            zoneDesc.textContent = zone.desc;

            content.appendChild(zoneName);
            content.appendChild(zoneDesc);
            row.appendChild(content);
            breakdown.appendChild(row);
        });
        lbBody.appendChild(breakdown);

        // Tags
        lbTags.innerHTML = '';
        layout.tags.forEach(function(t) {
            var chip = document.createElement('span');
            chip.className = 'pl-tag';
            chip.textContent = t;
            lbTags.appendChild(chip);
        });

        // Add to Quote state
        var quoteList = getQuoteList();
        if (quoteList.indexOf(layout.id) !== -1) {
            addToQuoteBtn.textContent = 'Added to Quote';
            addToQuoteBtn.classList.add('added');
        } else {
            addToQuoteBtn.textContent = 'Add to Quote';
            addToQuoteBtn.classList.remove('added');
        }

        // Nav arrows
        prevBtn.disabled = activeIdx === 0;
        nextBtn.disabled = activeIdx === filteredIds.length - 1;
        prevBtn.style.opacity = prevBtn.disabled ? '0.35' : '1';
        nextBtn.style.opacity = nextBtn.disabled ? '0.35' : '1';
    }

    // -- Expose LAYOUTS globally before any UI code --
    window.XCM_LAYOUTS = LAYOUTS;

    // -- Only attach DOM listeners when the full UI exists --
    if (hasUI) {
        filterBtns.forEach(function(btn) {
            btn.addEventListener('click', function() {
                filterBtns.forEach(function(b){ b.classList.remove('active'); });
                btn.classList.add('active');
                renderGrid(btn.dataset.cat);
            });
        });

        closeBtn.addEventListener('click', closeLightbox);
        backdrop.addEventListener('click', closeLightbox);

        stylePreviewBtn.addEventListener('click', function() {
            var id = filteredIds[activeIdx];
            var layout = LAYOUTS.filter(function(l) { return l.id === id; })[0];
            if (layout) buildStyledPreview(layout);
        });

        addToQuoteBtn.addEventListener('click', function() {
            var id = filteredIds[activeIdx];
            if (!id) return;
            var list = getQuoteList();
            if (list.indexOf(id) === -1) {
                list.push(id);
                saveQuoteList(list);
                addToQuoteBtn.textContent = 'Added to Quote';
                addToQuoteBtn.classList.add('added');
            }
        });

        prevBtn.addEventListener('click', function() {
            if (activeIdx > 0) { activeIdx--; populateLightbox(); }
        });
        nextBtn.addEventListener('click', function() {
            if (activeIdx < filteredIds.length - 1) { activeIdx++; populateLightbox(); }
        });

        document.addEventListener('keydown', function(e) {
            if (!lightbox.classList.contains('open')) return;
            if (e.key === 'Escape') { closeLightbox(); }
            if (e.key === 'ArrowLeft'  && activeIdx > 0) { activeIdx--; populateLightbox(); }
            if (e.key === 'ArrowRight' && activeIdx < filteredIds.length - 1) { activeIdx++; populateLightbox(); }
        });

        // -- Initial render --
        renderGrid('all');
    }

}());
