import { proxyRequest, type PagesFunctionContext } from './proxy'

/**
 * Cloudflare Pages Function entry point.
 *
 * `frontend/public/_routes.json` restricts invocation to `/api/*`, so static
 * assets are served directly and do not consume the Functions request quota.
 */
export const onRequest = (
  context: PagesFunctionContext,
): Promise<Response> => proxyRequest(context)
