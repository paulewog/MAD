package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// 1.1 Web App Manifest
// ---------------------------------------------------------------------------

func TestManifestServedCorrectly(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))
	resp, err := http.Get(ts.URL + "/static/manifest.json")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("expected 200, got %d", resp.StatusCode)
	}
	ct := resp.Header.Get("Content-Type")
	if !strings.Contains(ct, "json") {
		t.Fatalf("expected json content-type, got %q", ct)
	}

	body, _ := io.ReadAll(resp.Body)
	var m map[string]interface{}
	if err := json.Unmarshal(body, &m); err != nil {
		t.Fatalf("manifest is not valid JSON: %v", err)
	}
}

func TestManifestRequiredFields(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))
	resp, err := http.Get(ts.URL + "/static/manifest.json")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	var m map[string]interface{}
	json.Unmarshal(body, &m)

	checks := map[string]string{
		"name":             "",
		"short_name":       "",
		"start_url":        "/",
		"display":          "standalone",
		"background_color": "#0d1117",
		"theme_color":      "#0d1117",
		"scope":            "/",
	}
	for field, expected := range checks {
		v, ok := m[field]
		if !ok {
			t.Errorf("manifest missing field %q", field)
			continue
		}
		s, _ := v.(string)
		if s == "" {
			t.Errorf("manifest field %q is empty", field)
		}
		if expected != "" && s != expected {
			t.Errorf("manifest %q = %q, want %q", field, s, expected)
		}
	}

	icons, ok := m["icons"].([]interface{})
	if !ok || len(icons) == 0 {
		t.Fatal("manifest icons is empty or missing")
	}
}

func TestManifestIconEntries(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))
	resp, _ := http.Get(ts.URL + "/static/manifest.json")
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	var m struct {
		Icons []struct {
			Src   string `json:"src"`
			Sizes string `json:"sizes"`
			Type  string `json:"type"`
		} `json:"icons"`
	}
	json.Unmarshal(body, &m)

	has192, has512 := false, false
	for _, icon := range m.Icons {
		if icon.Src == "" {
			t.Error("icon has empty src")
		}
		if icon.Type == "" {
			t.Error("icon has empty type")
		}
		if icon.Sizes == "192x192" {
			has192 = true
		}
		if icon.Sizes == "512x512" {
			has512 = true
		}
	}
	if !has192 {
		t.Error("missing 192x192 icon")
	}
	if !has512 {
		t.Error("missing 512x512 icon")
	}
}

func TestManifestIconSrcsResolve(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))
	resp, _ := http.Get(ts.URL + "/static/manifest.json")
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	var m struct {
		Icons []struct {
			Src string `json:"src"`
		} `json:"icons"`
	}
	json.Unmarshal(body, &m)

	for _, icon := range m.Icons {
		r, err := http.Get(ts.URL + icon.Src)
		if err != nil {
			t.Errorf("failed to fetch icon %q: %v", icon.Src, err)
			continue
		}
		r.Body.Close()
		if r.StatusCode != 200 {
			t.Errorf("icon %q returned %d, want 200", icon.Src, r.StatusCode)
		}
	}
}

// ---------------------------------------------------------------------------
// 1.2 App Icons
// ---------------------------------------------------------------------------

func TestIconFilesServed(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))

	for _, path := range []string{"/static/icon-192.png", "/static/icon-512.png"} {
		resp, err := http.Get(ts.URL + path)
		if err != nil {
			t.Fatalf("%s: %v", path, err)
		}
		defer resp.Body.Close()

		if resp.StatusCode != 200 {
			t.Errorf("%s: status %d, want 200", path, resp.StatusCode)
		}
		ct := resp.Header.Get("Content-Type")
		if !strings.HasPrefix(ct, "image/") {
			t.Errorf("%s: content-type %q, want image/*", path, ct)
		}
	}
}

func TestIconFilesArePNG(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))

	for _, path := range []string{"/static/icon-192.png", "/static/icon-512.png"} {
		resp, _ := http.Get(ts.URL + path)
		defer resp.Body.Close()

		magic := make([]byte, 4)
		io.ReadFull(resp.Body, magic)
		// PNG magic: 0x89 P N G
		if magic[0] != 0x89 || magic[1] != 'P' || magic[2] != 'N' || magic[3] != 'G' {
			t.Errorf("%s: not a valid PNG (magic: %x)", path, magic)
		}
	}
}

// ---------------------------------------------------------------------------
// 1.3 Service Worker
// ---------------------------------------------------------------------------

func TestServiceWorkerServed(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))
	resp, err := http.Get(ts.URL + "/sw.js")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		t.Fatalf("GET /sw.js: status %d, want 200", resp.StatusCode)
	}
	ct := resp.Header.Get("Content-Type")
	if !strings.Contains(ct, "javascript") {
		t.Fatalf("GET /sw.js: content-type %q, want javascript", ct)
	}
}

func TestServiceWorkerScope(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))
	resp, _ := http.Get(ts.URL + "/sw.js")
	defer resp.Body.Close()

	// Served from /sw.js (root) — scope is fine. But also check the header is present.
	swa := resp.Header.Get("Service-Worker-Allowed")
	if swa != "/" {
		t.Errorf("Service-Worker-Allowed = %q, want %q", swa, "/")
	}
}

func TestServiceWorkerCacheControl(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))
	resp, _ := http.Get(ts.URL + "/sw.js")
	defer resp.Body.Close()

	cc := resp.Header.Get("Cache-Control")
	if !strings.Contains(cc, "no-cache") {
		t.Errorf("Cache-Control = %q, want no-cache", cc)
	}
}

func TestServiceWorkerContent(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))
	resp, _ := http.Get(ts.URL + "/sw.js")
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	src := string(body)

	if !strings.Contains(src, "install") {
		t.Error("SW missing install event listener")
	}
	if !strings.Contains(src, "fetch") {
		t.Error("SW missing fetch event listener")
	}
	if len(src) == 0 {
		t.Error("SW file is empty")
	}
}

// ---------------------------------------------------------------------------
// 1.4 HTML Template — index.html
// ---------------------------------------------------------------------------

func getIndexHTML(t *testing.T, ts *httptest.Server) string {
	t.Helper()
	resp, err := http.Get(ts.URL + "/")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	return string(body)
}

func TestIndexManifestLink(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))
	html := getIndexHTML(t, ts)
	if !strings.Contains(html, `<link rel="manifest" href="/static/manifest.json">`) {
		t.Error("index.html missing manifest link")
	}
}

func TestIndexThemeColor(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))
	html := getIndexHTML(t, ts)
	if !strings.Contains(html, `<meta name="theme-color" content="#0d1117">`) {
		t.Error("index.html missing theme-color meta")
	}
}

func TestIndexApplePWATags(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))
	html := getIndexHTML(t, ts)
	if !strings.Contains(html, `<meta name="apple-mobile-web-app-capable" content="yes">`) {
		t.Error("index.html missing apple-mobile-web-app-capable")
	}
	if !strings.Contains(html, `<meta name="apple-mobile-web-app-status-bar-style"`) {
		t.Error("index.html missing apple-mobile-web-app-status-bar-style")
	}
}

func TestIndexAppleTouchIcon(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))
	html := getIndexHTML(t, ts)
	if !strings.Contains(html, `<link rel="apple-touch-icon"`) {
		t.Error("index.html missing apple-touch-icon")
	}
	if !strings.Contains(html, `href="/static/icon-192.png"`) {
		t.Error("apple-touch-icon does not point to 192px icon")
	}
}

func TestIndexServiceWorkerRegistration(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))
	html := getIndexHTML(t, ts)
	if !strings.Contains(html, `navigator.serviceWorker.register`) {
		t.Error("index.html missing SW registration script")
	}
	if !strings.Contains(html, `/sw.js`) {
		t.Error("SW registration path does not match /sw.js")
	}
}

// ---------------------------------------------------------------------------
// 1.5 HTML Template — client.html
// ---------------------------------------------------------------------------

func getClientHTML(t *testing.T, ts *httptest.Server, hub *Hub, apiKey string) string {
	t.Helper()
	conn := wsConnect(t, ts, apiKey, "pwa-test-client")
	defer conn.Close()
	waitForClient(hub, "pwa-test-client", 2*time.Second)

	resp, err := http.Get(ts.URL + "/clients/pwa-test-client")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	return string(body)
}

func TestClientManifestLink(t *testing.T) {
	hub, ts := startTestServerT(t, testConfig(""))
	html := getClientHTML(t, ts, hub, "")
	if !strings.Contains(html, `<link rel="manifest" href="/static/manifest.json">`) {
		t.Error("client.html missing manifest link")
	}
}

func TestClientThemeColor(t *testing.T) {
	hub, ts := startTestServerT(t, testConfig(""))
	html := getClientHTML(t, ts, hub, "")
	if !strings.Contains(html, `<meta name="theme-color" content="#0d1117">`) {
		t.Error("client.html missing theme-color meta")
	}
}

func TestClientApplePWATags(t *testing.T) {
	hub, ts := startTestServerT(t, testConfig(""))
	html := getClientHTML(t, ts, hub, "")
	if !strings.Contains(html, `<meta name="apple-mobile-web-app-capable" content="yes">`) {
		t.Error("client.html missing apple-mobile-web-app-capable")
	}
	if !strings.Contains(html, `<meta name="apple-mobile-web-app-status-bar-style"`) {
		t.Error("client.html missing apple-mobile-web-app-status-bar-style")
	}
}

func TestClientAppleTouchIcon(t *testing.T) {
	hub, ts := startTestServerT(t, testConfig(""))
	html := getClientHTML(t, ts, hub, "")
	if !strings.Contains(html, `<link rel="apple-touch-icon"`) {
		t.Error("client.html missing apple-touch-icon")
	}
}

func TestClientServiceWorkerRegistration(t *testing.T) {
	hub, ts := startTestServerT(t, testConfig(""))
	html := getClientHTML(t, ts, hub, "")
	if !strings.Contains(html, `navigator.serviceWorker.register`) {
		t.Error("client.html missing SW registration script")
	}
}

// ---------------------------------------------------------------------------
// 1.6 Static File Serving / Embed
// ---------------------------------------------------------------------------

func TestStaticFilesEmbedded(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))
	paths := []string{
		"/static/manifest.json",
		"/static/sw.js",
		"/static/icon-192.png",
		"/static/icon-512.png",
	}
	for _, p := range paths {
		resp, err := http.Get(ts.URL + p)
		if err != nil {
			t.Errorf("%s: %v", p, err)
			continue
		}
		resp.Body.Close()
		if resp.StatusCode != 200 {
			t.Errorf("%s: status %d, want 200", p, resp.StatusCode)
		}
	}
}

func TestRootSWMatchesEmbedded(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))

	// Fetch from /sw.js (root route)
	resp1, _ := http.Get(ts.URL + "/sw.js")
	body1, _ := io.ReadAll(resp1.Body)
	resp1.Body.Close()

	// Fetch from /static/sw.js (embedded static)
	resp2, _ := http.Get(ts.URL + "/static/sw.js")
	body2, _ := io.ReadAll(resp2.Body)
	resp2.Body.Close()

	if string(body1) != string(body2) {
		t.Error("/sw.js and /static/sw.js serve different content")
	}
}

func TestSWRouteDoesNotBreakExisting(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))

	paths := map[string]int{
		"/":           200,
		"/favicon.svg": 200,
	}
	for p, want := range paths {
		resp, err := http.Get(ts.URL + p)
		if err != nil {
			t.Errorf("%s: %v", p, err)
			continue
		}
		resp.Body.Close()
		if resp.StatusCode != want {
			t.Errorf("%s: status %d, want %d", p, resp.StatusCode, want)
		}
	}
}

// ---------------------------------------------------------------------------
// 1.7 Regression
// ---------------------------------------------------------------------------

func TestPWARegressionDashboard(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))
	resp, _ := http.Get(ts.URL + "/")
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Errorf("/ returned %d", resp.StatusCode)
	}
	ct := resp.Header.Get("Content-Type")
	if !strings.Contains(ct, "text/html") {
		t.Errorf("/ content-type %q, want text/html", ct)
	}
}

func TestPWARegressionFavicon(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))
	resp, _ := http.Get(ts.URL + "/favicon.svg")
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Errorf("/favicon.svg returned %d", resp.StatusCode)
	}
	ct := resp.Header.Get("Content-Type")
	if !strings.Contains(ct, "image/svg+xml") {
		t.Errorf("/favicon.svg content-type %q", ct)
	}
}

func TestPWARegressionAPIClients(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))
	resp, _ := http.Get(ts.URL + "/api/clients")
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Errorf("/api/clients returned %d", resp.StatusCode)
	}
	ct := resp.Header.Get("Content-Type")
	if !strings.Contains(ct, "json") {
		t.Errorf("/api/clients content-type %q", ct)
	}
}

func TestPWARegression404(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))
	resp, _ := http.Get(ts.URL + "/nonexistent-path-xyz")
	defer resp.Body.Close()
	if resp.StatusCode != 404 {
		t.Errorf("unknown path returned %d, want 404", resp.StatusCode)
	}
}

func TestPWARegressionClientPage(t *testing.T) {
	hub, ts := startTestServerT(t, testConfig(""))
	conn := wsConnect(t, ts, "", "regress-client")
	defer conn.Close()
	waitForClient(hub, "regress-client", 2*time.Second)

	resp, _ := http.Get(ts.URL + "/clients/regress-client")
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Errorf("/clients/regress-client returned %d", resp.StatusCode)
	}
	ct := resp.Header.Get("Content-Type")
	if !strings.Contains(ct, "text/html") {
		t.Errorf("client page content-type %q", ct)
	}
}

// ---------------------------------------------------------------------------
// 2.1 Edge: Manifest parsing
// ---------------------------------------------------------------------------

func TestManifestExtraFieldsStillValidJSON(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))
	resp, _ := http.Get(ts.URL + "/static/manifest.json")
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	var m map[string]interface{}
	if err := json.Unmarshal(body, &m); err != nil {
		t.Errorf("manifest with extra fields is not valid JSON: %v", err)
	}
}

// ---------------------------------------------------------------------------
// 2.2 Edge: Service Worker scope boundary
// ---------------------------------------------------------------------------

func TestSWAllowedHeaderOnlySW(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))

	// Service-Worker-Allowed should be on /sw.js
	resp1, _ := http.Get(ts.URL + "/sw.js")
	resp1.Body.Close()
	if resp1.Header.Get("Service-Worker-Allowed") != "/" {
		t.Error("/sw.js missing Service-Worker-Allowed header")
	}

	// Should NOT be on regular static files
	resp2, _ := http.Get(ts.URL + "/static/manifest.json")
	resp2.Body.Close()
	if resp2.Header.Get("Service-Worker-Allowed") != "" {
		t.Error("/static/manifest.json should not have Service-Worker-Allowed header")
	}
}

// ---------------------------------------------------------------------------
// 2.4 Edge: Content-Type strictness
// ---------------------------------------------------------------------------

func TestManifestContentTypeStrict(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))
	resp, _ := http.Get(ts.URL + "/static/manifest.json")
	defer resp.Body.Close()
	ct := resp.Header.Get("Content-Type")
	if !strings.Contains(ct, "json") {
		t.Errorf("manifest content-type %q is not json — browsers will reject it", ct)
	}
}

func TestSWContentTypeStrict(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))
	resp, _ := http.Get(ts.URL + "/sw.js")
	defer resp.Body.Close()
	ct := resp.Header.Get("Content-Type")
	if !strings.Contains(ct, "javascript") {
		t.Errorf("sw.js content-type %q is not javascript — browsers will reject it", ct)
	}
}

// ---------------------------------------------------------------------------
// 2.5 Edge: Concurrent static file requests
// ---------------------------------------------------------------------------

func TestConcurrentPWARequests(t *testing.T) {
	_, ts := startTestServerT(t, testConfig(""))

	paths := []string{
		"/static/manifest.json",
		"/static/icon-192.png",
		"/static/icon-512.png",
		"/sw.js",
	}

	var wg sync.WaitGroup
	errs := make(chan string, len(paths)*10)

	for i := 0; i < 10; i++ {
		for _, p := range paths {
			wg.Add(1)
			go func(path string) {
				defer wg.Done()
				resp, err := http.Get(ts.URL + path)
				if err != nil {
					errs <- path + ": " + err.Error()
					return
				}
				resp.Body.Close()
				if resp.StatusCode != 200 {
					errs <- path + ": non-200"
				}
			}(p)
		}
	}
	wg.Wait()
	close(errs)

	for e := range errs {
		t.Error(e)
	}
}

// ---------------------------------------------------------------------------
// 2.6 Edge: Auth interaction — PWA assets without auth
// ---------------------------------------------------------------------------

func TestPWAAssetsNoAuthRequired(t *testing.T) {
	// Configure server WITH dashboard and API keys
	cfg := &Config{
		Port:         0,
		APIKey:       "secret-api-key",
		DashboardKey: "secret-dash-key",
		MaxLogLines:  1000,
	}
	_, ts := startTestServerT(t, cfg)

	paths := []string{
		"/static/manifest.json",
		"/static/icon-192.png",
		"/static/icon-512.png",
		"/sw.js",
		"/favicon.svg",
	}

	for _, p := range paths {
		resp, err := http.Get(ts.URL + p)
		if err != nil {
			t.Errorf("%s: %v", p, err)
			continue
		}
		resp.Body.Close()
		if resp.StatusCode != 200 {
			t.Errorf("%s returned %d without auth — should be accessible without ?key=", p, resp.StatusCode)
		}
	}
}

func TestDashboardRequiresAuthButAssetsDoNot(t *testing.T) {
	cfg := &Config{
		Port:         0,
		DashboardKey: "dashkey",
		MaxLogLines:  1000,
	}
	_, ts := startTestServerT(t, cfg)

	// Dashboard returns 200 but shows key modal when not authenticated
	resp, _ := http.Get(ts.URL + "/")
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Errorf("/ should return 200 with key modal, got %d", resp.StatusCode)
	}
	if !strings.Contains(string(body), "key-modal") {
		t.Error("/ should show key modal when DashboardKey is set and not authenticated")
	}

	// But manifest should not
	resp2, _ := http.Get(ts.URL + "/static/manifest.json")
	resp2.Body.Close()
	if resp2.StatusCode != 200 {
		t.Errorf("/static/manifest.json returned %d without auth", resp2.StatusCode)
	}
}
