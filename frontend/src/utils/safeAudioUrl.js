// ── Dead-provider-URL guards (mirrors VapiRecordingService) ──────────────
// These helpers prevent the UI from rendering <audio> elements that point
// at Vapi provider hosts.  After 2026-07-15 those URLs require Bearer auth
// which a browser <audio src> tag cannot attach, so they produce broken
// 401 responses or stop working entirely after 2026-07-25.

const DEAD_PROVIDER_HOSTS = ["storage.vapi.ai", "calllogs.vapi.ai"];
const S3_URL_MARKERS = ["amazonaws.com", "minio"];

/**
 * True when ``url`` points at a Vapi provider host that requires
 * Bearer auth  —  the browser cannot render such a URL in an
 * ``<audio>`` tag, so the UI must show a placeholder instead.
 *
 * @param {string|null|undefined} url
 * @returns {boolean}
 */
export function isDeadVapiUrl(url) {
  if (!url || typeof url !== "string") return false;
  const hasProviderHost = DEAD_PROVIDER_HOSTS.some((host) =>
    url.includes(host),
  );
  if (!hasProviderHost) return false;
  // An S3 / MinIO URL is always safe regardless of host hints in the path.
  return !S3_URL_MARKERS.some((marker) => url.includes(marker));
}

/**
 * Return ``url`` if it is safe to render in an ``<audio>`` element,
 * or ``null`` if the URL points at a dead provider host.
 *
 * Callers use the ``null`` return to show a "Recording processing…"
 * placeholder instead of a broken audio player.
 *
 * @param {string|null|undefined} url
 * @returns {string|null}
 */
export function safeAudioUrl(url) {
  if (!url || typeof url !== "string") return null;
  return isDeadVapiUrl(url) ? null : url;
}
