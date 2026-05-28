// Package server hosts a minimal OTLP/gRPC receiver. Why minimal vs the
// full OTel collector framework:
//
//   - The collector framework brings ≈300 MB of transitive deps and a
//     plugin / factory wiring story that's overkill when we only need ONE
//     receiver, ONE processor pipeline and ONE exporter.
//   - The OTLP wire spec is small (~150 LOC to handle ExportTraceServiceRequest)
//     and stable; we get bit-for-bit OTLP compliance without the framework.
//   - Building light keeps cold-start under 100 ms — important for
//     local-dev `docker compose up` iteration.
//
// If we ever need multi-pipeline routing, sampling, or tail-based sampling,
// we should reach for the OTel collector framework at that point. For now,
// less is more.
package server

import (
	"context"
	"fmt"
	"net"
	"sync"
	"time"

	chexp "github.com/future-agi/future-agi/fi-collector/exporter/clickhouse25exporter"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc"
)

// Config is what main() passes us. Public fields = YAML wire format.
type Config struct {
	GRPCAddr      string        `yaml:"grpc_addr"`        // :4317 default
	BatchMaxRows  int           `yaml:"batch_max_rows"`   // flush after N rows
	BatchMaxAge   time.Duration `yaml:"batch_max_age"`    // flush after X time
}

// Server owns the gRPC listener and the batch flusher goroutine.
type Server struct {
	cfg    Config
	writer *chwriter.Writer
	grpc   *grpc.Server

	// Batching: the gRPC handler pushes converted rows onto `pending` and
	// signals via `pendCh`. A single flusher goroutine drains it on either
	// the row-count or age trigger. One channel/one goroutine keeps lock
	// contention minimal at 100K spans/sec.
	pendMu sync.Mutex
	pend   []map[string]any
	pendCh chan struct{}

	stopCh chan struct{}
	wg     sync.WaitGroup
}

// New wires up the server but does NOT start serving. Call Run().
func New(cfg Config, writer *chwriter.Writer) *Server {
	if cfg.GRPCAddr == "" {
		cfg.GRPCAddr = ":4317"
	}
	if cfg.BatchMaxRows <= 0 {
		cfg.BatchMaxRows = 5000
	}
	if cfg.BatchMaxAge <= 0 {
		cfg.BatchMaxAge = 5 * time.Second
	}

	s := &Server{
		cfg:    cfg,
		writer: writer,
		pendCh: make(chan struct{}, 1),
		stopCh: make(chan struct{}),
	}
	return s
}

// Run blocks until ctx is cancelled or a serve error occurs. On shutdown
// we drain pending rows once before returning so an SIGTERM doesn't lose
// the in-flight batch (DECISIONS: in-flight loss bounded to last 5 s as
// the deliberate at-least-once boundary).
func (s *Server) Run(ctx context.Context) error {
	lis, err := net.Listen("tcp", s.cfg.GRPCAddr)
	if err != nil {
		return fmt.Errorf("listen %s: %w", s.cfg.GRPCAddr, err)
	}
	s.grpc = grpc.NewServer()
	ptraceotlp.RegisterGRPCServer(s.grpc, &otlpHandler{s: s})

	s.wg.Add(1)
	go s.flushLoop()

	serveErr := make(chan error, 1)
	go func() { serveErr <- s.grpc.Serve(lis) }()

	select {
	case <-ctx.Done():
		s.grpc.GracefulStop()
		close(s.stopCh)
		s.wg.Wait()
		s.drainNow(context.Background())
		return ctx.Err()
	case err := <-serveErr:
		close(s.stopCh)
		s.wg.Wait()
		s.drainNow(context.Background())
		return err
	}
}

// otlpHandler implements ptraceotlp.GRPCServer. Stateless per call.
type otlpHandler struct {
	ptraceotlp.UnimplementedGRPCServer
	s *Server
}

func (h *otlpHandler) Export(ctx context.Context, req ptraceotlp.ExportRequest) (ptraceotlp.ExportResponse, error) {
	rows, err := chexp.Convert(req.Traces())
	if err != nil {
		// Conversion failure = malformed payload from producer; surface
		// to the SDK so it retries to a different gateway if it has one.
		return ptraceotlp.NewExportResponse(), err
	}
	h.s.enqueue(rows)
	// Per OTLP spec, ExportResponse is empty on full success.
	return ptraceotlp.NewExportResponse(), nil
}

// enqueue parks rows on the pending buffer and signals the flusher.
// We choose non-blocking signalling: if the channel already holds a tick
// the flusher will already wake up and see this batch.
func (s *Server) enqueue(rows []map[string]any) {
	if len(rows) == 0 {
		return
	}
	s.pendMu.Lock()
	s.pend = append(s.pend, rows...)
	shouldKick := len(s.pend) >= s.cfg.BatchMaxRows
	s.pendMu.Unlock()
	if shouldKick {
		select {
		case s.pendCh <- struct{}{}:
		default:
		}
	}
}

// flushLoop runs until stopCh closes. Wakes on either an explicit kick
// (row-count threshold) or the time-based ticker.
func (s *Server) flushLoop() {
	defer s.wg.Done()
	t := time.NewTicker(s.cfg.BatchMaxAge)
	defer t.Stop()
	for {
		select {
		case <-s.stopCh:
			return
		case <-t.C:
			s.drainNow(context.Background())
		case <-s.pendCh:
			s.drainNow(context.Background())
		}
	}
}

// drainNow swaps the pending buffer and flushes it. Uses a fresh slice so
// the next request can immediately start filling without contending.
func (s *Server) drainNow(ctx context.Context) {
	s.pendMu.Lock()
	batch := s.pend
	s.pend = nil
	s.pendMu.Unlock()
	if len(batch) == 0 {
		return
	}
	_ = s.writer.Insert(ctx, batch)
	// Insert returns an error on dead-letter; the writer already persisted
	// the rows + bumped stats. We swallow here because the flusher's job
	// is to make progress, not propagate per-batch failures. /healthz
	// surfaces the writer's failure counter.
}
