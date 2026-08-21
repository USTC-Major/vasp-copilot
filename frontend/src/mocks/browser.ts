// ============================================================
// MSW Browser Worker Setup
// ============================================================

import { setupWorker } from 'msw/browser';
import { handlers } from './handlers';

export const worker = setupWorker(...handlers);