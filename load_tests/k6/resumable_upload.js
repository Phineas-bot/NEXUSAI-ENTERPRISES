import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  vus: __ENV.VUS ? parseInt(__ENV.VUS, 10) : 50,
  duration: __ENV.DURATION || '5m',
  thresholds: {
    http_req_duration: ['p(95)<2000'],
    http_req_failed: ['rate<0.01'],
  },
};

const restBase = __ENV.CLOUDSIM_REST_BASE || 'http://localhost:8000';
const authHeader = __ENV.AUTH_TOKEN ? { Authorization: `Bearer ${__ENV.AUTH_TOKEN}` } : {};

export default function () {
  // 1. Initiate upload session
  const initPayload = JSON.stringify({
    parent_id: 'root',
    size_bytes: 10 * 1024 * 1024,
    md5: 'stub-md5',
  });
  const initRes = http.post(`${restBase}/v1/uploads:sessions`, initPayload, {
    headers: { 'Content-Type': 'application/json', ...authHeader },
  });
  check(initRes, { 'session created': (r) => r.status === 200 || r.status === 201 });
  if (initRes.status >= 400) {
    sleep(1);
    return;
  }

  const session = initRes.json();
  const chunk = 'x'.repeat(1024 * 256); // 256 KB placeholder chunk
  session.upload_urls?.slice(0, 2).forEach((url, idx) => {
    const chunkRes = http.put(url, chunk, {
      headers: {
        'Content-Type': 'application/octet-stream',
        'Content-Range': `bytes ${idx * chunk.length}-${(idx + 1) * chunk.length - 1}`,
        'Chunk-Id': `${idx}`,
        'Session-Id': session.session_id,
        ...authHeader,
      },
    });
    check(chunkRes, { 'chunk uploaded': (r) => r.status < 400 });
  });

  // 3. Finalize
  const finalizeRes = http.patch(
    `${restBase}/v1/uploads/${session.session_id}:commit`,
    JSON.stringify({ checksum: 'stub-md5' }),
    { headers: { 'Content-Type': 'application/json', ...authHeader } }
  );
  check(finalizeRes, { 'finalized': (r) => r.status < 400 });
  sleep(1);
}
