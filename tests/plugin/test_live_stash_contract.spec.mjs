import { test, expect } from '@playwright/test';

const STASH_HTTP_URL = process.env.STASH_URL ?? 'http://localhost:9999';
const STASH_WS_URL = STASH_HTTP_URL.replace(/^http/, 'ws') + '/graphql';

test.describe("Live Stash Contract Verification (No Mocks)", () => {

  test.beforeEach(async ({ request }) => {
    try {
      const res = await request.get(`${STASH_HTTP_URL}/`, { timeout: 1500 });
      if (!res.ok() && res.status() >= 500) {
        test.skip(true, `Live Stash server at ${STASH_HTTP_URL} returned HTTP ${res.status()}`);
      }
    } catch (err) {
      test.skip(true, `Live Stash server is not reachable at ${STASH_HTTP_URL}`);
    }
  });

  test("a) GraphQL server accepts PluginArgInput shape with PluginValueInput { str }", async ({ request }) => {
    const query = `
      mutation RunProbeContractTest($plugin_id: ID!, $task_name: String!, $args: [PluginArgInput!]) {
        runPluginTask(
          plugin_id: $plugin_id,
          task_name: $task_name,
          args: $args
        )
      }
    `;
    const targetDir = "C:\\Packs";
    const filesPayload = [{ scene_id: 1, path: "C:\\Media\\test.mp4" }];
    const variables = {
      plugin_id: "__contract_test__",
      task_name: "ProbeFiles",
      args: [
        { key: "mode", value: { str: "probe" } },
        { key: "payload", value: { str: JSON.stringify({ target_dir: targetDir, files: filesPayload }) } }
      ]
    };

    const response = await request.post(`${STASH_HTTP_URL}/graphql`, {
      data: { query, variables }
    });
    expect(response.status()).toBe(200);

    const json = await response.json();
    if (json.errors) {
      for (const err of json.errors) {
        expect(err.message).not.toContain("must be a PluginValueInput");
        expect(err.message).not.toContain("GRAPHQL_VALIDATION_FAILED");
      }
    }
    expect(json).toHaveProperty("data");
  });

  test("b) WebSocket connection negotiates graphql-transport-ws and receives connection_ack", async ({ page }) => {
    await page.goto(`${STASH_HTTP_URL}/`);

    const wsResult = await page.evaluate(async (wsUrl) => {
      return new Promise((resolve) => {
        const timeout = setTimeout(() => {
          resolve({ error: "WebSocket connection timed out" });
        }, 8000);

        try {
          const ws = new WebSocket(wsUrl, "graphql-transport-ws");
          let negotiatedProtocol = null;
          let ackReceived = false;

          ws.onopen = () => {
            negotiatedProtocol = ws.protocol;
            ws.send(JSON.stringify({ type: "connection_init" }));
          };

          ws.onmessage = (event) => {
            try {
              const msg = JSON.parse(event.data);
              if (msg.type === "connection_ack") {
                ackReceived = true;
                clearTimeout(timeout);
                ws.close();
                resolve({
                  negotiatedProtocol,
                  ackReceived,
                  rawMessage: msg
                });
              }
            } catch (e) {
              clearTimeout(timeout);
              ws.close();
              resolve({ error: "Failed to parse message: " + e.message });
            }
          };

          ws.onerror = () => {
            clearTimeout(timeout);
            resolve({ error: "WebSocket error event fired" });
          };
        } catch (err) {
          clearTimeout(timeout);
          resolve({ error: "WebSocket constructor threw: " + err.message });
        }
      });
    }, STASH_WS_URL);

    expect(wsResult.error).toBeUndefined();
    expect(wsResult.negotiatedProtocol).toBe("graphql-transport-ws");
    expect(wsResult.ackReceived).toBe(true);
  });

  test("c) Static asset route serves review.html when installed (graceful skip otherwise)", async ({ request }) => {
    const response = await request.get(`${STASH_HTTP_URL}/plugin/deepseek-megapack/assets/review.html`);
    if (response.status() === 404) {
      test.skip(true, "Plugin is not installed in the live Stash instance yet.");
      return;
    }
    expect(response.status()).toBe(200);
    const text = await response.text();
    expect(text).toContain("DeepSeek Megapack Builder");
  });

});
