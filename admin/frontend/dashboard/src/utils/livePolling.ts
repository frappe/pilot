/** Live-metrics poll pacing. A line needs two points, and benches without a monitor
 * daemon (macOS, local dev) seed zero, so the chart has to accumulate them one poll
 * at a time - poll fast until it can draw, then settle to the steady cadence. */

export const LIVE_POLL_MS = 10000
export const LIVE_WARMUP_POLL_MS = 1000
const MIN_CHART_POINTS = 2

export const livePollDelayMs = ({ isLive, pointCount }) => {
  return isLive && pointCount < MIN_CHART_POINTS ? LIVE_WARMUP_POLL_MS : LIVE_POLL_MS
}
