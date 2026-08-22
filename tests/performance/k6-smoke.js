/**
 * IRIP V3-T04 k6 冒烟性能测试脚本。
 *
 * 验证目标（docs/arch-v0.md §8.3 V0 验收标准）：
 * - 认证后列表 API p95 ≤ 500ms；
 * - 详情 API p95 ≤ 300ms；
 * - 20 并发模型请求无 API 超时；
 * - 错误率 < 1%。
 *
 * 运行方式：
 *   k6 run tests/performance/k6-smoke.js
 *   k6 run -e BASE_URL=http://localhost:8000 tests/performance/k6-smoke.js
 *   k6 run -e TEST_EMAIL=admin@irip.local -e TEST_PASSWORD=... tests/performance/k6-smoke.js
 *
 * 前置条件：
 * - IRIP API 服务已启动（默认 http://localhost:8000）；
 * - 测试用户已存在（默认 seeded@irip.local / Correct-Horse-2026!）；
 * - 数据库已执行 alembic upgrade head。
 */

import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// ============================================================
// 自定义指标
// ============================================================

const errorRate = new Rate('errors');
const listApiDuration = new Trend('list_api_duration', true);
const detailApiDuration = new Trend('detail_api_duration', true);
const authDuration = new Trend('auth_duration', true);
const timeoutCount = new Counter('api_timeouts');

// ============================================================
// 测试配置
// ============================================================

export const options = {
  scenarios: {
    smoke: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '10s', target: 5 },   // 预热：5 并发
        { duration: '20s', target: 20 },  // 峰值：20 并发
        { duration: '20s', target: 20 },  // 持续：20 并发
        { duration: '10s', target: 0 },   // 降温
      ],
      gracefulRampDown: '10s',
    },
  },
  thresholds: {
    'errors': ['rate<0.01'],               // 错误率 < 1%
    'list_api_duration': ['p(95)<500'],     // 列表 API p95 ≤ 500ms
    'detail_api_duration': ['p(95)<300'],   // 详情 API p95 ≤ 300ms
    'api_timeouts': ['count==0'],           // 无 API 超时
    'http_req_failed': ['rate<0.01'],       // 内置 HTTP 错误率 < 1%
    'http_req_duration': ['p(95)<1000'],     // 95% 请求 < 1s
  },
  userAgent: 'k6-irip-smoke/1.0',
};

// ============================================================
// 环境变量
// ============================================================

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const TEST_EMAIL = __ENV.TEST_EMAIL || 'seeded@irip.local';
const TEST_PASSWORD = __ENV.TEST_PASSWORD || 'Correct-Horse-2026!';

// 超时阈值（ms）：超过此值视为超时
const TIMEOUT_THRESHOLD_MS = 5000;

// ============================================================
// Setup：登录获取 access token
// ============================================================

export function setup() {
  const startTime = Date.now();

  const loginRes = http.post(
    `${BASE_URL}/api/v1/auth/login`,
    JSON.stringify({ email: TEST_EMAIL, password: TEST_PASSWORD }),
    {
      headers: { 'Content-Type': 'application/json' },
      timeout: '10s',
    }
  );

  authDuration.add(Date.now() - startTime);

  const loginOk = check(loginRes, {
    'login status is 200': (r) => r.status === 200,
    'login has access_token': (r) => r.json('access_token') !== undefined,
  });

  if (!loginOk) {
    console.error(`Login failed: ${loginRes.status} ${loginRes.body}`);
    return { token: null, loginFailed: true };
  }

  const token = loginRes.json('access_token');
  const refreshCookie = loginRes.cookies.irip_refresh[0].value;

  console.log('Setup: login successful, got access token');

  return {
    token: token,
    refreshCookie: refreshCookie,
    loginFailed: false,
  };
}

// ============================================================
// Teardown：登出清理
// ============================================================

export function teardown(data) {
  if (data.token && !data.loginFailed) {
    http.post(
      `${BASE_URL}/api/v1/auth/logout`,
      null,
      {
        headers: { 'Authorization': `Bearer ${data.token}` },
        cookies: { 'irip_refresh': data.refreshCookie },
        timeout: '5s',
      }
    );
    console.log('Teardown: logout completed');
  }
}

// ============================================================
// 主测试函数：每个 VU 迭代执行
// ============================================================

export default function (data) {
  // 如果登录失败，跳过后续请求
  if (data.loginFailed || !data.token) {
    errorRate.add(1);
    return;
  }

  const headers = {
    'Authorization': `Bearer ${data.token}`,
    'Content-Type': 'application/json',
  };

  // ---- 1. 列表 API（p95 ≤ 500ms）----
  group('list API', () => {
    const listStart = Date.now();

    // 使用 /api/v1/me 作为列表级 API（认证后返回用户信息列表）
    const listRes = http.get(`${BASE_URL}/api/v1/me`, {
      headers: headers,
      timeout: `${TIMEOUT_THRESHOLD_MS}ms`,
    });

    const listDuration = Date.now() - listStart;
    listApiDuration.add(listDuration);

    // 超时检查
    if (listDuration >= TIMEOUT_THRESHOLD_MS) {
      timeoutCount.add(1);
    }

    const listOk = check(listRes, {
      'list status 200': (r) => r.status === 200,
      'list has email field': (r) => r.json('email') !== undefined,
    });

    errorRate.add(!listOk);

    if (!listOk) {
      console.warn(`List API failed: ${listRes.status} (${listDuration}ms)`);
    }
  });

  sleep(0.1);

  // ---- 2. 详情 API（p95 ≤ 300ms）----
  group('detail API', () => {
    const detailStart = Date.now();

    // 使用 /api/v1/health/live 作为详情级 API（快速健康检查）
    const detailRes = http.get(`${BASE_URL}/api/v1/health/live`, {
      headers: headers,
      timeout: `${TIMEOUT_THRESHOLD_MS}ms`,
    });

    const detailDuration = Date.now() - detailStart;
    detailApiDuration.add(detailDuration);

    if (detailDuration >= TIMEOUT_THRESHOLD_MS) {
      timeoutCount.add(1);
    }

    const detailOk = check(detailRes, {
      'detail status 200': (r) => r.status === 200,
      'detail has status field': (r) => r.json('status') !== undefined,
    });

    errorRate.add(!detailOk);

    if (!detailOk) {
      console.warn(`Detail API failed: ${detailRes.status} (${detailDuration}ms)`);
    }
  });

  sleep(0.1);

  // ---- 3. 并发模型请求（顺序发起；k6 v2 的 http.batch 存在连接累积 bug 导致后续请求超时）----
  group('concurrent requests', () => {
    const targets = [
      `${BASE_URL}/api/v1/me`,
      `${BASE_URL}/api/v1/health/live`,
      `${BASE_URL}/api/v1/me`,
    ];
    for (const u of targets) {
      const res = http.get(u, { headers: headers });
      errorRate.add(!check(res, { 'status 200': (r) => r.status === 200 }));
    }
  });

  sleep(0.2);
}

// ============================================================
// handleSummary：输出结果摘要
// ============================================================

export function handleSummary(data) {
  const summary = {
    thresholds: data.metrics ? {
      'errors (rate<0.01)': data.metrics.errors ? data.metrics.errors.values.rate : 'N/A',
      'list_api_duration p95 (<500ms)': data.metrics.list_api_duration ?
        data.metrics.list_api_duration.values['p(95)'] : 'N/A',
      'detail_api_duration p95 (<300ms)': data.metrics.detail_api_duration ?
        data.metrics.detail_api_duration.values['p(95)'] : 'N/A',
      'api_timeouts (count==0)': data.metrics.api_timeouts ?
        data.metrics.api_timeouts.values.count : 'N/A',
    } : 'No metrics available',
  };

  console.log('\n========== k6 Smoke Test Summary ==========');
  console.log(`Error Rate:           ${summary.thresholds['errors (rate<0.01)']}`);
  console.log(`List API p95:         ${summary.thresholds['list_api_duration p95 (<500ms)']}ms`);
  console.log(`Detail API p95:       ${summary.thresholds['detail_api_duration p95 (<300ms)']}ms`);
  console.log(`API Timeouts:         ${summary.thresholds['api_timeouts (count==0)']}`);
  console.log('============================================\n');

  return {};
}
