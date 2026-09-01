import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function setupMocks(page) {
  // Stash static plugin assets
  page.route('**/plugin*/**/main.js*', async (route) => {
    const filePath = path.resolve('plugin/main.js');
    return route.fulfill({
      status: 200,
      contentType: 'application/javascript',
      body: fs.readFileSync(filePath, 'utf8'),
    });
  });

  page.route('**/plugin*/**/style.css*', async (route) => {
    const filePath = path.resolve('plugin/style.css');
    return route.fulfill({
      status: 200,
      contentType: 'text/css',
      body: fs.readFileSync(filePath, 'utf8'),
    });
  });

  page.route('**/plugin*/**/review.html*', async (route) => {
    const filePath = path.resolve('plugin/assets/review.html');
    return route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: fs.readFileSync(filePath, 'utf8'),
    });
  });

  page.route('**/*review.js*', async (route) => {
    const filePath = path.resolve('plugin/assets/review.js');
    return route.fulfill({
      status: 200,
      contentType: 'application/javascript',
      body: fs.readFileSync(filePath, 'utf8'),
    });
  });

  // Build pre-flight (todo 7 of staged-wizard-inplace-seed): the authoritative
  // on-disk probe must succeed or the build is blocked fail-closed.
  page.route('**/api/fs/exists*', async (route) => {
    const postData = JSON.parse(route.request().postData() || '{}');
    const results = {};
    for (const p of postData.paths || []) results[p] = true;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ results }),
    });
  });
}

test.describe('Task Failure Sentinel Detection & False-Success Protection', () => {

  test('1. buildMegapack() and probeFiles() generate unique run_id nonces in mutation payloads', async ({ page }) => {
    setupMocks(page);

    let probePayload = null;
    let buildPayload = null;

    await page.route('**/graphql', async (route) => {
      const postData = JSON.parse(route.request().postData() || '{}');
      const query = postData.query || '';

      if (query.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  {
                    id: 101,
                    title: 'Nonce Test Scene',
                    date: '2026-08-28',
                    paths: {},
                    files: [{ id: 1001, path: 'C:/Packs/My Awesome Megapack/s101.mp4', size: 1048576 }],
                    performers: [],
                    tags: [],
                  },
                ],
              },
            },
          }),
        });
      }

      if (query.includes('RunProbe')) {
        const payloadArg = postData.variables?.args?.find((a) => a.key === 'payload');
        if (payloadArg) {
          probePayload = JSON.parse(payloadArg.value.str);
        }
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { runPluginTask: 'job-probe-nonce-1' } }),
        });
      }

      if (query.includes('RunBuild')) {
        const payloadArg = postData.variables?.args?.find((a) => a.key === 'payload');
        if (payloadArg) {
          buildPayload = JSON.parse(payloadArg.value.str);
        }
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { runPluginTask: 'job-build-nonce-1' } }),
        });
      }

      return route.continue();
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=101&mode=megapack');
    await page.locator("#output-dir").fill("C:\\Packs");
    await expect(page.locator('#loading-state')).toBeHidden({ timeout: 5000 });

    // Trigger Probe
    await page.locator('#btn-probe').click();
    await expect.poll(() => probePayload).not.toBeNull();
    expect(typeof probePayload.run_id).toBe('string');
    expect(probePayload.run_id.length).toBeGreaterThan(5);

    // Trigger Build
    await page.locator('#btn-build').click();
    await expect.poll(() => buildPayload).not.toBeNull();
    expect(typeof buildPayload.run_id).toBe('string');
    expect(buildPayload.run_id.length).toBeGreaterThan(5);

    // Nonces must be unique per dispatch
    expect(buildPayload.run_id).not.toBe(probePayload.run_id);
  });

  test('2. WebSocket opens loggingSubscribe with subprotocol graphql-transport-ws and buffers live log stream', async ({ page }) => {
    setupMocks(page);

    await page.route('**/graphql', async (route) => {
      const postData = JSON.parse(route.request().postData() || '{}');
      const query = postData.query || '';

      if (query.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  {
                    id: 201,
                    title: 'WS Test Scene',
                    paths: {},
                    files: [{ id: 2001, path: 'C:/Packs/My Awesome Megapack/s201.mp4' }],
                    performers: [],
                    tags: [],
                  },
                ],
              },
            },
          }),
        });
      }

      if (query.includes('RunBuild')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { runPluginTask: 'job-ws-sub-1' } }),
        });
      }

      return route.continue();
    });

    // Mock WebSocket in page
    await page.addInitScript(() => {
      class MockWS {
        constructor(url, protocols) {
          this.url = url;
          this.protocols = protocols;
          window.__mockWsProtocols = protocols;
          window.__mockWsSent = [];
          window.__mockWsInstance = this;
          setTimeout(() => {
            if (this.onopen) this.onopen();
          }, 10);
        }
        send(data) {
          window.__mockWsSent.push(JSON.parse(data));
        }
        close() {
          if (this.onclose) this.onclose();
        }
      }
      window.WebSocket = MockWS;
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=201&mode=megapack');
    await page.locator("#output-dir").fill("C:\\Packs");
    await page.locator('#btn-build').click();

    // Verify subprotocol
    await expect.poll(() => page.evaluate(() => window.__mockWsProtocols)).toBe('graphql-transport-ws');

    // Verify loggingSubscribe subscription was sent
    await expect.poll(() =>
      page.evaluate(() => {
        const sent = window.__mockWsSent || [];
        return sent.some(
          (msg) =>
            msg.type === 'subscribe' &&
            msg.payload?.query &&
            msg.payload.query.includes('loggingSubscribe')
        );
      })
    ).toBe(true);
  });

  test('3. Live failure detection via loggingSubscribe suppresses handoff panel and displays error status', async ({ page }) => {
    setupMocks(page);

    let dispatchedRunId = null;

    await page.route('**/graphql', async (route) => {
      const postData = JSON.parse(route.request().postData() || '{}');
      const query = postData.query || '';

      if (query.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  {
                    id: 301,
                    title: 'Failure Test Scene',
                    paths: {},
                    files: [{ id: 3001, path: 'C:/Packs/My Awesome Megapack/s301.mp4' }],
                    performers: [],
                    tags: [],
                  },
                ],
              },
            },
          }),
        });
      }

      if (query.includes('RunBuild')) {
        const payloadArg = postData.variables?.args?.find((a) => a.key === 'payload');
        if (payloadArg) {
          dispatchedRunId = JSON.parse(payloadArg.value.str).run_id;
        }
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { runPluginTask: 'job-fail-live-1' } }),
        });
      }

      return route.continue();
    });

    await page.addInitScript(() => {
      class MockWS {
        constructor(url, protocols) {
          window.__mockWsInstance = this;
          setTimeout(() => {
            if (this.onopen) this.onopen();
          }, 10);
        }
        send() {}
        close() {}
      }
      window.WebSocket = MockWS;
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=301&mode=megapack');
    await page.locator("#output-dir").fill("C:\\Packs");
    await page.locator('#btn-build').click();

    await expect.poll(() => dispatchedRunId).not.toBeNull();
    await expect.poll(() => page.evaluate(() => Boolean(window.__mockWsInstance))).toBe(true);

    // Deliver stderr log entry via WebSocket loggingSubscribe stream (with Stash prefix)
    await page.evaluate((runId) => {
      const ws = window.__mockWsInstance;
      ws.onmessage({
        data: JSON.stringify({
          type: 'next',
          payload: {
            data: {
              // Stash's real schema is `loggingSubscribe: [LogEntry!]!` --
              // a batch per message, not a bare object.
              loggingSubscribe: [{
                time: '2026-08-28T01:00:00Z',
                level: 'Error',
                message: `[Plugin / DeepSeek Megapack Generator] DEEPSEEK_TASK_FAILED ${runId}: Consolidation failed: insufficient free space on C:\\Packs`,
              }],
            },
          },
        }),
      });
    }, dispatchedRunId);

    // Stash reports FINISHED with findJob.error = null (the exact bug condition)
    await page.evaluate(() => {
      const ws = window.__mockWsInstance;
      ws.onmessage({
        data: JSON.stringify({
          type: 'next',
          payload: {
            data: {
              jobsSubscribe: {
                job: {
                  id: 'job-fail-live-1',
                  status: 'FINISHED',
                  progress: 1.0,
                  error: null,
                },
              },
            },
          },
        }),
      });
    });

    // Verify error is displayed in UI status bar
    const statusText = page.locator('#status-text');
    await expect(statusText).toBeVisible();
    await expect(statusText).toContainText('Consolidation failed: insufficient free space on C:\\Packs');

    // Verify handoff panel / completion UI is SUPPRESSED
    const summaryBox = page.locator('#artifact-summary');
    await expect(summaryBox).toBeHidden();
  });

  test('4. Exact nonce isolation: Unrelated run_id errors from concurrent tasks are ignored and do NOT cause false failure', async ({ page }) => {
    setupMocks(page);

    await page.route('**/graphql', async (route) => {
      const postData = JSON.parse(route.request().postData() || '{}');
      const query = postData.query || '';

      if (query.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  {
                    id: 401,
                    title: 'Isolation Scene',
                    paths: {},
                    files: [{ id: 4001, path: 'C:/Packs/My Awesome Megapack/s401.mp4' }],
                    performers: [],
                    tags: [],
                  },
                ],
              },
            },
          }),
        });
      }

      if (query.includes('RunBuild')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { runPluginTask: 'job-iso-1' } }),
        });
      }

      return route.continue();
    });

    await page.addInitScript(() => {
      class MockWS {
        constructor(url, protocols) {
          window.__mockWsInstance = this;
          setTimeout(() => {
            if (this.onopen) this.onopen();
          }, 10);
        }
        send() {}
        close() {}
      }
      window.WebSocket = MockWS;
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=401&mode=megapack');
    await page.locator("#output-dir").fill("C:\\Packs");
    await page.locator('#btn-build').click();
    await expect.poll(() => page.evaluate(() => Boolean(window.__mockWsInstance))).toBe(true);

    // Deliver an error log belonging to a DIFFERENT concurrent task / run_id
    await page.evaluate(() => {
      const ws = window.__mockWsInstance;
      ws.onmessage({
        data: JSON.stringify({
          type: 'next',
          payload: {
            data: {
              // Stash's real schema is `loggingSubscribe: [LogEntry!]!` --
              // a batch per message, not a bare object.
              loggingSubscribe: [{
                time: '2026-08-28T01:00:00Z',
                level: 'Error',
                message: `[Plugin / DeepSeek Megapack Generator] DEEPSEEK_TASK_FAILED concurrent-other-nonce-888: Some background error`,
              }],
            },
          },
        }),
      });
    });

    // Deliver FINISHED status for our job
    await page.evaluate(() => {
      const ws = window.__mockWsInstance;
      ws.onmessage({
        data: JSON.stringify({
          type: 'next',
          payload: {
            data: {
              jobsSubscribe: {
                job: {
                  id: 'job-iso-1',
                  status: 'FINISHED',
                  progress: 1.0,
                  error: null,
                },
              },
            },
          },
        }),
      });
    });

    // Job must succeed cleanly because error belonged to a different nonce
    await expect(page.locator('#status-text')).toContainText('completed successfully!');
    await expect(page.locator('#artifact-summary')).toBeVisible();
  });

  test('5. Case sensitivity and enum matching: findFailureSentinel correctly handles Stash mixed-case LogLevel enum', async ({ page }) => {
    setupMocks(page);
    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=1');

    const result = await page.evaluate(() => {
      const runId = 'test-case-nonce';
      const errorMsg = 'Critical filesystem error';

      const errorEntry = {
        level: 'Error', // Stash LogLevel enum value
        message: `[Plugin / DeepSeek Megapack Generator] DEEPSEEK_TASK_FAILED ${runId}: ${errorMsg}`,
      };

      const infoEntry = {
        level: 'Info',
        message: `[Plugin / DeepSeek Megapack Generator] DEEPSEEK_TASK_FAILED ${runId}: ${errorMsg}`,
      };

      const warningEntry = {
        level: 'Warning',
        message: `[Plugin / DeepSeek Megapack Generator] DEEPSEEK_TASK_FAILED ${runId}: ${errorMsg}`,
      };

      return {
        matchedError: window.findFailureSentinel([errorEntry], runId),
        matchedInfo: window.findFailureSentinel([infoEntry], runId),
        matchedWarning: window.findFailureSentinel([warningEntry], runId),
        emptyLogs: window.findFailureSentinel([], runId),
        nullLogs: window.findFailureSentinel(null, runId),
      };
    });

    expect(result.matchedError).toBe('Critical filesystem error');
    expect(result.matchedInfo).toBeNull();
    expect(result.matchedWarning).toBeNull();
    expect(result.emptyLogs).toBeNull();
    expect(result.nullLogs).toBeNull();
  });

  test('6. Fallback one-shot logs query detects failure when WebSocket stream was unavailable', async ({ page }) => {
    setupMocks(page);

    let dispatchedRunId = null;

    await page.route('**/graphql', async (route) => {
      const postData = JSON.parse(route.request().postData() || '{}');
      const query = postData.query || '';

      if (query.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  {
                    id: 601,
                    title: 'Fallback Scene',
                    paths: {},
                    files: [{ id: 6001, path: 'C:/Packs/My Awesome Megapack/s601.mp4' }],
                    performers: [],
                    tags: [],
                  },
                ],
              },
            },
          }),
        });
      }

      if (query.includes('RunBuild')) {
        const payloadArg = postData.variables?.args?.find((a) => a.key === 'payload');
        if (payloadArg) {
          dispatchedRunId = JSON.parse(payloadArg.value.str).run_id;
        }
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { runPluginTask: 'job-fallback-601' } }),
        });
      }

      if (query.includes('FindJob') || query.includes('findJob')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              findJob: {
                id: 'job-fallback-601',
                status: 'FINISHED',
                progress: 1.0,
                error: null,
              },
            },
          }),
        });
      }

      if (query.includes('Logs') || query.includes('logs')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              logs: [
                {
                  time: '2026-08-28T01:00:00Z',
                  level: 'Error',
                  message: `[Plugin / DeepSeek Megapack Generator] DEEPSEEK_TASK_FAILED ${dispatchedRunId}: Missing consolidated media file on disk`,
                },
              ],
            },
          }),
        });
      }

      return route.continue();
    });

    // Mock WebSocket failure to force HTTP polling + logs query fallback
    await page.addInitScript(() => {
      class FailingWebSocket {
        constructor() {
          setTimeout(() => {
            if (this.onerror) this.onerror(new Event('error'));
          }, 20);
        }
        send() {}
        close() {}
      }
      window.WebSocket = FailingWebSocket;
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=601&mode=megapack');
    await page.locator("#output-dir").fill("C:\\Packs");
    await page.locator('#btn-build').click();

    // Verify error was detected from fallback logs query
    const statusText = page.locator('#status-text');
    await expect(statusText).toContainText('Missing consolidated media file on disk', { timeout: 8000 });

    // Verify handoff panel is suppressed
    await expect(page.locator('#artifact-summary')).toBeHidden();
  });

  test('7. Fallback failure warning: when both WebSocket and logs query fail, UI displays informative status warning', async ({ page }) => {
    setupMocks(page);

    await page.route('**/graphql', async (route) => {
      const postData = JSON.parse(route.request().postData() || '{}');
      const query = postData.query || '';

      if (query.includes('FindScenes')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              findScenes: {
                scenes: [
                  {
                    id: 701,
                    title: 'Warning Scene',
                    paths: {},
                    files: [{ id: 7001, path: 'C:/Packs/My Awesome Megapack/s701.mp4' }],
                    performers: [],
                    tags: [],
                  },
                ],
              },
            },
          }),
        });
      }

      if (query.includes('RunBuild')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: { runPluginTask: 'job-warn-701' } }),
        });
      }

      if (query.includes('FindJob') || query.includes('findJob')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              findJob: {
                id: 'job-warn-701',
                status: 'FINISHED',
                progress: 1.0,
                error: null,
              },
            },
          }),
        });
      }

      if (query.includes('Logs') || query.includes('logs')) {
        // Return server error for logs query
        return route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ errors: [{ message: 'Logs ring buffer corrupted' }] }),
        });
      }

      return route.continue();
    });

    // Mock WebSocket failure
    await page.addInitScript(() => {
      class FailingWebSocket {
        constructor() {
          setTimeout(() => {
            if (this.onerror) this.onerror(new Event('error'));
          }, 20);
        }
        send() {}
        close() {}
      }
      window.WebSocket = FailingWebSocket;
    });

    await page.goto('http://localhost:9999/plugins/empornium-megapack/review.html?scenes=701&mode=megapack');
    await page.locator("#output-dir").fill("C:\\Packs");
    await page.locator('#btn-build').click();

    // Verify warning status is displayed
    const statusText = page.locator('#status-text');
    await expect(statusText).toContainText('log verification failed', { timeout: 8000 });

    // Verify clean success handoff panel was NOT rendered
    await expect(page.locator('#artifact-summary')).toBeHidden();
  });

});
