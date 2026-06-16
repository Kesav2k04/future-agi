import { useCallback, useEffect } from "react";
import { useAuthContext } from "src/auth/hooks";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useErrorFeedStore } from "./store";
import {
  hydrateFromCache,
  prewarmSocket,
  runFollowUp as engineRunFollowUp,
  startRun as engineStartRun,
} from "./clusterAnalyzeSocket";

// Socket engine lives at module scope — runs keep progressing even when
// the Analyze tab is unmounted.

export function useAnalyzeRunner(clusterId, error) {
  const { user } = useAuthContext();
  const { currentWorkspaceId } = useWorkspace();
  const clearAnalyzePendingStart = useErrorFeedStore(
    (s) => s.clearAnalyzePendingStart,
  );
  const pendingStart = useErrorFeedStore(
    (s) => !!s.analyzePendingStartByCluster[clusterId],
  );
  const hasThread = useErrorFeedStore(
    (s) => !!s.analyzeThreadsByCluster[clusterId],
  );

  // Prewarm socket so first analyze doesn't pay 20-30s cold-start cost.
  useEffect(() => {
    if (user?.accessToken) {
      prewarmSocket({
        token: user.accessToken,
        workspaceId: currentWorkspaceId,
      });
    }
  }, [user?.accessToken, currentWorkspaceId]);

  // Seed from cached synthesis on fresh load (no live thread).
  useEffect(() => {
    if (!clusterId || hasThread) return;
    hydrateFromCache({ clusterId, rca: error?.rca });
  }, [clusterId, hasThread, error?.rca]);

  const startRun = useCallback(() => {
    if (!clusterId) return;
    engineStartRun({
      clusterId,
      projectId: error?.projectId,
      token: user?.accessToken,
      workspaceId: currentWorkspaceId,
    });
  }, [clusterId, error?.projectId, user?.accessToken, currentWorkspaceId]);

  // Auto-fire when pending-start flag flips on.
  useEffect(() => {
    if (!clusterId || !pendingStart) return;
    clearAnalyzePendingStart(clusterId);
    startRun();
  }, [clusterId, pendingStart, clearAnalyzePendingStart, startRun]);

  return { startRun };
}

export function useFollowUpRunner(clusterId, error) {
  const { user } = useAuthContext();
  const { currentWorkspaceId } = useWorkspace();

  const runFollowUp = useCallback(
    (question) => {
      if (!clusterId) return;
      engineRunFollowUp({
        clusterId,
        question,
        projectId: error?.projectId,
        token: user?.accessToken,
        workspaceId: currentWorkspaceId,
      });
    },
    [clusterId, error?.projectId, user?.accessToken, currentWorkspaceId],
  );

  return { runFollowUp };
}
