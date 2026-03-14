import { describe, it, expect, vi, beforeEach } from 'vitest';

// We need to mock import.meta.env before importing logger
vi.stubEnv('VITE_LOG_LEVEL', 'debug');

// Dynamic import so env stub takes effect
const { logger } = await import('../logger');

describe('logger', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('logs info messages via console.info', () => {
    const spy = vi.spyOn(console, 'info').mockImplementation(() => {});
    logger.info('test message', { key: 'value' });
    expect(spy).toHaveBeenCalledOnce();
    const parsed = JSON.parse(spy.mock.calls[0]![0] as string);
    expect(parsed.msg).toBe('test message');
    expect(parsed.key).toBe('value');
    expect(parsed.level).toBe('info');
    expect(parsed.ts).toBeDefined();
  });

  it('logs error messages via console.error', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    logger.error('bad thing');
    expect(spy).toHaveBeenCalledOnce();
    const parsed = JSON.parse(spy.mock.calls[0]![0] as string);
    expect(parsed.level).toBe('error');
  });

  it('logs warn messages via console.warn', () => {
    const spy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    logger.warn('warning');
    expect(spy).toHaveBeenCalledOnce();
  });

  it('logs debug messages via console.debug', () => {
    const spy = vi.spyOn(console, 'debug').mockImplementation(() => {});
    logger.debug('debug detail');
    expect(spy).toHaveBeenCalledOnce();
  });
});
