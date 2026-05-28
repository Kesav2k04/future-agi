package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

// Spin up the server, point it at an httptest CH, fire one OTLP request,
// confirm the resulting CH HTTP POST contains the converted row.
func TestServerEnd2End(t *testing.T) {
	var seen int32
	var seenBody string
	chSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&seen, 1)
		b := make([]byte, 1<<14)
		n, _ := r.Body.Read(b)
		seenBody = string(b[:n])
		w.WriteHeader(200)
	}))
	defer chSrv.Close()

	w, _ := chwriter.New(chwriter.Config{
		URL:            chSrv.URL,
		Database:       "default",
		Table:          "spans",
		MaxRetries:     1,
		InitialBackoff: time.Millisecond,
		MaxBackoff:     time.Millisecond,
		RequestTimeout: 2 * time.Second,
		DeadLetterFile: t.TempDir() + "/dl.jsonl",
	})

	s := New(Config{GRPCAddr: "127.0.0.1:0", BatchMaxRows: 1, BatchMaxAge: 50 * time.Millisecond}, w)
	// We need a known listen address to dial; replicate Run's bind step.
	// Easier: use a non-zero port — pick one that's likely free.
	addr := "127.0.0.1:24317"
	s.cfg.GRPCAddr = addr

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { _ = s.Run(ctx) }()
	// Wait for listener to be ready.
	if !waitPort(addr, 2*time.Second) {
		t.Fatalf("server didn't listen on %s", addr)
	}

	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	client := ptraceotlp.NewGRPCClient(conn)

	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("fi.project_id", "33333333-3333-4333-8333-333333333333")
	sp := rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	sp.SetName("e2e-test-span")
	sp.SetTraceID([16]byte{0xaa})
	sp.SetSpanID([8]byte{0xbb})
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Now()))
	sp.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Now().Add(50 * time.Millisecond)))

	req := ptraceotlp.NewExportRequestFromTraces(traces)
	if _, err := client.Export(context.Background(), req); err != nil {
		t.Fatalf("OTLP Export: %v", err)
	}

	// Wait up to 1 s for the batcher to flush.
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) && atomic.LoadInt32(&seen) == 0 {
		time.Sleep(10 * time.Millisecond)
	}
	if atomic.LoadInt32(&seen) != 1 {
		t.Fatalf("CH not POST'd; seen=%d", seen)
	}
	if !strings.Contains(seenBody, "e2e-test-span") {
		t.Errorf("CH body missing span name: %q", seenBody)
	}
	if !strings.Contains(seenBody, "33333333-3333-4333-8333-333333333333") {
		t.Errorf("CH body missing project_id: %q", seenBody)
	}
}

// waitPort polls until something accepts on addr or deadline. Simple enough
// not to need a /healthz round trip.
func waitPort(addr string, d time.Duration) bool {
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		conn, err := grpcDial(addr)
		if err == nil {
			conn.Close()
			return true
		}
		time.Sleep(20 * time.Millisecond)
	}
	return false
}

func grpcDial(addr string) (*grpc.ClientConn, error) {
	return grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
}
