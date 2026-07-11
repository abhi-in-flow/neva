/**
 * Build venue-TV ticker lines from a canonical MetricsResponse payload.
 *
 * Mirrors contracts/api_types.py MetricsResponse fields. Presentation only —
 * no game logic or metric computation.
 */

/**
 * @param {object|null|undefined} metrics - Parsed GET /api/metrics body.
 * @returns {string[]} Non-empty ticker lines for rotation.
 */
export function buildMetricsTickerLines(metrics) {
  if (!metrics) return [];
  const lines = [];
  if (typeof metrics.validated_pairs === 'number') {
    lines.push(`${metrics.validated_pairs} validated pairs collected`);
  }
  if (typeof metrics.training_eligible_pairs === 'number') {
    lines.push(`${metrics.training_eligible_pairs} training-eligible pairs`);
  }
  if (typeof metrics.language_count === 'number' && metrics.language_count > 0) {
    lines.push(`${metrics.language_count} languages spoken`);
  }
  if (Array.isArray(metrics.languages) && metrics.languages.length > 0) {
    lines.push(metrics.languages.join(' · '));
  }
  if (typeof metrics.cost_per_validated_sample_usd === 'number') {
    lines.push(`$${metrics.cost_per_validated_sample_usd.toFixed(3)} per validated sample`);
  }
  if (typeof metrics.gauntlet_pass_rate === 'number') {
    lines.push(`${Math.round(metrics.gauntlet_pass_rate * 100)}% gauntlet pass rate`);
  }
  if (typeof metrics.deck_images_per_minute === 'number') {
    lines.push(`${metrics.deck_images_per_minute.toFixed(1)} deck images/min`);
  }
  if (typeof metrics.deck_cost_per_image_usd === 'number') {
    lines.push(`$${metrics.deck_cost_per_image_usd.toFixed(4)} per deck image`);
  }
  return lines;
}
