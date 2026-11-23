import 'dotenv/config';
import express, { type NextFunction, type Request, type Response } from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import {
  getHealthStatus,
  handleMentorRequest,
  handleRoadmapRequest,
} from './lib/careerService.ts';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PORT = Number(process.env.PORT) || 3000;

function sendJson(res: Response, status: number, payload: unknown): void {
  res
    .status(status)
    .setHeader('Content-Type', 'application/json; charset=utf-8')
    .setHeader('Cache-Control', 'no-store')
    .json(payload);
}

async function createServer() {
  const app = express();
  app.use(express.json({ limit: '1mb' }));

  app.use('/api', (_req: Request, res: Response, next: NextFunction) => {
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    next();
  });

  app.get('/api/health', async (req: Request, res: Response) => {
    const verifyLiveApi = req.query.verify === '1';
    const result = await getHealthStatus({ verifyLiveApi });
    sendJson(res, result.status, result.body);
  });

  app.post('/api/roadmap', async (req: Request, res: Response) => {
    const result = await handleRoadmapRequest(req.body);
    sendJson(res, result.status, result.body);
  });

  app.post('/api/mentor', async (req: Request, res: Response) => {
    const result = await handleMentorRequest(req.body);
    sendJson(res, result.status, result.body);
  });

  app.get('/download/ai-career-navigator-code.zip', (_req: Request, res: Response) => {
    const zipPath = path.resolve(__dirname, 'ai-career-navigator-code.zip');
    if (!fs.existsSync(zipPath)) {
      sendJson(res, 404, {
        success: false,
        error: 'Archive file not found.',
      });
      return;
    }
    res.download(zipPath, 'ai-career-navigator-code.zip');
  });

  // Catch-all for unmatched /api/* routes so Express never falls through to HTML
  app.use('/api', (_req: Request, res: Response) => {
    sendJson(res, 404, {
      success: false,
      error: 'The requested API endpoint does not exist.',
    });
  });

  // Global Express JSON error handler (catches malformed JSON bodies & unexpected errors)
  app.use((err: unknown, req: Request, res: Response, next: NextFunction) => {
    if (res.headersSent) {
      next(err);
      return;
    }
    if (req.path.startsWith('/api')) {
      const isSyntaxErr =
        err instanceof SyntaxError && 'status' in err && (err as { status?: number }).status === 400;
      sendJson(res, isSyntaxErr ? 400 : 500, {
        success: false,
        error: isSyntaxErr
          ? 'Malformed JSON in request body.'
          : 'An unexpected server error occurred. Please try again.',
      });
      return;
    }
    next(err);
  });

  if (process.env.NODE_ENV !== 'production') {
    const { createServer: createViteServer } = await import('vite');
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: 'spa',
    });
    app.use(vite.middlewares);
  } else {
    const distDir = path.resolve(__dirname, 'dist');
    app.use(express.static(distDir));
    app.get('*', (_req: Request, res: Response) => {
      res.sendFile(path.join(distDir, 'index.html'));
    });
  }

  app.listen(PORT, '0.0.0.0', () => {
    console.log(`AI Career Navigator server listening on http://0.0.0.0:${PORT}`);
  });
}

createServer().catch((err) => {
  console.error('Failed to start server:', err);
  process.exit(1);
});
