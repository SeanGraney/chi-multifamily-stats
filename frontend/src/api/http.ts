/**
 * The two things every call in this SPA needs: a JSON round trip, and a
 * failure that says something.
 *
 * Small on purpose (D13 — no TanStack Query, no client library). What it
 * exists to stop is the pattern the views would otherwise repeat five times:
 * `res.ok` checked in some places and not others, and FastAPI's `{detail: ...}`
 * thrown away in favour of "Request failed". A 422 from `PUT /api/config` or
 * `POST /api/search/plan` carries the *reason* the input was refused, and that
 * reason is what the form has to show inline — dropping it would leave the user
 * looking at a red box that does not say which field is wrong.
 *
 * D5/D13: nothing here interprets a payload. It moves JSON.
 */

/** A non-2xx response, carrying whatever the server said about why. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * FastAPI's error body in the two shapes it actually arrives in: `{detail:
 * "..."}` from an `HTTPException`, and `{detail: [{loc, msg}, ...]}` from
 * request-model validation. Both are worth reading; a validation error names
 * the field, which is the whole point of a 422 to a form.
 */
function describe(status: number, body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((entry) => {
        const item = entry as { loc?: unknown[]; msg?: string };
        const field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : undefined;
        return field ? `${String(field)}: ${item.msg ?? ""}`.trim() : (item.msg ?? "");
      })
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  return `${fallback} (HTTP ${status})`;
}

async function parse<T>(response: Response, fallback: string): Promise<T> {
  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!response.ok) {
    throw new ApiError(response.status, describe(response.status, body, fallback));
  }
  return body as T;
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  return parse<T>(await fetch(path, { signal }), `GET ${path} failed`);
}

export async function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  return parse<T>(response, `POST ${path} failed`);
}

export async function putJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  return parse<T>(response, `PUT ${path} failed`);
}

/** True for the abort a cancelled in-flight request raises, which is a normal
 *  event (a newer keystroke won) and never something to show the user. */
export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}
