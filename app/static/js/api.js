/**
 * Central API client for AI Agent Reliability Platform Dashboard
 */

const API = {
  baseUrl: window.location.origin,

  async request(path, options = {}) {
    const url = `${this.baseUrl}${path}`;
    const defaults = {
      headers: {
        'Content-Type': 'application/json',
      },
    };

    const config = { ...defaults, ...options };
    if (config.body && typeof config.body === 'object') {
      config.body = JSON.stringify(config.body);
    }

    try {
      const response = await fetch(url, config);
      if (!response.ok) {
        let errDetail = `HTTP ${response.status}`;
        try {
          const errJson = await response.json();
          errDetail = errJson.detail || JSON.stringify(errJson);
        } catch (e) {}
        throw new Error(errDetail);
      }
      return await response.json();
    } catch (err) {
      console.error(`API Error [${path}]:`, err);
      throw err;
    }
  },

  // REST shortcuts
  get(path) { return this.request(path, { method: 'GET' }); },
  post(path, body) { return this.request(path, { method: 'POST', body }); },
  patch(path, body) { return this.request(path, { method: 'PATCH', body }); },
  delete(path) { return this.request(path, { method: 'DELETE' }); },

  // SSE Stream listener
  subscribeStream(runId, onEvent, onError) {
    const eventSource = new EventSource(`${this.baseUrl}/evaluations/stream/${runId}`);

    eventSource.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        onEvent(data);
        if (data.event === 'run_complete' || data.event === 'error' || data.event === 'completed' || data.event === 'failed') {
          eventSource.close();
        }
      } catch (err) {
        console.error('Failed to parse SSE event:', err);
      }
    };

    eventSource.onerror = (err) => {
      console.error('SSE connection error:', err);
      if (onError) onError(err);
      eventSource.close();
    };

    return eventSource;
  }
};
