/**
 * Reuse an in-progress request so leaving and reopening a screen does not
 * cancel useful work and start the same provider request again.
 */
export function shareRequest<K, V>(
  requests: Map<K, Promise<V>>,
  key: K,
  load: () => Promise<V>,
): Promise<V> {
  const current = requests.get(key);
  if (current) return current;

  const request = load().finally(() => {
    if (requests.get(key) === request) requests.delete(key);
  });
  requests.set(key, request);
  return request;
}
