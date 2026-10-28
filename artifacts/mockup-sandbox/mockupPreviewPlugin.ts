import { mkdirSync, writeFileSync } from 'fs';
import { request as httpRequest, type IncomingMessage, type ServerResponse } from 'node:http';
import type { Socket } from 'node:net';
import path from 'path';
import chokidar, { type FSWatcher } from 'chokidar';
import glob from 'fast-glob';
import type { Plugin } from 'vite';

/**
 * Vite plugin that dynamically discovers mockup components and writes them to
 * a generated module under src/.generated. Uses fast-glob for initial
 * discovery and a dedicated chokidar watcher (with awaitWriteFinish) for
 * reliable file monitoring.
 *
 * Adapted from the component gallery plugin in pid2
 * (pkg/pid2/src/lib/vite/componentGalleryPlugin.ts).
 *
 * Generated module: "src/.generated/mockup-components.ts"
 * Exports: { modules: Record<string, () => Promise<Record<string, unknown>>> }
 *
 * When mockup files are added or removed, the generated module is rewritten.
 * Vite's normal file watching handles the update. If a relevant preview
 * request still returns 404 before the watcher settles, the dev server rescans
 * automatically. Individual file edits are handled by React Fast Refresh and
 * imported CSS HMR (no manual CSS invalidation needed).
 */

const MOCKUPS_DIR = 'src/components/mockups';
const GENERATED_MODULE = 'src/.generated/mockup-components.ts';

interface DiscoveredComponent {
  globKey: string;
  importPath: string;
}

function isMockupRequest(pathname: string, configuredBasePath: string): boolean {
  const basePath = configuredBasePath.startsWith('/')
    ? configuredBasePath
    : `/${configuredBasePath}`;
  const mountPath = basePath.replace(/\/+$/, '') || '/';
  return pathname === mountPath || pathname.startsWith(`${mountPath}/`);
}

function corralProxyOptions(request: IncomingMessage) {
  const target = new URL(process.env.CORRAL_PREVIEW_PROXY_TARGET || 'http://127.0.0.1:5000');
  return {
    hostname: target.hostname,
    port: Number(target.port || (target.protocol === 'https:' ? 443 : 80)),
    method: request.method,
    path: request.url || '/',
    headers: { ...request.headers, host: target.host },
  };
}

function proxyToCorral(request: IncomingMessage, response: ServerResponse): void {
  const upstream = httpRequest(corralProxyOptions(request), (upstreamResponse) => {
    response.statusCode = upstreamResponse.statusCode || 502;
    for (const [name, value] of Object.entries(upstreamResponse.headers)) {
      if (value !== undefined) response.setHeader(name, value);
    }
    upstreamResponse.pipe(response);
  });
  upstream.on('error', () => {
    if (!response.headersSent) {
      response.statusCode = 502;
      response.end('Corral preview is unavailable.');
    } else {
      response.destroy();
    }
  });
  request.pipe(upstream);
}

function proxyUpgradeToCorral(
  request: IncomingMessage,
  socket: Socket,
  head: Buffer,
): void {
  const upstream = httpRequest(corralProxyOptions(request));
  upstream.on('upgrade', (upstreamResponse, upstreamSocket, upstreamHead) => {
    const statusCode = upstreamResponse.statusCode || 502;
    socket.write(`HTTP/${upstreamResponse.httpVersion} ${statusCode} ${upstreamResponse.statusMessage || ''}\r\n`);
    for (const [name, value] of Object.entries(upstreamResponse.headers)) {
      if (Array.isArray(value)) {
        for (const item of value) socket.write(`${name}: ${item}\r\n`);
      } else if (value !== undefined) {
        socket.write(`${name}: ${value}\r\n`);
      }
    }
    socket.write('\r\n');
    if (upstreamHead.length) socket.write(upstreamHead);
    if (head.length) upstreamSocket.write(head);
    upstreamSocket.pipe(socket).pipe(upstreamSocket);
  });
  upstream.on('response', (upstreamResponse) => {
    socket.end(
      `HTTP/1.1 ${upstreamResponse.statusCode || 502} ${upstreamResponse.statusMessage || 'Bad Gateway'}\r\nConnection: close\r\n\r\n`,
    );
    upstreamResponse.resume();
  });
  upstream.on('error', () => socket.destroy());
  upstream.end();
}

export function mockupPreviewPlugin(): Plugin {
  let root = '';
  let currentSource = '';
  let watcher: FSWatcher | null = null;

  function getMockupsAbsDir(): string {
    return path.join(root, MOCKUPS_DIR);
  }

  function getGeneratedModuleAbsPath(): string {
    return path.join(root, GENERATED_MODULE);
  }

  function isMockupFile(absolutePath: string): boolean {
    const rel = path.relative(getMockupsAbsDir(), absolutePath);
    return (
      !rel.startsWith('..') && !path.isAbsolute(rel) && rel.endsWith('.tsx')
    );
  }

  function isPreviewTarget(relativeToMockups: string): boolean {
    return relativeToMockups
      .split(path.sep)
      .every((segment) => !segment.startsWith('_'));
  }

  async function discoverComponents(): Promise<Array<DiscoveredComponent>> {
    const files = await glob(`${MOCKUPS_DIR}/**/*.tsx`, {
      cwd: root,
      ignore: ['**/_*/**', '**/_*.tsx'],
    });

    return files.map((f) => ({
      globKey: './' + f.slice('src/'.length),
      importPath: path.posix.relative('src/.generated', f),
    }));
  }

  function generateSource(components: Array<DiscoveredComponent>): string {
    const entries = components
      .map(
        (c) =>
          `  ${JSON.stringify(c.globKey)}: () => import(${JSON.stringify(c.importPath)})`,
      )
      .join(',\n');

    return [
      '// This file is auto-generated by mockupPreviewPlugin.ts.',
      'type ModuleMap = Record<string, () => Promise<Record<string, unknown>>>;',
      'export const modules: ModuleMap = {',
      entries,
      '};',
      '',
    ].join('\n');
  }

  function shouldAutoRescan(pathname: string): boolean {
    return (
      pathname.includes('/components/mockups/') ||
      pathname.includes('/.generated/mockup-components')
    );
  }

  let refreshInFlight = false;
  let refreshQueued = false;

  async function refresh(): Promise<boolean> {
    if (refreshInFlight) {
      refreshQueued = true;
      return false;
    }

    refreshInFlight = true;
    let changed = false;
    try {
      const components = await discoverComponents();
      const newSource = generateSource(components);
      if (newSource !== currentSource) {
        currentSource = newSource;
        const generatedModuleAbsPath = getGeneratedModuleAbsPath();
        mkdirSync(path.dirname(generatedModuleAbsPath), { recursive: true });
        writeFileSync(generatedModuleAbsPath, currentSource);
        changed = true;
      }
    } finally {
      refreshInFlight = false;
    }

    if (refreshQueued) {
      refreshQueued = false;
      const followUp = await refresh();
      return changed || followUp;
    }

    return changed;
  }

  async function onFileAddedOrRemoved(): Promise<void> {
    await refresh();
  }

  return {
    name: 'mockup-preview',
    enforce: 'pre',

    configResolved(config) {
      root = config.root;
    },

    async buildStart() {
      await refresh();
    },

    async configureServer(viteServer) {
      await refresh();

      const mockupsAbsDir = getMockupsAbsDir();
      mkdirSync(mockupsAbsDir, { recursive: true });
      const configuredBasePath = process.env.BASE_PATH || '/__mockup/';

      viteServer.middlewares.use((req, res, next) => {
        const pathname = new URL(req.url ?? '/', 'http://127.0.0.1').pathname;
        if (isMockupRequest(pathname, configuredBasePath)) {
          next();
          return;
        }
        proxyToCorral(req, res);
      });

      viteServer.httpServer?.on('upgrade', (req, socket, head) => {
        const pathname = new URL(req.url ?? '/', 'http://127.0.0.1').pathname;
        if (!isMockupRequest(pathname, configuredBasePath)) {
          proxyUpgradeToCorral(req, socket, head);
        }
      });

      watcher = chokidar.watch(mockupsAbsDir, {
        ignoreInitial: true,
        awaitWriteFinish: {
          stabilityThreshold: 100,
          pollInterval: 50,
        },
      });

      watcher.on('add', (file) => {
        if (
          isMockupFile(file) &&
          isPreviewTarget(path.relative(mockupsAbsDir, file))
        ) {
          void onFileAddedOrRemoved();
        }
      });

      watcher.on('unlink', (file) => {
        if (isMockupFile(file)) {
          void onFileAddedOrRemoved();
        }
      });

      viteServer.middlewares.use((req, res, next) => {
        const requestUrl = new URL(req.url ?? '/', 'http://127.0.0.1');
        const pathname = requestUrl.pathname;
        const originalEnd = res.end.bind(res);

        res.end = ((...args: Parameters<typeof originalEnd>) => {
          if (res.statusCode === 404 && shouldAutoRescan(pathname)) {
            void refresh();
          }
          return originalEnd(...args);
        }) as typeof res.end;

        next();
      });
    },

    async closeWatcher() {
      if (watcher) {
        await watcher.close();
      }
    },
  };
}
