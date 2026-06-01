import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Button,
  Chip,
  Collapse,
  Stack,
  Tooltip,
  Typography,
  alpha,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { useErrorFeedStore } from "../store";

// Run-sequence definitions + makeStepMessage / buildSynthesis live in
// `../useAnalyzeRunner` now — that hook owns the actual streaming so
// both the headline card and this tab observe the same thread state.
// Follow-up Q&A is handed off to Falcon, so there's no in-tab chat input.

// ── Visual primitives ─────────────────────────────────────────────────────

const ACCENT = "#7857FC";

// One block of a step's expanded reasoning — Claude-Code-style.
function StepDetailBlock({ block }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  if (block.kind === "reasoning") {
    return (
      <Typography
        fontSize="11.5px"
        color="text.secondary"
        sx={{ lineHeight: 1.65 }}
      >
        {block.text}
      </Typography>
    );
  }

  if (block.kind === "tool") {
    return (
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "6px",
          bgcolor: isDark ? alpha("#fff", 0.025) : alpha("#000", 0.02),
          px: 1,
          py: 0.75,
        }}
      >
        <Stack direction="row" alignItems="center" gap={0.5}>
          <Iconify
            icon="mdi:wrench-outline"
            width={11}
            sx={{ color: ACCENT }}
          />
          <Typography
            fontSize="11px"
            fontWeight={600}
            sx={{
              fontFamily: "ui-monospace, SFMono-Regular, monospace",
              color: "text.primary",
            }}
          >
            {block.name}
          </Typography>
        </Stack>
        {block.input != null && (
          <Typography
            fontSize="10.5px"
            sx={{
              fontFamily: "ui-monospace, SFMono-Regular, monospace",
              color: "text.disabled",
              mt: 0.3,
              wordBreak: "break-word",
            }}
          >
            {block.input}
          </Typography>
        )}
        {block.output != null && (
          <Typography
            fontSize="10.5px"
            sx={{
              fontFamily: "ui-monospace, SFMono-Regular, monospace",
              color: "text.secondary",
              mt: 0.3,
              wordBreak: "break-word",
            }}
          >
            → {block.output}
          </Typography>
        )}
      </Box>
    );
  }

  if (block.kind === "list") {
    return (
      <Box>
        {block.title && (
          <Typography
            fontSize="9.5px"
            fontWeight={700}
            color="text.disabled"
            sx={{ textTransform: "uppercase", letterSpacing: "0.06em", mb: 0.5 }}
          >
            {block.title}
          </Typography>
        )}
        <Stack gap={0.4}>
          {block.items.map((it, i) => (
            <Stack key={i} direction="row" gap={0.75} alignItems="flex-start">
              <Box
                sx={{
                  width: 4,
                  height: 4,
                  borderRadius: "50%",
                  bgcolor: "text.disabled",
                  mt: "7px",
                  flexShrink: 0,
                }}
              />
              <Typography
                fontSize="11.5px"
                color="text.secondary"
                sx={{ lineHeight: 1.55 }}
              >
                {it}
              </Typography>
            </Stack>
          ))}
        </Stack>
      </Box>
    );
  }

  if (block.kind === "code") {
    return (
      <Box
        component="pre"
        sx={{
          m: 0,
          p: 1,
          borderRadius: "6px",
          bgcolor: isDark ? alpha("#fff", 0.03) : alpha("#000", 0.03),
          fontFamily: "ui-monospace, SFMono-Regular, monospace",
          fontSize: "10.5px",
          lineHeight: 1.5,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          color: "text.secondary",
          overflow: "auto",
        }}
      >
        {block.text}
      </Box>
    );
  }
  return null;
}
StepDetailBlock.propTypes = { block: PropTypes.object.isRequired };

function StepCard({ step }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const isRunning = step.status === "running";
  const isQueued = step.status === "queued";
  const isDone = step.status === "done";
  const hasDetails = (isRunning || isDone) && step.details?.length > 0;
  // Done steps default collapsed; the actively-running step auto-expands so
  // you watch the reasoning stream live (like Claude Code).
  const [expanded, setExpanded] = useState(false);
  const open = expanded || (isRunning && hasDetails);

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: isRunning ? alpha(ACCENT, 0.35) : "divider",
        borderRadius: "8px",
        bgcolor: isRunning
          ? alpha(ACCENT, isDark ? 0.08 : 0.04)
          : isDark
            ? alpha("#fff", 0.02)
            : "background.paper",
        opacity: isQueued ? 0.55 : 1,
        transition: "all 0.2s",
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        gap={1.25}
        onClick={hasDetails ? () => setExpanded((v) => !v) : undefined}
        sx={{
          px: 1.5,
          py: 1.25,
          cursor: hasDetails ? "pointer" : "default",
          userSelect: "none",
          "&:hover": hasDetails
            ? { bgcolor: isDark ? alpha("#fff", 0.02) : alpha("#000", 0.015) }
            : {},
        }}
      >
        <Box
          sx={{
            width: 18,
            height: 18,
            borderRadius: "50%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
            mt: "1px",
            bgcolor: isDone
              ? alpha("#5ACE6D", isDark ? 0.18 : 0.14)
              : isRunning
                ? alpha(ACCENT, 0.18)
                : isDark
                  ? alpha("#fff", 0.06)
                  : alpha("#000", 0.05),
          }}
        >
          {isRunning ? (
            <Box
              sx={{
                width: 10,
                height: 10,
                borderRadius: "50%",
                border: "2px solid",
                borderColor: alpha(ACCENT, 0.25),
                borderTopColor: ACCENT,
                animation: "spin 0.8s linear infinite",
                "@keyframes spin": { to: { transform: "rotate(360deg)" } },
              }}
            />
          ) : isDone ? (
            <Iconify icon="mdi:check" width={12} sx={{ color: "#5ACE6D" }} />
          ) : (
            <Iconify icon="mdi:dots-horizontal" width={12} sx={{ color: "text.disabled" }} />
          )}
        </Box>
        <Stack gap={0.4} flex={1} minWidth={0}>
          <Typography fontSize="12.5px" fontWeight={600} color="text.primary">
            {step.title}
          </Typography>
          {(isRunning || isDone) && (
            <Typography fontSize="11.5px" color="text.secondary" sx={{ lineHeight: 1.5 }}>
              {step.detail}
            </Typography>
          )}
          {isDone && step.chips?.length > 0 && (
            <Stack direction="row" gap={0.5} flexWrap="wrap" sx={{ mt: 0.25 }}>
              {step.chips.map((c) => (
                <Chip
                  key={c}
                  label={c}
                  size="small"
                  sx={{
                    height: 18,
                    fontSize: "10px",
                    fontFamily: "ui-monospace, SFMono-Regular, monospace",
                    borderRadius: "4px",
                    bgcolor: "action.hover",
                    color: "text.secondary",
                    "& .MuiChip-label": { px: "6px" },
                  }}
                />
              ))}
            </Stack>
          )}
        </Stack>
        {hasDetails && (
          <Stack direction="row" alignItems="center" gap={0.3} sx={{ flexShrink: 0, mt: "1px" }}>
            <Typography fontSize="10px" color="text.disabled">
              {open ? "Hide" : "Reasoning"}
            </Typography>
            <Iconify
              icon={open ? "mdi:chevron-up" : "mdi:chevron-down"}
              width={15}
              sx={{ color: "text.disabled" }}
            />
          </Stack>
        )}
      </Stack>

      {hasDetails && (
        <Collapse in={open} unmountOnExit>
          <Box
            sx={{
              px: 1.5,
              pb: 1.5,
              pt: 0.25,
              ml: "30px",
              borderTop: "1px dashed",
              borderColor: "divider",
            }}
          >
            <Stack gap={1} sx={{ pt: 1 }}>
              {step.details.map((block, i) => (
                <StepDetailBlock key={i} block={block} />
              ))}
            </Stack>
          </Box>
        </Collapse>
      )}
    </Box>
  );
}
StepCard.propTypes = { step: PropTypes.object.isRequired };

function SynthesisCard({ synthesis }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: alpha("#7857FC", 0.3),
        borderRadius: "8px",
        bgcolor: alpha("#7857FC", isDark ? 0.06 : 0.03),
        p: 1.5,
        position: "relative",
      }}
    >
      <Stack direction="row" alignItems="center" gap={0.5} sx={{ mb: 1 }}>
        <Iconify icon="mdi:star-four-points" width={12} sx={{ color: "#7857FC" }} />
        <Typography
          fontSize="10.5px"
          fontWeight={700}
          sx={{
            color: "#7857FC",
            textTransform: "uppercase",
            letterSpacing: "0.08em",
          }}
        >
          Synthesis
        </Typography>
      </Stack>
      <Typography fontSize="13.5px" color="text.primary" sx={{ lineHeight: 1.55, mb: 1 }}>
        {synthesis.headline}
      </Typography>
      <Stack direction="row" gap={1} alignItems="flex-start">
        <Typography
          fontSize="10px"
          fontWeight={700}
          sx={{
            color: "#5ACE6D",
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            mt: "3px",
            flexShrink: 0,
            px: 0.75,
            py: 0.25,
            borderRadius: "3px",
            bgcolor: alpha("#5ACE6D", isDark ? 0.14 : 0.12),
          }}
        >
          Fix
        </Typography>
        <Typography fontSize="12.5px" color="text.secondary" sx={{ lineHeight: 1.6, flex: 1 }}>
          {synthesis.fix}
        </Typography>
      </Stack>
    </Box>
  );
}
SynthesisCard.propTypes = {
  synthesis: PropTypes.object.isRequired,
};

function RunHeader({ label, timestamp }) {
  return (
    <Stack direction="row" alignItems="center" gap={1.25} sx={{ py: 0.5 }}>
      <Box sx={{ flex: 1, height: "1px", bgcolor: "divider" }} />
      <Stack direction="row" alignItems="center" gap={0.5}>
        <Iconify
          icon="mdi:star-four-points-outline"
          width={11}
          sx={{ color: "text.disabled" }}
        />
        <Typography
          fontSize="10px"
          fontWeight={600}
          color="text.disabled"
          sx={{ textTransform: "uppercase", letterSpacing: "0.08em" }}
        >
          {label} · {timestamp}
        </Typography>
      </Stack>
      <Box sx={{ flex: 1, height: "1px", bgcolor: "divider" }} />
    </Stack>
  );
}
RunHeader.propTypes = { label: PropTypes.string, timestamp: PropTypes.string };

// ── Main AnalyzeTab ───────────────────────────────────────────────────────

export default function AnalyzeTab({ error }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const clusterId = error?.clusterId;
  const thread = useErrorFeedStore(
    (s) => s.analyzeThreadsByCluster[clusterId] ?? null,
  );
  const setAnalyzePendingStart = useErrorFeedStore(
    (s) => s.setAnalyzePendingStart,
  );

  const messages = thread?.messages ?? [];
  const runState = thread?.runState ?? "idle";
  const isStreaming = runState === "streaming";

  // Render order: completed synthesis on top, then the steps / run headers
  // below it. While streaming there's no synthesis yet, so it's just steps.
  const orderedMessages = useMemo(
    () => [
      ...messages.filter((m) => m.type === "synthesis"),
      ...messages.filter((m) => m.type !== "synthesis"),
    ],
    [messages],
  );

  const scrollerRef = useRef(null);

  // While streaming, follow the latest step (scroll to bottom). Once the
  // run finishes, jump to the top so the synthesis (now on top) is visible.
  useEffect(() => {
    if (!scrollerRef.current) return;
    scrollerRef.current.scrollTop =
      runState === "streaming" ? scrollerRef.current.scrollHeight : 0;
  }, [messages.length, runState]);

  // Both empty-state CTA and Re-run dispatch via the pending flag so the
  // shared runner (and therefore the headline card) sees the same trigger.
  const onTriggerRun = () => setAnalyzePendingStart(clusterId, true);

  // Format the run-started timestamp once.
  const startedLabel = useMemo(() => {
    if (!thread?.startedAt) return null;
    const d = new Date(thread.startedAt);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }, [thread?.startedAt]);

  return (
    <Stack
      gap={1.5}
      sx={{
        width: "100%",
        height: "calc(100vh - 320px)",
        minHeight: 480,
        py: 0.5,
      }}
    >
      {/* Context strip */}
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{
          px: 1.5,
          py: 1,
          borderRadius: "8px",
          border: "1px solid",
          borderColor: "divider",
          bgcolor: isDark ? alpha("#fff", 0.02) : alpha("#000", 0.02),
          flexShrink: 0,
        }}
      >
        <Iconify icon="mdi:layers-outline" width={14} sx={{ color: "text.disabled" }} />
        <Typography fontSize="12px" fontWeight={600} color="text.primary" noWrap>
          {error?.error?.name ?? "Cluster"}
        </Typography>
        <Typography fontSize="11.5px" color="text.disabled">
          · {error?.traceCount?.toLocaleString() ?? "—"} traces
        </Typography>
        {startedLabel && (
          <Typography fontSize="11.5px" color="text.disabled">
            · started {startedLabel}
          </Typography>
        )}
        <Box sx={{ flex: 1 }} />
        <Tooltip title="Re-run with current cluster state (1 credit)" arrow>
          <span>
            <Button
              size="small"
              variant="text"
              disabled={isStreaming}
              onClick={onTriggerRun}
              startIcon={<Iconify icon="mdi:refresh" width={12} />}
              sx={{
                height: 24,
                fontSize: "11.5px",
                textTransform: "none",
                color: "text.secondary",
                "&:hover": { color: "text.primary" },
              }}
            >
              Re-run
            </Button>
          </span>
        </Tooltip>
      </Stack>

      {/* Scrollable message stream */}
      <Box
        ref={scrollerRef}
        sx={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          bgcolor: isDark ? alpha("#fff", 0.012) : "background.paper",
        }}
      >
        <Stack gap={1.25} sx={{ p: 1.5 }}>
          {messages.length === 0 ? (
            <Stack
              alignItems="center"
              justifyContent="center"
              gap={1.25}
              sx={{ py: 6, px: 2, textAlign: "center", maxWidth: 460, mx: "auto" }}
            >
              <Box
                sx={{
                  width: 44,
                  height: 44,
                  borderRadius: "50%",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  bgcolor: alpha("#7857FC", isDark ? 0.16 : 0.1),
                }}
              >
                <Iconify
                  icon="mdi:star-four-points-outline"
                  width={20}
                  sx={{ color: "#7857FC" }}
                />
              </Box>
              <Typography fontSize="14px" fontWeight={600} color="text.primary">
                No analysis yet
              </Typography>
              <Typography
                fontSize="12px"
                color="text.secondary"
                sx={{ lineHeight: 1.55 }}
              >
                Kick off a cluster-level analysis. Sub-agents will sample
                representative calls, compare against a passing baseline, and
                synthesise the result here.
              </Typography>
              <Button
                size="small"
                variant="contained"
                startIcon={<Iconify icon="mdi:star-four-points" width={13} />}
                onClick={onTriggerRun}
                sx={{
                  mt: 0.5,
                  height: 32,
                  fontSize: "12.5px",
                  fontWeight: 600,
                  borderRadius: "8px",
                  textTransform: "none",
                  // White button in dark theme, purple in light.
                  bgcolor: isDark ? "#fff" : "#7857FC",
                  color: isDark ? "#111" : "#fff",
                  px: 1.75,
                  "&:hover": { bgcolor: isDark ? "#e8e8e8" : "#6845E8" },
                  boxShadow: "none",
                }}
              >
                Analyze this cluster
              </Button>
            </Stack>
          ) : (
            orderedMessages.map((m) => {
              if (m.type === "step") return <StepCard key={m.id} step={m} />;
              if (m.type === "synthesis")
                return <SynthesisCard key={m.id} synthesis={m} />;
              if (m.type === "run_header")
                return <RunHeader key={m.id} label={m.label} timestamp={m.timestamp} />;
              return null;
            })
          )}
        </Stack>
      </Box>
    </Stack>
  );
}
AnalyzeTab.propTypes = { error: PropTypes.object };
