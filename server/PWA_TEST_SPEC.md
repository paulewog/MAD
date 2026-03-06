# Test Specification: PWA Support for MAD Web App

## 1. Behaviors That MUST Be Verified

### 1.1 Web App Manifest

**Manifest file exists and is served correctly**
- A GET request to `/static/manifest.json` returns HTTP 200
- The response `Content-Type` header is `application/json` (or contains `json`)
- The response body is valid JSON

**Manifest contains required PWA fields**
- `name` is a non-empty string
- `short_name` is a non-empty string
- `start_url` is `"/"`
- `display` is `"standalone"`
- `background_color` is `"#0d1117"`
- `theme_color` is `"#0d1117"`
- `scope` is `"/"`
- `icons` is a non-empty array

**Manifest icon entries are well-formed**
- The `icons` array contains at least one entry with `sizes` of `"192x192"`
- The `icons` array contains at least one entry with `sizes` of `"512x512"`
- Each icon entry has a non-empty `src` field
- Each icon entry has a non-empty `type` field (e.g. `"image/png"` or `"image/svg+xml"`)
- Every icon `src` path resolves to an HTTP 200 when fetched from the server

### 1.2 App Icons

**Icon files exist and are served**
- A GET request to `/static/icon-192.png` returns HTTP 200
- A GET request to `/static/icon-512.png` returns HTTP 200
- Both responses have a `Content-Type` starting with `image/`

**Icon dimensions (if verifiable server-side)**
- The 192x192 PNG file is a valid PNG (starts with the PNG magic bytes `\x89PNG`)
- The 512x512 PNG file is a valid PNG (starts with the PNG magic bytes `\x89PNG`)

### 1.3 Service Worker

**Service worker file is served**
- A GET request to the service worker URL returns HTTP 200
- The response `Content-Type` contains `javascript`

**Service worker scope is correct**
- Either the service worker is served from the root path (`/sw.js`), OR
- The response includes a `Service-Worker-Allowed` header with value `"/"` when served from `/static/sw.js`
- This is critical: a service worker served from `/static/sw.js` without the `Service-Worker-Allowed: /` header can only control pages under `/static/`

**Service worker content is functional**
- The file contains an `install` event listener
- The file contains a `fetch` event listener
- The file does not contain syntax errors (it is parseable JavaScript)

### 1.4 HTML Template Updates — `index.html`

**Manifest link is present**
- The rendered HTML of `GET /` contains `<link rel="manifest" href="/static/manifest.json">`

**Theme color meta tag is present**
- The rendered HTML contains `<meta name="theme-color" content="#0d1117">`

**Apple mobile web app meta tags are present**
- The rendered HTML contains `<meta name="apple-mobile-web-app-capable" content="yes">`
- The rendered HTML contains `<meta name="apple-mobile-web-app-status-bar-style"` with a value (e.g. `"black-translucent"`)

**Apple touch icon is present**
- The rendered HTML contains `<link rel="apple-touch-icon"` with an `href` pointing to the 192px icon

**Service worker registration script is present**
- The rendered HTML contains a `<script>` block that includes `navigator.serviceWorker.register`
- The registration path matches the actual service worker URL (either `/sw.js` or `/static/sw.js`)

### 1.5 HTML Template Updates — `client.html`

All the same checks as 1.4 above, but against a rendered client page (`GET /clients/{id}`). Specifically:
- Manifest link is present in `<head>`
- Theme color meta tag is present
- Apple mobile web app meta tags are present
- Apple touch icon is present
- Service worker registration script is present

### 1.6 Go Embed / Static File Serving

**New static files are picked up by the embed directive**
- The `staticFS` embed includes `manifest.json`, `sw.js`, `icon-192.png`, and `icon-512.png`
- Verified by: all four files return HTTP 200 via the running test server

**If a root `/sw.js` route was added**
- `GET /sw.js` returns the same content as the embedded service worker file
- The route does not break existing routes (`/`, `/clients/{id}`, `/api/clients`, `/ws`, `/favicon.svg`)

### 1.7 Existing Functionality Is Not Broken (Regression)

- `GET /` still returns HTTP 200 with HTML content
- `GET /favicon.svg` still returns HTTP 200 with `image/svg+xml`
- `GET /api/clients` still returns HTTP 200 with JSON (with valid auth)
- WebSocket upgrade at `/ws` still succeeds
- `GET /clients/{id}` still returns HTML for a known client
- Unknown paths still return HTTP 404

---

## 2. Edge Cases

### 2.1 Manifest Parsing

- If `manifest.json` is somehow corrupted or empty, the server should still serve it (it's a static file) — the browser handles parse errors gracefully. Verify the server does not crash.
- Manifest with extra/unknown fields should still be valid JSON (forward-compatible).

### 2.2 Service Worker Scope Boundary

- If the service worker is at `/static/sw.js` without a `Service-Worker-Allowed` header, confirm that this is detected as a problem (the test should fail, flagging the missing header).
- If a `Service-Worker-Allowed: /` header is set, it must be set only on the service worker file response, not on all static files.

### 2.3 Cache Behavior

- The service worker's `install` handler should open a cache and pre-cache the app shell assets. If any asset URL in the cache list is wrong (404), the install event should still complete (or handle the error), not leave the SW in a broken `installing` state.

### 2.4 Content-Type Headers

- `manifest.json` must be served as `application/json`, NOT `text/plain` or `application/octet-stream`. Some browsers reject manifests with wrong MIME types.
- `sw.js` must be served as `application/javascript` or `text/javascript`. Browsers reject service workers with MIME type mismatches.

### 2.5 Concurrent Static File Requests

- Multiple simultaneous requests to manifest, icons, and service worker should all succeed. No race conditions in Go's `embed.FS` serving (this is inherently safe, but worth confirming under test).

### 2.6 Auth Interaction

- Static PWA assets (`manifest.json`, `sw.js`, icons) must be accessible WITHOUT authentication. Service workers and manifests are fetched by the browser automatically — they cannot pass `?key=` params.
- If the dashboard requires a `?key=` param, the manifest/SW/icon requests must still succeed without it.

---

## 3. What Constitutes Failure

### Hard Failures (test must fail)

- Any of the new static files (`manifest.json`, `sw.js`, `icon-192.png`, `icon-512.png`) returns non-200 status
- `manifest.json` is not valid JSON
- `manifest.json` is missing any of: `name`, `short_name`, `start_url`, `display`, `icons`
- `display` is not `"standalone"`
- `start_url` is not `"/"`
- `icons` array does not include both 192x192 and 512x512 sizes
- An icon `src` referenced in the manifest 404s when fetched
- Service worker file is empty or returns wrong content type
- Service worker is served from `/static/sw.js` without `Service-Worker-Allowed: /` header (and no `/sw.js` root route exists)
- Neither `index.html` nor `client.html` contains the manifest `<link>` tag
- Neither template contains the service worker registration script
- Any previously-passing test in `server_test.go` now fails (regression)

### Soft Failures (warnings, not blockers)

- Missing `apple-mobile-web-app-capable` meta tag (only affects iOS)
- Missing `apple-touch-icon` link (only affects iOS home screen icon)
- Missing `description` field in manifest (not required for installability, but recommended)

---

## 4. Out of Scope

The following are explicitly NOT part of this test spec:

- **Visual/UI assertions**: No testing of how the install banner looks, whether icons render correctly visually, or CSS styling of any kind
- **Actual browser install flow**: Testing the end-to-end "Add to Home Screen" prompt requires a real browser; this is manual QA territory
- **Service worker caching correctness**: Whether the cache-first/network-first strategy actually works offline is a browser-level concern. We only verify the SW file exists, is served correctly, and contains the expected event listeners
- **Performance**: No lighthouse scores, load time benchmarks, or cache hit rate measurements
- **Icon image quality**: We verify PNGs exist and are valid, not that they look good or have correct dimensions at the pixel level (would require image decoding libraries)
- **HTTPS requirement**: PWA install requires HTTPS in production. Test servers use HTTP, which is fine for functional verification. HTTPS is an infrastructure/deployment concern
- **Push notifications**: Not part of this feature
- **Background sync**: Not part of this feature
- **Offline functionality**: The plan calls for a minimal service worker with basic caching; full offline support is not a goal
- **Cross-browser compatibility**: Tests run against Go's httptest server, not against Chrome/Safari/Firefox rendering engines
- **Mobile device testing**: Physical device or emulator testing is manual QA
