import { useEffect, useState } from "react";
import type { components } from "../api/schema";
import { getJson, isAbort, putJson } from "../api/http";

type ApiKeyStatus = components["schemas"]["ApiKeyStatus"];

/**
 * Settings — API-key entry (F0-S4's "entered in Settings" half, PM ruling 2).
 *
 * F0-S4 built and verified the storage half (`storage/secrets.py`) and the
 * sending half (`X-Api-Key`); the entry half had no surface to verify against,
 * so QA correctly refused to close it. Without this screen the only way to
 * configure a key is hand-editing `~/.rentcomp/secrets.json` or exporting an
 * env var.
 *
 * [SECURITY INVARIANT] THE KEY IS WRITE-ONLY FROM THIS SCREEN'S POINT OF VIEW
 * ---------------------------------------------------------------------------
 * `GET /api/settings/api-key` returns a boolean and nothing else, so there is
 * no stored value to prefill even if this component wanted one — and the input
 * is cleared the moment a save succeeds, so the credential does not linger in
 * the DOM afterwards. That matters concretely, not theoretically: the DOM ends
 * up in devtools, in HAR exports, and in this repo's retained Playwright
 * failure traces.
 *
 * The §2.3 config knobs also now have a route (`GET`/`PUT /api/config`), but a
 * UI for them is not this story — the PM scoped item 1 as the route and item 2
 * as "the Settings API-key entry UI and route". Flagged in the handoff so it
 * can be queued rather than quietly assumed done.
 */
export default function Settings() {
  const [key, setKey] = useState("");
  const [status, setStatus] = useState<ApiKeyStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getJson<ApiKeyStatus>("/api/settings/api-key", controller.signal)
      .then(setStatus)
      .catch((err: unknown) => {
        if (isAbort(err)) return;
        setError(err instanceof Error ? err.message : String(err));
      });
    return () => controller.abort();
  }, []);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      setStatus(await putJson<ApiKeyStatus>("/api/settings/api-key", { api_key: key }));
      // Cleared on success, never on failure — a rejected key the user can
      // still see is a key they can fix.
      setKey("");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="p-6 max-w-[560px]">
      <h1 className="text-amber text-xl">Settings</h1>

      <h2 className="mt-4 text-sm font-bold text-white">RentCast API key</h2>
      <p className="mt-1 text-xs text-grey">
        Stored locally at <code>~/.rentcomp/secrets.json</code> (mode 0600) and sent only as the{" "}
        <code>X-Api-Key</code> header. It is never displayed again after saving, and no endpoint
        returns it.
      </p>

      <label htmlFor="settings-api-key" className="mt-3 block text-xs uppercase text-grey">
        API key
      </label>
      <input
        id="settings-api-key"
        data-testid="settings-api-key"
        type="password"
        autoComplete="off"
        value={key}
        onChange={(event) => setKey(event.target.value)}
        className="w-full border border-grey bg-transparent px-2 py-1 text-sm"
      />

      <button
        type="button"
        data-testid="settings-api-key-save"
        onClick={save}
        disabled={saving}
        className="mt-3 border border-green px-4 py-1 text-sm text-green"
      >
        {saving ? "Saving…" : "Save"}
      </button>

      <p data-testid="settings-api-key-status" className="mt-3 text-sm">
        {status === null ? (
          <span className="text-grey">Checking…</span>
        ) : status.configured ? (
          <span className="text-green">API key configured.</span>
        ) : (
          <span className="text-grey">
            No API key configured — live pulls are unavailable until one is saved.
          </span>
        )}
      </p>

      {error && (
        <p data-testid="settings-api-key-error" role="alert" className="mt-2 text-sm text-rust">
          {error}
        </p>
      )}

      <p className="mt-6 text-xs text-grey">
        A key alone does not enable live calls: <code>RENTCOMP_LIVE=1</code> must be set as well
        (D17). Fixture mode is the default everywhere, deliberately.
      </p>
    </section>
  );
}
