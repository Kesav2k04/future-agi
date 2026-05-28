package chwriter

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func mkConfig(t *testing.T, url string) Config {
	t.Helper()
	dir := t.TempDir()
	return Config{
		URL:            url,
		Database:       "default",
		Table:          "spans",
		MaxRetries:     3,
		InitialBackoff: 5 * time.Millisecond,
		MaxBackoff:     20 * time.Millisecond,
		RequestTimeout: 1 * time.Second,
		DeadLetterFile: filepath.Join(dir, "dead.jsonl"),
	}
}

func TestInsertSuccess(t *testing.T) {
	var bodies []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("database") != "default" {
			t.Errorf("missing database query param: %s", r.URL.RawQuery)
		}
		if !strings.Contains(r.URL.Query().Get("query"), "INSERT INTO spans FORMAT JSONEachRow") {
			t.Errorf("unexpected query: %s", r.URL.Query().Get("query"))
		}
		b, _ := io.ReadAll(r.Body)
		bodies = append(bodies, string(b))
		w.WriteHeader(200)
	}))
	defer srv.Close()

	w, err := New(mkConfig(t, srv.URL))
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()

	rows := []map[string]any{
		{"id": "s1", "project_id": "00000000-0000-0000-0000-000000000001", "name": "root"},
		{"id": "s2", "project_id": "00000000-0000-0000-0000-000000000001", "name": "child"},
	}
	if err := w.Insert(context.Background(), rows); err != nil {
		t.Fatalf("Insert: %v", err)
	}
	if got := atomic.LoadUint64(&w.stats.BatchesInserted); got != 1 {
		t.Errorf("BatchesInserted=%d want 1", got)
	}
	if got := atomic.LoadUint64(&w.stats.RowsInserted); got != 2 {
		t.Errorf("RowsInserted=%d want 2", got)
	}
	if n := strings.Count(bodies[0], "\n"); n != 2 {
		t.Errorf("expected 2 newlines in JSONEachRow body, got %d in %q", n, bodies[0])
	}
}

func TestInsertRetriesOn5xx(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := atomic.AddInt32(&calls, 1)
		if n < 3 {
			http.Error(w, "transient", http.StatusInternalServerError)
			return
		}
		w.WriteHeader(200)
	}))
	defer srv.Close()

	w, _ := New(mkConfig(t, srv.URL))
	defer w.Close()

	if err := w.Insert(context.Background(), []map[string]any{{"id": "x"}}); err != nil {
		t.Fatalf("expected success after retries, got %v", err)
	}
	if atomic.LoadInt32(&calls) != 3 {
		t.Errorf("calls=%d want 3", calls)
	}
	if atomic.LoadUint64(&w.stats.BatchesRetried) != 2 {
		t.Errorf("BatchesRetried=%d want 2", w.stats.BatchesRetried)
	}
}

func TestInsert4xxDeadLettersWithoutRetry(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		http.Error(w, "bad schema", http.StatusBadRequest)
	}))
	defer srv.Close()

	w, _ := New(mkConfig(t, srv.URL))
	defer w.Close()

	rows := []map[string]any{{"id": "s1"}, {"id": "s2"}}
	err := w.Insert(context.Background(), rows)
	if err == nil {
		t.Fatal("expected error on non-retryable status")
	}
	if atomic.LoadInt32(&calls) != 1 {
		t.Errorf("4xx should not retry; calls=%d", calls)
	}
	// Dead-letter file must contain both rows.
	b, err := os.ReadFile(w.cfg.DeadLetterFile)
	if err != nil {
		t.Fatalf("read dead-letter: %v", err)
	}
	if c := strings.Count(string(b), "\n"); c != 2 {
		t.Errorf("dead-letter should have 2 lines, got %d: %q", c, string(b))
	}
	for _, line := range strings.Split(strings.TrimRight(string(b), "\n"), "\n") {
		var m map[string]any
		if err := json.Unmarshal([]byte(line), &m); err != nil {
			t.Errorf("dead-letter line not valid JSON: %v", err)
		}
	}
	if atomic.LoadUint64(&w.stats.RowsDeadLettered) != 2 {
		t.Errorf("RowsDeadLettered=%d want 2", w.stats.RowsDeadLettered)
	}
}

func TestInsertEmptyBatchNoop(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Error("server should not be called on empty batch")
	}))
	defer srv.Close()
	w, _ := New(mkConfig(t, srv.URL))
	defer w.Close()
	if err := w.Insert(context.Background(), nil); err != nil {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestEncodeBatchEscapesUnescapedHTML(t *testing.T) {
	// CH treats &, <, > literally; we must NOT use the json package's default
	// HTML-escape which would turn "a&b" into "a&b" and bloat payloads.
	b, err := encodeBatch([]map[string]any{{"k": "<a&b>"}})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(b), "<a&b>") {
		t.Errorf("HTML should not be escaped: %q", string(b))
	}
}

func TestContextCancellationStopsRetries(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "5xx", http.StatusInternalServerError)
	}))
	defer srv.Close()
	cfg := mkConfig(t, srv.URL)
	cfg.MaxRetries = 100        // would take ~10s without cancellation
	cfg.InitialBackoff = 50 * time.Millisecond
	w, _ := New(cfg)
	defer w.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	t0 := time.Now()
	err := w.Insert(ctx, []map[string]any{{"id": "x"}})
	if err == nil {
		t.Fatal("expected error from cancelled context")
	}
	if elapsed := time.Since(t0); elapsed > 500*time.Millisecond {
		t.Errorf("context cancellation should bound runtime; ran for %v", elapsed)
	}
}
