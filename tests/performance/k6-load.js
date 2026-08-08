/**
 * IRIP P2-I10: k6 渐进负载测试 + 浸泡测试。
 *
 * 三阶段测试：
 * 1. 正常负载（100 并发，5 分钟）— 验证基线性能
 * 2. 峰值负载（500 并发，3 分钟）— 验证容量上限
 * 3. 浸泡测试（100 并发，1 小时）— 验证内存泄漏/资源耗尽
 *
 * 运行方式：
 *   k6 run tests/performance/k6-load.js
 *   k6 run -e BASE_URL=http://localhost:8000 -e TEST_EMAIL=admin@irip.local -e TEST_PASSWORD=... tests/performance/k6-load.js
 *
 * CI 中运行（需要 k6 action）：
 *   k6 run --stage 100:5m,500:3m,100:1h tests/performance/k6-load.js
 */

import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// ============================================================
// 自定义指标
// ============================================================

const errorRate = new Rate('errors');
const apiDuration = new Trend('api_duration', true);
const authDuration = new Trend('auth_duration', true);
const timeoutCount = new Counter('api_timeouts');

// ============================================================
// 测试配置
// ============================================================

export const options = {
  scenarios: {
    // 阶段 1：正常负载（100 并发，5 分钟）
    normal_load: {
      executor: 'ramping-vus',
      startTime: '0s',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 100 },  // 预热
        { duration: '4m', target: 100 },   // 持续
        { duration: '30s', target: 0 },     // 降温
      ],
      gracefulRampDown: '30s',
      tags: { phase: 'normal' },
    },
    // 阶段 2：峰值负载（500 并发，3 分钟）
    peak_load: {
      executor: 'ramping-vus',
      startTime: '5m30s',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 500 },  // 预热
        { duration: '2m', target: 500 },    // 持续
        { duration: '30s', target: 0 },      // 降温
      ],
      gracefulRampDown: '30s',
      tags: { phase: 'peak' },
    },
    // 阶段 3：浸泡测试（100 并发，1 小时）
    soak: {
      executor: 'constant-vus',
      startTime: '9m',
      vus: 100,
      duration: '1h',
      tags: { phase: 'soak' },
    },
  },
  thresholds: {
    'errors': ['rate<0.05'],              // 错误率 < 5%（负载测试放宽）
    'api_duration': ['p(95)<1000'],       // p95 ≤ 1s（负载下放宽）
    'api_duration{phase:peak}': ['p(95)<2000'],  // 峰值 p95 ≤ 2s
    'api_timeouts': ['count<10'],          // 允许少量超时
    'http_req_failed': ['rate<0.05'],      // HTTP 失败率 < 5%
  },
  noConnectionRefuse: true,
  userAgent: 'k6-irip-load/1.0',
};

// ============================================================
// 环境变量
// ============================================================

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const TEST_EMAIL = __ENV.TEST_EMAIL || 'seeded@irip.local';
const TEST_PASSWORD = __ENV.TEST_PASSWORD || 'Correct-Horse-2026!';
const TIMEOUT_THRESHOLD_MS = 10000;

// ============================================================
// Setup
// ============================================================

export function setup() {
  const loginRes = http.post(
    `${BASE_URL}/api/v1/auth/login`,
    JSON.stringify({ email: TEST_EMAIL, password: TEST_PASSWORD }),
    { headers: { 'Content-Type': 'application/json' }, timeout: '10s' }
  );

  const ok = check(loginRes, {
    'login 200': (r) => r.status === 200,
    'login token': (r) => r.json('access_token') !== undefined,
  });

  if (!ok) {
    console.error(`Login failed: ${loginRes.status}`);
    return { token: null, loginFailed: true };
  }

  return {
    token: loginRes.json('access_token'),
    loginFailed: false,
  };
}

// ============================================================
// 主测试
// ============================================================

export default function (data) {
  if (data.loginFailed || !data.token) {
    errorRate.add(1);
    return;
  }

  const headers = {
    'Authorization': `Bearer ${data.token}`,
    'Content-Type': 'application/json',
  };

  group('API calls', () => {
    const start = Date.now();

    const res = http.get(`${BASE_URL}/api/v1/me`, {
      headers: headers,
      timeout: `${TIMEOUT_THRESHOLD_MS}ms`,
    });

    const duration = Date.now() - start;
    apiDuration.add(duration);

    if (duration >= TIMEOUT_THRESHOLD_MS) {
      timeoutCount.add(1);
    }

    const ok = check(res, {
      'status 200': (r) => r.status === 200,
    });

    errorRate.add(!ok);
  });

  sleep(0.5);
}

// ============================================================
// Summary
// ============================================================

export function handleSummary(data) {
  const m = data.metrics || {};
  console.log('\n========== k6 Load Test Summary ==========');
  console.log(`Error Rate:     ${m.errors ? m.errors.value : 'N/A'}`);
  console.log(`API p95:        ${m.api_duration ? m.api_duration['p(95)'] : 'N/A'}ms`);
  console.log(`API p99:        ${m.api_duration ? m.api_duration['p(99)'] : 'N/A'}ms`);
  console.log(`Timeouts:       ${m.api_timeouts ? m.api_timeouts.count : 'N/A'}`);
  console.log(`HTTP failed:    ${m.http_req_failed ? m.http_req_failed.value : 'N/A'}`);
  console.log(`Iterations:     ${m.iterations ? m.iterations.value : 'N/A'}`);
  console.log('============================================\n');

  return {};
}
