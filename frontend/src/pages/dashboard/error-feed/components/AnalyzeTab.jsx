import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Button,
  Chip,
  Collapse,
  IconButton,
  Stack,
  TextField,
  Tooltip,
  Typography,
  alpha,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { useErrorFeedStore } from "../store";
import { useFollowUpRunner } from "../useAnalyzeRunner";

// Run-sequence definitions + makeStepMessage / buildSynthesis live in
// `../useAnalyzeRunner` now — that hook owns the actual streaming so
// both the headline card and this tab observe the same thread state.
// Follow-up Q&A is handled in-tab via useFollowUpRunner (mounted below);
// it streams a sub-agent's steps + answer + suggestion chips per question.

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

// ── Follow-up message renderers ──────────────────────────────────────────

// User-submitted question — right-aligned chat bubble.
function UserQuestionBubble({ text }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  return (
    <Stack direction="row" justifyContent="flex-end" sx={{ pl: 6 }}>
      <Box
        sx={{
          px: 1.5,
          py: 1,
          borderRadius: "12px 12px 4px 12px",
          maxWidth: "85%",
          bgcolor: isDark ? alpha("#fff", 0.07) : alpha(ACCENT, 0.08),
          border: "1px solid",
          borderColor: isDark ? alpha("#fff", 0.1) : alpha(ACCENT, 0.16),
        }}
      >
        <Typography fontSize="13px" color="text.primary" sx={{ lineHeight: 1.55 }}>
          {text}
        </Typography>
      </Box>
    </Stack>
  );
}
UserQuestionBubble.propTypes = { text: PropTypes.string.isRequired };

// Falcon's short pre-sub-agent intro line.
function AssistantIntro({ text }) {
  return (
    <Stack direction="row" alignItems="flex-start" gap={1} sx={{ pl: 0.5 }}>
      <Iconify
        icon="mdi:star-four-points"
        width={14}
        sx={{ color: ACCENT, mt: "3px", flexShrink: 0 }}
      />
      <Typography
        fontSize="13px"
        color="text.primary"
        sx={{ lineHeight: 1.55 }}
      >
        {text}
      </Typography>
    </Stack>
  );
}
AssistantIntro.propTypes = { text: PropTypes.string.isRequired };

// One row inside the sub-agent's mini step list — denser than StepCard
// because we're nested inside a card already. Same status semantics
// (queued / running / done).
function SubagentStepRow({ step }) {
  const status = step.status ?? "queued";
  const isDone = status === "done";
  const isRunning = status === "running";
  return (
    <Stack direction="row" alignItems="flex-start" gap={1} sx={{ py: 0.4 }}>
      <Box
        sx={{
          width: 14,
          height: 14,
          mt: "2px",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        {isDone ? (
          <Iconify icon="mdi:check" width={13} sx={{ color: "#5ACE6D" }} />
        ) : isRunning ? (
          <Box
            sx={{
              width: 11,
              height: 11,
              borderRadius: "50%",
              border: "2px solid",
              borderColor: alpha(ACCENT, 0.25),
              borderTopColor: ACCENT,
              animation: "spin 0.8s linear infinite",
              "@keyframes spin": { to: { transform: "rotate(360deg)" } },
            }}
          />
        ) : (
          <Iconify
            icon="mdi:circle-outline"
            width={11}
            sx={{ color: "text.disabled" }}
          />
        )}
      </Box>
      <Stack gap={0.15} sx={{ minWidth: 0 }}>
        <Typography
          fontSize="12.5px"
          fontWeight={isRunning ? 600 : 500}
          color={isRunning ? "text.primary" : isDone ? "text.primary" : "text.secondary"}
          sx={{ lineHeight: 1.4 }}
        >
          {step.title}
        </Typography>
        {isDone && step.detail && step.detail !== "—" && (
          <Typography fontSize="11.5px" color="text.secondary" sx={{ lineHeight: 1.45 }}>
            {step.detail}
          </Typography>
        )}
      </Stack>
    </Stack>
  );
}
SubagentStepRow.propTypes = { step: PropTypes.object.isRequired };

// Light **bold** parsing for the sub-agent answer body — minimal markdown.
function MiniMarkdown({ text }) {
  const segments = useMemo(() => {
    const out = [];
    const re = /\*\*([^*]+)\*\*|`([^`]+)`/g;
    let last = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) out.push({ t: text.slice(last, m.index) });
      if (m[1] != null) out.push({ t: m[1], b: true });
      else if (m[2] != null) out.push({ t: m[2], code: true });
      last = re.lastIndex;
    }
    if (last < text.length) out.push({ t: text.slice(last) });
    return out;
  }, [text]);
  return (
    <Typography
      component="div"
      fontSize="13px"
      color="text.primary"
      sx={{ lineHeight: 1.6, whiteSpace: "pre-wrap" }}
    >
      {segments.map((s, i) =>
        s.b ? (
          <Box key={i} component="span" sx={{ fontWeight: 700 }}>
            {s.t}
          </Box>
        ) : s.code ? (
          <Box
            key={i}
            component="code"
            sx={{
              fontFamily: "ui-monospace, SFMono-Regular, monospace",
              fontSize: "12px",
              px: 0.5,
              py: 0.1,
              borderRadius: "3px",
              bgcolor: (theme) =>
                theme.palette.mode === "dark"
                  ? alpha("#fff", 0.07)
                  : alpha("#000", 0.05),
            }}
          >
            {s.t}
          </Box>
        ) : (
          <React.Fragment key={i}>{s.t}</React.Fragment>
        ),
      )}
    </Typography>
  );
}
MiniMarkdown.propTypes = { text: PropTypes.string.isRequired };

// The sub-agent container: header strip + sub-step list + final answer.
function SubagentCard({ msg }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const isStreaming = msg.status === "streaming";
  return (
    <Stack gap={1.25} sx={{ pl: 3 }}>
      <Box
        sx={{
          border: "1px solid",
          borderColor: isDark ? alpha("#fff", 0.08) : "divider",
          borderRadius: "10px",
          overflow: "hidden",
          bgcolor: isDark ? alpha("#fff", 0.02) : alpha("#000", 0.015),
        }}
      >
        {/* Header */}
        <Stack
          direction="row"
          alignItems="center"
          gap={0.85}
          sx={{
            px: 1.5,
            py: 0.85,
            borderBottom: "1px solid",
            borderColor: "divider",
            bgcolor: isDark ? alpha(ACCENT, 0.08) : alpha(ACCENT, 0.05),
          }}
        >
          <Iconify icon="mdi:robot-outline" width={14} sx={{ color: ACCENT }} />
          <Typography
            fontSize="10.5px"
            fontWeight={700}
            sx={{
              color: ACCENT,
              textTransform: "uppercase",
              letterSpacing: "0.07em",
            }}
          >
            Sub-agent · {msg.title}
          </Typography>
          {msg.traceShortId && (
            <>
              <Box
                sx={{
                  width: 3,
                  height: 3,
                  borderRadius: "50%",
                  bgcolor: "text.disabled",
                }}
              />
              <Typography
                fontSize="10.5px"
                color="text.disabled"
                sx={{
                  fontFamily: "ui-monospace, SFMono-Regular, monospace",
                }}
              >
                {msg.traceShortId}
              </Typography>
            </>
          )}
          <Box sx={{ flex: 1 }} />
          {isStreaming && (
            <Typography
              fontSize="10.5px"
              fontWeight={600}
              color="text.disabled"
              sx={{ textTransform: "uppercase", letterSpacing: "0.07em" }}
            >
              running
            </Typography>
          )}
        </Stack>

        {/* Sub-steps */}
        <Box sx={{ px: 1.5, py: 1 }}>
          <Stack gap={0}>
            {msg.steps?.map((s) => <SubagentStepRow key={s.id} step={s} />)}
          </Stack>
        </Box>
      </Box>

      {/* Final answer — appears once all steps are done. */}
      {msg.answer && <MiniMarkdown text={msg.answer} />}
    </Stack>
  );
}
SubagentCard.propTypes = { msg: PropTypes.object.isRequired };

// "Try asking" suggestion chips — clicking one submits it as the next
// follow-up. Disabled while the parent is streaming.
function SuggestionChips({ items, disabled, onPick }) {
  if (!items?.length) return null;
  return (
    <Stack gap={0.75} sx={{ pl: 3, pt: 0.5 }}>
      <Typography
        fontSize="9.5px"
        fontWeight={700}
        color="text.disabled"
        sx={{ textTransform: "uppercase", letterSpacing: "0.09em" }}
      >
        Try asking
      </Typography>
      <Stack direction="row" gap={0.75} flexWrap="wrap">
        {items.map((q) => (
          <Chip
            key={q}
            label={q}
            size="small"
            disabled={disabled}
            onClick={() => onPick?.(q)}
            sx={{
              height: 26,
              borderRadius: "13px",
              fontSize: "12px",
              fontWeight: 500,
              cursor: "pointer",
              bgcolor: (theme) =>
                theme.palette.mode === "dark"
                  ? alpha("#fff", 0.05)
                  : alpha("#000", 0.04),
              color: "text.primary",
              border: "1px solid",
              borderColor: "divider",
              "&:hover": {
                bgcolor: (theme) =>
                  theme.palette.mode === "dark"
                    ? alpha("#fff", 0.09)
                    : alpha("#000", 0.06),
              },
            }}
          />
        ))}
      </Stack>
    </Stack>
  );
}
SuggestionChips.propTypes = {
  items: PropTypes.array,
  disabled: PropTypes.bool,
  onPick: PropTypes.func,
};

// Sticky input bar at the bottom of the tab. Disabled until the main run
// finishes (so the user can't fork a sub-agent mid-cluster-analysis) and
// while a sub-agent is streaming (to avoid stacking parallel runs).
//
// The stroke is a purple gradient (Falcon brand) painted via the standard
// `background-clip` trick — `border: transparent` + two stacked
// backgrounds where the inner one is clipped to the padding-box and the
// outer gradient fills the border-box.
function FollowUpInput({ disabled, placeholder, onSubmit }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const [text, setText] = useState("");
  const submit = () => {
    const t = text.trim();
    if (!t || disabled) return;
    onSubmit?.(t);
    setText("");
  };
  // Inner bg has to be opaque for the background-clip trick to work; pick a
  // tone close to the surrounding container so the box still feels lifted.
  const innerBg = isDark ? "#1a1a1d" : "#ffffff";
  const gradientStroke = disabled
    ? `linear-gradient(110deg, ${alpha("#7857FC", 0.35)}, ${alpha("#C084FC", 0.35)})`
    : "linear-gradient(110deg, #7857FC 0%, #A78BFA 50%, #C084FC 100%)";
  return (
    <Box
      sx={{
        flexShrink: 0,
        border: "1.5px solid transparent",
        borderRadius: "10px",
        background: `linear-gradient(${innerBg}, ${innerBg}) padding-box, ${gradientStroke} border-box`,
        px: 1.25,
        py: 0.5,
        display: "flex",
        alignItems: "center",
        gap: 0.75,
        opacity: disabled ? 0.75 : 1,
        transition: "opacity 0.15s ease",
      }}
    >
      <Iconify
        icon="mdi:star-four-points"
        width={15}
        sx={{ color: ACCENT, ml: 0.25, flexShrink: 0 }}
      />
      <TextField
        fullWidth
        multiline
        maxRows={4}
        size="small"
        variant="standard"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
        disabled={disabled}
        placeholder={placeholder}
        InputProps={{
          disableUnderline: true,
          sx: { fontSize: "13px", lineHeight: 1.5, py: 0.5 },
        }}
      />
      <Tooltip title={disabled ? "Waiting for current run…" : "Send (Enter)"} arrow>
        <span>
          <IconButton
            size="small"
            onClick={submit}
            disabled={disabled || !text.trim()}
            sx={{
              width: 28,
              height: 28,
              borderRadius: "6px",
              bgcolor: text.trim() && !disabled ? ACCENT : "transparent",
              color: text.trim() && !disabled ? "#fff" : "text.disabled",
              "&:hover": {
                bgcolor:
                  text.trim() && !disabled
                    ? "#6845E8"
                    : isDark
                      ? alpha("#fff", 0.05)
                      : alpha("#000", 0.04),
              },
              "&.Mui-disabled": {
                color: "text.disabled",
              },
            }}
          >
            <Iconify icon="mdi:arrow-up" width={14} />
          </IconButton>
        </span>
      </Tooltip>
    </Box>
  );
}
FollowUpInput.propTypes = {
  disabled: PropTypes.bool,
  placeholder: PropTypes.string,
  onSubmit: PropTypes.func,
};

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
  // Owns the follow-up Q&A streaming. Independent of the main analyze
  // runner (which is mounted at the parent / headline-card layer) so the
  // two flows don't share timer state.
  const { runFollowUp } = useFollowUpRunner(clusterId, error);

  const messages = thread?.messages ?? [];
  const runState = thread?.runState ?? "idle";
  const followUpRunState = thread?.followUpRunState ?? "idle";
  const isStreaming = runState === "streaming";
  const isFollowUpStreaming = followUpRunState === "streaming";
  const mainRunDone = runState === "done";

  // Chronological order — the cluster steps build the case, the synthesis
  // is the headline, follow-ups continue the conversation below it.
  // (Earlier this tab pushed the synthesis to the top; that was fine while
  // there were no follow-ups but reads awkwardly once the user is mid-Q&A.)
  const scrollerRef = useRef(null);

  // Always follow the latest message — for streaming runs AND for follow-ups
  // the user just submitted, the bottom is where the action is.
  useEffect(() => {
    if (!scrollerRef.current) return;
    scrollerRef.current.scrollTop = scrollerRef.current.scrollHeight;
  }, [messages.length, runState, followUpRunState]);

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
            messages.map((m) => {
              if (m.type === "step") return <StepCard key={m.id} step={m} />;
              if (m.type === "synthesis")
                return <SynthesisCard key={m.id} synthesis={m} />;
              if (m.type === "run_header")
                return (
                  <RunHeader key={m.id} label={m.label} timestamp={m.timestamp} />
                );
              if (m.type === "user_question")
                return <UserQuestionBubble key={m.id} text={m.text} />;
              if (m.type === "assistant_intro")
                return <AssistantIntro key={m.id} text={m.text} />;
              if (m.type === "subagent")
                return <SubagentCard key={m.id} msg={m} />;
              if (m.type === "suggestions")
                return (
                  <SuggestionChips
                    key={m.id}
                    items={m.items}
                    disabled={isFollowUpStreaming}
                    onPick={runFollowUp}
                  />
                );
              return null;
            })
          )}
        </Stack>
      </Box>

      {/* Sticky follow-up input — visible once the main run has produced a
          synthesis. Disabled while a sub-agent is mid-stream. */}
      {mainRunDone && (
        <FollowUpInput
          disabled={isFollowUpStreaming}
          placeholder={
            isFollowUpStreaming
              ? "Falcon is investigating…"
              : "Ask Falcon a follow-up…"
          }
          onSubmit={runFollowUp}
        />
      )}
    </Stack>
  );
}
AnalyzeTab.propTypes = { error: PropTypes.object };
