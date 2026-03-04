import axios from 'axios';

/**
 * Base Axios instance for all MetaForge API requests.
 *
 * The Vite dev server proxies `/api` to the Gateway at `http://localhost:8000`,
 * so we only need a relative `baseURL` here.
 */
const apiClient = axios.create({
  baseURL: '/api/v1',
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 30_000,
});

export default apiClient;
