import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Next.js 16 renamed the `middleware` file convention to `proxy` — see
// node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/proxy.md.
export function proxy(request: NextRequest) {
  const token = request.cookies.get("katha_token");
  const isAuthRoute =
    request.nextUrl.pathname.startsWith("/family/login") ||
    request.nextUrl.pathname.startsWith("/family/auth") ||
    // Step 1 of the onboarding wizard (email entry) runs before any cookie
    // exists — the page itself checks auth client-side to decide which
    // step to render. See lib/api.ts isAuthenticated().
    request.nextUrl.pathname.startsWith("/family/onboarding");

  if (!token && !isAuthRoute) {
    return NextResponse.redirect(new URL("/family/login", request.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/family/:path*"],
};
