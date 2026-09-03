/**
 * Safe JSON fetch helper — prevents "Unexpected token" errors when server
 * returns non-JSON responses (e.g. plain text "Internal Server Error").
 */
export async function safeFetch(url, options = {}) {
  const res = await fetch(url, options);
  const contentType = res.headers.get('content-type') || '';

  if (!res.ok) {
    let errMsg = `Server error (${res.status})`;
    if (contentType.includes('application/json')) {
      try {
        const errData = await res.json();
        errMsg = errData.detail || JSON.stringify(errData);
      } catch { /* ignore */ }
    } else {
      try {
        const txt = await res.text();
        if (txt) errMsg = txt;
      } catch { /* ignore */ }
    }
    throw new Error(errMsg);
  }

  if (contentType.includes('application/json')) {
    return res.json();
  }

  // Fallback: try JSON parse, otherwise return text
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return { _raw: text };
  }
}
